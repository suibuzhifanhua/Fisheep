# -*- coding: utf-8 -*-
"""tests/contract/test_contract_security_gate.py — security_ops 写工具闸门**契约锁**（评审 P1-2）。

security_ops.py 是安全中心（>1500 行），此前只有按版本号堆叠的回归断言、
没有同名契约测试。本文件锁的是 gate_write_tool 的**放行条件**——任何一条
「改回坏形状」都必须变红，尤其是最容易被悄悄放松的三条：

  · _ALWAYS_ASK 参数级红线：auto 档也必须问（曾只在 ask_every 档问，
    模型自己传 auto_approve:true 就零询问）；
  · full 档是唯一豁免、background 命中直接拒绝而不是挂起（挂起=静默结束任务）；
  · 一次性令牌消费后放行，且 wecom_run_js 两道关共用同一枚凭据。
"""
import io
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import security_ops as SO  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """审计落盘打桩 + 清空令牌/凭据表，防止用例间串扰造成的假绿。"""
    rec = []
    monkeypatch.setattr(SO, "audit", lambda *a, **k: rec.append(a))
    with SO._lock:
        SO._grants.clear()
        SO._pending.clear()
        SO._JS_APPROVED.clear()
    return rec


def _cfg(level):
    return {"security": {"permission_level": level}}


def _token(name, args):
    return SO.args_hash("perm", name, json.dumps(
        args or {}, ensure_ascii=False, sort_keys=True, default=str))


# ---------- ① 参数级红线（v1.0.614 / v1.0.660 两次审查修复的形状） ----------

def test_alway_ask_table_is_pinned():
    assert set(SO._ALWAYS_ASK) >= {"run_audit_flow", "wecom_run_js", "pc_takeover"}
    assert "auto_approve" in SO._ALWAYS_ASK["run_audit_flow"]
    assert "expression" in SO._ALWAYS_ASK["wecom_run_js"]
    assert "task" in SO._ALWAYS_ASK["pc_takeover"]


@pytest.mark.parametrize("name,args", [
    ("run_audit_flow", {"flow": "monthly", "auto_approve": True}),
    ("wecom_run_js", {"expression": "1+1"}),
    ("pc_takeover", {"task": "整理桌面"}),
])
def test_auto_level_still_asks_for_irreversible_writes(name, args):
    """改回「只在 ask_every 档询问」→ 这三条必红。"""
    r = SO.gate_write_tool(name, args, _cfg("auto"))
    assert isinstance(r, dict) and r.get("__approval_needed__") is True, (name, r)


def test_param_not_hit_is_not_gated_in_auto():
    """同一工具、参数未命中 → auto 档不询问（防止一刀切把正常调用也拦住）。"""
    args = {"flow": "monthly"}
    assert SO._always_ask_hit("run_audit_flow", args) is False
    assert SO.gate_write_tool("run_audit_flow", args, _cfg("auto")) is None


def test_full_level_is_the_only_exemption():
    assert SO.gate_write_tool("wecom_run_js", {"expression": "1+1"}, _cfg("full")) is None
    assert SO.gate_write_tool("wecom_run_js", {"expression": "1+1"}, _cfg("auto")) is not None
    assert SO.gate_write_tool("wecom_run_js", {"expression": "1+1"},
                              _cfg("ask_every")) is not None


def test_read_tools_are_never_asked_even_when_param_hits():
    args = {"expression": "1+1"}
    assert SO.gate_write_tool("wecom_run_js", args, _cfg("auto"), is_read=True) is None
    assert SO.gate_write_tool("wecom_run_js", args, _cfg("ask_every"), is_read=True) is None


def test_never_gate_tools_stay_free_in_ask_every():
    for name in ("finish", "ask_user", "list_skills"):
        assert SO.gate_write_tool(name, {}, _cfg("ask_every")) is None, name


def test_ask_every_still_gates_ordinary_write():
    r = SO.gate_write_tool("pc_files", {"path": "a.txt"}, _cfg("ask_every"))
    assert isinstance(r, dict) and r.get("__approval_needed__") is True


# ---------- ② 后台会话：拒绝而非挂起 ----------

def test_background_denies_instead_of_hanging():
    r = SO.gate_write_tool("wecom_run_js", {"expression": "1+1"},
                           _cfg("auto"), background=True)
    assert isinstance(r, dict)
    assert r.get("ok") is False
    assert not r.get("__approval_needed__"), "后台没有弹窗可点，挂起=静默结束任务"
    assert r.get("error")


def test_background_denial_uses_per_tool_reason():
    r1 = SO.gate_write_tool("wecom_run_js", {"expression": "1+1"},
                            _cfg("auto"), background=True)
    r2 = SO.gate_write_tool("run_audit_flow", {"auto_approve": True},
                            _cfg("auto"), background=True)
    assert r1["error"] != r2["error"], "拒绝文案按工具区分，不能退回单一硬编码串"


# ---------- ③ 一次性令牌 / 两道关共用凭据 ----------

def test_one_shot_token_allows_exactly_once():
    name, args = "wecom_run_js", {"expression": "1+1"}
    with SO._lock:
        SO._grants[_token(name, args)] = time.time() + 600
    assert SO.gate_write_tool(name, args, _cfg("auto")) is None
    # 令牌已被消费：同一调用必须重新询问
    r = SO.gate_write_tool(name, args, _cfg("auto"))
    assert isinstance(r, dict) and r.get("__approval_needed__") is True


def test_js_approval_credential_is_registered_on_allow():
    name, args = "wecom_run_js", {"expression": "1+1"}
    with SO._lock:
        SO._grants[_token(name, args)] = time.time() + 600
    SO.gate_write_tool(name, args, _cfg("auto"))
    assert SO._JS_APPROVED, "闸门放行时必须登记凭据，否则执行器会弹第二遍窗"


# ---------- ④ 源码形态锁：分支不许被顺手删掉 ----------

def test_source_shape_keeps_full_and_background_branches():
    src = io.open(os.path.join(ROOT, "security_ops.py"), encoding="utf-8").read()
    body = src.split("def gate_write_tool(", 1)[1].split("\ndef audit_jsonl(", 1)[0]
    assert 'lv != "full"' in body, "full 档豁免被删"
    assert "if background:" in body, "后台拒绝分支被删"
    assert "bg_deny_result(name, args)" in body
    assert "_always_ask_hit(name, args)" in body
    assert "consume(h)" in body
