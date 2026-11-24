# -*- coding: utf-8 -*-
"""v1.0.756（评审 P1-1）行为锁：巨型函数棘轮「真递减」+ 每 N 版递减软目标。

背景：v1.0.755 的棘轮只保证「不涨」（CEILING 硬上界 + 测试断言改成 <=），
并不推动主动变少。本批把两个 >100 行函数里的纯逻辑抽成模块级 helper：
    services/rule_check.py :: check_finance      106 → 94 行（抽 _check_biz_type）
    buildlib/ocr_sync.py   :: sync_ocr_runtime   111 → 92 行（抽 _write_ocr_versions）
实测 34 → 32，把 GIANT_FUNC_BASELINE / CEILING 同步下调到 32，并加
「每 10 版递减 1」软目标（只提醒不拦截，下限 20 保护 agent_loop 这类主循环）。

判据（改回坏形状必红）：
  1. helper 必须是模块级函数且确实被调用，主函数体里不得再内联那段逻辑；
  2. 主函数仍 <=100 行，全仓实测巨型函数 == 32 == BASELINE == CEILING；
  3. 判定口径逐字不变：check_finance 的业务类型项在 5 组样例上结果一致
     （含 v1.0.652「费用类付款（非电子类）」假绿回归样例）；
  4. 软目标时间表可注入验证、下限不可穿透，且门禁里确实有提醒分支；
  5. 基线与上界不得被抬高回 34。
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from buildlib import gates as G          # noqa: E402
from buildlib import ocr_sync            # noqa: E402
from services import rule_check as RC    # noqa: E402


def _src(rel):
    with io.open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _find(src, name):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _span(src, name):
    n = _find(src, name)
    assert n is not None, "找不到函数 %s" % name
    return (n.end_lineno or n.lineno) - n.lineno + 1


def _body(src, name):
    n = _find(src, name)
    assert n is not None, "找不到函数 %s" % name
    lines = src.splitlines()
    return "\n".join(lines[n.lineno - 1:(n.end_lineno or n.lineno)])


def _top_level_funcs(src):
    return {n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}


class TestHelpersAreModuleLevel:
    """把 helper 内联回去 / 删掉 helper —— 结构锁必须红。"""

    def test_check_biz_type_is_module_level_and_called(self):
        src = _src("services/rule_check.py")
        assert "_check_biz_type" in _top_level_funcs(src), \
            "_check_biz_type 必须是模块级函数（不是 check_finance 里的内嵌函数）"
        body = _body(src, "check_finance")
        assert "_check_biz_type(fm)" in body, "check_finance 必须调用抽出的 helper"
        assert "_biz_neg" not in body, "业务类型判定不得内联回 check_finance"

    def test_write_ocr_versions_is_module_level_and_called(self):
        src = _src("buildlib/ocr_sync.py")
        assert "_write_ocr_versions" in _top_level_funcs(src), \
            "_write_ocr_versions 必须是模块级函数（不是内嵌闭包）"
        body = _body(src, "sync_ocr_runtime")
        assert "def _write_versions" not in body, "versions.json 写入不得内联回闭包"
        assert "_write_ocr_versions(ocr_dir)" in body, "必须显式把 ocr_dir 传给 helper"

    def test_helpers_are_callable_not_dead_code(self):
        assert callable(RC._check_biz_type)
        assert callable(ocr_sync._write_ocr_versions)


class TestRatchetBitesAt32:
    def test_main_functions_back_under_100_lines(self):
        assert _span(_src("services/rule_check.py"), "check_finance") <= 100
        assert _span(_src("buildlib/ocr_sync.py"), "sync_ocr_runtime") <= 100

    def test_measured_count_is_32_and_matches_baseline(self):
        n = len(G.scan_giant_functions())
        assert n == 32, "实测巨型函数 %d 个，基线已下调到 32" % n
        assert G.GIANT_FUNC_BASELINE == 32
        assert G.GIANT_FUNC_BASELINE_CEILING == 32

    def test_baseline_cannot_be_raised_back_to_34(self):
        assert G.GIANT_FUNC_BASELINE_CEILING <= 32
        assert G.GIANT_FUNC_BASELINE <= G.GIANT_FUNC_BASELINE_CEILING

    def test_two_new_giant_functions_block_the_build(self, monkeypatch):
        """模拟把 helper 内联回去（实测回到 34）：门禁必须返回非零。"""
        # scan_giant_functions() 元素是 (行数, 路径, 函数名)，sorted(rows) 依赖首列可比较
        fake = list(G.scan_giant_functions()) + [
            (101, "x.py", "inlined_back_1"), (101, "y.py", "inlined_back_2")]
        monkeypatch.setattr(G, "scan_giant_functions", lambda files=None: fake)
        assert G.check_giant_functions() != 0


class TestJudgementUnchanged:
    """拆分只是搬代码：业务类型判定口径（含假绿回归）逐字不变。"""

    @staticmethod
    def _biz(data):
        r = RC.check_finance(data)
        return next(c for c in r["checks"] if c["item"] == "业务类型")

    def test_flat_dict_electronic_is_green(self):
        c = self._biz({"业务类型": "费用类付款（电子）"})
        assert c["pass"] is True
        assert c["expect"] == "费用类付款（电子）"
        assert c["actual"] == "费用类付款（电子）"

    def test_flat_dict_non_electronic_is_red(self):
        """v1.0.652 审计 P0-5：旧子串匹配会把「非电子类」判成假绿。"""
        c = self._biz({"业务类型": "费用类付款（非电子类）"})
        assert c["pass"] is False
        assert "非电子" in c["actual"] or c["actual"] == "（未取到）"

    def test_fields_array_electronic_is_green(self):
        data = {"fields": [{"key": "业务类型", "count": 1,
                            "snippets": ["业务类型：费用类付款（电子）"]}]}
        c = self._biz(data)
        assert c["pass"] is True
        assert c["actual"] == "费用类付款（电子）"

    def test_fields_array_non_electronic_is_red(self):
        data = {"fields": [{"key": "业务类型", "count": 1,
                            "snippets": ["业务类型：费用类付款（非电子类）"]}]}
        c = self._biz(data)
        assert c["pass"] is False

    def test_missing_field_is_red_with_note(self):
        c = self._biz({})
        assert c["pass"] is False
        assert "未读到该字段" in c["note"]

    def test_helper_result_equals_inlined_result(self):
        for data in ({"业务类型": "费用类付款（电子）"},
                     {"业务类型": "费用类付款（非电子类）"},
                     {"fields": [{"key": "业务类型", "count": 1,
                                  "snippets": ["业务类型：费用类付款（电子）"]}]},
                     {}):
            assert RC._check_biz_type(RC._field_map(data)) == self._biz(data)


class TestSoftTargetSchedule:
    """「每 10 版递减 1」只提醒不拦截，但时间表本身被锁住。"""

    def test_constants_are_sane(self):
        assert G.GIANT_FUNC_DECREMENT_EVERY_VERSIONS >= 1
        assert G.GIANT_FUNC_BASELINE_SET_AT_PATCH == 756
        assert 0 < G.GIANT_FUNC_SOFT_FLOOR <= G.GIANT_FUNC_BASELINE

    def test_schedule(self):
        assert G.giant_func_soft_target(756) == 32
        assert G.giant_func_soft_target(765) == 32
        assert G.giant_func_soft_target(766) == 31
        assert G.giant_func_soft_target(776) == 30
        assert G.giant_func_soft_target(956) == G.GIANT_FUNC_SOFT_FLOOR
        assert G.giant_func_soft_target(5000) == G.GIANT_FUNC_SOFT_FLOOR

    def test_current_patch_is_readable_int(self):
        assert isinstance(G._current_patch(), int)

    def test_gate_contains_soft_reminder_branch(self):
        body = _body(_src("buildlib/gates.py"), "check_giant_functions")
        assert "giant_func_soft_target" in body, "软目标不得从门禁里删掉"
        assert "软提醒" in body
