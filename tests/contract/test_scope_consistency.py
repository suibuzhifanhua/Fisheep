# -*- coding: utf-8 -*-
"""tests/contract/test_scope_consistency.py — 排除名单单源的**一致性锁**（v1.0.755，评审 P1-3）。

AGENTS.md 第六节要求「根下新增含源码/测试的目录时，必须同步 pytest.ini +
8 处静态扫描排除名单 + .gitignore」，历史上漏一处就出过 10 error / 棘轮假红。
本文件把这个手工同步表变成可验证的契约：

  · 各 profile 组装结果与原手写集合逐字相等（保证本次重构零行为变化）；
  · 所有消费方实际使用的集合 == scope 组装结果（漏同步立刻红）；
  · pytest.ini 的 norecursedirs == scope.NORECURSEDIRS；
  · 往 CORE 加一项，所有 profile 必须自动包含（「改一处、处处生效」）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from buildlib import scope  # noqa: E402
from buildlib import gates, spec_refresh  # noqa: E402

# 原手写集合（从本次重构前的源码逐字抄录，是"零行为变化"的判据）
_EXPECTED = {
    "silent_pass": {"dist", "build", "待发布版本", ".pytest_cache", "产物库",
                    "__pycache__", "docs"},
    "hygiene": {"dist", "build", "待发布版本", ".pytest_cache", "产物库",
                "__pycache__", ".git", "组件", "_dist_backup", "_rollback",
                "_chat_archive"},
    "spec_refresh": {"build", "dist", "待发布版本", "产物库", "__pycache__",
                     ".pytest_cache", ".git", "node_modules", "docs", "tools",
                     "tests", "js", "css"},
    "silent_except": {"__pycache__", "build", "dist", "node_modules", ".git",
                      "docs", "产物库", "tests"},
    "prototype_writes": {"build", "dist", "待发布版本", "tests", "docs",
                         "__pycache__", ".git", "产物库", "js", "archive",
                         "node_modules"},
    "source_walk": {"build", "dist", "待发布版本", "tests", "docs",
                    "__pycache__", ".git", "产物库", "js", "archive"},
    "keep_list": {"build", "dist", "待发布版本", "tests", "docs",
                  "__pycache__", ".git"},
}


def test_profiles_match_original_literals():
    for profile, expected in _EXPECTED.items():
        assert set(scope.skip_dirs(profile)) == expected, profile


def test_unknown_profile_fails_loud():
    try:
        scope.skip_dirs("typo_profile")
    except KeyError:
        return
    raise AssertionError("未知 profile 必须抛错，不能静默回退到 CORE")


def test_all_consumers_use_scope():
    assert set(gates._scope_skip_dirs("silent_pass")) == _EXPECTED["silent_pass"]
    assert set(gates._HYGIENE_SKIP_DIRS) == _EXPECTED["hygiene"]
    assert set(spec_refresh._SPEC_SKIP_DIRS) == _EXPECTED["spec_refresh"]
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import scan_silent_except as sc
    assert set(sc.SKIP_DIRS) == _EXPECTED["silent_except"]


def test_pytest_ini_matches_scope():
    """pytest.ini 的 norecursedirs 显式覆盖默认列表，漏写一项就收进第三方测试。"""
    text = io.open(os.path.join(ROOT, "pytest.ini"), encoding="utf-8").read()
    line = [ln for ln in text.splitlines() if ln.strip().startswith("norecursedirs")]
    assert len(line) == 1
    got = tuple(line[0].split("=", 1)[1].split())
    assert got == scope.NORECURSEDIRS, (got, scope.NORECURSEDIRS)


def test_adding_to_core_propagates_everywhere(monkeypatch):
    """「改一处、处处生效」：CORE 增一项，所有 profile 都自动包含它。"""
    new_core = scope.CORE_SKIP_DIRS | {"__probe_dir__"}
    monkeypatch.setattr(scope, "CORE_SKIP_DIRS", new_core)
    for profile in _EXPECTED:
        assert "__probe_dir__" in scope.skip_dirs(profile), profile


def test_mirror_dirname_is_shared_constant():
    assert scope.MIRROR_DIRNAME in scope.NORECURSEDIRS
    assert scope.MIRROR_DIRNAME in scope.skip_dirs("silent_pass")
    assert scope.MIRROR_DIRNAME in scope.skip_dirs("spec_refresh")
