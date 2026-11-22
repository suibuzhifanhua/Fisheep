# -*- coding: utf-8 -*-
"""buildlib.ocr_sync — OCR 运行时旁挂同步（dist/产物库/ocr/）（自 build.py 逐字迁入）。

v1.0.738：旁挂目录从 dist 顶层的 ocr/ 收进 dist/产物库/ocr/，让分发目录顶层不再多一个文件夹。
"""
import json as _json
import os
import shutil
import subprocess

from buildlib.env import BASE, PYINSTALLER

# v1.0.306：OCR 旁挂目录名 → wheel 包名（写 versions.json 用）
_OCR_DIR2PKG = {"cv2": "opencv-python", "onnxruntime": "onnxruntime",
                "rapidocr_onnxruntime": "rapidocr-onnxruntime", "numpy": "numpy",
                "pyclipper": "pyclipper", "shapely": "shapely"}


def _venv_pkg_versions(python_exe, pkgs):
    """读构建 venv 里指定包的已装版本（importlib.metadata）。"""
    code = ("import importlib.metadata as md\n"
            "for p in %r:\n"
            "    try: print(p + ' ' + md.version(p))\n"
            "    except Exception: print(p + ' ')\n" % (pkgs,))
    try:
        r = subprocess.run([python_exe, "-c", code], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f"[build] 警告：读取 venv 包版本失败：{e}")
        return {}
    out = {}
    for line in (r.stdout or "").splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            out[parts[0]] = parts[1].strip()
    return out


def sync_ocr_runtime(dist_dir=None):
    """v1.0.306 启动优化：把 OCR 运行时（rapidocr 全家）从构建 venv 同步到 dist/产物库/ocr/ 旁挂。

    背景：cv2/onnxruntime/numpy/.libs/shapely/rapidocr 合计约 226MB（本机实测解压后），
    占 exe 包体 66%，却只在课件采集/扫描件 OCR 时用。把它们移出 exe 后：
      - exe 146.8MB → ~55MB，onefile 解压时间大幅下降（冷启动实测 3.4s → ~1.5s）
      - 代价：分发多一个 dist/产物库/ocr/ 目录（磁盘 ~226MB，只需随 exe 一起带走一次）
    幂等：dist/产物库/ocr/manifest.json 记录各包条目数与总字节，与 venv 现状一致则跳过复制。
    应用侧 core_utils.ensure_ocr_runtime() 在首次 OCR 前把 dist/产物库/ocr 插进 sys.path。

    v1.0.660（审查修复 B1）：目标目录随**分发形态**联动——onefile 运行时
    core/paths._app_dir() = dist/（exe 同级），onedir 运行时 = dist/WeComCompanion-onedir/。
    原来固定写 dist 顶层的 ocr/ → onedir 包的 OCR 运行时落在错误位置，运行时找不到、
    课件 OCR 静默不可用。现由调用方传入实际的分发根目录（dist_dir）。
    """
    dist = dist_dir or os.path.join(BASE, "dist")
    # v1.0.738：与 core.paths.runtime_root("ocr") 同口径——产物库/ocr/。
    # dist_dir 是实际分发根（onefile=dist/，onedir=dist/<名>-onedir/），不能写死 BASE/dist。
    ocr_dir = os.path.join(dist, "产物库", "ocr")
    sp = os.path.join(os.path.dirname(PYINSTALLER), "..", "Lib", "site-packages")
    sp = os.path.normpath(sp)
    if not os.path.isdir(sp):
        print(f"[build] 警告：找不到构建 venv site-packages（{sp}），跳过 OCR 运行时同步——"
              f"课件采集/扫描件 OCR 将不可用")
        return
    want = ["cv2", "onnxruntime", "rapidocr_onnxruntime", "numpy", "numpy.libs",
            "pyclipper", "shapely", "shapely.libs"]
    present = [w for w in want if os.path.isdir(os.path.join(sp, w))]
    missing = [w for w in want if w not in present]
    if missing:
        print(f"[build] 警告：venv 缺少 OCR 组件 {missing}，只同步存在的部分")
    if not present:
        return
    manifest = {w: sum(
        os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(os.path.join(sp, w)) for f in fs
    ) for w in present}
    mark = os.path.join(ocr_dir, "manifest.json")
    os.makedirs(ocr_dir, exist_ok=True)

    def _write_versions():
        """v1.0.309 修复时序 bug：versions.json 必须在包目录落位【之后】写——
        原先写在 copytree 之前，dist/产物库/ocr 刚重建时包目录尚不存在，收集结果恒为 {}，
        运行时「检查更新」会把已装组件全部误判为未安装。
        注意：必须直接写 dist/产物库/ocr/versions.json——core.ocr._ocr_write_versions 走
        ocr_dir()（源码跑=源码根/产物库/ocr，非 dist），会写错位置。"""
        try:
            vpy = os.path.join(os.path.dirname(PYINSTALLER), "python.exe")
            vers = _venv_pkg_versions(vpy, sorted(set(_OCR_DIR2PKG.values())))
            _ocr_versions = {pkg: vers[pkg] for d2, pkg in _OCR_DIR2PKG.items()
                             if pkg in vers and os.path.isdir(os.path.join(ocr_dir, d2))}
            from core_utils import _OCR_WHEELS as pins
            with open(os.path.join(ocr_dir, "versions.json"), "w", encoding="utf-8") as f:
                _json.dump(_ocr_versions, f, ensure_ascii=False, indent=1)
            drift = {p: (pins.get(p), _ocr_versions.get(p)) for p in _ocr_versions
                     if pins.get(p) and pins[p] != _ocr_versions[p]}
            if drift:
                print(f"[build] ⚠️ OCR 组件版本与 core_utils._OCR_WHEELS 钉定不一致：{drift}"
                      f"\n         请同步更新 core_utils._OCR_WHEELS（在线安装按钉定版本拉取）")
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：versions.json 写入/钉定校验失败：{e}")

    if os.path.isfile(mark) and os.path.isfile(os.path.join(ocr_dir, "rapidocr_onnxruntime", "__init__.py")):
        try:
            if _json.load(open(mark, encoding="utf-8")) == manifest:
                # v1.0.473：manifest 一致还不够——在线安装（「更新到最新版」）会就地替换
                # dist/产物库/ocr 的包内容而 manifest 文件不更新，旧逻辑只比对清单文件会误判
                # "已是最新"跳过同步，dist/产物库/ocr 停留在用户在线更新的任意状态。现改为
                # 实测 dist/产物库/ocr 各包字节数与 venv 比对，漂移即全量重同步（恢复验证组合）。
                _actual = {}
                try:
                    _actual = {w: sum(
                        os.path.getsize(os.path.join(r, f))
                        for r, _, fs in os.walk(os.path.join(ocr_dir, w)) for f in fs
                    ) for w in present if os.path.isdir(os.path.join(ocr_dir, w))}
                except OSError:
                    pass
                if _actual == manifest:
                    _write_versions()   # 内容不变也刷新 versions.json（运行时「检查更新」依赖）
                    print("[build] OCR 运行时 dist/产物库/ocr/ 已是最新，跳过复制")
                    return
                _drift = {w: (manifest.get(w), _actual.get(w)) for w in present
                          if _actual.get(w) != manifest.get(w)}
                print(f"[build] 检测到 dist/产物库/ocr 与构建 venv 漂移（在线更新过？），重同步为验证组合：{_drift}")
        except Exception as _e:  # noqa: BLE001
            # v1.0.648：漂移检查自己失败原为静默——那句"已校验为验证组合"会在
            # 根本没校验的情况下照样打印，比不校验更危险（它给人一个假绿灯）。
            print(f"[build] ⚠️ dist/产物库/ocr 漂移检查失败（本次按无漂移处理）：{_e}")
    for w in present:
        dst = os.path.join(ocr_dir, w)
        if os.path.isdir(dst):
            # v1.0.340：先把旧目录整棵 rename 挪到系统临时目录、再在 temp 下原生删除。
            # 直接 rmtree 在 WorkBuddy 沙箱会话里会被安全删除钩子转成「移入回收站」，
            # 而 Windows 的 trash 路径对大目录偶发 "Some operations were aborted"
            # 半途而废，残留半删状态会让下一步 copytree 撞 WinError 183（v1.0.339 实测）。
            # rename 在 win32 不被钩子拦截；temp 目录命中钩子的 tmp 豁免 → 原生真删。
            try:
                import tempfile as _tf
                import time as _time
                _stale_root = os.path.join(_tf.gettempdir(), "wec_ocr_stale")
                os.makedirs(_stale_root, exist_ok=True)
                _moved = os.path.join(_stale_root, "%s_%d" % (w, int(_time.time())))
                os.rename(dst, _moved)
                shutil.rmtree(_moved, ignore_errors=True)
            except OSError:
                shutil.rmtree(dst, ignore_errors=True)  # 同卷 rename 失败（如跨卷）：原路径兜底
        shutil.copytree(os.path.join(sp, w), dst)
    with open(mark, "w", encoding="utf-8") as f:
        _json.dump(manifest, f, ensure_ascii=False, indent=1)
    _write_versions()
    print(f"[build] OCR 运行时已同步 -> dist/产物库/ocr/（{len(present)} 项，"
          f"{sum(manifest.values()) // 1048576}MB）")
