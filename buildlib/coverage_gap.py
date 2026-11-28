# -*- coding: utf-8 -*-
"""buildlib.coverage_gap — 千行大文件的**契约测试覆盖缺口**静态扫描（评审 P1-2）。

背景：项目有 1600+ 条测试但此前没有覆盖率门槛，>1000 行的生产文件里有一批
「没有同名/同前缀专测」的高风险模块（安全中心、LLM 全链路、工具注册表、
管理路由、关闭守卫）。v1.0.755 先做了三件零依赖、可验证的事：

  ① scan_large_sources() 找出所有 >LARGE_FILE_LINES 行的生产 .py（观察项）；
  ② HIGH_RISK 高风险清单必须有 tests/contract/test_contract_<stem>.py 契约测试，
     且该测试必须引用登记表里的关键符号（防「空壳契约」）；find_gaps() 返回硬缺口；
  ③ pytest_cov_args() fail-open 钩子（只采集，不拦）。

v1.0.756（评审 P1-2 字面补做）：pytest-cov==7.0.0 / diff-cover==9.7.1 已钉进
requirements-dev.txt 并装入构建 venv，**真实「新增代码行覆盖率 ≥70%」增量门槛**
由 check_incremental_coverage() 实现并接入 build.py::_run_pytest_gate：
  · pytest_cov_args() 产出 .coverage.xml（Cobertura）；
  · parse_diff_added_lines + untracked_py_lines 取本轮新增生产 .py 行；
  · parse_coverage_xml 取每行 hits，只统计 coverage 认为可测的行
    （注释/空行/docstring 自动排除——与 diff-cover 同算法）；
  · 覆盖率 <70% / XML 缺失 / 工具缺失 / git 判定失败一律 fail-closed 中止发包；
  · diff-cover CLI 用 --diff-file 跑 --fail-under=70 作交叉报告，返回码并入判定。
D5 已闭环，不再登记「待 diff-cover」。

CLI: python -m buildlib.coverage_gap                → 覆盖矩阵 + 硬缺口
     python -m buildlib.coverage_gap --incremental  → 单独跑增量覆盖率门槛
"""
import contextlib
import io
import os
import re
import subprocess
import sys

from buildlib import scope
from buildlib.env import BASE   # noqa: E402（.cov_omit.rc 落 build/，gitignored）

LARGE_FILE_LINES = 1000
CONTRACT_DIR = "tests/contract"
CONTRACT_PREFIX = "test_contract_"

# 高风险模块 → (契约测试文件名, 该测试必须引用的关键符号)。
# 文件名**显式声明**而不是按模块 stem 硬推：契约测试按能力域命名
# （security_gate / llm_stream），比 test_contract_security_ops 更能说明它锁的是哪条红线。
# 新增高风险模块时在这里登记一行即可自动纳入硬门槛；观察项逐步移入，见
# docs/testing-contracts.md 的「棘轮」约定。
HIGH_RISK = {
    "security_ops": ("test_contract_security_gate",
                     ("gate_write_tool", "_ALWAYS_ASK", "bg_deny_result")),
    "llm_client": ("test_contract_llm_stream",
                   ("is_stream_interrupt", "stream_llm_events", "abort_active_responses")),
    "executors": ("test_contract_executors",
                  ("_TOOL_HANDLERS", "run_tool", "LOOP_HANDLED_TOOLS")),
    "b_admin": ("test_contract_b_admin",
                ("mgmt_admin", "post_config", "api_status")),
    "close_guard": ("test_contract_close_guard",
                    ("install", "close_ask_pending", "apply_web_choice")),
}

# 扫描生产源码时的跳过目录：复用 scope 单一事实源，再补上纯开发期/构建期目录。
_SKIP_DIRS = frozenset(
    set(scope.skip_dirs("source_walk"))
    | {"tools", "css", "node_modules", ".pytest_cache", "组件",
       "_dist_backup", "_rollback", "_chat_archive", "_chat_archive"}
)


def _line_count(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def scan_large_sources(root):
    """返回 [(相对路径, 行数)]，按行数降序。只扫生产 .py，开发期目录全部跳过。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                n = _line_count(full)
            except OSError:
                continue
            if n > LARGE_FILE_LINES:
                out.append((os.path.relpath(full, root).replace("\\", "/"), n))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def contract_path(root, stem):
    """高风险模块用登记表里的显式文件名；观察项按 stem 约定推导。"""
    if stem in HIGH_RISK:
        fname = HIGH_RISK[stem][0] + ".py"
    else:
        fname = CONTRACT_PREFIX + stem + ".py"
    return os.path.join(root, CONTRACT_DIR, fname)


def find_gaps(root):
    """硬缺口清单：高风险模块缺契约测试 / 契约测试未引用关键符号 / 契约测试是空壳。"""
    gaps = []
    for stem, (_fname, symbols) in HIGH_RISK.items():
        path = contract_path(root, stem)
        if not os.path.isfile(path):
            gaps.append("%s: 缺契约测试 %s" % (stem, os.path.relpath(path, root)))
            continue
        try:
            text = io.open(path, encoding="utf-8").read()
        except OSError as e:
            gaps.append("%s: 契约测试不可读 %s" % (stem, e))
            continue
        missing = [s for s in symbols if s not in text]
        if missing:
            gaps.append("%s: 契约测试未引用关键符号 %s" % (stem, ", ".join(missing)))
        elif len(text.splitlines()) < 25:
            gaps.append("%s: 契约测试疑似空壳（<25 行）" % stem)
    return gaps


def coverage_matrix(root):
    """[(相对路径, 行数, 契约测试文件 或 '', 是否高风险)]，供 CLI / 测试断言。"""
    large = scan_large_sources(root)
    rows = []
    for rel, n in large:
        stem = os.path.splitext(os.path.basename(rel))[0]
        cp = contract_path(root, stem)
        has = os.path.relpath(cp, root).replace("\\", "/") if os.path.isfile(cp) else ""
        rows.append((rel, n, has, stem in HIGH_RISK))
    return rows


def pytest_cov_args():
    """pytest-cov 参数：采集行覆盖率并产出 Cobertura XML（增量门槛的数据源）。

    v1.0.756 起不再是「装了才检查」的 fail-open 钩子：pytest-cov / diff-cover
    已钉进 requirements-dev.txt，缺任一个都会在 check_incremental_coverage()
    里 fail-closed 拦下打包。仍不设 --cov-fail-under——70% 只针对**新增代码行**，
    不是全仓绝对值（全仓绝对值会误杀合格构建）。
    """
    try:
        import importlib.util
        if importlib.util.find_spec("pytest_cov") is None:
            return []
    except Exception:  # noqa: BLE001  silent-ok: 探测失败由下游 fail-closed 统一处理
        return []
    # 采集面只留生产源码：--cov=. 会把 tests/ 与 dist/（内含整套第三方 ASR
    # site-packages）一起测，XML 膨胀到几十 MB、term-missing 报告被噪声淹掉，
    # 而增量门槛本来就只认 _is_production_py 的文件——排除它们不改变判定口径。
    # 注意：pytest-cov **没有 --cov-omit 命令行参数**（coverage.py 的 omit 只走
    # 配置文件）——omit 模式写进 build/.cov_omit.rc 再经 --cov-config 传入。
    # 模式单源仍是 scope.skip_dirs（与门禁同一份排除名单，防漂移）。
    pats = ["*/%s/*" % d for d in sorted(set(scope.skip_dirs("source_walk")) | {"tests"})]
    rc = os.path.join(BASE, "build", ".cov_omit.rc")
    with contextlib.suppress(OSError):
        # 写不了就退回全量采集（口径不变，只是报告有噪声）——suppress 不计裸 except 棘轮
        os.makedirs(os.path.dirname(rc), exist_ok=True)
        with open(rc, "w", encoding="utf-8") as f:
            f.write("[run]\nomit =\n")
            f.write("\n".join("    " + p for p in pats) + "\n")
    return ["--cov=.", "--cov-config=%s" % rc,
            "--cov-report=term-missing:skip-covered",
            "--cov-report=xml:%s" % COVERAGE_XML]


# ---------- v1.0.756（评审 P1-2 字面补做）：真实「新增代码行 ≥70%」增量门槛 ----------
# v1.0.755 的 pytest_cov_args() 只是 fail-open 采集钩子，真门槛登记在 D5 待办。
# 本轮改为**硬拦截**：pytest 全绿之后算本轮新增生产 .py 行的覆盖率，<70% 直接
# 中止发包；工具缺失 / XML 缺失 / git 判定失败一律 fail-closed。
# 口径与 diff-cover 一致：只统计 coverage 认为「可测」的行（Cobertura XML 里的
# line 节点，含 hits=0），注释 / 空行 / docstring 自动不计入分母。
INCREMENTAL_MIN_COVERAGE = 70
COVERAGE_XML = ".coverage.xml"

# 「生产源码」口径复用 scope 单一事实源（与 scan_large_sources 同），另排除纯构建期
# 目录：它们不进 exe 运行时，覆盖率采集时不一定被 import。
# 「logo」= v1.0.785 收编的「AI 鱼&羊」品牌包：01_source 是独立的几何真源渲染链
# （pycairo 迷你工程，主程序不 import、spec datas 不打包），不是生产码——
# 首收编那轮 1697 行 0% 覆盖把增量门槛打爆就是没排它的锅。
_PRODUCTION_SKIP = frozenset(set(scope.skip_dirs("source_walk")) | {"tools", "logo"})


def _is_production_py(rel):
    """增量门槛只管生产 .py：镜像 / tests / docs / 构建期目录都不算。"""
    rel = (rel or "").replace("\\", "/").lstrip("./")
    if not rel.endswith(".py") or rel.startswith("../"):
        return False
    parts = rel.split("/")
    return not any(p in _PRODUCTION_SKIP for p in parts[:-1])


def _git_exe():
    import shutil
    p = shutil.which("git")
    if p:
        return p
    cand = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                        "PortableGit", "versions", "1.3.0", "cmd", "git.exe")
    return cand if os.path.isfile(cand) else None


def _git_out(base, *args):
    """跑 git 并返回 (stdout, err)。任何失败都由调用方 fail-closed 处理。"""
    exe = _git_exe()
    if not exe:
        return None, "找不到 git（增量覆盖率门槛无法判定）"
    try:
        r = subprocess.run([exe, *args], cwd=base, capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001  silent-ok: 失败信息原样回传，由 fail-closed 统一处理
        return None, "git 执行失败：%s" % e
    if r.returncode != 0:
        msg = (r.stderr or b"").decode("utf-8", "replace")[:200]
        return None, "git %s 失败：%s" % (" ".join(args), msg)
    return (r.stdout or b"").decode("utf-8", "replace"), None


def parse_diff_added_lines(diff_text):
    """把 `git diff --unified=0 HEAD -- '*.py'` 解析成 {rel: set(新增行号)}。

    只保留生产 .py；`+++ /dev/null`（删除文件）与 hunk 里的删除行自动跳过。
    纯函数，tests/contract/test_incremental_coverage.py 用样例 diff 锁住解析口径。
    """
    out = {}
    cur = None
    new_ln = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            cur = None
            if path != "/dev/null":
                if path.startswith("b/"):
                    path = path[2:]
                if _is_production_py(path):
                    cur = path
                    out.setdefault(cur, set())
            continue
        if cur is None:
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            new_ln = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out[cur].add(new_ln)
            new_ln += 1
        elif line.startswith(" "):
            new_ln += 1
        # '-' 删除行 / '\ No newline' / 空行都不推进新文件行号
    return {k: v for k, v in out.items() if v}


def untracked_py_lines(base):
    """未跟踪的新 .py 文件所有行都算新增（稍后与 coverage 可测行取交集）。"""
    txt, err = _git_out(base, "ls-files", "--others", "--exclude-standard", "--", "*.py")
    if err:
        return {}, err
    out = {}
    for rel in txt.splitlines():
        rel = rel.strip().strip('"')
        if not _is_production_py(rel):
            continue
        full = os.path.join(base, *rel.split("/"))
        try:
            n = _line_count(full)
        except OSError:
            continue
        out[rel] = set(range(1, n + 1))
    return out, None


def parse_coverage_xml(xml_path, base=None):
    """Cobertura XML → {rel: {行号: hits}}。hits=0 的行也在表里（可测但未执行）。"""
    import xml.etree.ElementTree as ET
    out = {}
    for cls in ET.parse(xml_path).iter("class"):
        fn = (cls.get("filename") or "").replace("\\", "/")
        if not fn:
            continue
        if os.path.isabs(fn) and base:
            try:
                fn = os.path.relpath(fn, base).replace("\\", "/")
            except ValueError:
                continue
        if not _is_production_py(fn):
            continue
        lines = {}
        for ln in cls.findall("lines/line"):
            try:
                lines[int(ln.get("number"))] = int(ln.get("hits") or 0)
            except (TypeError, ValueError):
                continue
        out.setdefault(fn, {}).update(lines)
    return out


def incremental_report(added_map, cov_map, min_pct=None):
    """纯函数：算新增行覆盖率。改回「只看全仓绝对值」或「不达标也放行」必红。"""
    min_pct = INCREMENTAL_MIN_COVERAGE if min_pct is None else min_pct
    covered = total = 0
    per_file = []
    for rel in sorted(added_map):
        added = added_map[rel]
        m = cov_map.get(rel)
        if m is None:
            # 文件根本没进 coverage 报告 = 一行都没被测到（fail-closed 口径）
            total += len(added)
            per_file.append((rel, 0, len(added)))
            continue
        c = t = 0
        for ln in sorted(added):
            if ln not in m:
                continue          # 注释 / 空行 / docstring：coverage 不视为可测行
            t += 1
            if m[ln] > 0:
                c += 1
        covered += c
        total += t
        per_file.append((rel, c, t))
    pct = 100.0 if total == 0 else round(covered * 100.0 / total, 2)
    return {"covered": covered, "total": total, "pct": pct,
            "min_pct": min_pct, "passed": pct >= float(min_pct),
            "per_file": per_file, "no_new_lines": total == 0}


def check_incremental_coverage(base=None, min_pct=None, emit=True):
    """v1.0.756 硬门槛：返回 0=达标 / 1=未达标或无法判定（fail-closed）。

    主判据用上面三个纯函数（可单元测试、含未跟踪新文件）；另用 diff-cover CLI
    跑一遍 `--fail-under=70 --diff-file=<git diff>` 作交叉报告，其返回码**并入**
    放行判定（D5 处置动作字面落地）。CI 上会打印逐文件增量覆盖率。
    """
    import contextlib
    import importlib.util
    import tempfile
    base = base or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    min_pct = INCREMENTAL_MIN_COVERAGE if min_pct is None else min_pct
    xml_path = os.path.join(base, COVERAGE_XML)
    if not os.path.isfile(xml_path):
        print("[coverage-gap] ❌ 找不到 %s：pytest-cov 未采集，增量门槛无法判定（fail-closed）"
              % COVERAGE_XML)
        return 1
    if importlib.util.find_spec("diff_cover") is None:
        print("[coverage-gap] ❌ diff-cover 未安装（requirements-dev.txt 已钉版）："
              "增量门槛无法判定（fail-closed）")
        return 1
    diff_txt, err = _git_out(base, "diff", "--unified=0", "--no-color", "HEAD", "--", "*.py")
    if err:
        print("[coverage-gap] ❌ %s（fail-closed）" % err)
        return 1
    added = parse_diff_added_lines(diff_txt)
    untracked, err2 = untracked_py_lines(base)
    if err2:
        print("[coverage-gap] ❌ %s（fail-closed）" % err2)
        return 1
    for rel, lines in untracked.items():
        added.setdefault(rel, set()).update(lines)
    cov = parse_coverage_xml(xml_path, base)
    rep = incremental_report(added, cov, min_pct)
    if emit:
        print("[coverage-gap] 增量覆盖率：%d/%d 行 = %.2f%%（门槛 %s%%）%s" % (
            rep["covered"], rep["total"], rep["pct"], rep["min_pct"],
            "；本轮无新增生产 .py 可测行" if rep["no_new_lines"] else ""))
        for rel, c, t in rep["per_file"]:
            if t != c:
                print("[coverage-gap]   %-46s %4d/%-4d 行" % (rel, c, t))
    # diff-cover 交叉报告：用 --diff-file 喂 git diff，绕开本地仓库无 origin/main
    # 的问题（默认 compare_branch 是 origin/main，三点 diff 还不包含工作区改动）。
    diff_file = None
    try:
        fd, diff_file = tempfile.mkstemp(prefix="fisheep_cov_", suffix=".diff")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(diff_txt)
        r = subprocess.run(
            [sys.executable, "-m", "diff_cover.diff_cover_tool", xml_path,
             "--diff-file", diff_file, "--fail-under", str(min_pct)],
            cwd=base, capture_output=True, timeout=300)
        dc_out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
        if emit:
            print("[coverage-gap] diff-cover 交叉报告（rc=%d）：" % r.returncode)
            for ln in dc_out.splitlines()[-25:]:
                print("[coverage-gap]   " + ln)
        dc_ok = (r.returncode == 0)
    except Exception as e:  # noqa: BLE001  silent-ok: 交叉报告失败按「不通过」并入判定
        print("[coverage-gap] ⚠️ diff-cover 交叉报告执行失败：%s" % e)
        dc_ok = False
    finally:
        if diff_file:
            with contextlib.suppress(OSError):
                os.unlink(diff_file)
    if not rep["passed"] or not dc_ok:
        print("[coverage-gap] ❌ 增量覆盖率门槛未通过（主判据 %s / diff-cover %s）——中止发包"
              % ("通过" if rep["passed"] else "未达标", "通过" if dc_ok else "未通过"))
        return 1
    print("[coverage-gap] ✓ 新增代码行覆盖率达标（≥%s%%）" % min_pct)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--incremental" in argv:
        return check_incremental_coverage()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = coverage_matrix(root)
    print("[coverage-gap] >%d 行的生产文件：%d 个" % (LARGE_FILE_LINES, len(rows)))
    for rel, n, has, high in rows:
        flag = "HIGH" if high else "    "
        print("  %s %5d  %-42s  契约: %s" % (flag, n, rel, has or "—"))
    gaps = find_gaps(root)
    n_high = sum(1 for r in rows if r[3])
    covered = sum(1 for r in rows if r[3] and r[2])
    print("[coverage-gap] 高风险契约覆盖：%d/%d；硬缺口：%d" % (covered, n_high, len(gaps)))
    for g in gaps:
        print("  ❌ " + g)
    if gaps:
        return 1
    print("[coverage-gap] ✓ 高风险模块契约测试齐全（观察项见 docs/testing-contracts.md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
