# -*- coding: utf-8 -*-
"""v1.0.756（评审 P1-2 字面补做）行为锁：真实「新增代码行 ≥70%」增量门槛。

v1.0.755 只有 fail-open 采集钩子 + docs/deferred.md D5 降级登记；本批把门槛
真的接进 build.py::_run_pytest_gate，返回码直接决定放不放行。改回坏形状必红：
  · 把 70% 调低 / 删掉 Cobertura XML 采集 / 把接线改成「只打印不 return」；
  · 让「不达标也放行」、把交叉报告返回码踢出判定；
  · 把 D5 改回「部分落地」、把依赖从 requirements-dev.txt 拿掉。
同时也锁住报告 P1-2 的验收判据：改 security_ops.gate_write_tool 放行条件，
契约测试必须存在（tests/contract/test_contract_security_gate.py 引用该符号）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from buildlib import coverage_gap as CG  # noqa: E402


def _src(rel):
    with io.open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _mini_xml(path):
    io.open(path, "w", encoding="utf-8").write(
        '<?xml version="1.0" ?>\n'
        '<coverage><sources><source>.</source></sources>'
        '<packages><package name="p"><classes>'
        '<class filename="m.py" name="m.py"><lines>'
        '<line number="1" hits="1"/><line number="2" hits="0"/>'
        '</lines></class></classes></package></packages></coverage>\n')


class TestThresholdConstants:
    def test_min_is_70(self):
        assert CG.INCREMENTAL_MIN_COVERAGE == 70

    def test_cov_args_produce_cobertura_xml_and_no_absolute_fail_under(self):
        args = CG.pytest_cov_args()
        assert isinstance(args, list)
        assert any(a.startswith("--cov-report=xml:") for a in args)
        assert CG.COVERAGE_XML in " ".join(args)
        assert not any(a.startswith("--cov-fail-under") for a in args), \
            "70% 只针对新增代码行，全仓绝对值会误杀合格构建"

    def test_dev_requirements_pin_both_tools(self):
        text = _src("requirements-dev.txt")
        assert "pytest-cov==7.0.0" in text
        assert "diff-cover==9.7.1" in text


class TestPureFunctions:
    def test_fully_covered_passes(self):
        r = CG.incremental_report({"m.py": {1, 2}}, {"m.py": {1: 5, 2: 1}})
        assert r["pct"] == 100.0 and r["passed"] is True

    def test_quarter_covered_fails(self):
        r = CG.incremental_report({"m.py": {1, 2, 3, 4}},
                                  {"m.py": {1: 1, 2: 0, 3: 0, 4: 0}})
        assert r["pct"] == 25.0 and r["passed"] is False
        assert r["covered"] == 1 and r["total"] == 4

    def test_min_pct_is_injectable(self):
        r = CG.incremental_report({"m.py": {1, 2}}, {"m.py": {1: 1, 2: 0}}, 50)
        assert r["pct"] == 50.0 and r["passed"] is True
        assert CG.incremental_report({"m.py": {1, 2}},
                                     {"m.py": {1: 1, 2: 0}}, 51)["passed"] is False

    def test_non_measurable_lines_are_not_counted(self):
        """注释 / 空行 / docstring 不在 Cobertura 的 line 节点里 → 不进分母。"""
        r = CG.incremental_report({"m.py": {1, 2, 3, 99}},
                                  {"m.py": {1: 1, 2: 0, 3: 1}})
        assert r["total"] == 3 and r["covered"] == 2
        assert r["pct"] == 66.67 and r["passed"] is False

    def test_file_missing_from_coverage_is_all_uncovered(self):
        r = CG.incremental_report({"ghost.py": {1, 2}}, {})
        assert r["total"] == 2 and r["covered"] == 0 and r["passed"] is False

    def test_no_new_lines_is_explicitly_flagged(self):
        r = CG.incremental_report({}, {})
        assert r["no_new_lines"] is True and r["pct"] == 100.0
        assert r["passed"] is True

    def test_parse_diff_added_lines_multiple_hunks(self):
        diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
                "@@ -1,0 +2,2 @@\n+aa\n+bb\n"
                "@@ -10 +20 @@\n+cc\n"
                "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n"
                "@@ -1 +0,0 @@\n-old\n")
        assert CG.parse_diff_added_lines(diff) == {"m.py": {2, 3, 20}}

    def test_parse_diff_skips_tests_and_non_py(self):
        diff = ("+++ b/tests/test_x.py\n@@ -1 +1 @@\n+aa\n"
                "+++ b/README.md\n@@ -1 +1 @@\n+bb\n"
                "+++ b/core/x.py\n@@ -1 +1 @@\n+cc\n")
        assert CG.parse_diff_added_lines(diff) == {"core/x.py": {1}}

    def test_is_production_py_excludes_mirror_and_build_dirs(self):
        assert CG._is_production_py("core/x.py") is True
        assert CG._is_production_py("tests/test_x.py") is False
        assert CG._is_production_py("待发布版本/core/x.py") is False
        assert CG._is_production_py("build/x.py") is False
        assert CG._is_production_py("tools/publish_github.py") is False
        # v1.0.785：品牌包 logo/01_source 是独立几何渲染链（不进 exe/不被 import），
        # 不算生产码——首收编那轮 1697 行 0% 覆盖打爆增量门槛就是没排它的锅。
        assert CG._is_production_py("logo/01_source/brand_kit.py") is False

    def test_parse_coverage_xml(self, tmp_path):
        p = str(tmp_path / "cov.xml")
        _mini_xml(p)
        assert CG.parse_coverage_xml(p) == {"m.py": {1: 1, 2: 0}}


class TestGateIsWiredAndFailClosed:
    def test_build_py_returns_the_gate_result(self):
        text = _src("build.py")
        assert "return coverage_gap.check_incremental_coverage(base=BASE)" in text, \
            "门槛返回码必须直接并入 _run_pytest_gate 放行判定，不能只打印"
        assert "增量门槛见 docs/deferred.md D5" not in text, \
            "改回「只采集不拦」的降级文案必红"
        assert "绝不因为「没装插件」拦下合格构建" not in \
            text.split("def _run_pytest_gate")[1][:3000], \
            "本函数里不得再出现 fail-open 降级"

    def test_gate_body_has_double_criterion_and_fail_closed_branches(self):
        text = _src("buildlib/coverage_gap.py")
        assert "if not rep[\"passed\"] or not dc_ok:" in text
        assert "fail-closed" in text
        assert "--fail-under" in text and "--diff-file" in text

    def test_missing_xml_fails_closed(self, tmp_path):
        # base 下没有 .coverage.xml → 不判定覆盖率，直接拦
        assert CG.check_incremental_coverage(base=str(tmp_path), emit=False) == 1

    def test_failed_main_criterion_blocks_even_if_diff_cover_passes(self, tmp_path, monkeypatch):
        _mini_xml(str(tmp_path / CG.COVERAGE_XML))
        monkeypatch.setattr(CG, "_git_out", lambda *a, **k: ("", None))

        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        monkeypatch.setattr(CG.subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(CG, "incremental_report", lambda *a, **k: {
            "covered": 0, "total": 10, "pct": 0.0, "min_pct": 70,
            "passed": False, "per_file": [], "no_new_lines": False})
        assert CG.check_incremental_coverage(base=str(tmp_path), emit=False) == 1

    def test_failed_diff_cover_blocks_even_if_main_criterion_passes(self, tmp_path, monkeypatch):
        _mini_xml(str(tmp_path / CG.COVERAGE_XML))
        monkeypatch.setattr(CG, "_git_out", lambda *a, **k: ("", None))

        class R:
            returncode = 1
            stdout = b""
            stderr = b"below threshold"

        monkeypatch.setattr(CG.subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(CG, "incremental_report", lambda *a, **k: {
            "covered": 10, "total": 10, "pct": 100.0, "min_pct": 70,
            "passed": True, "per_file": [], "no_new_lines": False})
        assert CG.check_incremental_coverage(base=str(tmp_path), emit=False) == 1

    def test_happy_path_returns_zero_and_prints_ci_report(self, tmp_path, monkeypatch, capsys):
        _mini_xml(str(tmp_path / CG.COVERAGE_XML))
        monkeypatch.setattr(CG, "_git_out", lambda *a, **k: ("", None))

        class R:
            returncode = 0
            stdout = b"diff-cover ok\n"
            stderr = b""

        monkeypatch.setattr(CG.subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(CG, "incremental_report", lambda *a, **k: {
            "covered": 9, "total": 10, "pct": 90.0, "min_pct": 70,
            "passed": True, "per_file": [("m.py", 9, 10)], "no_new_lines": False})
        assert CG.check_incremental_coverage(base=str(tmp_path), emit=True) == 0
        out = capsys.readouterr().out
        assert "增量覆盖率：9/10 行 = 90.00%" in out
        assert "diff-cover 交叉报告" in out
        assert "✓ 新增代码行覆盖率达标" in out

    def test_missing_diff_cover_fails_closed(self, tmp_path, monkeypatch):
        import importlib.util
        _mini_xml(str(tmp_path / CG.COVERAGE_XML))
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        assert CG.check_incremental_coverage(base=str(tmp_path), emit=False) == 1


class TestDocsAndAcceptance:
    def test_d5_is_closed_not_partial(self):
        text = _src("docs/deferred.md")
        assert "| 状态 | **已闭环**" in text
        assert "**部分落地**（v1.0.755" not in text
        assert text.count("| 复核版本 |") == 10, "D1–D10 复核版本行不能增减"

    def test_testing_contracts_section_5_is_enabled(self):
        text = _src("docs/testing-contracts.md")
        assert "v1.0.756 已启用" in text
        assert "当前构建 venv **未装**" not in text

    def test_gitignore_ignores_coverage_artifacts(self):
        text = _src(".gitignore")
        assert ".coverage.*" in text

    def test_acceptance_gate_write_tool_has_contract_test(self):
        """报告 P1-2 验收：改放行条件时至少一条契约测试变红。"""
        text = _src("tests/contract/test_contract_security_gate.py")
        assert "gate_write_tool" in text
        assert len(text.splitlines()) >= 25
