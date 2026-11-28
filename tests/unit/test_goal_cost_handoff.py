# -*- coding: utf-8 -*-
"""v1.0.781 目标驱动执行第二批：目标级成本归属（P0-1）+ 预算渐进预警（P0-2）
+ 阻塞移交（P1）。

  · P0-1：agent_loop 任务生命周期内把目标标签（前 80 字）+ pane 绑进
    usage_stats contextvar——record_usage 自动附带 goal/pane 字段；
    结束后 finally 恢复，不串味到下一任务。聚合走 goal_summary / /api/usage/goal，
    老记录（无 goal 字段）归「未归属」桶不丢数据。
  · P0-2：软预算（token/分钟任一）与显式轮次预算（目标预算/快捷任务）各在
    消耗到 80% 时给一次性预警——触顶强制收尾前先让用户知道资源将尽。
  · P1：judge 判「blocked」不再直接交卷收尾——移交说明写进上下文 + 存断点
    （finish 占位补配对，审批挂起同款机制）+ need_continue stop_reason=blocked；
    「继续」从断点续推。断点 blocked_handoffs 计数自限：移交满 3 次后接受
    交卷、以「阻塞」收尾（不死锁、不无限 ping-pong）。
"""
import json
from datetime import date


# ============================================================ P0-1：目标级成本归属


def test_usage_goal_contextvar_attaches_and_clears(tmp_path, monkeypatch):
    """contextvar 有值 → 记录带 goal/pane；reset 后 → 不带；标签截 80 字。"""
    import services.usage_stats as U
    monkeypatch.setattr(U, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(U, "_cleanup_done", True)

    tok = U.set_usage_goal("给企微发 10 条消息", "paneX")
    U.record_usage("m1", {"prompt_tokens": 10, "completion_tokens": 5})
    U.reset_usage_goal(tok)
    U.record_usage("m1", {"prompt_tokens": 3, "completion_tokens": 1})

    p = tmp_path / f"usage_{date.today().strftime('%Y%m')}.jsonl"
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert recs[0]["goal"] == "给企微发 10 条消息" and recs[0]["pane"] == "paneX"
    assert "goal" not in recs[1] and "pane" not in recs[1], \
        "reset 之后记录不得再带归属字段"

    tok2 = U.set_usage_goal("x" * 300)   # 超长标签截断
    U.record_usage("m1", {"prompt_tokens": 1, "completion_tokens": 1})
    U.reset_usage_goal(tok2)
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs[2]["goal"]) == 80, "目标标签必须截 80 字"
    assert "pane" not in recs[2], "只传 goal 不传 pane 时不得写空 pane 字段"


def test_agent_loop_wrapper_binds_goal_context(monkeypatch):
    """公共入口包装器：首个 set 来自 messages 第一条 user 消息，finally 必 reset。"""
    import agent.loop as loop
    calls = []
    monkeypatch.setattr(loop, "set_usage_goal",
                        lambda g, p=None: calls.append(("set", g, p)))
    monkeypatch.setattr(loop, "reset_usage_goal", lambda t: calls.append(("reset",)))
    monkeypatch.setattr(loop, "stream_llm_events", _fake_finish_stream())
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}]}
    list(loop.agent_loop(messages=[{"role": "user", "content": "给企微发 10 条消息"}],
                         cfg=cfg, pane_id="pane-gctx", max_rounds=5, notify=False))
    assert calls and calls[0] == ("set", "给企微发 10 条消息", "pane-gctx"), \
        f"首个 set 必须绑定 messages 里的目标标签 + pane，实际 {calls[:2]}"
    assert calls[-1] == ("reset",), f"任务结束必须 reset（finally），实际 {calls[-1]}"


def test_resume_path_sets_goal_from_core(monkeypatch):
    """续跑路径不传 messages——core 建好 loop_msgs 后按 _goal_text 再 set 一次。"""
    import agent.session as S
    import agent.loop as loop
    calls = []
    monkeypatch.setattr(loop, "set_usage_goal",
                        lambda g, p=None: calls.append(("set", g, p)))
    monkeypatch.setattr(loop, "reset_usage_goal", lambda t: calls.append(("reset",)))
    monkeypatch.setattr(loop, "stream_llm_events", _fake_finish_stream())
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    S.chat_states["pane-gctx-resume"] = {
        "loop_msgs": [{"role": "user", "content": "完成目标：给企微发 10 条消息"},
                      {"role": "assistant", "content": "已发 2 条，继续…"}],
        "rounds": 2, "readonly": False, "model_id": None,
    }
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}]}
    list(loop.agent_loop(cfg=cfg, pane_id="pane-gctx-resume", max_rounds=5,
                         carry_rounds=True, notify=False))
    labels = [c for c in calls if c[0] == "set"]
    assert any(c[1] == "完成目标：给企微发 10 条消息" for c in labels), \
        f"core 必须按断点里的 _goal_text 重新 set 归属，实际 {labels}"


def test_goal_summary_groups(tmp_path, monkeypatch):
    """goal_summary：按 goal 聚合、panes 汇总、老记录归「未归属」桶。"""
    import services.usage_stats as U
    monkeypatch.setattr(U, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(U, "_cleanup_done", True)
    today = date.today().strftime("%Y-%m-%d")
    lines = [
        {"date": today, "model": "m", "prompt_tokens": 100, "completion_tokens": 50,
         "total_tokens": 150, "kind": "chat", "goal": "给企微发 10 条消息", "pane": "p1"},
        {"date": today, "model": "m", "prompt_tokens": 10, "completion_tokens": 5,
         "total_tokens": 15, "kind": "chat", "goal": "给企微发 10 条消息", "pane": "p2"},
        {"date": today, "model": "m", "prompt_tokens": 7, "completion_tokens": 3,
         "total_tokens": 10, "kind": "chat"},
    ]
    with open(tmp_path / f"usage_{date.today().strftime('%Y%m')}.jsonl",
              "w", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    s = U.goal_summary(30)
    goals = {g["goal"]: g for g in s["goals"]}
    assert goals["给企微发 10 条消息"]["total"] == 165
    assert goals["给企微发 10 条消息"]["requests"] == 2
    assert goals["给企微发 10 条消息"]["panes"] == ["p1", "p2"]
    assert goals["未归属"]["total"] == 10, "无 goal 字段的老记录必须归「未归属」桶"
    assert s["totals"]["requests"] == 3 and s["totals"]["total"] == 175
    assert s["goals"][0]["goal"] == "给企微发 10 条消息", "按 total 降序"


def test_usage_goal_endpoint(app_client):
    """/api/usage/goal 端点可用（读真实月档，返回结构稳定）。"""
    client, _cfg = app_client
    d = client.get("/api/usage/goal").get_json()
    assert "goals" in d and "totals" in d and "days" in d
    assert isinstance(d["goals"], list)


# ============================================================ P0-2：预算渐进预警


def _fake_burn_stream(usage_per_round=90):
    """假 LLM 流：每轮报固定 usage + 调一次无害未知工具（dispatch 返回
    「unknown tool」结构化错误，循环继续不收尾）——把 _usage_sum / 轮次计数推到预警阈值。
    工具名必须带 read_ 前缀：未知工具若判写类，LoopGuard 错循环第 10 次会
    kill 整个任务（预算测试还没跑完就被熔断）；读类报错走 CDP 抖动口径
    （计数复位，恒 ok）。"""
    def fake(model, messages, tools=None, poll_abort=None, **kw):
        yield ("token", "推进中…")
        yield ("done", {
            "usage": {"prompt_tokens": usage_per_round, "completion_tokens": 5},
            "tool_calls": [{"id": "fc_nop", "name": "read_probe_tool", "args": {}}],
        })
    return fake


def _parse(evs):
    return [json.loads(e.split("data: ", 1)[1].strip())
            for e in evs if e.startswith("data: ")]


def test_soft_budget_80_percent_warns_once(monkeypatch):
    """无限模式软预算：token 到 80% 一次性预警（且先于触顶强制收尾提示）。"""
    import agent.loop as loop
    monkeypatch.setattr(loop, "stream_llm_events", _fake_burn_stream(usage_per_round=90))
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}],
           "agent": {"soft_budget_tokens": 100, "soft_budget_minutes": 0}}
    evs = list(loop.agent_loop(messages=[{"role": "user", "content": "跑个长任务"}],
                              cfg=cfg, pane_id="pane-w80", notify=False))
    parsed = _parse(evs)
    warns = [m for m in parsed if "资源预算已消耗约 80%" in str(m.get("text", ""))]
    assert len(warns) == 1, f"80% 预警必须恰好一次（一次性 flag），实际 {len(warns)}"
    tails = [m for m in parsed if "已达到任务资源预算" in str(m.get("text", ""))]
    assert tails, "超过 100% 后必须进入既有强制收尾路径"
    warn_idx = parsed.index(warns[0])
    assert warn_idx < parsed.index(tails[0]), "80% 预警必须先于触顶收尾提示出现"
    needs = [m for m in parsed if m.get("type") == "need_continue"]
    assert needs and needs[0].get("stop_reason") == "budget"


def test_goal_rounds_80_percent_warns_once(monkeypatch):
    """显式轮次预算（目标预算语义）：到 80% 轮数一次性预警，触顶后仍按原路径收尾。"""
    import agent.loop as loop
    monkeypatch.setattr(loop, "stream_llm_events", _fake_burn_stream(usage_per_round=0))
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}]}
    evs = list(loop.agent_loop(messages=[{"role": "user", "content": "20 轮目标"}],
                              cfg=cfg, pane_id="pane-r80", max_rounds=10,
                              carry_rounds=True, notify=False))
    parsed = _parse(evs)
    warns = [m for m in parsed if "轮次预算已消耗约 80%" in str(m.get("text", ""))]
    assert len(warns) == 1, f"轮数 80% 预警必须恰好一次，实际 {len(warns)}"
    tails = [m for m in parsed if "已达到主思考轮次上限" in str(m.get("text", ""))]
    assert tails, "10 轮触顶后必须进入既有自动收尾路径"
    assert parsed.index(warns[0]) < parsed.index(tails[0]), "80% 预警必须先于触顶提示"
    needs = [m for m in parsed if m.get("type") == "need_continue"]
    assert needs and needs[0].get("stop_reason") == "budget"


# ============================================================ P1：阻塞移交


def _judge_blocked_fake(model, msgs, disable_thinking=None, poll_abort=None, deadline=None, **kw):
    return '{"verdict": "blocked", "reason": "企微未登录，无权限发送"}'


def _run_blocked_once(monkeypatch, pane, messages=None, carry=False):
    import agent.session as S
    import agent.loop as loop
    import agent.loop_ops as ops
    monkeypatch.setattr(loop, "stream_llm_events", _fake_finish_stream())
    monkeypatch.setattr(loop, "save_chat_log", lambda *a, **k: None)
    monkeypatch.setattr(loop, "judge_model",
                        lambda cfg: {"id": "j1", "name": "judge"})
    monkeypatch.setattr(ops, "_llm_complete", _judge_blocked_fake)
    cfg = {"models": [{"id": "m1", "name": "m1", "enabled": True, "is_default": True}]}
    kw = dict(cfg=cfg, pane_id=pane, max_rounds=5, carry_rounds=carry, notify=False)
    if messages is not None:
        kw["messages"] = messages
    parsed = _parse(list(loop.agent_loop(**kw)))
    return parsed, S.chat_states.get(pane)


def test_blocked_handoff_three_times_then_accept(monkeypatch):
    """judge 持续判 blocked：前 3 段移交（need_continue stop_reason=blocked + 断点
    blocked_handoffs 递增），第 4 段接受交卷 final stop_reason=blocked。"""
    pane = "pane-bh-1"
    # 第 1 段（新对话）
    p1, st1 = _run_blocked_once(monkeypatch, pane,
                               messages=[{"role": "user", "content": "完成目标：给企微发 10 条消息"}])
    needs = [m for m in p1 if m.get("type") == "need_continue"]
    assert needs and needs[0].get("stop_reason") == "blocked", \
        f"移交必须发 need_continue stop_reason=blocked，实际 {needs}"
    assert not [m for m in p1 if m.get("type") == "final"], "移交段不得交卷"
    assert st1 and st1.get("blocked_handoffs") == 1, "断点必须记 blocked_handoffs=1"
    msgs1 = st1["loop_msgs"]
    # 移交说明必须进上下文（恢复时模型可见）——且位于 tool 占位配对**之后**
    notice_i = next(i for i, m in enumerate(msgs1)
                    if m.get("role") == "user" and "judge 判定目标受阻" in str(m.get("content", "")))
    tool_i = max(i for i, m in enumerate(msgs1) if m.get("role") == "tool")
    asst_i = max(i for i, m in enumerate(msgs1)
                 if m.get("role") == "assistant" and m.get("tool_calls"))
    assert asst_i < tool_i < notice_i, \
        "顺序必须 assistant(tool_calls) → tool 占位 → 移交说明（严格后端配对要求）"
    assert "阻碍因素" in str(msgs1[notice_i]["content"])

    # 第 2、3 段（续跑）：计数递增，仍移交
    for expect_n in (2, 3):
        p, st = _run_blocked_once(monkeypatch, pane, messages=None)
        needs = [m for m in p if m.get("type") == "need_continue"]
        assert needs and needs[0].get("stop_reason") == "blocked"
        assert st and st.get("blocked_handoffs") == expect_n, \
            f"第 {expect_n} 次移交后断点计数必须 {expect_n}，实际 {st and st.get('blocked_handoffs')}"

    # 第 4 段：移交满 3 次 → 接受交卷，final stop_reason=blocked
    p4, st4 = _run_blocked_once(monkeypatch, pane, messages=None)
    finals = [m for m in p4 if m.get("type") == "final"]
    assert finals and finals[0].get("stop_reason") == "blocked", \
        f"移交上限后必须接受交卷以「阻塞」收尾，实际 {finals}"
    assert not [m for m in p4 if m.get("type") == "need_continue"], "收尾段不得再发移交"
    done = [m for m in p4 if m.get("type") == "done"]
    assert done, "交卷路径必须带 done 事件（_task_summary 出口）"
    # 交卷后断点不应残留移交计数语义（任务已收束）——只断言任务结束
    assert st4 is None or "blocked_handoffs" not in (st4 or {}), \
        "交卷收尾后残留断点不应继续携带移交计数（若断点残留也应被后续 save 覆盖）"


def _fake_finish_stream():
    """假 LLM 流：第一轮即调 finish 交卷（质量校验通过：final_answer 非空、无幻觉文件）。"""
    def fake(model, messages, tools=None, poll_abort=None, **kw):
        yield ("token", "推进中…")
        yield ("done", {"tool_calls": [
            {"id": "fc_1", "name": "finish",
             "args": {"final_answer": "目标已完成：10 条全部发出。"}}
        ]})
    return fake
