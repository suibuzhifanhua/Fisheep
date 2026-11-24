# -*- coding: utf-8 -*-
"""tests/contract/test_run_trace.py — run_id 轨迹的**行为锁**（评审 P1-4）。

最要紧的两条：
  · `_sse_stream` 的泵线程是普通 Thread，**不继承 contextvars**——改回
    `Thread(target=_pump)` 轨迹会全部串不上，下面两条正反锁会立刻红；
  · 轨迹是旁路观测，record() 永不抛异常，非任务期 log_event 格式逐字不变。
"""
import contextvars
import io
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import run_trace as RT  # noqa: E402
from core import paths  # noqa: E402

_RID = "20261123-191900-abcdef12"


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "workspace_root", lambda: str(tmp_path))
    return str(tmp_path)


def test_new_run_id_shape_and_unique():
    rid = RT.new_run_id()
    assert len(rid) == 24 and rid[8] == "-" and rid[15] == "-"
    assert rid != RT.new_run_id()


def test_bind_unbind_resets():
    assert RT.current_run_id() is None
    tok = RT.bind("probe")
    assert RT.current_run_id() == "probe"
    RT.unbind(tok)
    assert RT.current_run_id() is None


def test_record_without_run_id_is_noop(runs_dir):
    assert RT.record("orphan") is False
    assert RT.list_runs() == []


def test_record_and_read_roundtrip(runs_dir):
    assert RT.record("chat_start", run_id=_RID, pane_id="p1", mode="agent") is True
    RT.record("sse", run_id=_RID, type="done")
    events = RT.read_run(_RID)
    assert len(events) == 2
    assert events[0]["event"] == "chat_start" and events[0]["pane_id"] == "p1"
    assert all(e["run_id"] == _RID for e in events)
    assert all("ts" in e for e in events)


def test_bad_lines_are_skipped_not_fatal(runs_dir):
    os.makedirs(os.path.join(runs_dir, "runs"))
    with io.open(RT.run_path(_RID), "w", encoding="utf-8") as f:
        f.write('{"event":"ok","run_id":"%s","ts":1}\n' % _RID)
        f.write("{broken json\n")
        f.write('{"event":"done","run_id":"%s","ts":3}\n' % _RID)
    assert [e["event"] for e in RT.read_run(_RID)] == ["ok", "done"]


def test_record_sse_extracts_summary_only(runs_dir):
    long_text = "x" * 500
    assert RT.record_sse('data: {"type":"token","text":"%s"}\n\n' % long_text,
                         run_id=_RID) is True
    e = RT.read_run(_RID)[0]
    assert e["event"] == "sse" and e["type"] == "token"
    assert len(e["text"]) == RT.SSE_FIELD_MAX


def test_record_sse_skips_heartbeat_and_malformed(runs_dir):
    assert RT.record_sse(": keepalive\n\n", run_id=_RID) is False
    assert RT.record_sse("data: not-json\n\n", run_id=_RID) is False
    assert RT.record_sse("data: [1,2]\n\n", run_id=_RID) is False


def test_summarize_duration_and_counts():
    events = [{"ts": 1.0, "event": "run_start"},
              {"ts": 2.0, "event": "sse", "type": "tool_result"},
              {"ts": 5.5, "event": "sse", "type": "error"}]
    s = RT.summarize(events)
    assert s["events"] == 3 and s["duration_s"] == 4.5 and s["errors"] == 1
    assert s["types"]["tool_result"] == 1


def test_list_runs_is_reverse_sorted(runs_dir):
    for rid in ("20261123-191900-aaaaaaaa", "20261123-192000-bbbbbbbb"):
        RT.record("chat_start", run_id=rid)
    assert RT.list_runs() == ["20261123-192000-bbbbbbbb", "20261123-191900-aaaaaaaa"]


# ---------- contextvars 跨线程传播（本实现最容易踩的坑） ----------

def test_copy_context_propagates_run_id_to_thread():
    tok = RT.bind("probe-run")
    seen = []
    ctx = contextvars.copy_context()
    t = threading.Thread(target=ctx.run,
                         args=(lambda: seen.append(RT.current_run_id()),))
    t.start()
    t.join()
    RT.unbind(tok)
    assert seen == ["probe-run"]


def test_plain_thread_does_not_inherit_run_id():
    """反向锁：证明必须用 copy_context——改回普通 Thread 这条不会自动变红，
    但下一条端到端锁会红（轨迹里只剩主线程事件）。"""
    tok = RT.bind("probe-run")
    seen = []
    t = threading.Thread(target=lambda: seen.append(RT.current_run_id()))
    t.start()
    t.join()
    RT.unbind(tok)
    assert seen == [None]


def test_sse_stream_records_whole_chain(runs_dir):
    from agent import sse_stream as S

    def gen():
        yield 'data: {"type":"token","text":"hi"}\n\n'
        yield 'data: {"type":"tool_result","name":"pc_files","summary":"ok"}\n\n'
        yield 'data: {"type":"done"}\n\n'

    out = list(S._sse_stream(gen(), pane_id="p1", run_id=_RID))
    assert len(out) == 3, "轨迹旁路不得改变 SSE 透传行为"
    events = RT.read_run(_RID)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    sse = [e for e in events if e["event"] == "sse"]
    assert [e["type"] for e in sse] == ["token", "tool_result", "done"]
    assert RT.current_run_id() is None, "生成器结束后必须复位，防线程池复用串号"


# ---------- log_event 关联 ----------

def _patched_logging(monkeypatch):
    import core.logging as L
    lines = []
    monkeypatch.setattr(L.paths, "workspace_ready", lambda: True)
    monkeypatch.setattr(L, "flush_early_logs", lambda: 0)
    monkeypatch.setattr(L, "_write_log_line",
                        lambda line, log_dir=None: lines.append(line))
    return L, lines


def test_log_event_includes_run_id_when_bound(monkeypatch):
    L, lines = _patched_logging(monkeypatch)
    tok = RT.bind("probe-run")
    L.log_event("[agent] hello")
    RT.unbind(tok)
    assert "[run=probe-run] [agent] hello" in lines[0]


def test_log_event_format_unchanged_without_run_id(monkeypatch):
    L, lines = _patched_logging(monkeypatch)
    L.log_event("[agent] hello")
    assert "[agent] hello" in lines[0] and "[run=" not in lines[0]


# ---------- 路由形状锁 ----------

def test_all_chat_sse_entries_carry_run_id():
    src = io.open(os.path.join(ROOT, "routes", "chat.py"), encoding="utf-8").read()
    assert src.count("_sse_response(") == 4, "3 个 SSE 入口 + 1 处定义"
    assert '"X-Run-Id": run_id' in src
    assert src.count("str(data.get(\"run_id\")") == 2, "chat 与 continue 都要复用/新建 run_id"


def test_run_id_validator_rejects_traversal():
    from routes.b_admin import _RUN_ID_RE
    assert _RUN_ID_RE.match(_RID)
    for bad in ("../../etc/passwd", "../secret", "abc", _RID + "/../x"):
        assert not _RUN_ID_RE.match(bad), bad
