# -*- coding: utf-8 -*-
"""buildlib.release — --release/--lean 剥离开发机数据、--prune-release 清理旧剥离份。

自 build.py 逐字迁入（v1.0.65x 拆分）。
"""
import hashlib
import io
import json
import os
import shutil
import time

from buildlib.env import BASE


def _release_excluded_dir():
    return os.path.join(BASE, "build", "_release_excluded")


def _release_excluded_stamps():
    """按时间戳目录名排序返回 (目录名, 字节数) 列表。"""
    root = _release_excluded_dir()
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        total = 0
        for dirpath, _dn, filenames in os.walk(d):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:  # noqa: BLE001
                    pass
        out.append((name, total))
    return out


def _print_release_excluded_stats():
    """v1.0.619（审查⑦）：--release/--lean 每次都把开发机的 config.json/产物库/日志/ocr
    **整份搬进** build/_release_excluded/<时间戳>/，以前跑完只报本次搬了什么，
    总量从不露面——于是那里能无声涨到几个 GB，而且装的正是最不该外发的那批数据（PII）。
    现在每次剥离后把累计家底报出来，并给出"只留最新一份"的显式清理入口。"""
    stamps = _release_excluded_stamps()
    if not stamps:
        return
    total_mb = sum(s for _, s in stamps) / 1048576.0
    print("[release] build/_release_excluded 累计 %d 份、共 %.1f MB（最早 %s，最新 %s）"
          % (len(stamps), total_mb, stamps[0][0], stamps[-1][0]))
    print("[release] 这些是开发机运行时数据（含对话/日志/config），**分发前别整目录外发**；"
          "确认不再需要回取时可清旧份：python build.py --prune-release --yes")


def release_mode_clean(lean=False):
    """v1.0.405（M4）：--release 发布模式——打包成功后从 dist 剥离开发机运行时数据。
    背景：DIST_PRESERVE 保留 config.json/产物库 是为「用户升级不丢配置」，但把 dist
    整目录发给别人时，开发机跑过的 config.json（内网模型地址/端口拓扑）与产物库
    （日志/对话/审计）会跟着泄露。v1.0.739 起 config 在 产物库/ 内，随整树一起剥离；
    发布产物 = exe + 产物库/（含 ocr 运行时），config 首启在 产物库/config.json 自动生成。
    v1.0.473（--lean 精简发布）：lean=True 时连 OCR 运行时（~271MB）一并移出——
    发布产物只剩 exe（~40MB）；OCR 组件在用户首次触发课件采集/扫描件识别时
    自动从国内镜像（阿里云/清华/北外 → PyPI 兜底）按需下载（core/ocr.py 已有
    完整逻辑，含版本钉定与断点续装）。自用/开发分发不带 --lean，保持开箱即用。
    v1.0.738：OCR 运行时已从 dist 顶层 ocr/ 收进 产物库/ocr/。--release 剥的是
    **开发机数据**，OCR 是**分发内容**——非 lean 时把整个产物库搬走后需把 ocr 段
    单独回移，否则开箱即用的 OCR 会被误当成用户数据一起剥掉；lean 时才一起剥。"""
    dist = os.path.join(BASE, "dist")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    excluded = os.path.join(BASE, "build", "_release_excluded", stamp)
    moved = []
    # v1.0.725：cdp_profiles（绑定浏览器的专用 profile）含开发机浏览数据，一并剥离
    for name in ("config.json", "产物库", "cdp_profiles", "companion.log",
                 "companion.log.1", "companion.log.2", "companion.log.3",
                 "companion.log.4", "companion.log.5"):
        src = os.path.join(dist, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(excluded, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, dst)
            moved.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[release] 警告：{name} 移出失败（保留原位，请手动检查）：{e}")
    # v1.0.738：上面 for 循环把整个 产物库/（含 ocr 段）搬到了 excluded。
    # 非 lean 时 OCR 是分发内容，必须单独回移；lean 时它留在 excluded 即完成剥离。
    _excluded_ocr = os.path.join(excluded, "产物库", "ocr")
    if not lean and os.path.isdir(_excluded_ocr):
        try:
            _back = os.path.join(dist, "产物库", "ocr")
            os.makedirs(os.path.dirname(_back), exist_ok=True)
            shutil.move(_excluded_ocr, _back)
            print("[release] OCR 运行时保留在 产物库/ocr/（开箱即用，未随开发机数据剥离）")
        except Exception as e:  # noqa: BLE001
            print(f"[release] 警告：OCR 运行时回移失败（发布产物将按需在线下载）：{e}")
    if lean:
        # 兼容旧布局：老版本把 OCR 放在 dist/ocr/ 顶层，--lean 也要一并剥掉
        _legacy_ocr = os.path.join(dist, "ocr")
        if os.path.isdir(_legacy_ocr):
            dst = os.path.join(excluded, "ocr")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.move(_legacy_ocr, dst)
                moved.append("ocr/")
                print(f"[lean] dist/ocr（旧布局 OCR 运行时）已移出发布产物 -> {dst}")
            except Exception as e:  # noqa: BLE001
                print(f"[lean] 警告：ocr/ 移出失败（保留原位）：{e}")
        if os.path.isdir(_excluded_ocr):
            moved.append("产物库/ocr/")
        print("[lean] 用户首次使用课件采集/扫描件识别时将自动从国内镜像下载（下载约 81MB、占用约 226MB，一次性）")
    print(f"[release] 发布模式：已剥离 {moved or '（无）'} -> {excluded}")
    try:
        _print_release_excluded_stats()
    except Exception as e:  # noqa: BLE001
        print(f"[release] 警告：累计体积统计失败（不影响剥离结果）：{e}")
    if lean:
        print("[release] 精简发布产物 = 仅 exe；OCR/扩展组件按需自动下载")
    else:
        print("[release] dist 现在只含 exe + 产物库/（含 ocr 运行时），首次运行在 产物库/config.json 自动生成默认 config")


def prune_release_excluded(auto_yes=False):
    """维护动作：删掉 build/_release_excluded 里除最新一份以外的旧剥离数据。
    逐份列出后再确认；**绝不碰** _dist_backup/_rollback/_chat_archive（那三处是回滚/存档，
    用户规则里明确"只列不删"）。"""
    stamps = _release_excluded_stamps()
    if len(stamps) <= 1:
        print("[prune] 只有 %d 份剥离数据，没有可清的旧份（最新一份始终保留）" % len(stamps))
        return 0
    doomed = stamps[:-1]
    total_mb = sum(s for _, s in doomed) / 1048576.0
    print("[prune] 拟删除 %d 份（共 %.1f MB），保留最新一份 %s：" %
          (len(doomed), total_mb, stamps[-1][0]))
    for name, size in doomed:
        print("        %s  %.1f MB" % (name, size / 1048576.0))
    if not auto_yes:
        try:
            ans = input("确认删除以上旧份？[y/N] ").strip().lower()
        except Exception:  # noqa: BLE001
            ans = "n"
        if ans != "y":
            print("[prune] 未删除任何文件（需要 --yes 或交互输入 y）")
            return 1
    removed = 0
    for name, _s in doomed:
        d = os.path.join(_release_excluded_dir(), name)
        try:
            shutil.rmtree(d)
            removed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[prune] 删除失败（保留）{name}：{e}")
    print(f"[prune] 已删除 {removed}/{len(doomed)} 份，释放约 {total_mb:.1f} MB")
    return 0

# ---------- v1.0.756（评审 P2-1 字面补做）：发布元数据自动化 ----------
# 报告要求「在公共 README 明示公共镜像对应版本 X」，并把 RELEASE.json 从人工
# 步骤变成构建自动产物。这里每次镜像重建时幂等写：
#   · RELEASE.json：构建号恒取 version.py、全树内容指纹、文件数、生成时间；
#     发布号 release_tag / released_at / summary 只从 publish/RELEASE.json 同步，
#     **脚本绝不自己产生发布号、绝不推送**（docs/release-policy.md 第 2/3 节）。
#   · 公共 README：顶部插入/替换一条版本标记，重复调用不叠加。
# 口径：构建号 = 本地每次打包递增；发布号 = 人工对外发版时才填。
RELEASE_JSON_NAME = "RELEASE.json"
README_MARKER = "> 本公共镜像对应构建版本"


def _build_version(base=None):
    """读 version.py 的 __version__（不引入正则，避免源码里堆转义）。"""
    base = base or BASE
    try:
        with io.open(os.path.join(base, "version.py"), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("__version__"):
                    parts = ln.split("=")
                    if len(parts) >= 2:
                        return parts[1].strip().strip('"').strip("'")
        return None
    except OSError:
        return None


def _mirror_digest(mirror_dir):
    """镜像全树内容指纹（排除 RELEASE.json 自身，避免自指抖动）。

    按相对路径排序逐文件喂 sha256；返回 (hexdigest, 文件总数)。
    """
    h = hashlib.sha256()
    n_files = 0
    for root, dirnames, filenames in os.walk(mirror_dir):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fn in sorted(filenames):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, mirror_dir).replace(os.sep, "/")
            n_files += 1
            if rel == RELEASE_JSON_NAME:
                continue
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            try:
                with io.open(p, "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<unreadable>")
            h.update(b"\n")
    # n_files 表示「写入 RELEASE.json 之后」的镜像文件数：首次调用时该文件还不存在，
    # 补 1 保证同一份镜像无论重建几次，这个计数都稳定。
    if not os.path.isfile(os.path.join(mirror_dir, RELEASE_JSON_NAME)):
        n_files += 1
    return h.hexdigest(), n_files


def _published_release(base):
    """人工发布号的单一事实源：publish/RELEASE.json（缺失/损坏视为未发版）。"""
    p = os.path.join(base, "publish", RELEASE_JSON_NAME)
    if not os.path.isfile(p):
        return {}
    try:
        with io.open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_release_metadata(mirror_dir, base=None):
    """幂等写 RELEASE.json + 公共 README 版本标记。返回 0=成功，非 0=失败。

    fail-closed：读不到版本 / 镜像缺 README 时拒绝写半个元数据并让镜像保留上一版。
    """
    base = base or BASE
    ver = _build_version(base)
    if not ver:
        print("[release] 读不到 version.py::__version__，发布元数据不写")
        return 1
    readme = os.path.join(mirror_dir, "README.md")
    if not os.path.isfile(readme):
        print("[release] 镜像缺 README.md，发布元数据不写")
        return 1
    try:
        with io.open(readme, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:  # noqa: BLE001
        print("[release] 公共 README 不可读：%s" % e)
        return 1
    marker = "%s **v%s**（生成于 %s）。发布号与发版说明见 RELEASE.json；镜像由构建自动生成，勿手改。" % (
        README_MARKER, ver, time.strftime("%Y-%m-%d"))
    lines = [l for l in lines if not l.startswith(README_MARKER)]
    at = 1 if lines and lines[0].startswith("# ") else 0
    lines[at:at] = [marker]
    try:
        with io.open(readme, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
    except OSError as e:  # noqa: BLE001
        print("[release] 公共 README 写入失败：%s" % e)
        return 1
    digest, n_files = _mirror_digest(mirror_dir)
    prev = _published_release(base)
    data = {
        "schema": 1,
        "build_version": ver,
        "release_tag": prev.get("release_tag"),
        "released_at": prev.get("released_at"),
        "summary": prev.get("summary"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "version.py::__version__",
        "mirror_files": n_files,
        "mirror_tree_sha256": digest,
        "note": "构建号每次打包自动同步；release_tag 仅由人工发版填写，本脚本不产生发布号、不推送",
    }
    try:
        with io.open(os.path.join(mirror_dir, RELEASE_JSON_NAME), "w",
                     encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except OSError as e:  # noqa: BLE001
        print("[release] RELEASE.json 写入失败：%s" % e)
        return 1
    print("[release] 发布元数据已写：v%s / %d 文件 / 公共 README 标记已同步"
          % (ver, n_files))
    return 0
