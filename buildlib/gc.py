# -*- coding: utf-8 -*-
"""buildlib.gc — 构建垃圾回收（v1.0.743）。

三类垃圾的根因都是「只写不清」的机制：
  S2  build/_dist_backup/ 旧布局孤儿条目——backup_dist_runtime 是覆盖合并，
      从不清理旧条目；v1.0.738 起 dist 顶层 cdp_profiles 已并入 产物库/，
      备份里的旧条目永不再被重写（实测 1.6G 死重）。
  S2b 备份里「不再备份」的 产物库 段——产物库/组件（v1.0.677 起）与
      产物库/cdp_profiles（v1.0.743 起，S1：浏览器档案可再生、非用户数据，
      却占备份 I/O 大头）都不再进备份，备份侧残留副本=死重。
  S3  build/_rollback/ 非机制文件——机制文件 = 当前品牌名 exe（紧邻上一版，
      backup_previous_exe）+ versions.json 引用的版本化 exe（version_store
      版本管理回滚靠它还原 exe）；其余（旧品牌名/版本库外版本化 exe）永留=垃圾。
      v1.0.745 修正：744 首轮把 versions.json 引用的 Fisheep_1.0.743.exe 当残留
      删了（S3 初版口径只认品牌名）——判定基准改为「versions.json 引用=机制」。
  G3  live dist/产物库/cdp_profiles 浏览器缓存——唯一随使用无限增长的垃圾；
      可再生目录（Cache/Code Cache/GPUCache/Service Worker/crx 缓存等）清掉，
      登录态（Cookies/Login Data/Local State）保留。
  G4  build/_chat_archive 是用户数据（归档不是删除）→ **只列不删**。

时机：构建收尾（应用已退出、无文件锁）或 `python build.py --gc` 单独跑。
两者都在构建进程内执行——build.py 启动时已清 _SANDBOX_VARS
（见 build.py main() 顶部注释），平台安全删除钩子对其无效。
"""
import contextlib
import os
import shutil

from buildlib.env import BASE


def _paths(base):
    """四个 gc 作用目录（相对 base，默认项目根）。"""
    base = base or BASE
    return (os.path.join(base, "dist"),
            os.path.join(base, "build", "_dist_backup"),
            os.path.join(base, "build", "_rollback"),
            os.path.join(base, "build", "_chat_archive"))


# G3：live 档案内可再生的缓存目录（相对档案根）。不在清单里的=不碰。
_LIVE_CACHE_DIRS = (
    "component_crx_cache", "extensions_crx_cache",
    "ProvenanceData", "ProvenanceDataAllowList", "ProvenanceDataTensors",
    "Crashpad", "GPUPersistentCache", "ShaderCache", "GrShaderCache",
    "Default/Cache", "Default/Code Cache", "Default/GPUCache", "Default/Service Worker",
)

# S2b：不再进备份的 产物库 段（可再生运行时，同口径见 buildlib.backup._backup_ignore）。
_UNBACKED_SUBPATHS = (
    os.path.join("产物库", "组件"),               # v1.0.677 口径
    os.path.join("产物库", "cdp_profiles"),       # v1.0.743（S1）
)


def _dir_size(path):
    """目录下文件体积总和（不可读文件跳过）。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for _fn in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, _fn))
    return total


def _entry_size(path):
    if os.path.isfile(path):
        with contextlib.suppress(OSError):
            return os.path.getsize(path)
        return 0
    return _dir_size(path)


def prune_backup_orphans(dist_dir=None, backup_dir=None):
    """S2：_dist_backup 顶层**目录**条目在 dist 无同名源 = 可证孤儿（旧布局残留）→ 删。

    文件（config.json / companion.log.* 等）一律不碰——它们是 config 最后防线的
    源（backup_dist_runtime 的主动恢复逻辑读它），且 <50KB 不占空间。
    返回 (删除数, 释放字节)。
    """
    removed, freed = 0, 0
    if not (dist_dir and backup_dir and os.path.isdir(dist_dir) and os.path.isdir(backup_dir)):
        return removed, freed
    for name in os.listdir(backup_dir):
        p = os.path.join(backup_dir, name)
        if not os.path.isdir(p) or os.path.exists(os.path.join(dist_dir, name)):
            continue
        with contextlib.suppress(OSError):
            freed += _dir_size(p)
            shutil.rmtree(p)
            removed += 1
            print(f"[gc] _dist_backup/{name} = 旧布局孤儿条目（dist 无同名源，永不再备份）→ 删")
    return removed, freed


def prune_unbacked_copies(backup_dir=None):
    """S2b：备份目录里「不再备份」的 产物库 段副本（旧逻辑残留死重）→ 删。

    S1 起这两段不会再被 backup_dist_runtime 写入；出现即残留。
    返回 (删除数, 释放字节)。
    """
    removed, freed = 0, 0
    if not (backup_dir and os.path.isdir(backup_dir)):
        return removed, freed
    for rel in _UNBACKED_SUBPATHS:
        p = os.path.join(backup_dir, rel)
        if not os.path.isdir(p):
            continue
        with contextlib.suppress(OSError):
            freed += _dir_size(p)
            shutil.rmtree(p)
            removed += 1
            print(f"[gc] _dist_backup/{rel.replace(os.sep, '/')} = 不再备份段副本（可再生运行时）→ 删")
    return removed, freed


def _versioned_exe_names(base_dir=None):
    """versions.json 引用的版本化 exe 名集合（版本管理回滚的机制文件，不可删）。

    version_store.record_version 把每版 exe 备份成 {APP_ID}_{ver}.exe 并登记在
    产物库/版本/versions.json 的 exe 字段，switch_version 靠这些文件还原 exe。
    历史手工清理（v1.0.742 轮）曾把它们误判成「手工残留」删掉——判定基准由此
    修正为「versions.json 引用 = 机制」。读取失败=空集合（保守告警，不静默）。
    """
    import json
    p = os.path.join(base_dir or BASE, "产物库", "版本", "versions.json")
    try:
        with open(p, encoding="utf-8") as f:
            recs = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[gc] 警告：读 versions.json 失败（{e}）——版本化 exe 引用无法核对，仅保留当前品牌名")
        return set()
    names = set()
    for r in (recs if isinstance(recs, list) else []):
        if isinstance(r, dict) and r.get("exe"):
            names.add(os.path.basename(str(r["exe"])))
    return names


def prune_rollback_junk(rollback_dir=None, keep_name=None, keep_names=None):
    """S3：_rollback 机制文件 = 当前品牌名 exe（紧邻上一版，见 backup_previous_exe）
    + versions.json 引用的版本化 exe（版本管理回滚，见 _versioned_exe_names）；
    其余文件全是历史残留（旧品牌名/版本库外版本化 exe）→ 删（任何版本可由
    源码+git 重打）。返回 (删除数, 释放字节)。"""
    removed, freed = 0, 0
    if not (rollback_dir and os.path.isdir(rollback_dir)):
        return removed, freed
    if keep_name is None:
        from core.branding import EXE_NAME
        keep_name = EXE_NAME
    keep = {keep_name}
    if keep_names is None:
        keep_names = _versioned_exe_names()
    keep |= set(keep_names)
    for name in os.listdir(rollback_dir):
        if name in keep:
            continue
        p = os.path.join(rollback_dir, name)
        with contextlib.suppress(OSError):
            freed += _entry_size(p)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            removed += 1
            print(f"[gc] _rollback/{name} = 非机制残留（仅保留 {keep_name}）→ 删")
    return removed, freed


def gc_live_caches(dist_dir=None):
    """G3：清 live 产物库/cdp_profiles 的可再生浏览器缓存目录（登录态保留）。

    构建期应用已退出，无文件锁；个别锁文件静默跳过（suppress，下次再清）。
    返回释放字节。
    """
    freed = 0
    if not dist_dir:
        return freed
    root = os.path.join(dist_dir, "产物库", "cdp_profiles")
    if not os.path.isdir(root):
        return freed
    for base in ("edge", "chrome"):
        for rel in _LIVE_CACHE_DIRS:
            p = os.path.join(root, base, rel)
            if not os.path.isdir(p):
                continue
            with contextlib.suppress(OSError):
                freed += _dir_size(p)
                shutil.rmtree(p)
                print(f"[gc] live 档案 {base}/{rel.replace(os.sep, '/')} 缓存 → 清")
    return freed


def chat_archive_report(chat_dir=None):
    """G4：_chat_archive 是用户数据（归档不是删除）→ 只列份数/体积，**绝不删**。"""
    if not (chat_dir and os.path.isdir(chat_dir)):
        return 0, 0
    n = len([x for x in os.listdir(chat_dir)
             if os.path.isdir(os.path.join(chat_dir, x))])
    return n, _dir_size(chat_dir)


def _gc_candidates(base_dir=None):
    """列出 (标签, 名称, 字节) 三类可删候选（dry-run 与执行共用的判定口径）。"""
    dist, bak, rb, _chat = _paths(base_dir)
    from core.branding import EXE_NAME
    cands = []
    if os.path.isdir(bak) and os.path.isdir(dist):
        for name in os.listdir(bak):
            p = os.path.join(bak, name)
            if os.path.isdir(p) and not os.path.exists(os.path.join(dist, name)):
                cands.append(("S2 备份孤儿条目", f"_dist_backup/{name}", _dir_size(p)))
    for rel in _UNBACKED_SUBPATHS:
        p = os.path.join(bak, rel)
        if os.path.isdir(p):
            cands.append(("S2 不再备份段副本",
                          f"_dist_backup/{rel.replace(os.sep, '/')}", _dir_size(p)))
    if os.path.isdir(rb):
        _keep = {EXE_NAME} | _versioned_exe_names(base_dir)
        for name in os.listdir(rb):
            if name not in _keep:
                cands.append(("S3 回滚残留", f"_rollback/{name}",
                              _entry_size(os.path.join(rb, name))))
    root = os.path.join(dist, "产物库", "cdp_profiles")
    if os.path.isdir(root):
        for base in ("edge", "chrome"):
            for rel in _LIVE_CACHE_DIRS:
                p = os.path.join(root, base, rel)
                if os.path.isdir(p):
                    cands.append(("G3 live 档案缓存",
                                  f"cdp_profiles/{base}/{rel.replace(os.sep, '/')}", _dir_size(p)))
    return cands


def run_gc(base_dir=None, auto_yes=False):
    """--gc 维护模式：默认 dry-run（列清单再确认）；auto_yes 直接执行。

    返回删除条数（未执行=0）。
    """
    cands = _gc_candidates(base_dir)
    total = sum(sz for _t, _n, sz in cands)
    print("[gc] 垃圾回收候选：")
    if not cands:
        print("  （无）")
    for tag, name, size in cands:
        print(f"  {tag}  {name}  {size / 1048576:.0f}M")
    _n, _sz = chat_archive_report(_paths(base_dir)[3])
    print(f"  G4 _chat_archive：{_n} 份 / {_sz / 1048576:.0f}M（用户数据，只列不删）")
    print(f"  合计可释放 ≈ {total / 1048576 / 1024:.2f}G")
    if not cands:
        return 0
    if not auto_yes:
        try:
            ans = input("[gc] 确认删除以上条目？[y/N] ").strip().lower()
        except Exception:  # noqa: BLE001（无 stdin / 被掐断 → 按未确认处理）
            ans = "n"
        if ans != "y":
            print("[gc] 未确认——保持现状")
            return 0
    dist, bak, rb, _chat = _paths(base_dir)
    r1, f1 = prune_backup_orphans(dist_dir=dist, backup_dir=bak)
    r2, f2 = prune_unbacked_copies(backup_dir=bak)
    r3, f3 = prune_rollback_junk(rollback_dir=rb)
    f4 = gc_live_caches(dist_dir=dist)
    print(f"[gc] done：删 {r1 + r2 + r3} 项 / 释放 ≈{(f1 + f2 + f3 + f4) / 1048576 / 1024:.2f}G")
    return r1 + r2 + r3


def gc_after_build(base_dir=None):
    """构建收尾自动 gc（S2+S2b+S3+G3）：全部是**可证安全**的删除口径
    （孤儿=dist 无源 / 不再备份段 / 非机制 exe / 可再生缓存），无需确认——
    与既有 prune_stale_component_copy 同口径。G4 只报告。失败由调用方兜底。"""
    dist, bak, rb, chat = _paths(base_dir)
    r1, f1 = prune_backup_orphans(dist_dir=dist, backup_dir=bak)
    r2, f2 = prune_unbacked_copies(backup_dir=bak)
    r3, f3 = prune_rollback_junk(rollback_dir=rb)
    f4 = gc_live_caches(dist_dir=dist)
    if r1 or r2 or r3 or f4:
        print(f"[gc] 构建收尾垃圾回收：删 {r1 + r2 + r3} 项 / 释放 ≈{(f1 + f2 + f3 + f4) / 1048576:.0f}M")
    _n, _sz = chat_archive_report(chat)
    print(f"[gc] _chat_archive：{_n} 份 / {_sz / 1048576:.0f}M（用户数据，只列不删）")
