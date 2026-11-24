# -*- coding: utf-8 -*-
"""core.run_trace — 一次 agent 任务的 `run_id` 与结构化轨迹（评审 P1-4）。

一次 chat 任务生成一个 run_id，旁路录到 `产物库/runs/<run_id>.jsonl`：
  · 每行一条结构化事件（ts / run_id / event / 摘要字段），不落整段回复或页面快照；
  · SSE 是任务唯一的输出通道，工具调用 / LLM 结果 / 用量 / 错误 / done 都以事件
    形式经过它，所以在 `_sse_stream` 一个收口处旁路录制即可覆盖整条调用链，
    **不需要改十几处业务代码**（评审说「不动业务逻辑，风险低」即指此）；
  · contextvars 保存当前 run_id；`_sse_stream` 用 `copy_context()` 把值传播进
    后台泵线程（普通 Thread 不继承 contextvars，这是本实现最容易踩的坑）。

设计铁律：**record() 永不抛异常**。轨迹是旁路观测，任何 IO/序列化故障都只能
被忽略并留痕，绝不能把 SSE 主链路拖断。
"""
import contextvars
import io
import json
import os
import threading
import time
import uuid

from core import paths

RUNS_DIRNAME = "runs"
SSE_FIELD_MAX = 200          # 摘要字段截断长度，避免把整段回复写进轨迹
_run_id_var = contextvars.ContextVar("run_id", default=None)
_write_lock = threading.Lock()


def new_run_id():
    """可读前缀 + 随机后缀：同一秒内并行任务也不会撞号。"""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def current_run_id():
    return _run_id_var.get()


def bind(run_id):
    """绑定当前上下文，返回 token 供 unbind 复位（防止线程池复用串号）。"""
    return _run_id_var.set(run_id)


def unbind(token):
    if token is not None:
        _run_id_var.reset(token)


def runs_dir():
    return os.path.join(paths.workspace_root(), RUNS_DIRNAME)


def run_path(run_id):
    return os.path.join(runs_dir(), run_id + ".jsonl")


def _log_failure(msg):
    """轨迹自身的故障只留痕，不向主链路抛。"""
    try:
        from core.logging import log_event
        log_event("[run_trace] " + msg)
    except Exception:  # noqa: BLE001  silent-ok: 连日志都写不出去时只能放弃，不能拖断 SSE
        return


def record(event, run_id=None, **fields):
    """录一条结构化事件。无 run_id（非 chat 链路）时静默跳过，返回是否落盘。"""
    rid = run_id or _run_id_var.get()
    if not rid:
        return False
    try:
        row = {"ts": round(time.time(), 3), "run_id": rid, "event": event}
        for k, v in fields.items():
            row[k] = v
        line = json.dumps(row, ensure_ascii=False, default=str)
        d = runs_dir()
        with _write_lock:
            os.makedirs(d, exist_ok=True)
            with io.open(run_path(rid), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception as e:  # noqa: BLE001
        _log_failure("record 失败（忽略）：%s" % e)
        return False


def record_sse(item, run_id=None, **extra):
    """从一条 SSE `data: {...}` 字符串抽摘要并录制。解析失败一律忽略。

    v1.0.756（评审 P1-4 字面补做）：除文本摘要外，把 token / 耗时 / 判定依据
    等数值字段原样保留，供 summarize() 聚合「耗时分布 + token」。extra 允许
    SSE 收口处注入 elapsed_ms 等旁路字段，**不需要改十几处业务事件结构**。
    """
    if not isinstance(item, str) or not item.startswith("data: "):
        return False
    try:
        obj = json.loads(item[6:].strip())
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    fields = {"type": obj.get("type")}
    for k in ("tool", "name", "code", "round", "text", "reason", "summary",
              "verdict", "result", "stage", "model", "ok", "error"):
        if k in obj and obj[k] is not None:
            fields[k] = str(obj[k])[:SSE_FIELD_MAX]
    # 数值型字段原样保留（token / 耗时），不截断成字符串，便于聚合
    for k in ("tokens", "prompt_tokens", "completion_tokens", "usage",
              "duration_ms", "elapsed_ms"):
        if k in obj and obj[k] is not None:
            fields[k] = obj[k]
    fields.update(extra)
    return record("sse", run_id=run_id, **fields)


def read_run(run_id):
    """一次查询拿完整调用链；文件不存在 / 坏行都按空处理。"""
    p = run_path(run_id)
    if not os.path.isfile(p):
        return []
    out = []
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue        # 坏行跳过，不让一条截断的写坏整条轨迹
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def _is_error_event(e):
    low = str(e.get("type") or "").lower()
    return (low in ("error", "aborted")
            or str(e.get("code") or "").lower() in ("error", "aborted")
            or e.get("ok") is False
            or str(e.get("event") or "").lower() == "run_error")


def summarize(events):
    """耗时分布 + 事件计数 + token / 工具 / 失败定位，一次查询看清整个任务。

    v1.0.756（评审 P1-4）：新增 failed / tokens / tool_calls / first_error /
    slowest_gap_s；旧键（events / duration_s / types / errors / started_at /
    ended_at）全部保留，回放页与既有 API 不用改消费方式。
    """
    empty = {"events": 0, "duration_s": 0.0, "types": {}, "errors": 0,
             "failed": False, "tokens": 0, "tool_calls": 0,
             "first_error": None, "slowest_gap_s": 0.0,
             "started_at": None, "ended_at": None}
    if not events:
        return empty
    ts = sorted(e.get("ts") for e in events
                if isinstance(e.get("ts"), (int, float)))
    types = {}
    tokens = 0
    tool_calls = 0
    first_error = None
    for e in events:
        t = str(e.get("type") or e.get("event") or "?")
        types[t] = types.get(t, 0) + 1
        for k in ("tokens", "prompt_tokens", "completion_tokens"):
            v = e.get(k)
            if isinstance(v, (int, float)):
                tokens += int(v)
        if e.get("tool") or e.get("name"):
            tool_calls += 1
        if first_error is None and _is_error_event(e):
            first_error = e
    errors = sum(1 for e in events if _is_error_event(e))
    gaps = [round(b - a, 3) for a, b in zip(ts, ts[1:])]
    return {"events": len(events),
            "duration_s": round(ts[-1] - ts[0], 3) if len(ts) >= 2 else 0.0,
            "types": types, "errors": errors, "failed": errors > 0,
            "tokens": tokens, "tool_calls": tool_calls,
            "first_error": first_error,
            "slowest_gap_s": max(gaps) if gaps else 0.0,
            "started_at": ts[0] if ts else None,
            "ended_at": ts[-1] if ts else None}


def list_runs(limit=50):
    """最近 limit 个 run（按文件名倒序，文件名前缀即时间）。"""
    d = runs_dir()
    try:
        names = [n for n in os.listdir(d) if n.endswith(".jsonl")]
    except OSError:
        return []
    names.sort(reverse=True)
    return [n[:-6] for n in names[:limit]]


def list_runs_with_status(limit=50):
    """v1.0.756（评审 P1-4）：最近任务 + 失败标记，供回放页列表一次标红。

    每个 run 读一遍自己的 jsonl（最多 limit 个文件），不要求调用方再逐个请求
    /api/runs/<id>；「任取一次失败任务」在列表里直接可见。
    """
    out = []
    for rid in list_runs(limit):
        s = summarize(read_run(rid))
        out.append({"run_id": rid, "events": s["events"],
                    "errors": s["errors"], "failed": s["failed"],
                    "duration_s": s["duration_s"], "tokens": s["tokens"],
                    "tool_calls": s["tool_calls"],
                    "started_at": s["started_at"]})
    return out
