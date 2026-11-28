# -*- coding: utf-8 -*-
"""v1.0.780 目标驱动执行：P0 设置链路 + P1 目标复核 + 轮数跨断点累计。

四停止条件（截图语义）：目标完成 completed / 阻塞 blocked / 用量受限 budget /
手动暂停 user_stopped。本文件按真实分支打桩（假 LLM 流 / 假 judge 响应），
不做源码字面量断言。

  · P0：设置 goal_max_rounds（0=不限）→ personal 白名单读写 → /api/chat 预算解析
    （请求级显式值优先）→ pane 侧表 → /chat/continue 跨断点累计续扣。
  · P1：finish 交卷时 judge 复核 done/blocked/not_done——保守放行（judge 挂
    了不绑架任务）、每任务 2 次额度（成功后计数）；stop_reason 结构化上报。
"""
import json

# ============================================================ P0：配置链


def test_goal_max_rounds_default_unlimited():
    from core import config_store
    assert config_store.AGENT_SOFT_BUDGET_DEFAULTS.get("goal_max_rounds") == 0, \
        "默认必须 0（不限）——对存量行为零影响"


def test_personal_budget_roundtrip_goal_max_rounds(app_client):
    """personal 预算白名单端到端：GET 下发生效值 → POST 保存 → agent 读同一份数字。"""
    client, _cfg = app_client
    d = client.get("/api/personal/config").get_json()
    assert d["ok"] and d["budget"].get("goal_max_rounds") == 0, \
        "GET 必须下发 goal_max_rounds（生效值 0=不限）"
    d = client.post("/api/personal/config",
                   json={"budget": {"goal_max_rounds": 50}}).get_json()
    assert d["ok"]
    import core.config_store as cs
    cs._CFG_CACHE["cfg"] = None
    cfg = cs.load_config()
    assert cfg["agent"]["goal_max_rounds"] == 50, \
        "保存值必须落进 agent 读取的 cfg[agent] 段（白名单循环 AGENT_SOFT_BUDGET_DEFAULTS）"


def test_resolve_goal_budget_semantics():
    """预算解析：请求级显式值（快捷任务）> 全局设置；0/缺失/坏值 = 不限(None)。"""
    from routes.chat import _resolve_goal_budget
    assert _resolve_goal_budget({"agent": {"goal_max_rounds": 50}}, None) == 50
    assert _resolve_goal_budget({"agent": {"goal_max_rounds": 50}}, 6) == 6, \
        "快捷任务显式 6 轮优先，不得被全局设置改写"
    assert _resolve_goal_budget({"agent": {"goal_max_rounds": 0}}, None) is None
    assert _resolve_goal_budget({"agent": {}}, None) is None
    assert _resolve_goal_budget({}, None) is None
    assert _resolve_goal_budget({"agent": {"goal_max_rounds": "abc"}}, None) is None, \
        "坏值回落不限，不得崩溃"
    assert _resolve_goal_budget({"agent": {"goal_max_rounds": 10 ** 9}}, None) == 100000, \
        "超钳位上限回落 100000"
    assert _resolve_goal_budget({"agent": {"goal_max_rounds": 0}}, "abc") is None, \
        "请求级坏值同样回落不限"


# ============================================================ P1：目标复核（judge 层）


def _judge_fake(reply):
    def fake(model, msgs, disable_thinking=None, poll_abort=None, deadline=None, **kw):
        return reply
    return fake


def test_goal_verify_verdicts(monkeypatch):
    import agent.loop_ops as ops
    state = {"n": 0}
    monkeypatch.setattr(ops, "_llm_complete",
                        _judge_fake('{"verdict": "done", "reason": "全部完成"}'))
    v, d = ops._judge_goal_verify({"id": "j"}, [{"role": "user", "content": "x"}],
                                  "做完了", "给企微发 10 条消息", "paneX", state)
    assert v == "pass" and state["n"] == 1, "done → 放行且计 1 次额度"

    state2 = {"n": 0}
    monkeypatch.setattr(ops, "_llm_complete",
                        _judge_fake('{"verdict": "blocked", "reason": "缺权限"}'))
    v, d = ops._judge_goal_verify({"id": "j"}, [], "做完了", "给企微发 10 条消息", None, state2)
    assert v == "blocked" and "缺权限" in d, "blocked → 放行交卷但终止原因记阻塞"

    state3 = {"n": 0}
    monkeypatch.setattr(ops, "_llm_complete",
                        _judge_fake('{"verdict": "not_done", "reason": "只发了一半"}'))
    v, d = ops._judge_goal_verify({"id": "j"}, [], "做完了", "给企微发 10 条消息", None, state3)
    assert v == "reject" and "只发了一半" in d, "not_done → 打回继续推进"

    # 不适用路径：judge 未配置 / 无目标文本 → 放行且不烧额度
    v, _ = ops._judge_goal_verify(None, [], "做完了", "给企微发 10 条消息", None, {"n": 0})
    assert v == "pass"
    st = {"n": 0}
    v, _ = ops._judge_goal_verify({"id": "j"}, [], "做完了", "", None, st)
    assert v == "pass" and st["n"] == 0

    # 额度用尽（2 次）→ 放行且不再调 judge（防 judge 恒 not_done 时死锁在复核上）
    st2 = {"n": 2}
    monkeypatch.setattr(ops, "_llm_complete",
                        _judge_fake('{"verdict": "not_done", "reason": "x"}'))
    v, d = ops._judge_goal_verify({"id": "j"}, [], "做完了", "给企微发 10 条消息", None, st2)
    assert v == "pass" and "额度" in d and st2["n"] == 2

    # 响应不可解析 → 保守放行（但已拿到响应，计额度）
    st3 = {"n": 0}
    monkeypatch.setattr(ops, "_llm_complete", _judge_fake("口头回复，没有 JSON"))
    v, _ = ops._judge_goal_verify({"id": "j"}, [], "做完了", "给企微发 10 条消息", None, st3)
    assert v == "pass" and st3["n"] == 1

    # 无响应 → 放行且不烧额度（v1.0.361 O4 口径：失败不扣配额）
    st4 = {"n": 0}
    monkeypatch.setattr(ops, "_llm_complete", _judge_fake(None))
    v, _ = ops._judge_goal_verify({"id": "j"}, [], "做完了", "给企微发 10 条消息", None, st4)
    assert v == "pass" and st4["n"] == 0


def test_goal_verify_judge_exception_passes(monkeypatch):
    """judge 调用异常 → 保守放行（任务不得被 judge 层绑架）。"""
    import agent.loop_ops as ops

    def boom(*a, **k):
        raise RuntimeError("judge down")

    monkeypatch.setattr(ops, "_llm_complete", boom)
    v, d = ops._judge_goal_verify({"id": "j"}, [], "做完了", "给企微发 10 条消息", None, {"n": 0})
    assert v == "pass" and "异常" in d


# ============================================================ P1：主循环集成（carry + stop_reason）


def _fake_finish_stream():
    """假 LLM 流：第一轮即调 finish 交卷（真实 dispatch_tool 是纯函数 lambda）。"""
    def fake(model, messages, tools=None, poll_abort=None, **kw):
        yield ("token", "推进中…")
        yield ("done", {"tool_calls": [
            {"id": "fc_1", "name": "finish", "args": {"final_answer": "目标已完成：10 条全部发出。"}}
        ]})
    return fake


def _run_goal_loop(monkeypatch, pane, carry):
    import agent.session as S
    import agent.loop as loop
    # stream_llm_events 是 from llm_client import 进 loop 命名空间的——必须打
    # loop 自己的绑定（打 llm_client 只会改源模块，loop 里那份引用纹丝不动）。
    monkeypatch.setattr(loop, "stream_llm_events", _fake_finish_stream())
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    S.chat_states[pane] = {
        "loop_msgs": [{"role": "user", "content": "完成目标：给企微发 10 条消息"},
                      {"role": "assistant", "content": "已发 2 条，继续…"}],
        "rounds": 2, "readonly": False, "model_id": None,
    }
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}]}
    evs = list(loop.agent_loop(cfg=cfg, pane_id=pane, max_rounds=5,
                               carry_rounds=carry, notify=False))
    parsed = [json.loads(e.split("data: ", 1)[1].strip())
              for e in evs if e.startswith("data: ")]
    metas = [m for m in parsed if m.get("type") == "meta"]
    finals = [m for m in parsed if m.get("type") == "final"]
    return metas, finals, parsed


def test_carry_rounds_continues_budget_from_breakpoint(monkeypatch):
    """轮次预算跨断点累计：断点已耗 2 轮 → 恢复后首轮 round_no=2+1=3，meta rounds=3。"""
    metas, finals, _ = _run_goal_loop(monkeypatch, "pane-goal-carry", carry=True)
    per_round = [m for m in metas if m.get("rounds", 0) > 0]
    assert per_round and per_round[0]["rounds"] == 3, \
        f"carry 后首轮应按已耗 2 轮续扣（round_no=3），实际 {per_round}"
    assert finals and finals[0].get("stop_reason") == "completed", \
        "finish 正常交卷 → final 事件 stop_reason=completed"


def test_no_carry_resets_budget_as_before(monkeypatch):
    """对照组：carry=False（快捷任务旧语义）预算从 0 重置，首轮 round_no=1。"""
    metas, finals, _ = _run_goal_loop(monkeypatch, "pane-goal-nocarry", carry=False)
    per_round = [m for m in metas if m.get("rounds", 0) > 0]
    assert per_round and per_round[0]["rounds"] == 1, \
        "非目标路径必须保持 v1.0.129 原语义（恢复重置预算）"


def test_carry_without_breakpoint_is_noop(monkeypatch):
    """无断点的新对话：carry_rounds 无作用（current_round 恒 0）。"""
    import agent.session as S
    import agent.loop as loop
    monkeypatch.setattr(loop, "stream_llm_events", _fake_finish_stream())
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}]}
    evs = list(loop.agent_loop(messages=[{"role": "user", "content": "给企微发 10 条消息"}],
                               cfg=cfg, pane_id="pane-goal-fresh", max_rounds=5,
                               carry_rounds=True, notify=False))
    parsed = [json.loads(e.split("data: ", 1)[1].strip())
              for e in evs if e.startswith("data: ")]
    per_round = [m for m in parsed if m.get("type") == "meta" and m.get("rounds", 0) > 0]
    assert per_round and per_round[0]["rounds"] == 1
