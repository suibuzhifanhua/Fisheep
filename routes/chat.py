"""
routes/chat — 对话主入口 / 中断 / 断点继续 / 考试自动续跑（SSE）
====================================================================
从 backend.py 逐字迁入（T05）。SSE 字节契约（R2）与 agent 层不变。

auto_loop（v1.0.248）：任务 payload 带 "auto_loop": true 时启用考试无人值守续跑。
agent 会话以 finish 结束后，解析 final_answer 末行的 RESULT 标记：
  RESULT: FAIL → 自动开一个全新会话继续（新上下文，靠答题台账传递状态，
                 顺带解决重上下文轮撑爆本地模型的问题），最多 MAX 次续跑；
  RESULT: PASS / 无标记 / need_continue / error → 停止，交还用户。

v1.0.312 新增两项请求级能力（对齐 WorkBuddy「智能体团队管理/本地记忆沉淀」）：
  team=true   → 智能体团队流水线（规划→执行→审查，agent/team.py），
                总开关 feature_flags.team（设置→功能管理）
  记忆沉淀    → 会话完成后异步提炼「值得长期记住的事实」（memory_ops.remember_async），
                总开关 feature_flags.memory；在此处挂钩是因为最终答复文本在 SSE 流里
                最易取（token 累积），且对 auto_loop 续跑同样生效。
"""
import os   # v1.0.515 修复（审计 P2-12）：abort_all_tasks 用 os.path/os.remove 但原未导入——NameError 被吞，盘上断点永远清不掉
import json   # v1.0.645：会话渲染日志要把工具入参原样序列化落库
from flask import Blueprint, request, Response, jsonify, stream_with_context

from core.logging import log_event
from core import paths
import time as _time
from agent_tools import _cdp_port
from core.config_store import load_config
# v1.0.660（B7）：只读配置快照（进程内缓存，省掉每次深拷贝整份 config）
from core.config import load_config_view
from agent import (agent_loop, _auto_loop_chain, _sse_stream, build_chat_messages,
                   chat_states, _chat_state_lock, _set_abort, _is_aborted,
                   _abort_lock, _abort_flags, team_pipeline,
                   drop_chat_state, restore_persisted_chat_state)
# v1.0.660（审查修复 A2）：thinking 档位与 team 标记的 pane 侧表（断点持久化用）。
from agent.session import set_pane_extra, get_pane_extra, get_pane_permission
# v1.0.660（B4）：全局权限档解析（预算闸判定「本次实际生效的档位」用）
from security_ops import permission_level
# v1.0.628 修复（会话里 agent 回复全部存成空串的根因）：agent_loop / _plan_mode_gen
# yield 的是现成 SSE 字符串（_sse_stream 只透传不序列化），本文件原本用
# isinstance(ev, dict) 判定事件类型 → 真实流恒不命中：token 累积为空 → 会话落库把
# agent 那轮写成空串（多轮对话再把空 assistant 轮塞回上下文，思考型模型据此可能
# 进入只思考不输出的死循环）、记忆沉淀与技能自进化整轮静默跳过。v1.0.470 修
# team/auto_loop 同款 bug 时已建 _parse_sse_event，这一处漏改（单测喂 dict 假桩所以一直绿）。
from agent.team import _parse_sse_event

bp = Blueprint("chat", __name__, url_prefix="/api")

# v1.0.302：_auto_loop_chain（含 AUTO_LOOP_* 常量）已搬入 agent/loop.py（agent 核心层）——
# 它零 Flask 依赖却被业务层（study_engine，v1.0.768 已移除）反向 import，属层倒置；
# 现 routes 与业务层同向依赖 agent。


def _stat_block(done_event):
    """把 done 事件里的任务收据抽成一条可落库的渲染日志块（v1.0.650 F2）。

    后端自 v1.0.344 就把 rounds/tool_calls/tool_errors/batch_done/elapsed_s/
    prompt_tokens/completion_tokens 随 done 下发，但前端只读了两项、且从不落库——
    退出软件重开，气泡底部的统计条整条消失（v645 复原了步骤卡/推理框/错误行，漏了它）。
    只收数字、且拒收 bool（True 存成 1 会显示"1 次操作"）。
    """
    _s = (done_event or {}).get("summary")
    if not isinstance(_s, dict):
        return None
    _stat = {"k": "stat"}
    for _f in ("rounds", "tool_calls", "tool_errors", "batch_done",
               "elapsed_s", "prompt_tokens", "completion_tokens"):
        _v = _s.get(_f)
        if isinstance(_v, (int, float)) and not isinstance(_v, bool):
            _stat[_f] = _v
    return _stat if len(_stat) > 1 else None


class _StreamCollector:
    """SSE 事件流的累积器（v1.0.677：从 _memory_watch 里拆出）。

    为什么要拆：_memory_watch 原本同时负责"逐事件累积"与"流结束后落库/挂钩"，
    一次功能追加（结构化 final 事件）就把它顶过了 100 行——而 100 行正是本项目
    的巨型函数棘轮线（buildlib/gates.py）。巨型函数是历次"静默失效"的温床，
    宁可多写一个类，也不要让判据藏在一个没人敢读完的函数里。

    职责：吃 SSE 事件 → 攒出「最终答复 / 工具清单 / 渲染日志 blocks / 推理统计」。
    """

    def __init__(self):
        self.buf = ""
        self.final = ""
        self.used_tools = []
        self.blocks = []      # 渲染日志：[{k:tool|err|cont|stopped|reason,...}]，裁剪在存储层
        self._step = 0
        self._reason_len = 0
        self._reason_tail = ""

    def feed(self, d):
        """吃一个已解析的 SSE 事件 dict。"""
        _t = d.get("type")
        if _t == "token":
            if d.get("sys"):
                # v1.0.632（B3）：系统播报（进入收尾/软预算/流式中断重试/judge 介入/
                # 死循环干预…）不是模型说的话。用户点停止或任务超轮次时没有 `【完成】`，
                # 落库会走 `buf[-4000:]` 兜底支——不跳过的话这些方括号提示会被当成
                # AI 回复存进会话，下一轮再作为"模型自己说过的话"回灌上下文。
                return
            self.buf += d.get("text") or ""
            if "【完成】" in self.buf:
                self.final = self.buf.rsplit("【完成】", 1)[1]
        elif _t == "final":
            # v1.0.677（完成标记结构化）：结构化 final 事件是**权威来源**——
            # 原实现只在 token 文本里找「【完成】」字面量，于是：① 模型不走
            # finish 工具直接作答时永远抽不到 → 落库走 buf[-4000:] 兜底支，把
            # 一堆播报当成最终答复；② 模型自己输出「【完成】」字面量会把答案
            # 从该处截断；③ 团队模式 REVISE 补跑产生两段标记，rsplit 取到的
            # 是最后一段（这里是对的），但 scheduler 那份用的是 split（取第一段）
            # ——两份抽取实现口径分叉。现在统一以结构化事件为准。
            self.final = str(d.get("text") or "")
        elif _t == "tool":
            _n = str(d.get("name") or "")
            if _n and not _n.startswith("👥") and _n not in self.used_tools:
                self.used_tools.append(_n)
            self._step += 1
            try:
                # indent=2 是刻意的：前端那张卡的入参一直是 JSON.stringify(args,null,2)，
                # 存档换成紧凑写法，重开时 <pre> 里的缩进就和当时不一样了。
                _args = json.dumps(d.get("args") or {}, ensure_ascii=False, indent=2)
            except Exception:  # noqa: BLE001
                _args = str(d.get("args"))
            self.blocks.append({"k": "tool", "step": self._step, "name": _n, "args": _args})
        elif _t == "tool_result":
            # 贴到最近一张"还没有结果"的卡上——与前端 lastToolEl 同一个语义
            _summary = str(d.get("summary") or "")
            for _b in reversed(self.blocks):
                if _b.get("k") == "tool" and "res" not in _b:
                    _b["res"] = _summary
                    _b["ok"] = not _summary.startswith("失败")
                    if d.get("path"):
                        _b["path"] = str(d.get("path"))
                    break
        elif _t == "reasoning":
            _txt = str(d.get("text") or "")
            self._reason_len += len(_txt)
            self._reason_tail = (self._reason_tail + _txt)[-400:]   # 与实时框一致：只留尾部片段
        elif _t == "error":
            self.blocks.append({"k": "err", "text": str(d.get("text") or "")})
        elif _t == "need_continue":
            self.blocks.append({"k": "cont", "text": str(d.get("text") or "")})
        elif _t == "aborted":
            self.blocks.append({"k": "stopped"})
        elif _t == "done":
            # v1.0.650（F2 + "重开版式要一样"）：统计条落库，见 _stat_block
            _sb = _stat_block(d)
            if _sb:
                self.blocks.append(_sb)

    def finish(self):
        """流结束：把推理统计块补到 blocks 尾部，返回 blocks。"""
        if self._reason_len:
            self.blocks.append({"k": "reason", "n": self._reason_len, "tail": self._reason_tail})
        return self.blocks


def _memory_watch(gen, user_text, cfg, conv_id=None, mode="agent", user_display=None):
    """记忆沉淀包装器：透传 SSE 事件流，累积最终【完成】答复文本，流正常结束后：
    ① 异步提炼记忆（memory_ops.remember_async 内部有开关/去重/限流）；
    ② v1.0.329 技能自进化（skill_evolve.maybe_distill_async）：把可复用工作流
       沉淀为 auto_ 技能（内部有总闸/每日冷却/数量上限/去重四闸；ask 模式跳过）。
    v1.0.315：团队模式 REVISE 补跑后事件流含两段【完成】——取最后一次（rsplit）。
    v1.0.645（用户："退出软件重新点开那个任务，版面也要一样"）：顺手记一份**渲染日志**
       （blocks）随 agent 消息落库。会话原本只存两色文本，步骤卡/推理框/错误/已停止/
       产物文件行当时只活在 DOM 里，重开必然丢。这里能拿到完整事件流，是唯一的采集点。
    v1.0.677：事件累积逻辑拆到 _StreamCollector（本函数不再越过 100 行棘轮线）。
    """
    _col = _StreamCollector()
    for ev in gen:
        yield ev
        _d = _parse_sse_event(ev)
        if _d:
            _col.feed(_d)
    blocks = _col.finish()
    final = _col.final
    used_tools = _col.used_tools
    buf = _col.buf
    if final:
        try:
            from services.memory_ops import remember_async
            remember_async(user_text or "", final, cfg)
        except Exception as e:  # noqa: BLE001
            log_event(f"[memory] 挂钩失败（忽略）：{e}")
        # v1.0.329 技能自进化：ask 只读模式没有真正执行工作流，跳过
        if mode != "ask":
            try:
                from services.skill_evolve import maybe_distill_async
                maybe_distill_async(user_text or "", final, used_tools, cfg)
            except Exception as e:  # noqa: BLE001
                log_event(f"[evolve] 挂钩失败（忽略）：{e}")
    # v1.0.327 会话落库：本轮用户消息 + 最终答复（v1.0.645 起带上渲染日志）
    if conv_id:
        try:
            from services import conversations as _convs
            # display：界面上那行字（含附件引用行）与喂模型的 user_text 不同口径，
            # 存档两个都留——上下文照旧用 user_text，重开时气泡照 display 画。
            _convs.append_message(conv_id, "user", user_text or "", display=user_display)
            _convs.append_message(conv_id, "agent", final or buf[-4000:], blocks=blocks)
        except Exception as e:  # noqa: BLE001
            log_event(f"[conversations] 落库失败（忽略）：{e}")


def _plan_mode_gen(user_text, cfg, pane_id=None, model_id=None):
    """Plan 模式（v1.0.313）：不执行任何工具，单次 LLM 产出「执行方案」流。
    方案末尾附引导：用户确认后回「执行」或点前端按钮，即按方案开工（Agent 模式）。
    v1.0.472 修复（停止无反应 + 长时间白屏）：原实现用 _llm_complete 非流式等待——
    远端模型慢时一次请求挂 100s+，期间零事件输出（界面纯打字态），且点停止只能靠
    abort 标志，本函数从不检查 → 停止无效。改用 stream_llm_events 流式转发：
    ①方案文字逐字出现；②注册进硬停止池（abort_active_responses 可打断）；③
    poll_abort 在流事件间响应 abort 标志。
    v1.0.483 关键修复：**必须 yield "data: {...}\\n\\n" 字符串**——_sse_stream 只做
    透传不做序列化（agent_loop 产出的就是现成 SSE 字符串），本函数曾在 v1.0.472
    改流式时直接 yield dict → Flask 响应迭代 TypeError（expected bytes-like, dict
    found）→ 前端拿到空流，「先出方案」永远没回答。端到端测试已补。"""
    import json as _json
    from llm_client import resolve_model, stream_llm_events, is_stream_interrupt

    def _sse(obj):
        return "data: " + _json.dumps(obj, ensure_ascii=False) + "\n\n"

    model = resolve_model(cfg, model_id)
    if not model:
        yield _sse({"type": "error", "text": "未配置任何模型，无法生成方案。"})
        return
    plan_prompt = (
        "你是任务规划顾问。用户会给你一个任务——不要执行它，产出一份**可确认的执行方案**，格式：\n"
        "## 📋 执行方案\n"
        "1. 目标与产出物（会生成什么文件/改什么/结果在哪）\n"
        "2. 步骤（编号，每步一句话，标注用什么能力：页面操作/本机命令/文件生成/联网检索）\n"
        "3. 需要用户补充的信息（如有；没有写「无需补充」）\n"
        "4. 预计耗时与影响面（会不会动用户数据/页面）\n"
        "最后加一行：**确认后回复「执行」，我将严格按此方案开工。**\n"
        "只输出方案本身。"
    )
    yield _sse({"type": "meta", "model": model.get("name") or model.get("model") or "?",
                "ctx_chars": 0, "rounds": 0, "max_rounds": 1})
    poll = None if pane_id is None else (lambda: _is_aborted(pane_id))
    _t0 = _time.time()
    try:
        for ev in stream_llm_events(model, [{"role": "system", "content": plan_prompt},
                                            {"role": "user", "content": (user_text or "")[:3000]}],
                                     poll_abort=poll):
            if ev[0] == "token":
                yield _sse({"type": "token", "text": ev[1]})
            elif ev[0] == "reasoning":
                # v1.0.481：推理模型（reasoning-35b 等）先思考几分钟再出正文——转发给前端
                # （折叠推理框显示「已接收 N 字」），与 agent 模式一致，思考期不再失明。
                yield _sse({"type": "reasoning", "text": ev[1]})
            elif ev[0] == "error":
                code = ev[2] if len(ev) > 2 else None
                if code == "aborted":
                    return   # 用户中断：前端已显示已停止，不再补发 error 文案
                # v1.0.515 修复（审计 P2-13）：stream_interrupt 是"先报错再内部自动重试、
                # 继续吐后续事件"的契约——原实现收到即终止流，重试尚未发生方案就报失败
                #（agent_loop/_llm_complete 均已修过同款）。continue 消费重试。
                if is_stream_interrupt(code, ev[1] if len(ev) > 1 else ""):   # v1.0.654 A3
                    continue
                yield _sse({"type": "error", "text": f"方案生成失败：{ev[1]}"})
                return
            elif ev[0] == "done":
                break
        # v1.0.479：补发正式 done（chip 才会切「✅ 完成」终态，不再靠前端兜底渲染）
        yield _sse({"type": "done", "summary": {"tool_calls": 0, "elapsed_s": round(_time.time() - _t0, 1)}})
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "text": f"方案生成失败：{e}"})
        return


def _sse_response(gen, pane_id, run_id):
    """v1.0.755（评审 P1-4）：统一 SSE 响应构造——run_id 透传给轨迹包装器，
    同时放进 X-Run-Id 响应头，供前端/排障按一次任务关联整条调用链。"""
    return Response(stream_with_context(_sse_stream(gen, pane_id, run_id)),
                    mimetype="text/event-stream; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "X-Run-Id": run_id})


def _resolve_goal_budget(cfg, request_mr, pane_id=None):
    """v1.0.780（目标驱动执行 · P0）：解析本次目标的轮次预算。
    请求级显式值（快捷任务 5/6 轮约束）优先；没带才应用全局 goal_max_rounds 设置。
    0/缺失/坏值 = 不限 → None（走既有无限续跑模式，对存量行为零影响）。
    返回 int 或 None。抽成函数而非路由内联：单测直接打这份逻辑，不重复实现。"""
    _mr = request_mr
    if _mr in (None, ""):
        try:
            _gm = int(((cfg.get("agent") or {}).get("goal_max_rounds")) or 0)
        except (TypeError, ValueError):
            _gm = 0
        _gm = max(0, min(_gm, 100000))
        if _gm > 0:
            _mr = _gm
            log_event(f"[goal] 目标驱动执行生效：新目标轮次预算={_gm}（pane={pane_id}）")
    else:
        try:
            _mr = int(_mr)
        except (TypeError, ValueError):
            _mr = None
    return _mr if _mr else None


@bp.route("/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True, silent=True) or {}
    from core import run_trace as _rt
    run_id = str(data.get("run_id") or "").strip() or _rt.new_run_id()
    _rt.record("chat_start", run_id=run_id, pane_id=data.get("pane_id"),
               mode=str(data.get("mode") or "agent"),
               msg_count=len(data["messages"]) if isinstance(data.get("messages"), list) else 0)
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        # v1.0.652（审计 P2-13）：类型校验——传非 list 时下游 reversed(messages) TypeError → 500
        return jsonify({"ok": False, "error": "messages 应为数组"}), 400
    pane_id = data.get("pane_id")
    # v1.0.660（B7）：对话入口读配置用只读快照（整条 chat 链路只读不改配置），
    # 省掉每次请求那份整份 config 的深拷贝（含 8KB+ 的技能提示词）。
    cfg = load_config_view()
    port = _cdp_port(cfg)
    _set_abort(pane_id, False)
    # 兜底：清掉可能的全局 abort 残留（旧客户端曾用无 pane_id 的全停），
    # 否则新对话一进入 agent_loop 就会被 _is_aborted 误判为已停止而"不回答"
    _set_abort(None, False)
    # 新对话开始时，清理旧断点（如果存在）——v1.0.405（M5）：内存+盘上文件同步清
    if pane_id:
        drop_chat_state(pane_id)
    pick = data.get("pick")
    # 上下文拼装抽自原 api_chat 内联段（T04：行为等价迁移到 agent/context.py）
    # 注意：build_chat_messages 内部末尾已 msgs += messages 追加用户消息，
    # 此处绝不能再加一次，否则每条用户消息在 LLM 上下文中重复出现两次
    #（T04 重构回归：重构前 api_chat 内联拼装只加一次；context.py 保留追加后
    #  调用点未同步删除，导致所有对话消息翻倍、token 双倍消耗、本地模型上下文提前触顶压缩）。
    # v1.0.327 会话：conv_id 存在时把该会话历史拼进上下文（最近 20 条），实现多轮连续对话
    # v1.0.361 审查修复（S1）：历史拼接移到 build_chat_messages 之后——原实现引用未定义的
    # msgs 必抛 NameError 被吞，多轮历史从未生效，且即便生效也会被 build 整体覆盖。
    conv_id = data.get("conv_id") or ""
    _hist = []
    if conv_id:
        try:
            from services import conversations as _convs
            _hist = _convs.history_messages(conv_id) or []
            # v1.0.660（B8）：会话存档是磁盘 JSON（可被写坏/手改），加载后按字段
            # 校验一遍——history_messages 已做 role 白名单，但没管 content 类型；
            # content 变成 dict/list 时会被拼进 LLM 上下文（拼成字符串反而没事），
            # 而 blocks 里的非 dict 元素会让前端渲染崩。坏条目直接丢弃（fail-closed）。
            _hist = [m for m in _hist
                      if isinstance(m, dict) and isinstance(m.get("content"), str)]
        except Exception as _e:
            log_event(f"[chat] 会话历史加载失败（按无历史继续）：{_e}")

    msgs = build_chat_messages(messages, cfg, port, pick,
                               use_wecom_context=data.get("use_wecom_context"))
    if _hist:
        msgs = [{"role": "system", "content":
                 "[会话历史（以下是本对话此前的交流记录，供延续上下文）]"}] + _hist + msgs
    # max_rounds：任务级轮次预算（如工作台快捷任务的 5 轮约束），None 时用全局 MAX_ROUNDS
    # v1.0.780（目标驱动执行 · P0）：请求**没**带显式轮数预算（非常规快捷任务）时，
    # 应用全局「新目标最大执行轮数」设置（_resolve_goal_budget 统一解析）。
    # 本次生效的预算写 pane 侧表（goal_rounds）——「继续」恢复时从这里取，跨断点
    # 累计续扣（0 也写：清掉上一条目标的残留，防止旧预算泄漏进新目标）。
    _mr = _resolve_goal_budget(cfg, data.get("max_rounds"), pane_id)
    if pane_id:
        set_pane_extra(pane_id, goal_rounds=int(_mr or 0))
    # v1.0.486：**每任务独立模型**——多任务并行时各任务可选不同模型；None=全局默认
    _model_id = data.get("model_id") or None
    # v1.0.590：**每任务独立权限级别**——ask_every 每次询问 / auto 自动审批 / full 完全访问。
    # None=不传，由 agent_loop 回退到「本 pane 记忆 → 全局配置」
    _perm_lv = str(data.get("permission_level") or "").strip().lower() or None
    if _perm_lv not in (None, "ask_every", "auto", "full"):
        _perm_lv = None
    # v1.0.601：**每任务思考档位**——聊天输入行「🧠 思考」下拉带上（off/low/medium/high），
    # 空=跟随模型管理里的配置。非法值一律归 None，绝不拿乱码去改请求体。
    _thk = str(data.get("thinking") or "").strip().lower() or None
    if _thk not in (None, "off", "minimal", "low", "medium", "high"):
        _thk = None
    # 三工作模式（v1.0.313）：agent（直接干，默认）/ plan（先出方案确认再干）/ ask（只问答不碰文件）
    mode = str(data.get("mode") or "agent").lower()

    # v1.0.660（B4）：**全局每日 token 预算闸**。
    # 现状：软预算只看单任务（agent 段那套），用户一天跑几十个任务就能把额度花光，
    # 没有任何全局刹车。这里在请求入口按「当日累计 token」判定：超预算后，
    # 非「完全访问」档一律转「每次询问」——写操作必须逐次确认（复用现有审批机制，
    # 不新增第二套弹窗/令牌）。「完全访问」是用户显式选的"不再问我"，故不动它。
    try:
        from services.usage_stats import daily_total
        from core.config import daily_token_budget
        _daily_budget = daily_token_budget(cfg)
        if _daily_budget > 0:
            _used_today = daily_total()
            if _used_today >= _daily_budget:
                _eff_lv = _perm_lv or get_pane_permission(pane_id) or permission_level(cfg)
                if _eff_lv != "full":
                    _perm_lv = "ask_every"
                    log_event(f"[budget] 当日 token 用量 {_used_today}/{_daily_budget} 超预算，"
                              f"本次请求权限档转「每次询问」（原={_eff_lv}）")
    except Exception as e:  # noqa: BLE001
        # 预算闸读数失败**不得**挡住正常对话（fail-open 于可用性，
        # 审计/日志照常记录即可）
        log_event(f"[budget] 读取当日用量失败（跳过预算闸）：{e}")

    user_text = ""
    for m in reversed(messages):
        if (m.get("role") == "user") and (m.get("content") or "").strip():
            user_text = m["content"]
            break

    if mode == "plan":
        # Plan：不进 agent_loop（零工具执行），单次 LLM 产出可确认的执行方案
        # v1.0.472：流式化（可中断 + 逐字输出），透传 pane_id 供 abort 检查
        gen = _plan_mode_gen(user_text, cfg, pane_id=pane_id, model_id=_model_id)
        # v1.0.327 会话落库：方案也进会话历史（后续「按方案执行」的上下文依赖它）
        if conv_id:
            def _plan_recorder(g, _ut=user_text, _cid=conv_id):
                _buf = ""
                for _ev in g:
                    yield _ev
                    _d = _parse_sse_event(_ev)
                    if _d and _d.get("type") == "token":
                        _buf += _d.get("text") or ""
                try:
                    from services import conversations as _convs
                    _convs.append_message(_cid, "user", _ut)
                    _convs.append_message(_cid, "agent", "📋 执行方案：\n" + _buf[-6000:])
                except Exception as _e:
                    log_event(f"[conversations] plan 落库失败（忽略）：{_e}")
            gen = _plan_recorder(gen)
        return _sse_response(gen, pane_id, run_id)

    gen = agent_loop(msgs, cfg, max_rounds=_mr, pane_id=pane_id, readonly=(mode == "ask"),
                     model_id=_model_id, permission_level=_perm_lv, thinking=_thk)
    # 智能体团队（v1.0.312）：请求带 team=true 且总开关开启 → 规划→执行→审查 流水线
    _team_on = bool(data.get("team")) and bool((cfg.get("feature_flags") or {}).get("team", True))
    if _team_on:
        _ro = (mode == "ask")
        gen = team_pipeline(msgs, user_text, cfg,
                            gen_factory=lambda m, _mr=_mr, _pid=pane_id, _ro=_ro, _pl=_perm_lv, _tk=_thk: agent_loop(m, cfg, max_rounds=_mr, pane_id=_pid, readonly=_ro, model_id=_model_id, permission_level=_pl, thinking=_tk),
                            pane_id=pane_id)
    # v1.0.660（审查修复 A2）：thinking 档位与 team 标记随断点一起持久化。
    # 二者原本只在请求里活一次：断流后「继续」时 thinking 回落到模型默认档（同一条长任务
    # 前后推理强度不一致），team 流水线则退化成单 agent（审查环节消失）。
    # 这里登记到 pane 侧表，agent_loop 保存断点时（agent/session.py::_save_chat_state）
    # 一并写进断点，恢复时再取出。
    set_pane_extra(pane_id, thinking=_thk, team=_team_on)
    # auto_loop（v1.0.248）：考试无人值守——finish 且 RESULT: FAIL 时自动开新会话续跑
    if data.get("auto_loop") and pane_id:
        gen = _auto_loop_chain(gen, pane_id=pane_id, cfg=cfg, port=port,
                               messages=messages, pick=pick,
                               use_wecom_context=data.get("use_wecom_context"))
    # 本地记忆沉淀（v1.0.312）+ 技能自进化（v1.0.329）：流结束后异步提炼。
    # 三开关独立——remember_async/maybe_distill_async 内部各查自己的总闸；
    # v1.0.331 修复：有会话（conv_id）时必须包装，否则 memory/evolve 双关会话落库一起丢。
    # v1.0.660（审查修复 M3）：原条件 `_ff.get("evolve", True) is not False` 在 Python 里
    # 优先级高于 `or`，整句恒为 True（memory 缺省 True → 恒真），用户把 memory 与 evolve
    # 双关后包装仍在执行（会话落库包装 + blocks 采集照跑），与"三开关独立"的承诺不符。
    # 改为直接取布尔值：任一开关开着（或有会话）才包装，两个都显式关掉才真的不包装。
    _ff = cfg.get("feature_flags") or {}
    if conv_id or _ff.get("memory", True) or _ff.get("evolve", True):
        gen = _memory_watch(gen, user_text, cfg, conv_id=conv_id, mode=mode,
                          user_display=str(data.get("ui_text") or "").strip() or None)
    return _sse_response(gen, pane_id, run_id)


def abort_all_tasks():
    """v1.0.373：停止所有进行中的生成并清断点（/chat/abort 无 pane_id 分支与
    全局热键 Ctrl+Alt+S 共用；无 Flask 请求上下文依赖，可被热键线程直调）。
    v1.0.555（P1-11）：全局键改代次广播——原 _set_abort(None, True) 置位的全局键
    会被先收尾的任务清掉（agent_loop 收尾原无条件清 None 键），后台任务逃逸全停。
    代次一旦 +1 对所有已启动任务生效，且任务收尾无权清除。"""
    with _abort_lock:
        pane_ids = list(_abort_flags.keys())
    from agent.session import request_abort_all
    request_abort_all()          # 代次 +1（全局广播，不可被任务收尾清除）
    for p in pane_ids:
        _set_abort(p, True)      # 已在中止中的 pane 补显式键（双保险）
    try:
        from llm_client import abort_active_responses
        abort_active_responses()
    except Exception as e:  # noqa: BLE001
        log_event(f"[abort] 硬停止失败（标志位仍生效）：{e}")
    with _chat_state_lock:
        chat_states.clear()
    # v1.0.405（M5）：全停时盘上断点一并清（遍历断点目录）
    try:
        import glob as _glob
        for _f in _glob.glob(os.path.join(paths.workspace_root(), "断点", "*.json")):
            try:
                os.remove(_f)
            except OSError as e:
                # v1.0.646：全停却留下断点 = 下次「继续」会接着跑一个被停掉的任务
                log_event(f"[abort] 断点文件删除失败（{_f}）：{e}")
    except Exception as e:  # noqa: BLE001
        log_event(f"[abort] 断点目录清理失败（可忽略）：{e}")


@bp.route("/chat/abort", methods=["POST"])
def api_chat_abort():
    data = request.get_json(force=True, silent=True) or {}
    pane_id = data.get("pane_id")
    if pane_id:
        # 仅中断指定 pane，不影响另一面板进行中的生成
        _set_abort(pane_id, True)
        # v1.0.326 硬停止：主动 close 进行中的 LLM 连接——预填充慢时立即中断，
        # 不必等模型吐字（误伤其他 pane 并发生成，单用户场景可接受）
        try:
            from llm_client import abort_active_responses
            abort_active_responses()
        except Exception as e:  # noqa: BLE001
            log_event(f"[abort] 硬停止失败（标志位仍生效）：{e}")
        # 中断时清理对应 pane 的断点，避免用户想继续时拿到旧状态
        # v1.0.405（M5）：内存+盘上文件同步清
        drop_chat_state(pane_id)
    else:
        # 未指定 pane（旧客户端/全停）：中断所有进行中的会话并清理全部断点
        abort_all_tasks()
    return jsonify({"ok": True})


@bp.route("/chat/continue", methods=["POST"])
def api_chat_continue():
    data = request.get_json(force=True, silent=True) or {}
    pane_id = data.get("pane_id")
    from core import run_trace as _rt
    run_id = str(data.get("run_id") or "").strip() or _rt.new_run_id()
    _rt.record("chat_resume", run_id=run_id, pane_id=pane_id)
    # v1.0.405（M5）：内存无断点时先尝试从盘上恢复（重启后「继续」不丢）
    if pane_id and pane_id not in chat_states:
        restore_persisted_chat_state(pane_id)
    if not pane_id or pane_id not in chat_states:
        return jsonify({"ok": False, "error": "没有可继续的对话，或会话已过期。"}), 400
    # v1.0.660（B7）：恢复路径同样只读配置 → 用只读快照
    cfg = load_config_view()
    _set_abort(pane_id, False)
    # 清全局 abort 残留（与 /api/chat 一致的兜底）
    _set_abort(None, False)
    # v1.0.317：按需安装的用户决定随恢复透传（「用户已同意安装技能 X」/「用户拒绝安装」）
    resume_note = str(data.get("resume_note") or "")[:500] or None
    # v1.0.485 安全修复：恢复断点时**还原该会话的 readonly**——原实现 agent_loop 默认
    # readonly=False，只问答会话经 ask_user 挂起再「继续」后写类工具全部解禁
    # （实测只问答模式下 pc_run pip install 真实执行）。合并语义：只收紧不放松——
    # 持久化 readonly=True 时即使前端已切回 agent 模式也保持拦截（要动手请重新发消息）。
    with _chat_state_lock:
        persisted_ro = bool((chat_states.get(pane_id) or {}).get("readonly"))
        # v1.0.486：恢复会话自己的模型（断点持久化）；本次请求未显式指定才用持久值
        persisted_mid = (chat_states.get(pane_id) or {}).get("model_id") or None
        # v1.0.590：权限级别同样随断点恢复（挂起在审批弹窗的任务点「继续」不该掉回默认档）
        persisted_pl = (chat_states.get(pane_id) or {}).get("permission_level") or None
        # v1.0.660（审查修复 A2）：thinking 档位与 team 标记随断点恢复。
        # team：断流前是 team 流水线任务，断流后点「继续」若按单 agent 跑，
        #   规划/执行/审查三段结构就退化成一段（审查环节直接消失）。
        # thinking：档位只在会话开始时选一次，不还原则「继续」后推理强度与断流前不一致。
        persisted_thk = (chat_states.get(pane_id) or {}).get("thinking")
        persisted_team = bool((chat_states.get(pane_id) or {}).get("team"))
        persisted_msgs = list((chat_states.get(pane_id) or {}).get("loop_msgs") or [])
    requested_ro = str(data.get("mode") or "").lower() == "ask"
    readonly_eff = persisted_ro or requested_ro
    model_id_eff = data.get("model_id") or persisted_mid
    requested_pl = str(data.get("permission_level") or "").strip().lower() or None
    if requested_pl not in (None, "ask_every", "auto", "full"):
        requested_pl = None
    # 本次请求显式带了合法档位 → 以用户当下的选择为准（下拉框是用户主动改的）；
    # 没带（旧前端/后台任务）→ 沿用断点里的档位，绝不静默放宽。
    permission_eff = requested_pl or persisted_pl
    # v1.0.601：思考档位由前端每次「继续」重新带上（输入行的下拉就是当下意图）；
    # v1.0.660（A2）：前端没带时沿用断点里的档位——断流后「继续」不该把
    # 同一条长任务的推理强度悄悄降回模型默认档（它不是安全控制，不涉"静默放宽"）。
    _thk_c = str(data.get("thinking") or "").strip().lower() or None
    if _thk_c not in (None, "off", "minimal", "low", "medium", "high"):
        _thk_c = None
    _thk_c = _thk_c or persisted_thk
    # v1.0.660（A2）：把还原后的档位/团队标记写回 pane 侧表——本轮若再次挂起并
    # 保存断点，这两个值要跟着一起落盘（否则二次「继续」又丢）。
    set_pane_extra(pane_id, thinking=_thk_c, team=persisted_team)
    if persisted_ro:
        log_event(f"[chat] 断点恢复保持只问答拦截（pane={pane_id}，model={model_id_eff or '默认'}）")
    # v1.0.780（目标驱动执行 · P0）：从 pane 侧表恢复目标轮次预算——「继续」是
    # **同一目标**的接续，预算跨断点累计续扣（carry_rounds=True，断点已耗轮数
    # 从 state["rounds"] 接着扣）。不恢复的话断点恢复把预算重置为 0
    # （v1.0.129 原语义），「N 轮目标」变成「每段 N 轮」总量可超名义值。
    # 侧表无 goal_rounds（旧断点/从未启用目标预算）→ None=不限，行为同改前。
    _goal_n = int((get_pane_extra(pane_id).get("goal_rounds")) or 0)
    _gen = agent_loop(cfg=cfg, pane_id=pane_id, resume_note=resume_note,
                      max_rounds=(_goal_n if _goal_n > 0 else None),
                      carry_rounds=bool(_goal_n > 0),
                      readonly=readonly_eff, model_id=model_id_eff,
                      permission_level=permission_eff, thinking=_thk_c)
    # v1.0.660（A2）：team 任务断流后「继续」仍按团队流水线跑（规划→执行→审查），
    # 不再退化成单 agent。base_msgs 用断点里的历史消息，user_text 取最后一条用户消息，
    # 让规划阶段仍有上下文；执行阶段 agent_loop 走断点恢复（断点里的上下文优先）。
    if persisted_team and (cfg.get("feature_flags") or {}).get("team", True):
        _utext = ""
        for _m in reversed(persisted_msgs):
            if isinstance(_m, dict) and _m.get("role") == "user" and (_m.get("content") or "").strip():
                _utext = str(_m.get("content"))
                break
        _gen = team_pipeline(persisted_msgs, _utext, cfg,
                             gen_factory=lambda m, _cid=pane_id, _rn=resume_note, _ro=readonly_eff,
                                                _mi=model_id_eff, _pl=permission_eff, _tk=_thk_c,
                                                _gn=(_goal_n if _goal_n > 0 else None),
                                                _gc=bool(_goal_n > 0):
                                 agent_loop(cfg=cfg, pane_id=_cid, resume_note=_rn,
                                            readonly=_ro, model_id=_mi,
                                            permission_level=_pl, thinking=_tk,
                                            max_rounds=_gn, carry_rounds=_gc),
                             pane_id=pane_id)
        log_event(f"[chat] 断点恢复保持 team 流水线（pane={pane_id}）")
    return _sse_response(_gen, pane_id, run_id)