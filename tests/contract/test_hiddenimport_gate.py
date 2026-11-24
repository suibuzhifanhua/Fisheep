# -*- coding: utf-8 -*-
"""tests/contract/test_hiddenimport_gate.py — PyInstaller 缺失模块判定的**行为锁**（v1.0.755）。

本轮打包实测发现的门禁缺陷：`Analyzing hidden import 'core.profile_bundle'` 是
PyInstaller 的**正常分析日志**（模块只靠 hiddenimports 引入时必打），旧判定把它
当成「本包模块缺失」，把合格构建拦下并回滚版本。修好后用本文件锁住：
正常分析日志必须放行，真正的缺失信号必须拦，第三方缺依赖必须忽略。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from buildlib import orchestration as OR  # noqa: E402


def test_normal_analysis_log_is_not_a_missing_module():
    """改回「含 hidden import 就算缺失」→ 这条必红。"""
    assert OR._own_missing_lines(
        "50182 INFO: Analyzing hidden import 'core.profile_bundle'") == []


def test_real_build_log_shape_has_no_false_positive():
    src = "\n".join([
        "50182 INFO: Analyzing hidden import 'core.profile_bundle'",
        "50626 INFO: Analyzing hidden import 'jsonschema.benchmarks.unused_registry'",
        '51531 WARNING: Hidden import "pycparser.lextab" not found!',
        "63129 INFO: checking PYZ",
    ])
    assert OR._own_missing_lines(src) == []


def test_hidden_import_not_found_is_reported():
    line = '51531 WARNING: Hidden import "core.missing_mod" not found!'
    hits = OR._own_missing_lines(line)
    assert hits and "core.missing_mod" in hits[0]


def test_warnfile_missing_module_is_reported():
    """warn-*.txt 的 `missing module named X` 是 v1.0.652 补扫的那条路径。"""
    hits = OR._own_missing_lines("missing module named core.missing_mod")
    assert hits and "core.missing_mod" in hits[0]


def test_all_project_packages_are_covered():
    for pkg in ("services", "core", "desktop", "agent", "agent_tools",
                "routes", "wecom_bridge", "bridge"):
        line = 'WARNING: Hidden import "%s.missing_mod" not found!' % pkg
        assert OR._own_missing_lines(line), pkg


def test_third_party_missing_is_ignored():
    assert OR._own_missing_lines(
        'WARNING: Hidden import "pycparser.lextab" not found!') == []
    assert OR._own_missing_lines("missing module named jsonschema.foo") == []
