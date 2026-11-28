# -*- coding: utf-8 -*-
"""tests/contract/test_contract_gate_failopen.py — 门禁「看着跑了其实没拦」的行为锁。

四处 fail-open / 口径缺陷，改回坏形状必红：
  ① 包内资源核对返回 -1（归档读不了）时 `> 0` 判成通过 —— 必须 `!= 0` fail-closed；
  ② 并行串扰串行复跑全绿后直接 return 0，绕过末尾的增量覆盖率门槛；
  ③ --cov=. 把 tests/ 与 dist/（整套第三方 site-packages）一起采集，报告被噪声淹掉；
  ④ 便携 node 只装在 dist/产物库 时 find_node() 恒 None，JS 语法校验与 ESM 求值
     验证两道必跑闸门静默跳过。

①②④ 的判定段直接**取真实源码里的语句执行**（AST 摘出来跑），不是 grep 文案；
③ 断言的是 pytest_cov_args() 的实际产出与 fnmatch 语义。
"""
import ast
import fnmatch
import io
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import build as B  # noqa: E402
from buildlib import coverage_gap as CG  # noqa: E402
from buildlib import env as ENV  # noqa: E402


def _src(rel):
    with io.open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- ① 资源核对 fail-closed
def _asset_gate_result(rc):
    """把 build.py 里那段 `if verify_packaged_assets(...) ...: return 1` 摘出来真跑。

    返回 0 = 该 rc 下放行，1 = 该 rc 下拦。rc=-1 是「归档读不了」，必须拦。
    """
    tree = ast.parse(_src("build.py"))
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        calls = [n for n in ast.walk(node.test)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "verify_packaged_assets"]
        if calls:
            target = node
            break
    assert target is not None, "build.py 里找不到资源核对的判定段"
    # 用真实 parse 出来的壳子装 AST 片段，避免手搓 FunctionDef 漏 3.13 新字段
    mod = ast.parse("def _probe():\n    return 0\n")
    mod.body[0].body = [target, ast.parse("return 0").body[0]]
    ast.fix_missing_locations(mod)
    ns = {"verify_packaged_assets": lambda *a, **k: rc,
          "_dist_exe": "fake.exe", "onedir": False}
    exec(compile(mod, "<asset-gate>", "exec"), ns)
    return ns["_probe"]()


@pytest.mark.parametrize("rc,expected", [
    (-1, 1),   # 读不了归档 → 没法核对 ≠ 核对通过
    (1, 1),    # 不一致
    (3, 1),
    (0, 0),    # 一致 → 放行
])
def test_packaged_asset_gate_fails_closed_on_unreadable_archive(rc, expected):
    assert _asset_gate_result(rc) == expected


# ---------------------------------------------------------------- ② 串行复跑不 bypass 覆盖率
class _FakeRun:
    def __init__(self, rc, out=b""):
        self.returncode = rc
        self.stdout = out
        self.stderr = b""


@pytest.fixture
def gate(monkeypatch):
    """把 _run_pytest_gate 的外部依赖全替掉，只留它的放行判定本身。"""
    seen = {"runs": [], "cov": 0}
    monkeypatch.setattr(B.gate_scope, "classify", lambda base: ("full", [], "桩：全量"))
    monkeypatch.setattr(B, "_xdist_ok", lambda: True)
    monkeypatch.setattr(B.coverage_gap, "pytest_cov_args", lambda: [])

    def _cov(base=None, **kw):
        seen["cov"] += 1
        return seen["cov_rc"]

    monkeypatch.setattr(B.coverage_gap, "check_incremental_coverage", _cov)

    def _install(results):
        it = iter(results)

        def _run(cmd, **kw):
            seen["runs"].append(list(cmd))
            return next(it)

        monkeypatch.setattr(B.subprocess, "run", _run)

    seen["install"] = _install
    seen["cov_rc"] = 0
    return seen


_RED = _FakeRun(1, b"FAILED tests/unit/test_x.py::test_a - boom\n")


def test_serial_rerun_green_still_reaches_coverage_gate(gate):
    gate["install"]([_RED, _FakeRun(0, b"1 passed\n")])
    gate["cov_rc"] = 1
    assert B._run_pytest_gate() == 1, "串行复跑全绿后必须继续过覆盖率门槛，不能 return 0"
    assert gate["cov"] == 1
    assert len(gate["runs"]) == 2


def test_serial_rerun_green_and_coverage_green_passes(gate):
    gate["install"]([_RED, _FakeRun(0, b"1 passed\n")])
    gate["cov_rc"] = 0
    assert B._run_pytest_gate() == 0
    assert gate["cov"] == 1


def test_serial_rerun_still_red_blocks_without_coverage(gate):
    gate["install"]([_RED, _FakeRun(1, b"FAILED tests/unit/test_x.py::test_a\n")])
    assert B._run_pytest_gate() != 0
    assert gate["cov"] == 0


def test_plain_failure_blocks(gate):
    gate["install"]([_FakeRun(1, b"1 failed\n")])   # 无 FAILED 明细 → 不进串行复跑
    assert B._run_pytest_gate() != 0
    assert gate["cov"] == 0 and len(gate["runs"]) == 1


def test_all_green_reaches_coverage_gate(gate):
    gate["install"]([_FakeRun(0, b"2000 passed\n")])
    gate["cov_rc"] = 1
    assert B._run_pytest_gate() == 1
    assert gate["cov"] == 1


# ---------------------------------------------------------------- ③ 覆盖率采集面
def test_cov_args_omit_tests_and_dist_but_keep_production():
    args = CG.pytest_cov_args()
    # pytest-cov 没有 --cov-omit 命令行参数（v1.0.767 实测打包期 argparse 直接
    # 拒绝）——omit 必须走 coverage 配置文件，经 --cov-config 传入。
    cfg_arg = next((a for a in args if a.startswith("--cov-config=")), None)
    assert cfg_arg, "采集面没配 omit 配置，XML 会被第三方 site-packages 撑爆"
    assert "--cov-omit" not in " ".join(args)
    rc_path = cfg_arg.split("=", 1)[1]
    text = io.open(rc_path, encoding="utf-8").read()
    m = re.search(r"omit\s*=\s*\n((?:\s+.*\n?)+)", text)
    assert m, "配置文件缺 [run] omit 段"
    pats = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]

    def _omitted(rel):
        p = os.path.join(ROOT, *rel.split("/"))
        return any(fnmatch.fnmatch(p, pat) for pat in pats)

    assert _omitted("tests/unit/test_x.py")
    assert _omitted("dist/产物库/组件/asr/site-packages/foo/bar.py")
    assert not _omitted("core/paths.py")
    assert not _omitted("services/batch_flow.py")
    assert not _omitted("agent/loop.py")


def test_cov_still_reports_xml_and_no_absolute_threshold():
    args = CG.pytest_cov_args()
    assert "--cov=." in args
    assert any(a.startswith("--cov-report=xml:") for a in args)
    assert not any(a.startswith("--cov-fail-under") for a in args)


# ---------------------------------------------------------------- ④ node 闸门不再静默跳过
@pytest.fixture
def no_path_node(monkeypatch, tmp_path):
    monkeypatch.setattr(ENV.shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(ENV, "BASE", str(tmp_path))
    return tmp_path


def _plant(base, rel):
    p = os.path.join(str(base), *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8").write("")
    return p


def test_node_found_in_dist_runtime_is_prepended_to_path(no_path_node):
    exe = _plant(no_path_node, "dist/产物库/组件/node/current/node-v22.20.0-win-x64/node.exe")
    assert ENV.ensure_node_on_path() == exe
    assert os.environ["PATH"].split(os.pathsep)[0] == os.path.dirname(exe)


def test_node_found_in_source_runtime(no_path_node):
    exe = _plant(no_path_node, "产物库/组件/node/node.exe")
    assert ENV.ensure_node_on_path() == exe
    assert os.environ["PATH"].startswith(os.path.dirname(exe) + os.pathsep)


def test_no_node_anywhere_returns_none_and_leaves_path_alone(no_path_node):
    before = os.environ["PATH"]
    assert ENV.ensure_node_on_path() is None
    assert os.environ["PATH"] == before


def test_node_deeper_than_portable_layout_is_not_guessed(no_path_node):
    # 与 portable._exe_candidates 同口径：只探 2 层，不做全量递归
    _plant(no_path_node, "dist/产物库/组件/node/a/b/c/node.exe")
    assert ENV.ensure_node_on_path() is None


def test_existing_path_node_wins_and_path_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(ENV, "BASE", str(tmp_path))
    monkeypatch.setattr(ENV.shutil, "which", lambda name: "X:/sys/node.exe")
    before = os.environ.get("PATH", "")
    assert ENV.ensure_node_on_path() == "X:/sys/node.exe"
    assert os.environ.get("PATH", "") == before


def test_build_resolves_node_before_frontend_gate():
    """接线顺序锁：PATH 补必须在 check_frontend_structure 之前，否则补了也白补。"""
    fn = next(n for n in ast.parse(_src("build.py")).body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    order = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in ("ensure_node_on_path", "check_frontend_structure"):
            order.append((n.lineno, n.func.id))
    order.sort()
    names = [x[1] for x in order]
    assert names[:2] == ["ensure_node_on_path", "check_frontend_structure"], names
    # v1.0.768（学习模块移除）：原「漏收集的回归脚本」一节（test_e2e_learn_capture.py
    # 的 OOPIF 回归脚本收集锁）随学习模块的 e2e/回归文件一并删除。