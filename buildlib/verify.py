# -*- coding: utf-8 -*-
"""buildlib.verify — 包内前端资源一致性核对（自 build.py 逐字迁入）。"""
import io
import os

from buildlib.env import BASE

# v1.0.660（审查修复 B1）：onedir 形态下 PyInstaller 把 datas 放在
# <产物目录>/_internal/ 下（contents_directory 默认 _internal），exe 在上一级。
_ONEDIR_INTERNAL = "_internal"


def _source_assets():
    """源码树里需要进包的前端资源（相对 BASE 的路径，正斜杠）。"""
    src_assets = ["index.html"]
    for sub in ("css", "js"):
        d = os.path.join(BASE, sub)
        if os.path.isdir(d):
            src_assets += [sub + "/" + f for f in sorted(os.listdir(d))
                           if f.endswith(".css" if sub == "css" else ".js")]
    # v1.0.660（B1）：主 spec 的 datas 里还有 wecom_bridge/js（企微页面内的
    # 注入脚本）。它此前不在核对清单里——包内缺了它，界面照样能开，只是
    # 企微侧的注入脚本静默失效，正是"资源核对形同虚设"的形态。补上。
    _wb = os.path.join(BASE, "wecom_bridge", "js")
    if os.path.isdir(_wb):
        src_assets += ["wecom_bridge/js/" + f for f in sorted(os.listdir(_wb))
                       if f.endswith(".js")]
    return src_assets


def verify_packaged_assets(exe_path, onedir=None):
    """v1.0.619（审查⑧·打包一致性）：把**包内**的 index.html/css/*.js 与源码树逐字节比对。

    起因：spec 的 datas 打进 exe 后从不核对，历史上出过「dist 落后 4 个文件」——
    代码改了、exe 里还是旧前端，用户看到的是"改了没生效"，而排查方向会被带偏到 JS 逻辑。
    --smoke 只验 import，验不了资源新旧。

    v1.0.660（审查修复 B1）：**两种分发形态走两套比对口径**——
      · onefile：资源在 PyInstaller 归档（CArchive）里，按 TOC 名 extract；
      · onedir：资源是磁盘上的普通文件，放在 <exe 同级>/_internal/ 下，
        直接逐字节读文件比对（原来一律按 CArchive 读，对 onedir 语义不对，
        恒定读不到 → 判定"不一致"或直接跳过，等于没核对）。
    onedir=None 时按 exe 所在目录自动判别（有 _internal/ 即 onedir）。

    返回不一致的条目数；-1 表示这次没法比对（读不了归档/缺 PyInstaller），只警告不拦。
    """
    src_assets = _source_assets()
    if onedir is None:
        onedir = os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(exe_path)),
                                            _ONEDIR_INTERNAL))
    if onedir:
        return _verify_onedir(exe_path, src_assets)
    try:
        from PyInstaller.archive.readers import CArchiveReader
        arc = CArchiveReader(exe_path)
    except Exception as e:  # noqa: BLE001
        print(f"[assets] 警告：读不了 exe 归档，跳过包内资源一致性核对（{type(e).__name__}: {e}）")
        return -1
    diffs = []
    for rel in src_assets:
        src_file = os.path.join(BASE, rel.replace("/", os.sep))
        key = rel.replace("/", os.sep)
        try:
            got = arc.extract(key)
        except Exception:  # noqa: BLE001
            diffs.append(f"{rel}（包内没有这一项）")
            continue
        want = io.open(src_file, "rb").read()
        if got != want:
            diffs.append("%s（包内 %d 字节 / 源码 %d 字节）" % (rel, len(got), len(want)))
    if diffs:
        print(f"[assets] ❌ 包内前端资源与源码树不一致 {len(diffs)} 处（共比对 {len(src_assets)} 项）：")
        for d in diffs[:20]:
            print("[assets]   " + d)
        print("[build] ❌ 已判定本次产物不可发——请确认 spec 的 datas 覆盖到这些文件后重打")
        return len(diffs)
    print(f"[assets] 包内资源一致性：{len(src_assets)} 项与源码树逐字节一致 ✓")
    return 0


def _verify_onedir(exe_path, src_assets):
    """onedir 形态：资源是 <产物目录>/_internal/ 下的普通文件，逐字节比对。"""
    internal = os.path.join(os.path.dirname(os.path.abspath(exe_path)), _ONEDIR_INTERNAL)
    if not os.path.isdir(internal):
        print(f"[assets] ❌ onedir 产物缺 {_ONEDIR_INTERNAL}/ 目录（{internal}）——"
              f"包内资源无处可查，本次产物不可发")
        return 1
    diffs = []
    for rel in src_assets:
        src_file = os.path.join(BASE, rel.replace("/", os.sep))
        pkg_file = os.path.join(internal, rel.replace("/", os.sep))
        if not os.path.isfile(pkg_file):
            diffs.append(f"{rel}（包内没有这一项）")
            continue
        got = io.open(pkg_file, "rb").read()
        want = io.open(src_file, "rb").read()
        if got != want:
            diffs.append("%s（包内 %d 字节 / 源码 %d 字节）" % (rel, len(got), len(want)))
    if diffs:
        print(f"[assets] ❌ 包内前端资源与源码树不一致 {len(diffs)} 处"
              f"（共比对 {len(src_assets)} 项，onedir 形态）：")
        for d in diffs[:20]:
            print("[assets]   " + d)
        print("[build] ❌ 已判定本次产物不可发——请确认 spec 的 datas 覆盖到这些文件后重打")
        return len(diffs)
    print(f"[assets] 包内资源一致性（onedir）：{len(src_assets)} 项与源码树逐字节一致 ✓")
    return 0
