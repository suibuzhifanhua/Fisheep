# -*- coding: utf-8 -*-
"""tests/contract/test_ratchet_direction.py — 棘轮「只许往下」的**行为锁**（v1.0.755，评审 P1-1）。

背景：以前「基线不许被偷偷抬高」是靠 test_build_guard.py 里
`assert n == GIANT_FUNC_BASELINE` 顺带实现的，代价是**拆掉一个巨型函数后实测
33 != 34 也会红**——项目铁律要求全量 pytest 0 failed 才发版，于是没人敢拆。
本次把两件事分开：
  · 那条断言改成 `<=`，允许「实测减少」；
  · gates.check_giant_functions / check_silent_pass 增加 CEILING 硬校验，拦住抬高基线。

本文件锁的是**行为**，不是源码文案：把每处修复临时改回旧写法，对应用例必须变红。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from buildlib import gates as G  # noqa: E402


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


class TestGiantFuncDirection:
    def test_ceiling_constants_exist_and_hold_current_baseline(self):
        assert hasattr(G, "GIANT_FUNC_BASELINE_CEILING")
        assert G.GIANT_FUNC_BASELINE <= G.GIANT_FUNC_BASELINE_CEILING
        assert hasattr(G, "BARE_EXCEPT_BASELINE_CEILING")
        assert G.BARE_EXCEPT_BASELINE <= G.BARE_EXCEPT_BASELINE_CEILING

    def test_raised_giant_baseline_is_rejected(self, monkeypatch):
        """把基线抬高到上界之上 → 门禁必须拦（返回非 0）。

        改回旧写法（删掉 CEILING 校验）时，这里会走 scan 分支并返回 0 → 用例红。
        """
        monkeypatch.setattr(G, "GIANT_FUNC_BASELINE", G.GIANT_FUNC_BASELINE_CEILING + 5)
        assert G.check_giant_functions() != 0

    def test_decreased_giant_count_is_allowed(self, monkeypatch):
        """实测巨型函数减少（模拟拆完一个）→ 放行，且提示把基线下调。"""
        monkeypatch.setattr(G, "scan_giant_functions", lambda: [])
        assert G.check_giant_functions() == 0

    def test_increased_giant_count_is_rejected(self, monkeypatch):
        """实测超过基线 → 拦。这条锁的是棘轮原本的语义没被改坏。"""
        baseline = G.GIANT_FUNC_BASELINE
        rows = [(G.GIANT_FUNC_MAX_LINES + 1, os.path.join(ROOT, "x.py"), "fake")
                for _ in range(baseline + 1)]
        monkeypatch.setattr(G, "scan_giant_functions", lambda: rows)
        assert G.check_giant_functions() > 0


class TestBareExceptDirection:
    def test_raised_bare_baseline_is_rejected(self, monkeypatch):
        monkeypatch.setattr(G, "BARE_EXCEPT_BASELINE", G.BARE_EXCEPT_BASELINE_CEILING + 5)
        assert G.check_silent_pass() != 0

    def test_baseline_equality_assertions_allow_decrease(self):
        """两条断言必须是 <=（允许减少），不是 ==（焊死基线）。

        这是源码结构锁而非行为锁——它防的是有人把「允许减少」这条口径改回去，
        因为 test_build_guard 里那两条断言只能靠文本核对（行为上 33==34 与
        33<=34 的差别只有拆完函数那一刻才显现，造不出稳定的运行时 fixture）。
        """
        src = _src("tests/contract/test_build_guard.py")
        assert "assert n <= B.GIANT_FUNC_BASELINE" in src
        assert "assert count <= B.BARE_EXCEPT_BASELINE" in src
        assert "assert n == B.GIANT_FUNC_BASELINE" not in src
        assert "assert count == B.BARE_EXCEPT_BASELINE" not in src
