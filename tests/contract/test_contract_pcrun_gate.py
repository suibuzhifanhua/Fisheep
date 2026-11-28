# -*- coding: utf-8 -*-
"""tests/contract/test_contract_pcrun_gate.py — pc_run 闸门档位的行为锁。

评审 P0-1 配套：pc_run 是 shell 通道（cmd 默认），能硬删文件、能起任意进程，
而 auto 档下它此前零询问直执行——配合「一个 ^ 同时击穿三道扫描」的绕过，
企微消息 → 模型上下文 → pc_run 的攻击链无感可达。修复是把它进 _ALWAYS_ASK
（与 pc_takeover 同口径：参数级命中、full 档豁免、后台直接拒绝）。

本锁真跑 gate_write_tool，断言三档行为，改回 auto 直放行必红：
  · auto 档 + command 非空 → __approval_needed__（逐次确认）；
  · full 档 → 豁免放行（既有语义，防修过头）；
  · 后台会话（定时/刷课）→ bg_deny 而非挂起（挂起=静默结束任务）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import security_ops as SO  # noqa: E402


def _cfg(level):
    return {"security": {"permission_level": level}}


class TestPcRunAlwaysAsk:
    def test_registry_hit_is_param_level(self):
        assert SO._always_ask_hit("pc_run", {"command": "dir"}) is True
        assert SO._always_ask_hit("pc_run", {"command": ""}) is False
        assert SO._always_ask_hit("pc_run", None) is False
        # pc_run_argv 不在 _ALWAYS_ASK（shell=False 无展开，风险口径不同）
        assert "pc_run_argv" not in SO._ALWAYS_ASK

    def test_auto_level_prompts_on_every_call(self):
        gate = SO.gate_write_tool("pc_run", {"command": "dir"}, _cfg("auto"),
                                 level="auto")
        assert isinstance(gate, dict) and gate.get("__approval_needed__") is True

    def test_auto_level_empty_command_not_param_hit(self):
        # command 为空 → 参数级不命中，普通写工具口径（auto 档不再新增询问）
        assert SO.gate_write_tool("pc_run", {"command": ""}, _cfg("auto"),
                                 level="auto") is None

    def test_full_level_exempts(self):
        assert SO.gate_write_tool("pc_run", {"command": "dir"}, _cfg("full"),
                                  level="full") is None

    def test_background_denies_instead_of_hanging(self):
        gate = SO.gate_write_tool("pc_run", {"command": "dir"}, _cfg("auto"),
                                  level="auto", background=True)
        assert isinstance(gate, dict)
        assert gate.get("__approval_needed__") is not True
        assert gate.get("ok") is False and gate.get("error")
