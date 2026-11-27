"""
build.py — 一键打包（自动版本号 +1，并生成 exe 版本资源）
=========================================================
用法：
  python build.py                        # 展示拟递增版本并确认后打包（默认 patch +1）
  python build.py --yes                  # 跳过确认直接打包（CI 用）
  python build.py --version=1.2.3        # 指定版本号打包（不再自动 +1；必须高于当前版本）
  python build.py --current              # 仅打印当前版本号
  python build.py --prune-release --yes  # 维护：清 build/_release_excluded 的旧剥离份（保留最新一份）
  python build.py --gc [--yes]           # 维护：垃圾回收（_dist_backup 孤儿条目/_rollback 残留/live 档案缓存，不打包）
  python build.py --force-activity       # 只跳过「活动/断点」守卫（软件内一键打包用）
  python build.py --no-tests            # 逃生口：跳过冒烟前 pytest 门禁（v1.0.663 起门禁默认恒跑，0 failed）

每次修完 bug / 加完功能都跑一遍即可，版本号自动递增、互不相同。

v1.0.65x（C 档拆分）：实现按域移入 buildlib/（gates/versioning/backup/chatlog/
ocr_sync/pii/spec_refresh/release/verify/patching/orchestration），本文件只留
main() 编排与 CLI 解析。门禁调用（_run_gate）与各步顺序/输出/退出码保持不变；
`import build as B` 的名字契约由下方 __all__ 维持。

v1.0.660（审查修复 B6）：CLI 改用 argparse——自带 --help、未知参数直接报错
（原先 `--relese` 这类笔误被静默忽略，跑完一圈才发现参数没生效）。既有选项
语义全部保留（含 `--note="..."` / `--version=x.y.z` 的等号写法与裸位置参数版本号）。
"""
import argparse
import os
import re
import shutil
import subprocess   # 测试经 B.subprocess.run 对门禁打桩——必须留在本模块命名空间
import sys
import time

# 本文件大量打印 ✅/❌/⚠️：中文 Windows 控制台与 subprocess 管道默认 cp936，
# 编码不了会抛 UnicodeEncodeError——在「检查全部通过」的成功分支上崩掉，退出码非 0，
# 等于把合格构建判成失败。统一按 UTF-8 + replace 输出。
# v1.0.663（B7 硬杀洞补丁①）：再加 line_buffering——1.0.661 空号事故的直接损失就是
# 进程被外部硬杀时块缓冲里整段 PyInstaller 输出全部丢失（exit 1、无 traceback、
# 无人知道死在哪）。行缓冲后每行即 flush，硬杀最多丢正在写的那半行。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

from buildlib.env import BASE, VPATH, SPEC, PYINSTALLER, _SANDBOX_VARS, ensure_node_on_path   # noqa: E402
from core.branding import EXE_NAME, ALL_EXE_NAMES   # noqa: E402（v1.0.680/691 改名：旧名 exe 需移出 dist）
from buildlib.versioning import current_version, next_version, bump_version, make_version_info   # noqa: E402
from buildlib.spec_refresh import refresh_spec_hiddenimports, _source_module_list, _write_onedir_spec   # noqa: E402
from buildlib.backup import (backup_dist_runtime, restore_dist_runtime,   # noqa: E402
                             backup_previous_exe, migrate_dist_layout,
                             prune_stale_component_copy)
from buildlib.chatlog import (clear_chat_history, breakpoint_busy, newest_breakpoint_epoch,   # noqa: E402
                              _CHAT_DIRS, _CHAT_KEEP_DIRS, _CHAT_ARCHIVE,
                              _BP_BUSY_MIN, _BREAKPOINT_DIR)
from buildlib.ocr_sync import sync_ocr_runtime   # noqa: E402
from buildlib.pii import scan_dist_pii   # noqa: E402
from buildlib.gates import (check_undefined_names, check_dev_only_imports,   # noqa: E402
                            check_giant_functions, check_silent_pass, check_silent_except,
                            check_frontend_structure, check_dependency_consistency,
                            check_repo_hygiene, check_shell_channel_policy,
                            scan_giant_functions, _iter_lint_files,
                            _LINT_TARGETS, _NOISE_KEYS, _DEV_ONLY_ROOTS,
                            GIANT_FUNC_MAX_LINES, GIANT_FUNC_BASELINE, BARE_EXCEPT_BASELINE)
from buildlib.release import release_mode_clean, prune_release_excluded   # noqa: E402
from buildlib.gc import gc_after_build, run_gc   # noqa: E402   v1.0.743：构建收尾垃圾回收 + --gc 维护模式
from buildlib.verify import verify_packaged_assets   # noqa: E402
from buildlib.patching import _patch_pywebview_contextmenu   # noqa: E402
from buildlib.orchestration import (kill_running_app, run_pyinstaller,   # noqa: E402
                                    run_smoke, clean_pyinstaller_mid, rollback_version)
from buildlib import journal   # noqa: E402   v1.0.663：构建 journal + 空号自愈（B7 硬杀洞补丁）
from buildlib import gate_scope   # noqa: E402   v1.0.677：门禁分层（fast 301 条 / 全量）
from buildlib import coverage_gap   # noqa: E402   v1.0.755：评审 P1-2 覆盖缺口扫描 / 可选覆盖率钩子

# 拆分前 build.py 的模块级名字契约（tests/、build/_tmp_*.py 等按 B.<名字> 取用）。
__all__ = [
    "BASE", "VPATH", "SPEC", "PYINSTALLER", "_SANDBOX_VARS", "subprocess",
    "current_version", "next_version", "bump_version", "make_version_info",
    "refresh_spec_hiddenimports", "_source_module_list", "_write_onedir_spec",
    "backup_dist_runtime", "restore_dist_runtime", "backup_previous_exe", "migrate_dist_layout",
    "clear_chat_history", "breakpoint_busy", "newest_breakpoint_epoch",
    "_CHAT_DIRS", "_CHAT_KEEP_DIRS", "_CHAT_ARCHIVE", "_BP_BUSY_MIN", "_BREAKPOINT_DIR",
    "sync_ocr_runtime", "scan_dist_pii",
    "check_undefined_names", "check_dev_only_imports", "check_giant_functions",
    "check_silent_pass", "check_silent_except", "check_frontend_structure",
    "check_dependency_consistency", "check_repo_hygiene",
    "check_shell_channel_policy",
    "scan_giant_functions", "_iter_lint_files", "_LINT_TARGETS", "_NOISE_KEYS",
    "_DEV_ONLY_ROOTS", "GIANT_FUNC_MAX_LINES", "GIANT_FUNC_BASELINE", "BARE_EXCEPT_BASELINE",
    "release_mode_clean", "prune_release_excluded", "verify_packaged_assets",
    "_patch_pywebview_contextmenu", "kill_running_app", "run_pyinstaller",
    "run_smoke", "clean_pyinstaller_mid", "rollback_version", "journal",
    "_run_gate", "main",
]


def _run_gate(name, fn, force=False):
    """门禁统一执行器（v1.0.654 A11）：fn() 返回非零=命中拦截（各检查自带 ❌ 输出）。
    返回 True=放行继续打包；False=中止（main 返回 1）。

    v1.0.660（审查修复 B5）：force 只影响「门禁自身抛异常」这条 fail-closed 兜底，
    **不影响门禁的判定结果**；静态硬门禁（F821 / 开发期目录引用 / 两条棘轮）一律
    以 force=False 调用——它们的判定结果任何情况下都不可跳过。"""
    try:
        return not fn()
    except Exception as e:  # noqa: BLE001
        if force:
            print(f"[build] ⚠️ {name}执行失败（--force 放行）：{e}")
            return True
        print(f"[build] ❌ {name}执行失败（fail-closed）：{e}")
        return False


def _version_tuple(v):
    """版本号 → 可比较的元组（按段比大小，非字典序——1.0.9 < 1.0.10）。"""
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


def _check_explicit_version(explicit, cur):
    """v1.0.660（审查修复 B11）：校验显式指定的版本号。返回 (是否通过, 错误提示)。

    规则：格式 x.y.z，且**严格高于**当前版本——允许指定更低的号会让版本库
    时间线倒挂（回滚后的旧号被当成新版本登记，设置页的版本列表从此乱序）。
    原实现只校验格式，不比较大小。
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+", explicit):
        return False, f"非法版本号：{explicit}（应为 x.y.z）"
    if _version_tuple(explicit) <= _version_tuple(cur):
        return False, (f"指定版本必须高于当前版本：当前 v{cur}，指定 v{explicit}"
                      f"（版本号时间线不允许倒挂）")
    return True, ""


def _bump_version_with_journal(explicit=None):
    """v1.0.663（D2 审查修复）：先落盘 bump 意图，再改 version.py。

    原顺序是 `bump_version()` → `journal.stage("bump")`：两者之间被硬杀
    （TerminateProcess / 断电）时 version.py 已 +1，而 journal 最后一条仍是上一次
    构建的终态记录 → self_heal 的「非终态」判据不成立，空号照旧无人认领（661 形态）。
    改为先写 `bump_intent`（ver/frm 已填）再 bump：窗口内 journal 已是带版本的非终态
    记录，self_heal 按既有三条判据即可还原；若 bump 根本没发生（写完 intent 就死），
    version.py 仍等于旧版、判据②不成立，不会误回滚。
    返回 (旧版本, 新版本)，与 bump_version 一致。
    """
    cur = current_version()
    new = explicit or next_version(cur)
    journal.stage("bump_intent", ver=new, frm=cur)
    old_v, v = bump_version(explicit)
    journal.stage("bump", ver=v, frm=old_v)
    return old_v, v


def _parse_args(argv):
    """v1.0.660（审查修复 B6）：CLI 解析改用 argparse。

    语义与旧的手工 for 循环**完全一致**：
      --yes / --keep-chats / --prune-release / --onedir / --release / --lean / --current
      --version=X.Y.Z（指定版本，必须严格高于当前）/ --note="..."（版本介绍）
      裸位置参数 X.Y.Z（等价于 --version=X.Y.Z）
    差别只有一处，是刻意的：未知参数不再被静默忽略（`--relese` 这种笔误原先
    会一路跑到打包结束才被发现），由 argparse 直接报错退出。
    """
    p = argparse.ArgumentParser(
        prog="build.py",
        description="Fisheep 一键打包（自动版本号 +1，并生成 exe 版本资源）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python build.py --yes            # 跳过确认直接打包\n"
            "  python build.py --version=1.2.3   # 指定版本号（必须高于当前版本）\n"
            "  python build.py --onedir         # 目录版打包（冷启动更快）\n"
            "  python build.py --gc             # 维护：垃圾回收（不打包；--gc --yes 直接执行）\n"
        ),
    )
    p.add_argument("--yes", action="store_true",
                   help="跳过确认直接打包（CI / 软件内一键打包）")
    p.add_argument("--keep-chats", action="store_true",
                   help="打包后保留历史对话（默认清空并搬到 build/_chat_archive）")
    p.add_argument("--prune-release", action="store_true",
                   help="维护动作：只清理 build/_release_excluded 的旧剥离份，不打包")
    p.add_argument("--gc", action="store_true",
                   help="维护动作：垃圾回收——清 _dist_backup 孤儿条目/不再备份段副本/"
                        "_rollback 非机制 exe（versions.json 引用的版本化 exe 属机制文件，保留）/"
                        "live 档案浏览器缓存，并报告 _chat_archive 体积，不打包"
                        "（默认 dry-run 列清单再确认，加 --yes 直接执行）")
    p.add_argument("--onedir", action="store_true",
                   help="目录版打包（冷启动更快，分发形态不同；默认仍是 onefile）")
    p.add_argument("--force", action="store_true",
                   help="兼容别名：等价于旧版「全跳过」——活动/断点守卫 + 门禁自身崩溃时的兜底放行。"
                        "注意静态硬门禁（F821 / 开发期目录引用 / 棘轮）的判定结果任何情况下都不可跳过。"
                        "建议改用 --force-activity")
    p.add_argument("--force-activity", action="store_true",
                   help="只跳过「活动/断点」守卫（软件内一键打包真正需要的那个），"
                        "其余门禁判定一律不放行")
    p.add_argument("--release", action="store_true",
                   help="发布模式（打包成功后剥离开发机 config/产物库/日志）")
    p.add_argument("--lean", action="store_true",
                   help="精简发布（连 dist/产物库/ocr 一并剥离，OCR 首用自动下载）")
    p.add_argument("--public-exe", dest="public_exe", action="store_true",
                   help="脱敏公开 exe：构建收尾从脱敏镜像树（待发布版本/）重打包 exe 并跑 exe 级"
                        "脱敏门禁（下钻 PYZ 冻结代码）。门禁红 → 本次**不可发 public**（dev 产物不受"
                        "影响、版本不回滚），退出码 1。详见 buildlib.public_exe。")
    p.add_argument("--current", action="store_true",
                   help="仅打印当前版本号")
    p.add_argument("--with-tests", action="store_true",
                   help="保留兼容旧脚本：v1.0.663 起 pytest 门禁默认恒跑，传它不产生任何效果")
    p.add_argument("--no-tests", action="store_true",
                   help="跳过冒烟前的 pytest 门禁（逃生口；默认必须全量 pytest 0 failed 才发包）")
    p.add_argument("--skip-tests", action="store_true",
                   help="已弃用：等价 --no-tests（保留兼容旧脚本）")
    p.add_argument("--full-tests", action="store_true",
                   help="强制全量 pytest 门禁（v1.0.677 分层门禁的逃生口："
                        "默认按 git diff 判改动面——纯展示资产改动跑 fast 层 301 条，"
                        "含逻辑改动自动全量；传它跳过判定恒跑全量）")
    p.add_argument("--clean-build", action="store_true",
                   help="强制冷构建（删掉 PyInstaller 缓存后重建）。v1.0.677 起默认走"
                        "增量构建（复用 build/<exe名>/ 缓存）：实测冷构建 1m30s、"
                        "增量 1m20s，而 --clean 清缓存本身还要 2 分钟、构建后删缓存又要 40 秒"
                        "——冷构建一次共多花约 2 分 50 秒。发布（--release）默认强制冷构建。")
    p.add_argument("--version", dest="explicit", metavar="X.Y.Z",
                   help="指定版本号打包（不再自动 +1；必须严格高于当前版本）")
    p.add_argument("--note", dest="build_note", default="", metavar="TEXT",
                   help="版本介绍（进版本库，设置页展示）；推荐写成 --note=\"...\"")
    p.add_argument("version_positional", nargs="?", metavar="X.Y.Z",
                   help="位置参数形式的版本号（等价于 --version=X.Y.Z）")
    args = p.parse_args(argv)
    # v1.0.663（#18）：--with-tests 已是无操作（门禁默认恒跑），不存在"同时给"的冲突；
    # 旧冲突检查删除。--no-tests / --skip-tests 同义，见 main() 调用点。
    if args.explicit and args.version_positional and args.explicit != args.version_positional:
        p.error(f"--version={args.explicit} 与位置参数 {args.version_positional} 冲突")
    return args


def _xdist_ok():
    """pytest-xdist 是否可用（在打包 venv 里探测，不是 build 进程的解释器）。"""
    try:
        r = subprocess.run([sys.executable, "-c", "import xdist"], cwd=BASE,
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _parse_failed_ids(pytest_output):
    """从 pytest 输出里抽出失败用例的 nodeid（"FAILED tests/x.py::test_y" 行）。"""
    ids = []
    for ln in (pytest_output or "").splitlines():
        ln = ln.strip()
        if ln.startswith("FAILED "):
            nid = ln[7:].strip()
            # 去掉 " - 断言信息" 后缀
            nid = nid.split(" - ")[0].strip()
            if nid and nid not in ids:
                ids.append(nid)
    return ids


def _run_pytest_gate(full_only=False):
    """v1.0.660（审查修复 B15）：冒烟前的 pytest 门禁。返回 0=通过，非 0=不过。

    v1.0.663（#14+#18）：门禁默认恒跑，0 failed 才放行。
    v1.0.677（用户："打包和测试做回归的效率都不高"）两层提速：
      ① 分层：git diff 全落展示资产（图标/文案/样式，无 .py/.js）→ 跑 fast 层
         301 条（buildlib.gate_scope.FAST_FILES，实测 ~40s）；含逻辑改动/判定失败
         → 全量（fail-closed，判定绝不"猜"）。--full-tests 跳过判定恒全量。
      ② 并行：全量加 `-n 8 --dist loadfile`（xdist；同文件同 worker 防 fixture
         串扰；CDP 类测试全是 stub/临时目录，无共享端口）。xdist 缺失自动回退串行。
    fast 层不并行（~40s 串行已是 4min→40s 的主要收益，且子集小并行无收益）。

    v1.0.677（并行串扰兜底）：并行跑有失败时，**串行复跑失败用例**——
    真失败串行也红；只在并行时红的是**测试隔离缺陷**（进程级全局状态互串：
    core.paths.workspace_root 这类模块级补丁、产物库闸门），不是代码缺陷。
    实测（2026-11-15）：这类串扰把打包拦下并自动回滚了两次（11-14 与 11-15），
    而代码本身没问题。串行复跑全绿 → 放行并**大声报出**是隔离缺陷，请顺手修。
    """
    # 判定跑哪层：full_only 恒全量；否则按 git diff（判定失败 fail-closed 走全量）
    if full_only:
        targets, why = None, "--full-tests 强制全量"
    else:
        scope, _files, why = gate_scope.classify(BASE)
        targets = list(gate_scope.FAST_FILES) if scope == "fast" else None
        why += f"（→ {'fast 301 条' if targets else '全量'}）"
    print(f"[build] 门禁改动面判定：{why}")

    cmd = [sys.executable, "-m", "pytest"]
    cmd += list(targets) if targets is not None else ["tests"]
    parallel = False
    if targets is None and _xdist_ok():   # 仅全量层并行（fast 层 ~40s，串行已够）
        cmd += ["-n", "8", "--dist", "loadfile"]
        parallel = True
    # v1.0.756（评审 P1-2 字面补做）：覆盖率采集 + 真实增量门槛。
    # pytest-cov / diff-cover 已钉进 requirements-dev.txt，缺失不再 fail-open
    # 降级——由本函数末尾的 check_incremental_coverage() 统一 fail-closed。
    # 仍不设 --cov-fail-under：70% 只针对**新增代码行**，由 coverage_gap 对
    # git diff 新增行 ∩ Cobertura 可测行计算，全仓绝对值会误杀合格构建。
    cov_args = coverage_gap.pytest_cov_args()
    if cov_args:
        print("[build] 采集行覆盖率 → %s（新增代码行门槛 %s%%）" % (
            coverage_gap.COVERAGE_XML, coverage_gap.INCREMENTAL_MIN_COVERAGE))
        cmd += cov_args
    cmd += ["-q", "-p", "no:cacheprovider"]
    print("[build] " + " ".join(cmd))
    try:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, timeout=3600)
    except Exception as e:  # noqa: BLE001
        print(f"[build] ❌ pytest 门禁执行失败（fail-closed）：{e}")
        return 1
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    sys.stdout.write(out[-8000:])
    if r.returncode != 0:
        # v1.0.677：并行失败 → 串行复跑失败用例，区分「真失败」与「并行串扰」
        _failed = _parse_failed_ids(out)
        if parallel and _failed:
            print(f"[build] 并行跑有 {len(_failed)} 条失败——串行复跑以区分"
                  f"「真失败」与「并行串扰」：")
            cmd2 = ([sys.executable, "-m", "pytest"] + _failed
                    + ["-q", "-p", "no:cacheprovider"])
            try:
                r2 = subprocess.run(cmd2, cwd=BASE, capture_output=True, timeout=1800)
            except Exception as e:  # noqa: BLE001
                print(f"[build] ❌ 串行复跑执行失败（fail-closed）：{e}")
                return 1
            out2 = ((r2.stdout or b"") + (r2.stderr or b"")).decode("utf-8", "replace")
            sys.stdout.write(out2[-4000:])
            if r2.returncode == 0:
                print("[build] ⚠️ 串行复跑全绿 → 判定为**并行串扰（测试隔离缺陷）**"
                      "而非代码缺陷，本次放行。")
                print("[build]    请顺手修掉该测试的隔离问题：不要依赖别的用例改过的"
                      "进程级全局状态（core.paths.workspace_root 补丁、产物库闸门等）。")
                # 这里**不能 return 0**：那会跳过函数末尾的增量覆盖率门槛——
                # 「并行串扰」这条放行路径等于给覆盖率闸门开了一扇后门。
            else:
                print("[build] ❌ 串行复跑仍失败——真失败，已中止发包")
                return r2.returncode if r2.returncode > 0 else 1
        else:
            print("[build] ❌ pytest 未全绿——已中止发包（本次产物不可发）")
            return r.returncode if r.returncode > 0 else 1
    print(f"[build] pytest 门禁通过 ✓（{'并行' if parallel else '串行'}）")
    # v1.0.756（评审 P1-2 字面补做）：pytest 全绿还不够——本轮新增生产代码行的
    # 覆盖率必须 ≥70%。返回码直接并入放行判定（D5 处置动作），不再「只采集不拦」。
    return coverage_gap.check_incremental_coverage(base=BASE)


def _run_public_exe_gate(args, journal, v):
    """--public-exe 发布硬闸（v1.0.764，用户：以后待发布里所有都要脱敏）。

    从脱敏镜像树重打包「脱敏 exe」+ exe 级脱敏门禁（下钻 PYZ 冻结代码）。
    抽成独立函数（不嵌在 main() 里）以便单元测试——main() 前段是整包构建，
    无法在测试里隔离这一小段。

    返回：门禁红 → 1（阻塞发布）；否则 None（由 main 统一落 done 继续收尾）。
    **dev 产物与版本号不受影响、不回滚**（由 main 的 _build_ok / finally 控制）——
    这里只决定「能否发 public」，绝不牵连已合格的 dev exe 与版本登记。
    """
    if not args.public_exe:
        return None
    _pub_ok = False
    try:
        from buildlib.public_exe import run_public_release
        _pub_ok, _pub_lines = run_public_release()
        for _l in _pub_lines:
            print("[build] " + _l)
    except Exception as e:  # noqa: BLE001
        print(f"[build] ❌ 脱敏公开 exe 处理异常（dev 产物不受影响，发布阻塞）：{e}")
        _pub_ok = False
    if not _pub_ok:
        print("[build] ❌ 脱敏公开 exe 门禁未过——dev exe 已生成、版本已登记，"
              "但**不可发 public**。修复镜像敏感残留后重跑 "
              "python tools/build_public_exe.py（--verify-only 只复检不重打）。")
        journal.stage("done", ver=v)
        return 1
    return None


def main():
    # v1.0.338：打包进程自身也清掉沙箱会话变量——之前只对 pyinstaller 子进程清，
    # 进程自身的 OCR 运行时同步（shutil.rmtree dist/产物库/ocr/<pkg>）仍被安全删除钩子拦
    # （SAFE_DELETE_BULK_CONFIRM_REQUIRED 直接 SystemExit）。迁移机器后 dist/产物库/ocr
    # 必然要全量重建，必然撞上。清掉后钩子的 bulk guard 直接放行，删除走回收站路径。
    for _k in _SANDBOX_VARS:
        os.environ.pop(_k, None)
    # v1.0.663（B7 硬杀洞补丁③）：上次构建若被硬杀留下空号（version.py 停在已 bump
    # 的未登记版本），要在真正构建前自愈——否则本次 next_version 会从空号再 +1，
    # 空号被永久消耗（1.0.661 就是这么被 662 覆盖掉的）。三条判据缺一不可（见
    # journal.self_heal 文档），正常状态零动作。
    # D1：自愈放在参数解析之后（它要写 version.py，只读子命令不能触发）。
    args = _parse_args(sys.argv[1:])
    # v1.0.663（D1 审查修复）：自愈会**写 version.py**（回滚空号），只能由「真要打包」
    # 的调用触发。--current 只打印版本、--prune-release 只清旧剥离份——它们跑自愈，
    # 等于用户只想看一眼版本号，却把版本号改掉了；排查空号时现场也被顺带破坏。
    # v1.0.743：--gc 同属只读维护动作——self_heal 会写 version.py，清垃圾不该顺带改版本号。
    if not (args.current or args.prune_release or args.gc):
        journal.self_heal()
    explicit = args.explicit or args.version_positional
    auto_yes = args.yes
    release = args.release      # v1.0.405（M4）：--release 发布模式（打包后剥离开发机 config/产物库/日志）
    lean = args.lean            # v1.0.473：--lean 精简发布（连 dist/产物库/ocr 一并剥离，OCR 首用自动下载）
    if lean:
        release = True          # lean 隐含 release（剥离开发机数据是精简发布的前提）
    # v1.0.660（审查修复 B5）：force 语义拆分——
    #   force      = 活动/断点守卫的放行开关（--force 与 --force-activity 都放行这一类）；
    #   force_gate = 「门禁自身抛异常」时的 fail-open 兜底（仅 --force 放行；--force-activity 不放）。
    # 静态硬门禁（F821 / 开发期目录引用 / 两条棘轮）的判定结果**任何情况下都不可跳过**。
    force = args.force or args.force_activity
    force_gate = args.force
    build_note = args.build_note   # v1.0.360：--note="..." 版本介绍（进版本库，设置页展示）
    keep_chats = args.keep_chats   # v1.0.603：默认每次打包后清空历史对话（搬到 build/_chat_archive）
    prune_release = args.prune_release   # v1.0.619：清理 build/_release_excluded 旧份（维护动作，不打包）
    gc_only = args.gc                    # v1.0.743：垃圾回收（维护动作，不打包，与 --prune-release 同类）
    onedir = args.onedir       # v1.0.646：目录版打包（冷启动更快，分发形态不同；默认仍是 onefile）
    if args.current:
        print(current_version())
        return
    cur = current_version()
    new = explicit or next_version(cur)
    if prune_release:
        # 维护模式：只做清理，不打包（打包是另一件事，混在一起容易误删）
        return prune_release_excluded(auto_yes)
    if gc_only:
        # v1.0.743：维护模式：只跑垃圾回收，不打包。run_gc 的返回值是删除条数
        #（供测试断言用）——这里恒返回 0：非零退出码在这个 CLI 里 = 失败。
        run_gc(base_dir=BASE, auto_yes=auto_yes)
        return 0
    _patch_pywebview_contextmenu()   # v1.0.501：幂等——生产环境启用 WebView2 右键菜单
    # 便携 node 顶进 PATH：不补这一步，下面的 JS 语法校验会打印"跳过"、
    # pytest 里的 ESM 求值验证会整文件 skipif ——两道必跑闸门在开发机上静默失效。
    _node = ensure_node_on_path()
    if _node:
        print(f"[build] JS 校验用 node：{_node}")
    else:
        print("[build] ⚠️ 未找到 node（PATH / 产物库 / dist 便携运行时均无）——"
              "JS 语法校验与 ESM 求值验证本次跳过。可在设置页组件体检一键安装后重跑。")
    # v1.0.373：打包前前端结构校验（防同类错误复发关卡 2），实现见 buildlib.gates。
    if not check_frontend_structure(force_gate):
        return 1
    # v1.0.654 A11：以下六组门禁收进 _run_gate（原六段 try/except 同构样板）。
    # 各检查内部 fail-closed 自己打印 ❌，此处只决定放行/中止与异常兜底语义。
    # v1.0.660（B5）：这些全是**硬门禁**，force 一律为 False——force 只放行
    # 「门禁自己崩了」，不放行「门禁判定为不合格」。
    if not _run_gate("未定义名检查", check_undefined_names, False):   # v1.0.588：F821=硬拦截
        return 1
    if not _run_gate("开发期目录引用检查", check_dev_only_imports, False):   # v1.0.619
        return 1
    if not _run_gate("巨型函数棘轮检查", check_giant_functions, False):   # v1.0.632（B9）
        return 1
    if not _run_gate("静默 pass 棘轮", check_silent_pass, False):   # v1.0.648：硬棘轮
        return 1
    if not _run_gate("数据安全静默声明检查", check_silent_except, False):   # v1.0.648
        return 1
    if not _run_gate("依赖一致性检查", check_dependency_consistency, False):   # v1.0.661：pip check
        return 1
    if not _run_gate("shell 通道策略检查", check_shell_channel_policy, False):   # v1.0.756：P0-2 字面补做
        return 1
    # v1.0.731（架构建议）：文件级规模 / tools/ 临时资产的**软告警**，恒返回 0。
    # 刻意不走 _run_gate——它不参与拦截，只把「没人敢通读的大文件」打印到每次
    # 打包日志里，硬闸门会逼出「为拆而拆」的假拆分。
    check_repo_hygiene()
    if explicit:
        _ok, _why = _check_explicit_version(explicit, cur)
        if not _ok:
            print(f"[build] {_why}")
            return 1
        print(f"[build] 指定版本 -> {new}")
    else:
        print(f"[build] 当前版本 {cur}，拟递增为 {new}")
        # P3-4：自动 +1 前先展示并确认，避免正式/测试包版本难管控
        if not auto_yes:
            try:
                ans = input(f"是否确认以 v{new} 打包？[y/N] ").strip().lower()
            except EOFError:
                # v1.0.660（B6）：非交互环境（CI / 管道 / 重定向）忘加 --yes 时，
                # input() 抛 EOFError 会把裸栈打到控制台——用户看到的是一堆
                # traceback，而不是"这里需要你确认一下"。
                print("[build] 未收到确认输入（当前是非交互环境？）——已中止打包。"
                      "非交互执行请加 --yes。")
                return 1
            if ans not in ("y", "yes"):
                print("[build] 已取消")
                return
    # v1.0.373 审查修复（P2-7）：对话活动守卫前移到 bump_version 之前——原在打包阶段
    # 才检测，被中止时 version.py 已 +1（376 空号根因：版本号被白白消耗且无产物）。
    # v1.0.209/212：打包前检测运行活动——若最近 3 分钟内有 agent 活动
    #（用户正在跑对话/审核流程），中止打包并提示，防止 taskkill 误杀正在执行的
    # 任务（实测 14:58 打包强杀了用户 14:57 正在跑的审核流程，对话直接中断）。
    # v1.0.212 修复误判：改为按日志行的【时间戳】判断最近 180s 内是否有
    # [agent]/[llm] 活动（旧版看文件 mtime+尾部 4000 字符，任务已结束但旧日志
    # 还在尾部时误判"3 分钟内有活动"导致永远打不了包）。
    try:
        # v1.0.275：日志新旧两处位置都查（新=产物库/日志/，旧=dist 顶层，迁移过渡期）。
        # v1.0.302：扫描算法改接 core_utils.last_agent_activity_epoch 单点实现
        #（原与 knowledge_ops 600s 空闲判定各写一遍同款算法）。
        from core_utils import last_agent_activity_epoch
        _logps = [os.path.join(BASE, "dist", "产物库", "日志", "companion.log"),
                  os.path.join(BASE, "dist", "companion.log")]
        _ts = last_agent_activity_epoch(_logps)
        if not force and _ts is not None and time.time() - _ts < 180:
            print("[build] ⚠️ 检测到最近 3 分钟内有 agent/LLM 活动（用户可能正在跑任务），"
                  "中止打包以防误杀运行中的对话。")
            print("[build] 请等任务跑完或停止对话后再打包（软件内一键打包会自动带 --force-activity）。")
            return 1
        if not force and _ts is None:
            # v1.0.470 修复（P1-21）：守卫 fail-closed——读日志失败（文件占用/迁移中/
            # 路径变化）时原 except: pass 静默放行，后续 taskkill 照杀，保护在最需要的
            # 异常场景下失效。现无 force 时保守中止；--force-activity 为显式确认放行。
            print("[build] ⚠️ 无法读取活动日志（文件占用或路径变化），无法确认 agent 是否在跑。"
                  "为防误杀任务已中止打包；确认无任务在跑可用 --force-activity 强制打包。")
            return 1
        # v1.0.623：日志侧守卫看不见「挂起等待点继续」——那种状态不写日志。补上断点侧。
        _bp_busy, _bp_age = breakpoint_busy()
        if not force and _bp_busy:
            print(f"[build] ⚠️ 最近 {int(_bp_age) if _bp_age is not None else 0} 分钟内仍有断点落盘"
                  f"（有会话停在待点「继续」），中止打包——打包会把它的续跑状态搬进归档并重启进程。")
            print(f"[build] 请先点「继续」把它跑完（或 finish 掉那个会话）再打包；"
                  f"超过 {_BP_BUSY_MIN} 分钟没有断点写入即自动放行。"
                  f"确认无进行中任务可用 --force-activity。")
            return 1
    except SystemExit:
        raise
    except Exception as e:
        if not force:
            print(f"[build] ⚠️ 活动守卫异常（{e}），fail-closed 中止打包；"
                  f"确认无任务在跑可用 --force-activity。")
            return 1
        print(f"[build] 警告：活动守卫异常（{e}），已按 --force-activity 放行")
    # v1.0.663（B7 硬杀洞补丁② + D2）：bump 是构建里第一次不可逆动作，journal 从它记起。
    # D2：bump_intent 先于 version.py 写入落盘，硬杀窗口内 self_heal 也认得（见 helper）。
    old_v, v = _bump_version_with_journal(explicit)
    # v1.0.660（审查修复 B7）：bump 之后的**任何**失败都回滚版本号。
    # 原先只有 PyInstaller 自己失败时（在 buildlib/orchestration 内）才回滚，
    # 打包成功但冒烟失败 / 资源核对不一致时 version.py 保持已 +1，
    # 于是出现"376 空号"那种版本号被消耗却没有合格产物的形态。
    # 回滚复用 orchestration.rollback_version（唯一实现，不另写一份）。
    _build_ok = False
    try:
        make_version_info(v)      # 写 version_info.txt，spec 按文件名取用
        migrate_dist_layout()     # v1.0.275：backup 前旧布局就地迁入 产物库/（失败不致命）
        backed = backup_dist_runtime()
        backup_previous_exe()     # v1.0.301：上一版 exe 备份到 build/_rollback/（回滚用）
        kill_running_app()        # 释放旧 exe 文件锁，避免 PyInstaller 写入失败
        env = dict(os.environ)
        for k in _SANDBOX_VARS:
            env.pop(k, None)
        # v1.0.555（P0-11）：构建前自动刷新 hiddenimports（漏模块 → 发货 exe 功能全死）
        try:
            _added = refresh_spec_hiddenimports()
            if _added:
                print(f"[build] hiddenimports 自动补全 {len(_added)} 个模块：{', '.join(_added[:10])}"
                      + ("…" if len(_added) > 10 else ""))
            else:
                print("[build] hiddenimports 自动扫描：无新增（spec 与源码一致）")
        except Exception as e:
            print(f"[build] 警告：hiddenimports 自动生成失败（沿用 spec 现值）：{e}")
        if onedir:
            # v1.0.646：onedir 模式——onefile 每次启动都要把 ~90 个模块解压到临时目录，
            # 目录版免掉这一步（冷启动实测量级：onefile 3.4s → onedir ~1.5s，见 README）。
            # 代价：产物从单个 exe 变成「一个目录」，分发形态变了，故**默认仍是 onefile**，
            # 需要时显式加 --onedir。
            spec_path = _write_onedir_spec()
            print("[build] onedir 模式：产物为 dist/<exe名>-onedir/ 目录（冷启动更快，分发形态不同）")
        else:
            spec_path = SPEC
        # v1.0.677（性能）：默认**增量构建**——复用 build/<exe名>/ 缓存。
        # 实测（本机 2026-11-15）：冷构建（带 --clean）1m30s、增量 1m20s；
        # 但真正的大头不是这两者之差，而是 --clean **清缓存本身**要 2 分钟、
        # 构建后 clean_pyinstaller_mid() **删缓存**又要 40 秒 —— 冷构建一次
        # 共多花约 2 分 50 秒。
        #
        # 增量的正确性由三条既有闸门兜住（本项目原有，不新增）：
        #   ① hiddenimports 自动补全（refresh_spec_hiddenimports）——**新增模块**会改写
        #      spec → spec 变化 → PyInstaller 重新分析 → 新模块进包（已实测：
        #      增量包确实带上了新增的 core/masking.py）。
        #   ② 冒烟：frozen exe 内 113 个模块逐个 import（漏打包模块会红）。
        #   ③ 包内资源一致性：24 项前端资源与源码树逐字节比对（包里还是旧前端会红）。
        # 发布（--release）强制冷构建——对外分发的东西要可复现，不吃缓存。
        cold_build = bool(args.clean_build or release)
        cmd = [PYINSTALLER, spec_path, "--noconfirm"]
        if cold_build:
            cmd.append("--clean")
            print("[build] 冷构建模式（清空 PyInstaller 缓存后重建）")
        else:
            print("[build] 增量构建模式（复用 build/<exe名>/ 缓存；"
                  "要冷构建加 --clean-build）")
        print("[build] " + " ".join(cmd))
        journal.stage("pyinstaller_start", ver=v)
        try:
            run_pyinstaller(cmd, env, old_v)   # 失败内部回滚版本号后重新抛出
        finally:
            restore_dist_runtime(backed)
        journal.stage("pyinstaller_done", ver=v)
        # v1.0.470（P1-21）：打包成功后校验产物存在且为本次生成
        # v1.0.660（B1）：产物路径随**分发形态**联动——onedir 的 exe 在子目录里，
        # 写死 dist/Lingban.exe 会导致校验/冒烟跑的是上一次的 onefile 残留。
        # v1.0.680（改名）：产物名取自 core.branding（单一事实源）。
        from core.branding import ONEDIR_NAME   # EXE_NAME 已在模块顶部导入（v1.0.691：静态噪声门禁）
        if onedir:
            _app_dir = os.path.join(BASE, "dist", ONEDIR_NAME)
        else:
            _app_dir = os.path.join(BASE, "dist")
        _dist_exe = os.path.join(_app_dir, EXE_NAME)
        _dist_rel = os.path.relpath(_dist_exe, BASE).replace("\\", "/")
        if not os.path.isfile(_dist_exe):
            raise SystemExit(f"[build] PyInstaller 报告成功但 {_dist_rel} 不存在——请检查 spec")
        print(f"[build] 产物校验通过：{_dist_rel} ({os.path.getsize(_dist_exe) // 1048576}MB, mtime={time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(_dist_exe)))})")
        journal.stage("verify_done", ver=v)
        # v1.0.660（审查修复 B15）：README 的标准验证栈要求 pytest 0 failed，原门禁里
        # 只有静态检查 + 前端校验 + 冒烟，没有测试这一环。
        # v1.0.663（#18）：门禁默认恒开——标准验证栈是发包必过项，不再是 opt-in；
        # --no-tests / --skip-tests 是逃生口（如 Ollama 环境大面积不可用时的应急）。
        if not (args.no_tests or args.skip_tests):
            _rc = _run_pytest_gate(full_only=args.full_tests)
            if _rc:
                return _rc
            journal.stage("tests_done", ver=v)
        run_smoke(_dist_exe, env)   # v1.0.555（P0-11）：frozen exe 内 import 全量冒烟
        journal.stage("smoke_done", ver=v)
        # v1.0.619（审查⑧）：包内前端资源与源码树逐字节核对——--smoke 只管 import，
        # 管不了"exe 里带的还是旧 index.html/js"（历史上真出过 dist 落后 4 个文件）。
        # v1.0.660（B1）：onedir 走 _internal/ 目录比对，onefile 走 CArchive 比对。
        try:
            if verify_packaged_assets(_dist_exe, onedir=onedir) != 0:
                # v1.0.652（审计 P1-13）：资源不一致＝产物不可发，非零退出。
                # 用 != 0 而非 > 0：读不了归档时返回 -1，`> 0` 会把「没法核对」
                # 判成通过——exe 归档损坏照样发包（与下面 except 的 fail-closed 相反）。
                return 1
        except Exception as e:  # noqa: BLE001
            if force_gate:
                print(f"[build] ⚠️ 资源一致性核对异常（--force 放行）：{e}")
            else:
                print(f"[build] ❌ 资源一致性核对异常（fail-closed，--force 可放行）：{e}")
                return 1
        journal.stage("assets_done", ver=v)
        _build_ok = True
        # v1.0.712：dsh 离线安装包——全新机器（分支/管控网）npm 源不通，PC 接管只能
        # 靠随包分发的离线装。只在本机已装 dsh 且版本变化时重建（zip 数百 MB）；
        # 失败不挡构建（降级回在线安装，行为与 v1.0.711 前一致）。
        try:
            from agent_tools import dsh_bridge as _dshb
            _nb = _dshb.ensure_offline_bundle()
            if _nb:
                print(f"[build] dsh 离线包：{_nb}")
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：dsh 离线包处理失败（降级为在线安装）：{e}")
        # v1.0.677（性能）：**保留 PyInstaller 缓存**（增量构建要靠它），
        # 只在冷构建 / 发布模式下清理——原来无条件删，等于每次都强制冷构建。
        if cold_build:
            clean_pyinstaller_mid()
        else:
            print("[build] 保留 PyInstaller 缓存 build/<exe名>/（约 183MB）"
                  "——下次构建走增量（要清用 --clean-build）")
        # v1.0.677：清掉旧版备份逻辑留下的 产物库/组件 拷贝（1.17GB 死重）。
        # 放在**收尾**阶段而不是备份阶段：实测删这 70,961 个文件要 9 分 44 秒，
        # 挡在关键路径上会让升级后的第一次打包慢到无法忍受；产物此时已产出。
        try:
            prune_stale_component_copy()
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：清理过期组件拷贝失败（忽略，不影响产物）：{e}")
        # v1.0.680（改名）：dist 里不能同时躺着新旧两个 exe——用户双击时点错的那个
        # 就是上一版（旧名 exe 里的单实例 mutex/AUMID/自启都还是旧的）。
        # 把旧名 exe **移走**（不是删）：移到 build/_rollback/ 下仍可回滚。
        # v1.0.691：两代旧名（Lingban/WeComCompanion）都要移
        try:
            for _legacy_exe_name in ALL_EXE_NAMES:
                if _legacy_exe_name == EXE_NAME:
                    continue
                _legacy_exe = os.path.join(BASE, "dist", _legacy_exe_name)
                if not os.path.isfile(_legacy_exe):
                    continue
                _rb = os.path.join(BASE, "build", "_rollback")
                os.makedirs(_rb, exist_ok=True)
                _dst = os.path.join(_rb, _legacy_exe_name)
                if os.path.exists(_dst):
                    os.remove(_dst)
                shutil.move(_legacy_exe, _dst)
                print(f"[build] dist 里的旧名 exe 已移到 build/_rollback/{_legacy_exe_name}"
                      "（避免用户点到上一版；仍可回滚）")
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：旧名 exe 移出 dist 失败（可手工删）：{e}")
        # v1.0.743：构建收尾垃圾回收（S2 _dist_backup 孤儿条目 / S2b 不再备份段副本 /
        # S3 _rollback 非机制残留 / G3 live 档案浏览器缓存，判定口径见 buildlib.gc 文档）。
        # 放在旧名 exe 移动**之后**：刚移进来的旧名 exe 同属残留——S3 只保留当前品牌名。
        # 与 prune_stale_component_copy 同口径：构建进程已清 _SANDBOX_VARS（平台安全
        # 删除钩子对其无效）；失败不挡构建。
        try:
            gc_after_build()
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：构建收尾垃圾回收失败（忽略，不影响产物）：{e}")
        # v1.0.306 / v1.0.738：同步 OCR 运行时到 exe 同级 产物库/ocr/ 旁挂（exe 已不含 OCR 栈，缺了它
        # 课件采集/扫描件 OCR 不可用）。v1.0.660（B1）：目标目录随分发形态联动——
        # 运行时 core/paths._app_dir() = exe 所在目录，onefile 是 dist/、onedir 是
        # dist/<exe名>-onedir/，同步到别处运行时就是找不到。
        try:
            sync_ocr_runtime(dist_dir=_app_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：OCR 运行时同步失败（课件 OCR 将不可用，重跑 build 可补）：{e}")
        # v1.0.603（用户要求）：新版本从零历史对话开始（搬到 build/_chat_archive，可搬回）。
        # 放在冒烟通过之后——打包失败时绝不能顺手把用户的对话搬走。
        clear_chat_history(keep=keep_chats)
        print(f"[build] done -> {_dist_rel}  (v{v})")
        if release:
            # v1.0.619（审查⑦）：剥离开发机数据是**不可逆的位置变更**（config/产物库/日志——
            # --lean 还包含 271MB 的 产物库/ocr/），而且发生在打包成功之后，用户点一次"打包"就顺带做了。
            # 上面那句版本确认覆盖不了它，这里单独再确认一次；--yes（CI/软件内一键打包）跳过。
            if not auto_yes:
                print("[release] 本次是发布模式：打包成功后会把 dist 里的 "
                      "产物库（含 config.json）/ 日志" + ("+ 产物库/ocr/（--lean）" if lean else "") +
                      " 移到 build/_release_excluded/<时间戳>/（可取回，但 dist 里就没有了）。")
                try:
                    ans = input("确认继续剥离？[y/N] ").strip().lower()
                except Exception:  # noqa: BLE001
                    ans = "n"
                if ans != "y":
                    print("[release] 未确认——exe 已生成，dist 保持原样（不剥离）")
                    release = False
        if release:
            release_mode_clean(lean=lean)   # v1.0.405（M4）：发布模式剥离开发机数据；v1.0.473：--lean 连 产物库/ocr/ 一并剥离
        else:
            # v1.0.510（P0-1）：非发布模式打包后扫描 dist 内 PII 分发风险（软提醒不拦截）
            try:
                scan_dist_pii()
            except Exception as e:  # noqa: BLE001
                print(f"[build] 警告：PII 扫描失败（不拦截）：{e}")
        # v1.0.358：版本登记——源码 hash + exe 备份进版本库（产物库/版本/versions.json），
        # 供设置页「📦 版本管理」一键回退。
        # v1.0.616：保留个数从 `_KEEP` 取，不再写死字面量——这里曾印着「保留最近 8 个」，
        # 而 version_store._KEEP 早在 v1.0.373 就瘦身成 3（每次打包日志都在传播这个假数字）。
        try:
            from services import version_store
            version_store.record_version(note=build_note)
            print(f"[build] 版本已登记（产物库/版本/versions.json，保留最近 {version_store._KEEP} 个）")
            journal.stage("recorded", ver=v)
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：版本登记失败（不影响本次构建产物）：{e}")
        # v1.0.748：公共 GitHub 镜像自动更新——「每次版本更新都把脱敏做好」：
        # 每次构建收尾重走 导出 → 脱敏 → 门禁 → orphan 单次提交
        # （tools/publish_github.py --rebuild）。门禁红 → 保留上一版镜像
        # （公共镜像恒处门禁通过态，宁可旧不可脏）；失败不挡构建、不自动推送
        # （推送是外部可见动作，留人工）。
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(BASE, "tools", "publish_github.py"),
                 "--rebuild"],
                capture_output=True, timeout=600)
            out = (r.stdout or b"").decode("utf-8", "replace")
            if out.strip():
                print(out.rstrip())
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            if err:
                print("[publish] " + err.splitlines()[-1])
            if r.returncode != 0:
                print("[build] ⚠️ 本版本公共镜像未更新（门禁未过，保留上一版）——"
                      "修复敏感词残留后重跑 python tools/publish_github.py")
        except Exception as e:  # noqa: BLE001
            print(f"[build] 警告：公共镜像自动更新失败（不影响本次构建产物）：{e}")
        # v1.0.764（用户：以后待发布里所有都要脱敏）：--public-exe 发布硬闸
        # （脱敏 exe 重打包 + exe 级 PYZ 下钻门禁；实现见 _run_public_exe_gate）。
        _pub_gate = _run_public_exe_gate(args, journal, v)
        if _pub_gate is not None:
            return _pub_gate
        journal.stage("done", ver=v)
    finally:
        # v1.0.660（B7）：bump 之后任何一步失败都把版本号退回去——
        # "版本号 +1" 必须与"有一份合格产物"一一对应，否则版本库里出现空号。
        if not _build_ok:
            rollback_version(old_v)
            # v1.0.663（B7 硬杀洞补丁②）：正常失败收尾留痕——下次启动的 self_heal
            # 看到终态 rolled_back 就不会重复动作（硬杀恰恰是没有这条记录）。
            journal.stage("rolled_back", ver=v, frm=old_v)


if __name__ == "__main__":
    # v1.0.652（审计 P1-13）：显式传播退出码——main() 返回 None/0=成功，非零=门禁拦截。
    # 旧 `main()` 裸调用恒 exit 0，自动化把门禁拦截当构建成功。
    sys.exit(main() or 0)
