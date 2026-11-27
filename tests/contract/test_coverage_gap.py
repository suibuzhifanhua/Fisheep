# -*- coding: utf-8 -*-
"""tests/contract/test_coverage_gap.py — 覆盖缺口扫描器自身的**行为锁**（评审 P1-2）。

保证 buildlib/coverage_gap.py 不是「装个样子」：
  · 当前 5 个高风险模块契约齐全（find_gaps 为空、main 返回 0）；
  · 扫描只覆盖生产源码，tests/docs 里的超大测试文件不算进来；
  · 登记一个不存在的高风险模块 → 必须立刻报缺口（改回坏形状必红）；
  · pytest_cov_args 产出 Cobertura XML、绝不返回 --cov-fail-under；
  · 真实增量门槛在 check_incremental_coverage（另有 tests/contract/test_incremental_coverage.py 行为锁）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from buildlib import coverage_gap as CG  # noqa: E402


def test_no_gaps_for_high_risk_modules():
    assert CG.find_gaps(ROOT) == []


def test_main_succeeds_when_contracts_are_complete():
    assert CG.main([]) == 0


def test_main_fails_when_a_gap_is_injected(monkeypatch):
    monkeypatch.setattr(CG, "find_gaps", lambda _root: ["probe: 人为注入的缺口"])
    assert CG.main([]) == 1


def test_high_risk_matrix_is_fully_covered():
    rows = CG.coverage_matrix(ROOT)
    high = [r for r in rows if r[3]]
    assert len(high) == len(CG.HIGH_RISK), (len(high), len(CG.HIGH_RISK))
    assert all(r[2] for r in high), "高风险模块仍有缺契约测试的"


def test_scan_excludes_tests_and_docs():
    rows = CG.scan_large_sources(ROOT)
    assert rows, "一个 >1000 行的生产文件都没扫到，扫描器失效"
    assert not any(rel.startswith(("tests/", "docs/", "tools/", "待发布版本/"))
                   for rel, _n in rows)
    assert any(rel == "security_ops.py" for rel, _n in rows)


def test_missing_contract_is_reported(monkeypatch):
    monkeypatch.setitem(CG.HIGH_RISK, "__probe_module__",
                        ("test_contract___probe_module__", ("no_such_symbol",)))
    gaps = CG.find_gaps(ROOT)
    assert any("__probe_module__" in g for g in gaps)


def test_empty_contract_is_reported(tmp_path, monkeypatch):
    d = str(tmp_path)
    os.makedirs(os.path.join(d, "tests", "contract"))
    io.open(os.path.join(d, "tests", "contract", "test_contract_probe.py"),
            "w", encoding="utf-8").write("# empty\n")
    monkeypatch.setitem(CG.HIGH_RISK, "probe",
                        ("test_contract_probe", ("gate_write_tool",)))
    gaps = CG.find_gaps(d)
    assert any("probe" in g for g in gaps), "空壳契约必须被判为缺口"


def test_pytest_cov_args_feeds_the_incremental_gate():
    args = CG.pytest_cov_args()
    assert isinstance(args, list)
    assert not any(a.startswith("--cov-fail-under") for a in args), (
        "全仓绝对值门槛会拦下合格构建，70% 只针对新增代码行")
    assert any(a.startswith("--cov-report=xml:") for a in args), (
        "增量门槛的数据源是 Cobertura XML，删掉它就等于把门槛改回空转")
    assert CG.COVERAGE_XML in " ".join(args)


def test_incremental_threshold_is_70():
    assert CG.INCREMENTAL_MIN_COVERAGE == 70, "评审 P1-2 口径：新增代码行 ≥70%"
