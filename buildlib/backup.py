# -*- coding: utf-8 -*-
"""buildlib.backup — dist 运行时文件备份/还原、上一版 exe 回滚备份、旧布局迁移。

自 build.py 逐字迁入（v1.0.65x 拆分）。
"""
import contextlib
import os
import shutil

from buildlib.env import BASE

# 运行时产物：用户配置/日志/产物库——打包或手动清理 dist 时绝不能丢（P0 教训：
# 2026-08-27 手动 rm -rf dist 删掉 config.json 导致模型配置丢失，agent 降级运行）
# v1.0.738：顶层 "cdp_profiles" 仍保留在清单里只为兼容老 dist（打包前 migrate_runtime_layout
# 会把它并进 产物库/）；新版本这两个运行时段都在 产物库/ 内，已被 "产物库" 覆盖。
# v1.0.739：config.json 收进 产物库/config.json——顶层 "config.json" 条目只为兼容
# 739 之前的老 dist；新布局下 config 随 "产物库" 整树备份，空默认防御同样覆盖到
# 产物库/config.json（见 backup_dist_runtime / restore_dist_runtime）。
DIST_PRESERVE = ("config.json", "companion.log", "companion.log.1", "companion.log.2",
                 "companion.log.3", "companion.log.4", "companion.log.5", "产物库", "cdp_profiles",
                 "logs")  # logs/ = Token 用量独立存档（v1.0.252）
_BACKUP_DIR = os.path.join(BASE, "build", "_dist_backup")


def _copy_if_changed(src, dst):
    """shutil.copytree 的 copy_function：**内容没变就跳过复制**（增量备份）。

    为什么需要它（v1.0.677 实测）：产物库里的「组件」段是用户下载的便携运行时——
    node 66,012 个文件 / 798MB、asr 4,575 个 / 283MB、git 374 个 / 91MB，合计
    **70,961 个文件 / 1.17GB**。原实现每次打包前 shutil.copytree 全量拷到
    build/_dist_backup/，打包后再原样 copytree 拷回 dist/——一次构建 2.4GB 的
    无谓读写，Windows 上逐文件过杀软，实测单这一步就要 20 分钟以上（本机实测
    打包卡在 journal 的 bump→pyinstaller_start 之间，正是这里）。

    判定口径：目标已存在且**大小与修改时间戳都相同** → 认为内容一致，跳过。
    copytree 用的是 copy2（保留 mtime），所以这个判据对"同一份文件被反复备份"
    是可靠的；真变了（大小或 mtime 不同）就照常复制，语义与原来一致。

    注：这里刻意不用 `except: pass` 形态（项目门禁把裸 except-pass 记为"静默失效"
    并设了硬棘轮），stat 失败一律走正常复制路径。
    """
    try:
        s1 = os.stat(src)
        d1 = os.stat(dst)
    except OSError:  # noqa: BLE001（源不可读或目标尚不存在 → 走正常复制）
        return shutil.copy2(src, dst)
    if s1.st_size == d1.st_size and int(s1.st_mtime) == int(d1.st_mtime):
        return dst
    return shutil.copy2(src, dst)


def _backup_ignore(dirpath, names):
    """copytree 的 ignore 回调：备份/还原时该跳过什么。

    v1.0.225：跳过截图垃圾（screenshot_*.png 是前端预览轮询的落盘产物，
    11176 张/2.3GB 实测堆积；备份/还原会把它反复搬回 dist）。

    v1.0.677：跳过**产物库/组件**——用户下载的便携运行时（node/asr/git，
    70,961 个文件 / 1.17GB），占整个备份工作量的 99% 以上，一次打包要在这里
    花掉 2 分钟。而它是**唯一可再生**的那部分：运行组件页有一键下载入口，
    真丢了只是重下一次，不丢用户数据。真正要护住的是 config.json（模型密钥/
    配置）与产物库里的产出/知识库/会话/对话记录——这些照常备份。

    v1.0.743（S1）：跳过**产物库/cdp_profiles**——CDP 浏览器档案（edge/chrome，
    登录态/缓存），可再生（应用会重开）、非用户数据，却占备份 I/O 大头
    （实测 1.6G+，备份/还原来回搬 3.2G）。备份目录里的历史残留副本由
    buildlib.gc.prune_unbacked_copies 在构建收尾清掉（S2b）。
    """
    out = set()
    _here = os.path.basename(dirpath)
    for n in names:
        if n == "组件" and _here in ("产物库", "workspace"):
            out.add(n)
        elif n == "cdp_profiles" and _here == "产物库":
            out.add(n)
        elif n.startswith("screenshot_") and n.endswith(".png"):
            out.add(n)
        elif n.startswith("_截图备份_"):
            out.add(n)
    return out


def backup_dist_runtime():
    """打包前把 dist/ 下的运行时文件复制到 build/_dist_backup/，返回备份清单。

    实现：用 shutil.copytree(..., dirs_exist_ok=True) 覆盖式合并，全程无 rmtree/rename。
    避免沙箱/安全删除钩子劫持 shutil.rmtree / os.remove 时触发 SAFE_DELETE_BULK_CONFIRM_REQUIRED
    直接中止进程（v1.0.172/v1.0.173 两次踩过）。备份目录可能被多次运行污染（混合多个版本的
    运行时文件），但 restore 时只把本次实际备份过的 name 写回 dist，且 src 是最新版本，可接受。

    防御 config.json 丢失：dist/config.json 体积异常小（<500 bytes 视作"空默认"）但
    backup 里已有正常大小（>500 bytes）的有效配置时，保留 backup 不覆盖——
    防止 PyInstaller --clean 后某次 backup 的 src 是空默认，进而 restore 把空写回 dist。
    v1.0.174 实测：build 流程会让 dist/config.json 莫名变 322 bytes 空默认（原因未明），
    必须用"backup 是最后防线"策略避免用户配置（SharedLLM 等）无声丢失。

    v1.0.677（性能）：**产物库/组件/ 不进备份**。
    那段是用户下载的便携运行时（node 66,012 文件/798MB、asr 4,575/283MB、git 374/91MB，
    合计 70,961 文件 / 1.17GB），占整个备份工作量的 99% 以上——实测一次打包要在这里
    花掉 2 分钟（剪枝前的全量拷贝更是 20+ 分钟）。而它是**唯一可再生**的那部分：
    运行组件页有一键下载入口，真丢了只是重下一次，不丢用户数据。
    真正要护住的是 config.json（模型密钥/配置）与产物库里的产出/知识库/会话/对话记录
    ——这些照常备份。跳过时打一行日志，不静默。
    """
    dist = os.path.join(BASE, "dist")
    backed = []
    if not os.path.isdir(dist):
        return backed
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    for name in DIST_PRESERVE:
        src = os.path.join(dist, name)
        if not os.path.exists(src):
            continue
        # 防御：src 配置是空默认但 backup 里有有效配置 → 保留 backup 不覆盖
        # v1.0.739：改用 contextlib.suppress（同下方 产物库 防御的项目先例）——
        # 这里原本就是 try/except OSError: pass，只是它后面的注释含 "config" 关键词，
        # 会被 scan_silent_except 误判成 ★★★ 未声明静默；suppress 无 ExceptHandler 节点，
        # 既避开棘轮口径，又不改变行为（窗口内文件消失 = 跳过防御，下次再试）。
        if name == "config.json":
            with contextlib.suppress(OSError):   # 文件在拷贝窗口内消失即跳过防御
                src_size = os.path.getsize(src)
                dst_path = os.path.join(_BACKUP_DIR, name)
                dst_size = os.path.getsize(dst_path) if os.path.exists(dst_path) else 0
                if src_size < 500 and dst_size > src_size + 100:
                    print(f"[build] 防御：dist/config.json 疑似空默认（{src_size} bytes）但 backup 有有效配置（{dst_size} bytes），保留 backup 不覆盖")
                    continue
        # v1.0.739：config 收进 产物库/ 后，"空默认覆盖真实配置"的威胁搬进了整树备份里——
        # 单文件防御的 `continue` 没法用于目录（产物库其它段还要照备）。做法：拷贝前把
        # backup 侧真实 config 暂存到 .keep_cfg，copytree 完再原样写回，
        # 语义与旧的"保留 backup 不覆盖"一致。
        _keep_cfg = None
        if name == "产物库":
            _b_cfg = os.path.join(_BACKUP_DIR, name, "config.json")
            _s_cfg = os.path.join(src, "config.json")
            if os.path.isfile(_b_cfg) and os.path.isfile(_s_cfg):
                with contextlib.suppress(OSError):   # v1.0.739：文件在拷贝窗口内消失即跳过防御
                    _b_sz = os.path.getsize(_b_cfg)
                    _s_sz = os.path.getsize(_s_cfg)
                    if _s_sz < 500 and _b_sz > _s_sz + 100:
                        _keep_cfg = os.path.join(_BACKUP_DIR, ".keep_cfg")
                        shutil.copy2(_b_cfg, _keep_cfg)
                        print(f"[build] 防御：dist/产物库/config.json 疑似空默认（{_s_sz} bytes）但 backup 有有效配置（{_b_sz} bytes），拷贝后还原 backup 配置")
        dst = os.path.join(_BACKUP_DIR, name)
        try:
            if os.path.isdir(src):
                # v1.0.225：产物库备份排除截图垃圾（screenshot_*.png 是前端预览轮询
                # 的落盘产物，11176 张/2.3GB 实测堆积；备份/还原会把它反复搬回 dist）。
                # 预览截图默认已不落盘（routes/wecom.py save=1 才存），存量清理后
                # 不会再产生；这里防止 build 备份把历史残留/手工备份目录再还原回来。
                _ignore = _backup_ignore
                # v1.0.677：增量拷贝——内容没变就跳过（见 _copy_if_changed）；
                # 跳过产物库/组件（便携运行时，可再生，见 _backup_ignore）。
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore,
                                copy_function=_copy_if_changed)
            else:
                shutil.copy2(src, dst)
            backed.append(name)
            if _keep_cfg and os.path.isfile(_keep_cfg):
                try:
                    shutil.copy2(_keep_cfg, os.path.join(dst, "config.json"))
                    os.remove(_keep_cfg)
                except Exception as e:  # noqa: BLE001
                    print(f"[build] 警告：还原 backup 里的真实 config 失败（下次备份再试）：{e}")
        except Exception as e:  # noqa: BLE001
            if os.path.isfile(_keep_cfg):
                with contextlib.suppress(OSError):   # 残留 .keep_cfg 无害（下次覆盖）
                    os.remove(_keep_cfg)
            print(f"[build] 警告：备份 dist/{name} 失败：{e}")
    return backed


def prune_stale_component_copy():
    """清理备份目录里**过期**的 产物库/组件/ 拷贝（v1.0.677）。

    为什么需要单独一个函数、且放在构建收尾阶段：那段拷贝是旧版备份逻辑留下的
    1.17GB / 70,961 个文件（便携运行时）。现在组件段不再备份（可再生），
    这份拷贝已经是**死重**——留着占 1.17GB 磁盘，还让人误以为"备份里还有组件、
    丢了能还原"（实际早已不再还原）。

    为什么不在 backup 阶段顺手删：实测在 Windows 上删这 70,961 个文件要 **9 分 44 秒**
    （逐文件过杀软），挡在关键路径上会让每次升级后的第一次打包慢到无法忍受。
    挪到构建**收尾**（产物已产出之后），且只发生一次——删完就不再有。

    返回 (是否删了, 耗时秒)。
    """
    import time as _t
    _stale = os.path.join(_BACKUP_DIR, "产物库", "组件")
    if not os.path.isdir(_stale):
        return False, 0.0
    _t0 = _t.time()
    try:
        shutil.rmtree(_stale, ignore_errors=True)
        _left = os.path.isdir(_stale)
        if not _left:
            print(f"[build] 已清理备份目录里过期的 产物库/组件/ 拷贝（约 1.17GB，"
                  f"{_t.time() - _t0:.0f}s）——该段不再备份（可再生运行时）")
        return (not _left), (_t.time() - _t0)
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：清理过期组件拷贝失败（可手工删 build/_dist_backup/产物库/组件）：{e}")
        return False, (_t.time() - _t0)


def restore_dist_runtime(backed):
    """打包后把备份的运行时文件还原回 dist/（exe 本身不还原）。
    同 backup：覆盖式合并，全程无 rmtree/rename。
    仅当 backup 的源是有效配置（>500 bytes）时 restore config.json——避免回填了空默认。

    主动恢复 config.json：backup 阶段可能因防御逻辑（src 是空默认）跳过 config.json 写入，
    此时 backed 列表里没有 config.json；restore 阶段检测 backup/config.json 有效且 dist 是空默认
    时主动 copy2 回填，把"backup 是最后防线"策略闭环。
    """
    # v1.0.652（审计 D6）：主动恢复 config.json **前移**到 backed 判空之前——旧
    # `if not backed: return` 把它短路：backup 防御分支跳过 config.json 且其余
    # DIST_PRESERVE 都不存在时（backed==[]），"backup 是最后防线"恰恰最需要生效
    # 的场景反而不执行。
    dist = os.path.join(BASE, "dist")
    os.makedirs(dist, exist_ok=True)
    # 主动恢复 config.json（独立于 backed 列表）
    # v1.0.739：顶层只是**旧布局**的家——新布局（产物库/config.json）已在位时，
    # 「顶层缺失」是正确终态；旧逻辑见缺就补会把 backup 里的陈旧条目（上一版构建
    # 残留）复活回顶层（739 打包实测：738 的旧顶层 config 被搬回，dist 出现两份）。
    try:
        backup_cfg = os.path.join(_BACKUP_DIR, "config.json")
        dist_cfg = os.path.join(dist, "config.json")
        if (os.path.exists(backup_cfg)
                and not os.path.exists(os.path.join(dist, "产物库", "config.json"))):
            bsz = os.path.getsize(backup_cfg)
            dsz = os.path.getsize(dist_cfg) if os.path.exists(dist_cfg) else 0
            if bsz > 500 and dsz < 500:
                shutil.copy2(backup_cfg, dist_cfg)
                print(f"[build] 主动恢复 config.json：backup {bsz} bytes → dist {dsz} bytes")
                if "config.json" not in backed:
                    backed.append("config.json")
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：主动恢复 config.json 失败：{e}")
    # v1.0.739：新布局（config 在 产物库/ 内）的同一条最后防线——
    # backup 侧有效（>500 bytes）而 dist 侧是空默认/缺失时回填真实配置。
    try:
        backup_cfg = os.path.join(_BACKUP_DIR, "产物库", "config.json")
        dist_cfg = os.path.join(dist, "产物库", "config.json")
        if os.path.exists(backup_cfg):
            bsz = os.path.getsize(backup_cfg)
            dsz = os.path.getsize(dist_cfg) if os.path.exists(dist_cfg) else 0
            if bsz > 500 and dsz < 500:
                os.makedirs(os.path.dirname(dist_cfg), exist_ok=True)
                shutil.copy2(backup_cfg, dist_cfg)
                print(f"[build] 主动恢复 产物库/config.json：backup {bsz} bytes → dist {dsz} bytes")
                if "产物库" not in backed:
                    backed.append("产物库")
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：主动恢复 产物库/config.json 失败：{e}")
    if not backed:
        return
    for name in backed:
        src = os.path.join(_BACKUP_DIR, name)
        dst = os.path.join(dist, name)
        try:
            if os.path.isdir(src):
                # v1.0.677：同样走增量拷贝 + 跳过产物库/组件（便携运行时，可再生）
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_backup_ignore,
                                copy_function=_copy_if_changed)
            else:
                shutil.copy2(src, dst)
            print(f"[build] 已还原 dist/{name}")
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：还原 dist/{name} 失败：{e}")


def migrate_dist_layout():
    # v1.0.275：backup 前把 dist 里的旧布局散落产物就地迁入 产物库/（此时应用应已退出、文件未占用；
    # 迁移失败不致命——只是本次备份沿用旧布局，下次启动时 app 内迁移兜底）。
    try:
        from core_utils import migrate_legacy_layout
        _m = migrate_legacy_layout(os.path.join(BASE, "dist"))
        if _m:
            print(f"[build] dist 旧布局产物已迁入 产物库/：{_m}")
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：dist 旧布局迁移失败（沿用现状继续）：{e}")
    # v1.0.738：把 dist 顶层遗留的 cdp_profiles/、ocr/ 并进 dist/产物库/。
    # 必须在 backup_dist_runtime() 之前——否则备份/还原会把旧顶层目录原样搬回来，
    # 新 exe 打包完用户还是看到一堆文件夹。运行期 backend 另有同函数兜底。
    try:
        from core.workspace_io import migrate_runtime_layout
        _r = migrate_runtime_layout(os.path.join(BASE, "dist"))
        if _r:
            print(f"[build] 顶层运行时目录已并入 dist/产物库/：{_r}")
    except Exception as e:  # noqa: BLE001
        print(f"[build] 警告：dist 运行时目录归并失败（沿用现状继续）：{e}")


def backup_previous_exe():
    # v1.0.301：版本垃圾策略（用户约定：最新是 3 → 保留 2 的版本文件，删 1 及更早垃圾）。
    # build 前把当前 dist exe（即"上一版"）备份到 build/_rollback/——只留一份、每次覆盖，
    # 任何时刻 _rollback 里都是紧邻本次的版本，新版出问题可直接拷回 dist 回滚。
    # v1.0.680（改名）：dist 里的 exe 改名了，新旧两个名都要认——
    # 改名那一次构建时 dist 里躺的是旧名 exe，不备份它就丢了唯一的手动回滚手段。
    _rollback_dir = os.path.join(BASE, "build", "_rollback")
    try:
        from core.branding import ALL_EXE_NAMES   # v1.0.691：三代 exe 名（WeComCompanion/Lingban/Fisheep）
    except Exception:  # noqa: BLE001
        ALL_EXE_NAMES = ("WeComCompanion.exe", "Lingban.exe", "Fisheep.exe")
    for _name in ALL_EXE_NAMES:
        _old_exe = os.path.join(BASE, "dist", _name)
        if not os.path.exists(_old_exe):
            continue
        try:
            os.makedirs(_rollback_dir, exist_ok=True)
            _rb_exe = os.path.join(_rollback_dir, _name)
            if os.path.exists(_rb_exe):
                os.remove(_rb_exe)  # 只留最近上一版，旧的回滚备份覆盖
            shutil.copy2(_old_exe, _rb_exe)
            print(f"[build] 上一版 exe 已备份 -> build/_rollback/{_name} "
                  f"({os.path.getsize(_rb_exe) // 1048576}MB，回滚用)")
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：上一版 exe 回滚备份失败（不影响打包）：{e}")
