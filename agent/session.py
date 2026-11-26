"""agent.session — 会话状态：abort 标志 / 轮次与熔断常量 / 压缩阈值 / 断点存储。
逐字自 backend.py 迁出（T04）。
v1.0.405（M5）：断点持久化到 产物库/断点/<pane_id>.json——长任务跑几小时后重启
软件，「继续」不再直接 400 无断点；内存 chat_states 与盘上文件统一经
_save_chat_state / drop_chat_state / restore_persisted_chat_state 三个入口管理。"""

import json
import os
import re
import threading
import time

from core.logging import log_event
from core.config import atomic_write_json  # v1.0.510（P2-13）
from core.config import schema_ok, valid_or_default  # v1.0.660（B8）：加载期字段校验
from core import paths  # noqa: F401（路径函数属性查找，可被测试 patch）

_abort_lock = threading.Lock()
_abort_flags = {}
# v1.0.555（P1-11）：全局全停代次。原设计用 _abort_flags[None] 作全局键，
# 但 agent_loop 收尾无条件 _set_abort(None, False)——用户按"停止所有"后，
# 先收尾的 UI 任务会把全局键清掉，仍在跑的后班任务（pane 键未被
# abort_all 置位——只置位了当时已在中止中的 pane）只依赖全局键判定，
# 于是逃逸"全停"继续烧 token/操作页面。
# 代次语义：abort_all 时代次 +1；任务启动时快照代次，当前代次 > 快照 =
# 该任务被全停命中。任务收尾不清代次（天然免疫逃逸）；之后启动的新任务
# 快照新代次，不受旧全停影响（原设计要靠"新对话清全局键"兜底，语义更脆）。
_abort_gen = {"n": 0}


# v1.0.632（B7）：后台/无人值守会话的判定口径收敛到这一处。
# 此前 `("sched-", "study-")` 这个元组在 5 个地方各抄一遍（agent/loop.py 权限降级
# 与写工具闸门、agent/session.py 断点配额三条），而且抄成了两种顺序——
# 加一种后台会话（如批审引擎、宏重放）要改 5 处，漏一处的表现是：
#   · 漏在权限降级 → 后台任务在第一个写操作上永久挂住等一个没人点的弹窗；
#   · 漏在断点配额 → 后台断点把用户「继续」的断点挤掉。
# 两类都要跑很久才暴露，正是最不该靠"记得改全"的口径。
# 现有一种后台会话：services/scheduler.py 的 `sched-<id>`（全仓唯一 pane_id 生成点）。
# （v1.0.768 学习模块移除，原 `study-engine-<ts>` 一类随之消失。）再加第二种时改这一个元组即可。
BACKGROUND_PANE_PREFIXES = ("sched-",)


def is_background_pane(pane_id):
    """没有人在屏幕前点弹窗的会话 ⇒ True（pane_id 为空也算：直调同样没有弹窗）。"""
    if not pane_id:
        return True
    return str(pane_id).startswith(BACKGROUND_PANE_PREFIXES)


def abort_generation():
    """读取当前全停代次（任务启动时快照）。"""
    with _abort_lock:
        return _abort_gen["n"]


def request_abort_all():
    """v1.0.555（P1-11）：请求全停——代次 +1，对所有已启动任务生效。"""
    with _abort_lock:
        _abort_gen["n"] += 1
        _abort_flags.pop(None, None)   # 旧全局键废止，统一走代次判定


def globally_aborted_since(start_gen):
    """start_gen 之后是否发生过全停请求。"""
    with _abort_lock:
        return _abort_gen["n"] > start_gen


# ---------- v1.0.659：任务生命周期直采（「任务在跑」的精确判据） ----------
# agent_loop 生成器在飞 = 任务在跑（LLM 思考中/工具执行间隙全覆盖）。
# 背景（2026-11-07 10:31 绑定实测）：旧判据 llm_active_count()>0 把**维护类**
# LLM 调用（repair 自检体检/kb 消化/每日小结）也当「agent 任务」——开机自检
# 的 90s 体检调用在飞期间，用户点「重启并绑定」被误拒 3 次（"有任务正在运行"），
# 实际唯一在跑的只是体检。任务边界 = agent_loop 生成器生命周期：聊天 SSE、
# 跑课、批审、team 链、auto 续跑全走它；维护调用直连 llm_client 不经它，天然不计。
_task_gen_lock = threading.Lock()
_task_gen_live = {"n": 0}
# 最近一次任务活动时间戳（time.monotonic）：链切换/收尾间隙的 30s 宽限由此兜底
_last_task_active = [0.0]


def task_gen_enter():
    with _task_gen_lock:
        _task_gen_live["n"] += 1
        _last_task_active[0] = time.monotonic()


def task_gen_exit():
    with _task_gen_lock:
        _task_gen_live["n"] = max(0, _task_gen_live["n"] - 1)
        _last_task_active[0] = time.monotonic()


def agent_task_running():
    """有 agent_loop 生成器在飞 = 有任务正在跑（不含维护类 LLM 调用）。"""
    with _task_gen_lock:
        return _task_gen_live["n"] > 0


def track_task_gen(gen):
    """把任意 agent_loop 生成器包进任务计数：进（首次 next）/出（结束或 close）配对。
    消费方不迭代则永不计数（语义正确：没跑就不是任务）。"""
    task_gen_enter()
    try:
        yield from gen
    finally:
        task_gen_exit()


def _set_abort(pane_id, val):
    """设置/清除某个 pane 的中断标志。pane_id 为 None 时作为全局兜底键。"""
    with _abort_lock:
        if val:
            _abort_flags[pane_id] = True
        else:
            _abort_flags.pop(pane_id, None)


def _is_aborted(pane_id):
    """检查某个 pane 是否被请求中断。pane_id 为 None 时也兜底检查全局键。"""
    with _abort_lock:
        if pane_id is not None and _abort_flags.get(pane_id):
            return True
        return _abort_flags.get(None, False)

# 主思考轮次参考值：常规对话已改为「无限续跑」——max_rounds=None 时不设轮次上限，
# 上下文超 CONTEXT_COMPRESS_THRESHOLD 自动压缩后继续跑，不再 100 轮触顶中断；
# 该常量仅作为默认展示参考，以及显式传入 max_rounds 的任务级约束（如快捷任务 5 轮）的分母。
MAX_ROUNDS = 100
# 达到显式任务级轮次上限后的自动收尾续跑轮数：不再干等用户手动点「继续」，
# 自动追加收尾引导，让模型总结/收尾；收尾段用尽仍未完成才降级为 need_continue。
# （仅对显式传入 max_rounds 的约束型任务生效，无限续跑模式不涉及。）
TAIL_FINISH_ROUNDS = 10

# 死循环熔断阈值：写操作（点击/输入/滚动等）连续执行相同「工具名+参数」的警告/终止次数。
# P1-3 修复（财务审核误杀）：判定改在工具执行后，工具返回 error（CDP 抖动/页面未就绪等）
# 视为合理重试直接重置计数；仅当连续执行【成功】的同一写操作才累计。
# LOOP_WARN_AT=4：连续 4 次成功重复写操作时插入干预消息给模型一次重新决策机会；
# LOOP_KILL_AT=8：干预后仍连续重复到 8 次（即警告后最多再容忍 4 次）才判定死循环终止，
# 给财务审核等场景的偶发页面无响应留足重试空间。
LOOP_WARN_AT = 4
LOOP_KILL_AT = 8
# 读操作死循环熔断阈值：读类工具（read_wecom_page/read_wecom_fields/list_interactive_elements 等）
# 连续执行相同「工具名+参数」的警告/终止次数。写操作在页面上的实际行动会重置读计数，
# 因此「点下一题→读→点下一题→读」的逐题推进不会被误伤；而模型反复读同一元素/同一页
# （如答题时某选项反复读取无果）会累计触发干预，避免一路空转到轮次上限。
LOOP_READ_WARN_AT = 5
LOOP_READ_KILL_AT = 9
# LOOP_ERR_WARN_AT=6：同一写操作连续报错 6 次时插入干预消息；LOOP_ERR_KILL_AT=10：干预后仍持续报错到 10 次则终止。
# 与成功写操作计数独立：偶发 error 重试（1~3 次后成功/换操作）不会被误杀，持续 error 死循环会被拦截。
LOOP_ERR_WARN_AT = 6
LOOP_ERR_KILL_AT = 10
# P2 导航白名单（v1.0.247）：「上一题/下一题」连续点击是合法翻题（改答案回退时要连点数次「上一题」），
# 2026-09-01 17:24 实测：连点 4 次「上一题」（合法导航）即达 LOOP_WARN_AT=4 触发干预 + judge
# （本地模型 judge 一轮要数分钟，干预损失大于循环本身）。导航型点击目标用独立更高阈值，
# 其它写操作仍按 LOOP_WARN_AT/LOOP_KILL_AT。
LOOP_NAV_WARN_AT = 10
LOOP_NAV_KILL_AT = 20

# 上下文压缩阈值（字符）：loop_msgs 总字符数超过该值时压缩中间工具往返历史，
# 避免本地模型（LM Studio）上下文被截断而丢失原始任务指令。
# v1.0.287：80000 → 40000。实证（2026-09-05/06）：SharedLLM/vLLM 共享服务在 ~104K
# 字符（80000×reasoning1.3）上下文反复空转 0 字符至 900s 熔断（一夜 3 次），而引擎
# 跑课会话（<50K 字符）一夜 32 门零熔断——空转与上下文字符量强相关。压到 40000
# （reasoning ×1.3 = 52000）让稳态上下文落在实证安全区；压缩机制本身成熟
# （leading 6 + 摘要 + recent 20 + 大 tool 结果截断），代价只是更频繁的中间历史折叠。
# 有 max_input_tokens 的本地模型仍按 min(80000, mi×2) 放宽，不受影响。
CONTEXT_COMPRESS_THRESHOLD = 40000
# 压缩时保留最前面的原始指令（system/user 消息）条数
KEEP_LEADING_MSGS = 6
# 压缩时保留最近 N 条消息原文（保证 assistant tool_calls 与 tool 结果对应关系完整）
# v1.0.290：20 → 8。实证（2026-09-07 示例云平台考试假卡）：考试每答一题产生 4-6 条消息
# （LLM 请求 + 工具调用 + read_wecom_page 大快照 + LLM 请求），recent=20 时保留最近
# 3-4 题完整往返（含每题 2-5KB 页面快照），压缩后上下文仍顶在 52K，LLM 在压缩后的
# 大上下文上「只思考不输出工具调用」→ 假卡 3 分钟。recent=8 只保留最近约 2 题，
# 已答题（每题相互独立、对后续无价值）全部折叠为摘要，稳态上下文降到 ~20-30K，
# 落在实证零假卡安全区。答题/长流程受益最大；短对话（<8 条）不受影响。
KEEP_RECENT_MSGS = 8

# 被轮次上限中断的对话上下文，key=前端 paneId
chat_states = {}
# chat_states 在 Flask 多线程下被读写，统一加锁保护
_chat_state_lock = threading.Lock()
# chat_states 容量上限：超出时淘汰最旧断点（近似 LRU），避免多 pane 长期累积内存
CHAT_STATES_MAX = 10

# ---------- v1.0.660（B8）：断点文件 schema ----------
# 断点是用户可编辑的 JSON（也能被别的进程写坏）。没有 schema 时坏结构会直接透传：
# loop_msgs 变成字符串、rounds 变成 "3" → 下游把字符串当消息列表用，
# 症状是"点了继续没反应"或 500，且日志里看不出是数据坏了。
# 这里声明最小必要字段：缺字段用安全默认值兜底（fail-closed，不抛崩）。
_CHECKPOINT_SCHEMA = {
    "type": "object",
    "required": ["loop_msgs"],
    "properties": {
        "pane_id": {"type": "string"},
        "rounds": {"type": "integer"},
        "loop_msgs": {"type": "array", "items": {"type": "object"}},
        "readonly": {"type": "boolean"},
        "model_id": {"type": ["string", "null"]},
        "permission_level": {"type": "string"},
        "thinking": {"type": ["string", "null"]},
        "team": {"type": "boolean"},
        "ts": {"type": "number"},
    },
}
_CHECKPOINT_DEFAULTS = {
    "rounds": 0, "readonly": False, "model_id": None,
    "permission_level": "auto", "thinking": None, "team": False,
}


def _normalize_loop_msgs(msgs):
    """把断点里的 tool_calls 参数统一成 OpenAI 线上格式的 "arguments"（v1.0.660 B8）。

    症状：agent/loop.py 的 _collect_task_calls 长期同时兼容 "args" 与 "arguments"
    两个键——这正是"断点格式无 schema"的直接证据：写进去的键由各处随手决定，
    读的时候只能两个都试。旧断点里存的是 "args"（dict），新的是
    "arguments"（JSON 字符串），下游按哪种解析全看调用点，宏重放因此长期
    静默取到空参数。

    这里在**加载期**统一成一种：arguments 一律是 JSON 字符串（OpenAI 线上格式），
    旧的 dict 形态按 args 键取回并序列化。加载期归一 → 下游只需认一种形态。
    """
    out = []
    for m in (msgs or []):
        if not isinstance(m, dict):
            continue
        m = dict(m)
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            fixed = []
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                tc = dict(tc)
                fn = tc.get("function")
                if isinstance(fn, dict):
                    fn = dict(fn)
                    if fn.get("arguments") is None and fn.get("args") is not None:
                        a = fn.get("args")
                        fn["arguments"] = a if isinstance(a, str) else json.dumps(
                            a if isinstance(a, dict) else {}, ensure_ascii=False)
                    fn.pop("args", None)
                    tc["function"] = fn
                fixed.append(tc)
            m["tool_calls"] = fixed
        out.append(m)
    return out


def _bp_path(pane_id):
    """断点文件路径：pane_id 白名单化（防路径穿越），中英文/数字/下划线保留。"""
    safe = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "_", str(pane_id))[:80]
    return os.path.join(paths.workspace_root(), "断点", (safe or "pane") + ".json")


# ---------------- 按会话（pane）记忆的权限级别（v1.0.590） ----------------
# 与「每任务独立模型」同构：agent_loop 入口写一次，断点保存/恢复时随 pane 取用。
# 之所以做成侧表而不是给 _save_chat_state 加参数——它在 loop.py 里有 15 处调用点，
# 逐个加参会把一次功能改动放大成一次高风险大搬运（本项目已因此漏过 import）。
_PANE_PERM = {}
_perm_lock = threading.Lock()


def set_pane_permission(pane_id, level):
    if not pane_id:
        return
    with _perm_lock:
        if level:
            _PANE_PERM[str(pane_id)] = str(level)


def get_pane_permission(pane_id):
    if not pane_id:
        return None
    with _perm_lock:
        return _PANE_PERM.get(str(pane_id))


def drop_pane_permission(pane_id):
    """断点清理时同步清掉，避免长期运行累积。"""
    if not pane_id:
        return
    with _perm_lock:
        _PANE_PERM.pop(str(pane_id), None)


# ---------------- 会话级附加标记（thinking 档位 / team 标记） ----------------
# v1.0.660（审查修复 A2）：断点原本只记 readonly/model_id/permission_level，
# 于是「继续」恢复时 thinking 档位回到模型默认、team 流水线退化成单 agent。
# 与 _PANE_PERM 同一套侧表模式（_save_chat_state 在 loop.py 里有 15 处调用点，
# 逐个加参是高风险大搬运），由 agent_loop / routes 侧写入、断点保存时读取。
_PANE_EXTRA = {}
_extra_lock = threading.Lock()


def set_pane_extra(pane_id, **fields):
    """记录会话级附加标记（只写入给定键，不影响同会话其它键）。

    v1.0.660（A2）：None 也是有效值（thinking=None＝跟随模型默认档），故不过滤 None——
    否则上一条任务残留的 thinking 会留在侧表里，被下一条任务的断点写进去。
    """
    if not pane_id:
        return
    with _extra_lock:
        rec = _PANE_EXTRA.setdefault(str(pane_id), {})
        for k, v in fields.items():
            rec[k] = v


def get_pane_extra(pane_id):
    """取会话级附加标记（副本，调用方改它不影响侧表）。"""
    if not pane_id:
        return {}
    with _extra_lock:
        return dict(_PANE_EXTRA.get(str(pane_id)) or {})


def drop_pane_extra(pane_id):
    """断点清理时同步清掉，避免长期运行累积。"""
    if not pane_id:
        return
    with _extra_lock:
        _PANE_EXTRA.pop(str(pane_id), None)


# ---------------- 会话级"记住该命令前缀"免审批表（v1.0.660 功能 B2） ----------------
# 现状：审批令牌是一次性 args hash，同一条良性命令每轮都要再问一次
# （比如每次跑 `git status` 都弹窗确认，用户被迫反复点"批准"）。
# 本表让用户能对**当前会话**记住某个命令前缀，之后同前缀的命令直接放行。
#
# 关键约束：
#   · **只在本会话内有效**（按 pane_id 存），换个会话/重启即失效
#   · **只在内存里**（不落盘）——命令前缀本身也算敏感信息，落盘就等于留痕
#   · **有过期时间**——默认 30 分钟，避免"记住一次"变成永久授权
#   · 只对**命中前缀的具体命令**放行，不放宽其它工具/其它参数
_SESSION_ALLOW = {}      # pane_id -> {rule_id: {"prefix","tool","mode","expire","created"}}
_allow_lock = threading.Lock()
_ALLOW_TTL = 30 * 60     # 默认 30 分钟
_ALLOW_MAX = 32          # 单会话最多记 32 条（防无界增长）


def add_session_allow(pane_id, prefix, tool=None, ttl=None, mode="prefix"):
    """给当前会话加一条"命令免审批"规则，返回规则 id。

    prefix：命令文本前缀（mode=prefix 时按**开头**匹配，大小写不敏感；
            mode=regex 时按正则匹配，非法正则会被拒绝）
            按开头而非子串：子串口径下放行 `git commit` 会连带放行
            `del /f /s /q C:\\... && git commit`——一条良性规则授权了任意灾难命令。
    tool  ：限定工具名（None = 任何工具都算）
    ttl   ：有效期秒数（默认 30 分钟）
    """
    if not pane_id or not str(prefix or "").strip():
        return ""
    mode = "regex" if str(mode or "prefix").lower() == "regex" else "prefix"
    if mode == "regex":
        try:
            re.compile(str(prefix))
        except re.error:
            return ""      # 非法正则 → 不登记（调用方据此报错）
    now = time.time()
    with _allow_lock:
        rules = _SESSION_ALLOW.setdefault(str(pane_id), {})
        # 同前缀同工具 → 续期（不新增一条）
        for rid, r in rules.items():
            if r["prefix"] == prefix and r["tool"] == (tool or None) and r["mode"] == mode:
                r["expire"] = now + (ttl or _ALLOW_TTL)
                return rid
        rid = "sa-%d-%d" % (int(now * 1000) % 100000000, len(rules))
        rules[rid] = {"prefix": str(prefix), "tool": (tool or None), "mode": mode,
                      "expire": now + (ttl or _ALLOW_TTL), "created": now}
        # 超量：丢最早创建的
        while len(rules) > _ALLOW_MAX:
            oldest = min(rules, key=lambda k: rules[k]["created"])
            rules.pop(oldest, None)
        return rid


def list_session_allow(pane_id):
    """列出当前会话的免审批规则（过期的不返回）。"""
    if not pane_id:
        return []
    now = time.time()
    with _allow_lock:
        rules = _SESSION_ALLOW.get(str(pane_id)) or {}
        return [{"id": rid, "prefix": r["prefix"], "tool": r["tool"], "mode": r["mode"],
                 "expire": r["expire"]}
                for rid, r in rules.items() if r["expire"] > now]


def clear_session_allow(pane_id, rule_id=None):
    """清掉当前会话的免审批规则（rule_id 为空 = 全清）。返回清掉几条。"""
    if not pane_id:
        return 0
    with _allow_lock:
        rules = _SESSION_ALLOW.get(str(pane_id))
        if not rules:
            return 0
        if rule_id:
            n = 1 if rules.pop(rule_id, None) is not None else 0
        else:
            n = len(rules)
            rules.clear()
        if not rules:
            _SESSION_ALLOW.pop(str(pane_id), None)
        return n


def session_allows(pane_id, tool_name, args_text):
    """判断当前会话是否已记住该命令 → 命中则无需再问。

    args_text：命令文本（由调用方从工具参数里取）。
    """
    if not pane_id or not args_text:
        return False
    now = time.time()
    text = str(args_text)
    low = text.lower()
    with _allow_lock:
        rules = _SESSION_ALLOW.get(str(pane_id))
        if not rules:
            return False
        for rid in [k for k, v in rules.items() if v["expire"] <= now]:
            rules.pop(rid, None)      # 顺手清掉过期规则
        for r in rules.values():
            if r["tool"] and r["tool"] != tool_name:
                continue
            if r["mode"] == "regex":
                try:
                    if re.search(r["prefix"], text):
                        return True
                except re.error:
                    continue
            elif low.startswith(r["prefix"].lower()):
                return True
    return False


def _save_chat_state(pane_id, loop_msgs, rounds, readonly=False, model_id=None):
    """保存对话断点（供「继续」恢复）。容量超 CHAT_STATES_MAX 时淘汰最旧断点。
    v1.0.302：配额只算 UI pane 断点——淘汰条件从「总数超上限」改为「非后台前缀
    key 数超上限」，后台会话（一任务一个、跑完自行 pop）不计入配额，
    防长任务静默挤掉用户「继续」上下文。
    v1.0.361 审查修复（O1）：已存在的 key 先 pop 再重插（touch）——CPython dict
    对已存在 key 赋值不改变插入序，原实现「近似 LRU」实为 FIFO：反复「继续」的
    活跃 pane 保持最初位置被当最旧淘汰，反而长期不动的 pane 靠后存活。
    v1.0.405（M5）：同步落盘（产物库/断点/）——重启后 restore_persisted_chat_state
    可恢复。保存频率=轮次上限/挂起/正常收尾等终止路径，非每轮，同步写盘可接受。
    v1.0.485：**readonly 一并持久化**——只问答会话经 ask_user/request_skill 挂起再
    「继续」后，原实现 readonly 丢失（agent_loop 默认 False）→ 写类工具全部解禁，
    实测只问答模式下 pc_run（pip install）真实执行。"""
    if not pane_id:
        return
    with _chat_state_lock:
        # v1.0.361 审查修复（O1）：已存在的 key 先 pop 再重插（touch）——CPython dict
        # 对已存在 key 赋值不改变插入序，原实现「近似 LRU」实为 FIFO：反复「继续」的
        # 活跃 pane 保持最初位置被当最旧淘汰，反而长期不动的 pane 靠后存活。
        if pane_id in chat_states:
            chat_states.pop(pane_id, None)
        # v1.0.660（审查修复 A2/L12）：thinking 档位与 team 标记随断点一起持久化。
        # thinking：档位只在会话开始时选一次，中断后「继续」若回落到模型默认，
        #   同一条长任务的推理强度前后不一致（思考型模型尤其明显）。
        # team：标记丢了则 team 流水线任务「继续」后按单 agent 跑，审查环节消失。
        # ts：断点年龄戳——v1.0.361 只补了 touch，淘汰仍按 dict 插入序（FIFO），
        #   这里补上 ts 后按「最旧保存时间」淘汰，才真正是近似 LRU。
        _extra = get_pane_extra(pane_id)
        chat_states[pane_id] = {"loop_msgs": list(loop_msgs), "rounds": rounds,
                                 "readonly": bool(readonly), "model_id": model_id or None,
                                 "permission_level": get_pane_permission(pane_id) or "auto",
                                 "thinking": _extra.get("thinking"),
                                 "team": bool(_extra.get("team")),
                                 "ts": time.time()}
        while sum(1 for k in chat_states if not is_background_pane(k)) > CHAT_STATES_MAX:
            # v1.0.660（审查修复 L12）：按 ts 最旧淘汰，而不是按 dict 插入序。
            victim = min((k for k in chat_states if not is_background_pane(k)),
                         key=lambda k: chat_states[k].get("ts") or 0, default=None)
            if victim is None:
                break  # 无 UI key 可淘汰
            chat_states.pop(victim)
        # v1.0.515 修复（审计 P2-18）：豁免前缀（sched-）自身也设上限——
        # 原实现完全不计配额，每条断点含全量 loop_msgs（内存+盘上双份），长期多任务
        # 运行无界增长。豁免配额 20 条（定时任务一任务一个，20 足够），
        # 淘汰最旧的豁免 key。
        _exempt = [k for k in chat_states if is_background_pane(k)]
        while len(_exempt) > 20:
            # v1.0.660（审查修复 L12）：同样按 ts 最旧淘汰（豁免段原按插入序 FIFO）。
            victim = min(_exempt, key=lambda k: chat_states[k].get("ts") or 0)
            _exempt.remove(victim)
            chat_states.pop(victim, None)
            log_event(f"[session] 豁免断点超上限(20)，淘汰最旧：{victim}")
    _bp_write(pane_id, loop_msgs, rounds, readonly, model_id)


def _bp_write(pane_id, loop_msgs, rounds, readonly=False, model_id=None):
    """断点落盘（原子写）。失败不致命——内存断点仍在，仅重启丢失。"""
    try:
        os.makedirs(os.path.dirname(_bp_path(pane_id)), exist_ok=True)
        _extra = get_pane_extra(pane_id)
        # v1.0.510（P2-13）：tmp+os.replace 样板收敛到 core.config.atomic_write_json
        atomic_write_json(_bp_path(pane_id),
                          {"pane_id": pane_id, "rounds": int(rounds or 0),
                           "loop_msgs": list(loop_msgs), "readonly": bool(readonly),
                           "model_id": model_id or None,
                           "permission_level": get_pane_permission(pane_id) or "auto",
                           # v1.0.660（审查修复 A2）：thinking/team 一并落盘，
                           # 重启后「继续」仍能还原当时的档位与团队流水线。
                           "thinking": _extra.get("thinking"),
                           "team": bool(_extra.get("team")),
                           "ts": time.time()},
                          indent=None)
    except Exception as e:  # noqa: BLE001
        log_event(f"[session] 断点落盘失败（不影响本次会话）：{e}")


def drop_chat_state(pane_id):
    """清理断点（内存 + 盘上文件）。sse_stream 断连清理 / chat.py 中止与新会话覆盖时调用。
    v1.0.405（M5）新增：此前只有内存 pop，盘上文件无清理入口。"""
    if not pane_id:
        return
    with _chat_state_lock:
        chat_states.pop(pane_id, None)
    drop_pane_permission(pane_id)   # v1.0.590：权限级别侧表随断点一起清，避免长驻累积
    drop_pane_extra(pane_id)        # v1.0.660（A2）：thinking/team 侧表同样随断点清
    try:
        p = _bp_path(pane_id)
        if os.path.isfile(p):
            os.remove(p)
    except OSError as e:
        # v1.0.646：残留断点会让「继续」读到过期状态，删不掉必须留痕
        log_event(f"[session] 断点文件删除失败（残留会让「继续」读到旧状态）：{p}：{e}")
    except Exception as e:  # noqa: BLE001
        log_event(f"[session] 断点文件清理失败（残留可忽略）：{e}")


def restore_persisted_chat_state(pane_id):
    """从盘上恢复断点到内存 chat_states（存在且有效时）。返回是否恢复成功。
    routes/chat.py 的 /chat/continue 在内存无断点时调用——重启后「继续」可用。
    v1.0.485：readonly 一并恢复。
    v1.0.660（B8）：加载期做**字段级校验**（core.config.validate_schema）——
    断点文件是用户可编辑的 JSON（也能被别的进程写坏），坏结构直接透传会让下游
    拿到 None/字符串当消息用，症状是"点了继续没反应"或 500。
    校验不通过时按字段用安全默认值兜底（fail-closed：宁可少恢复一点，
    也不带坏数据进 agent 上下文）。"""
    if not pane_id:
        return False
    if pane_id in chat_states:
        return True   # 内存已有，无需恢复
    try:
        with open(_bp_path(pane_id), "r", encoding="utf-8") as f:
            d = json.load(f)
        # v1.0.660（B8）：断点 schema —— loop_msgs 必须是消息数组，
        # rounds 是整数，其余字段缺失时各自有安全默认值。
        _ok, _err = schema_ok(d, _CHECKPOINT_SCHEMA)
        if not _ok:
            log_event(f"[session] 断点 schema 校验不通过（按默认值兜底恢复）：{_err[:160]}")
            d = valid_or_default(d, _CHECKPOINT_SCHEMA, _CHECKPOINT_DEFAULTS)[1]
        msgs = d.get("loop_msgs")
        if isinstance(msgs, list) and msgs:
            # v1.0.660（B8）：加载期把 tool_calls 参数归一（"args"/"arguments" 两种形态统一）
            msgs = _normalize_loop_msgs(msgs)
            if not msgs:
                return False
            with _chat_state_lock:
                chat_states[pane_id] = {"loop_msgs": msgs, "rounds": int(d.get("rounds") or 0),
                                         "readonly": bool(d.get("readonly")),
                                         "model_id": d.get("model_id") or None,
                                         "permission_level": d.get("permission_level") or "auto",
                                         # v1.0.660（A2）：thinking 档位与 team 标记一并还原，
                                         # 否则「继续」后的档位/流水线形态与断流前不一致。
                                         "thinking": d.get("thinking"),
                                         "team": bool(d.get("team")),
                                         "ts": d.get("ts") or time.time()}
            # 锁外写侧表（避免与 _save_chat_state 的锁内读取形成嵌套顺序）
            set_pane_permission(pane_id, d.get("permission_level"))
            set_pane_extra(pane_id, thinking=d.get("thinking"),
                           team=bool(d.get("team")))
            log_event(f"[session] 已从盘上恢复断点：{pane_id}（{len(msgs)} 条消息，readonly={bool(d.get('readonly'))}，model={d.get('model_id') or '默认'}，权限={d.get('permission_level') or 'auto'}）")
            return True
    except FileNotFoundError:
        pass  # silent-ok: 断点文件不存在＝首次运行/已清断点，属正常路径
    except Exception as e:  # noqa: BLE001
        log_event(f"[session] 断点恢复失败：{e}")
    return False
