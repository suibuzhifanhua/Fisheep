# -*- coding: utf-8 -*-
"""tests/contract/test_contract_executors.py — agent_tools.executors 工具注册表**契约锁**（评审 P1-2）。

executors.py（>1100 行）是 TOOLS schema → 执行函数的唯一路由表。历史风险：
新增工具只写 schema 忘了接 handler（模型调了就得到 unknown tool，静默失效），
或 handler 返回 list 而非 dict（agent_loop 对 out 调 .get() 抛 AttributeError，
SSE 断流、前端 network error）。本文件把「注册表完备性」变成可执行契约。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from agent_tools.registry import TOOLS  # noqa: E402
from agent_tools import executors as E  # noqa: E402

# 这两个工具由 agent/loop.py 直接拦截处理（挂起等待用户安装/导入技能），
# 不经过 run_tool——是**显式豁免**，不是漏接。新增豁免必须在这里登记，
# 否则下面的完备性断言会红（防止「加个工具忘接 handler」被悄悄放过）。
LOOP_HANDLED_TOOLS = frozenset({"request_skill", "import_skill_source"})


def _schema_names():
    return [t["function"]["name"] for t in TOOLS]


def test_schema_names_are_unique():
    names = _schema_names()
    assert len(names) == len(set(names)), "TOOLS 里出现重名 schema"


def test_every_handler_has_a_schema():
    extra = set(E._TOOL_HANDLERS) - set(_schema_names())
    assert not extra, "有孤儿 handler（模型永远调不到）：%s" % sorted(extra)


def test_every_schema_has_handler_or_explicit_exemption():
    missing = set(_schema_names()) - set(E._TOOL_HANDLERS)
    assert missing == set(LOOP_HANDLED_TOOLS), (
        "新增工具未接 handler，或豁免清单与现状不一致：%s" % sorted(missing))


def test_pc_run_argv_is_wired_in_both_registry_and_handlers():
    """本轮新增的参数数组双通道：schema 与 handler 必须成对存在。"""
    assert "pc_run_argv" in set(_schema_names())
    assert "pc_run_argv" in E._TOOL_HANDLERS
    assert "pc_run" in E._TOOL_HANDLERS, "shell 兼容通道不得被误删"


def test_run_tool_unknown_returns_structured_error():
    r = E.run_tool("no_such_tool_xyz", {}, {})
    assert isinstance(r, dict) and r.get("ok") is False
    assert "unknown tool" in r.get("error", ""), "未知工具必须结构化兜底，不能抛异常断流"


def test_run_tool_wraps_non_dict_result(monkeypatch):
    monkeypatch.setattr(E, "_validate_tool_args", lambda *_a, **_k: None)
    monkeypatch.setitem(E._TOOL_HANDLERS, "_contract_probe", lambda a, c: [1, 2])
    r = E.run_tool("_contract_probe", {}, {})
    assert r == {"ok": True, "result": [1, 2]}


def test_source_shape_keeps_fallbacks():
    src = io.open(os.path.join(ROOT, "agent_tools", "executors.py"),
                  encoding="utf-8").read()
    body = src.split("def run_tool(", 1)[1]
    assert 'f"unknown tool {name}"' in body
    assert 'return {"ok": True, "result": out}' in body
