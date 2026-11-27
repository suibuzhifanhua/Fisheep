# -*- coding: utf-8 -*-
"""tests/contract/test_contract_llm_stream.py — llm_client 流中断 / 硬停止**契约锁**（评审 P1-2）。

llm_client.py 是 LLM 全链路（>1300 行）。两处历史上反复出事故的契约：
  · is_stream_interrupt 是「可自动重试的流式中断」的**单一事实来源**——
    此前 7 处手抄双轨判定各自漏改过一轮；aborted（用户点停止）绝不能被
    误判成可重试中断，否则点了停止还会自动重发；
  · stream_llm_events 的中断重试外层必须：瞬态中断自动重发、用户停止后立即
    收手、非中断错误不重试、重试有上限、done 立即返回；
  · abort_active_responses 靠 _ACTIVE_RESPONSES **强引用 set** 关 socket——
    v1.0.561 前用 WeakSet，临时对象被 GC 后集合恒空，硬停止从未生效。
"""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import llm_client as L  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """日志/连接池全局副作用打桩；重试抖动 sleep 置零（否则每条用例白等 2~3s）。"""
    monkeypatch.setattr(L, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(L, "_clear_http_pool", lambda: None)
    monkeypatch.setattr(L.time, "sleep", lambda *_a, **_k: None)
    with L._ACTIVE_LOCK:
        L._ACTIVE_RESPONSES.clear()


# ---------- ① 单一事实来源 ----------

def test_is_stream_interrupt_accepts_all_three_shapes():
    assert L.is_stream_interrupt("stream_interrupt", "") is True
    assert L.is_stream_interrupt(None, "LLM 流式中断：连接断开") is True
    assert L.is_stream_interrupt(None, "流异常中断") is True
    assert L.is_stream_interrupt(("error", "x", "stream_interrupt")) is True
    assert L.is_stream_interrupt(("error", "LLM 流式中断", None)) is True


def test_aborted_and_http_errors_are_not_retryable():
    """改回双轨手抄最容易漏的一条：用户停止不能被当成瞬态断流重发。"""
    assert L.is_stream_interrupt("aborted", "已停止（用户中断）") is False
    assert L.is_stream_interrupt("http_500", "请求失败") is False
    assert L.is_stream_interrupt(None, "") is False


def test_retry_outer_calls_single_source_not_hand_copied():
    src = io.open(os.path.join(ROOT, "llm_client.py"), encoding="utf-8").read()
    body = src.split("def stream_llm_events(", 1)[1].split(
        "def _stream_llm_events_once(", 1)[0]
    assert "is_stream_interrupt(" in body, "重试判定改回手抄双轨了"
    assert '"流式中断" in' not in body
    assert "if poll_abort is not None and poll_abort():" in body


# ---------- ② 中断重试外层 ----------

def _stub_once(monkeypatch, events_per_call):
    """按调用序返回事件序列的桩；events_per_call[i] 是第 i+1 次的 yield 列表。"""
    calls = []

    def fake(*_a, **_k):
        idx = len(calls)
        calls.append(idx)
        seq = events_per_call[min(idx, len(events_per_call) - 1)]
        for ev in seq:
            yield ev

    monkeypatch.setattr(L, "_stream_llm_events_once", fake)
    return calls


def test_transient_interrupt_retries_until_done(monkeypatch):
    calls = _stub_once(monkeypatch, [
        [("error", "LLM 流式中断", "stream_interrupt")],
        [("done", "ok")],
    ])
    events = list(L.stream_llm_events({"name": "stub"}, []))
    assert len(calls) == 2, "瞬态断流没有自动重发（旧行为：停住等用户点继续）"
    assert events[-1][0] == "done"
    assert sum(1 for e in events if e[0] == "error") == 1


def test_user_abort_stops_retry_immediately(monkeypatch):
    calls = _stub_once(monkeypatch, [
        [("error", "LLM 流式中断", "stream_interrupt")],
        [("done", "ok")],
    ])
    events = list(L.stream_llm_events({"name": "stub"}, [], poll_abort=lambda: True))
    assert len(calls) == 1, "点了停止还在重试（旧实现不感知 abort，每次再挂 90s+）"
    assert events[-1] == ("error", "已停止（用户中断）", "aborted")


def test_non_interrupt_error_is_not_retried(monkeypatch):
    calls = _stub_once(monkeypatch, [[("error", "HTTP 500", "http_error")]])
    events = list(L.stream_llm_events({"name": "stub"}, []))
    assert len(calls) == 1
    assert len(events) == 1 and events[0][0] == "error"


def test_retry_is_bounded(monkeypatch):
    calls = _stub_once(monkeypatch, [[("error", "LLM 流式中断", "stream_interrupt")]])
    events = list(L.stream_llm_events({"name": "stub"}, []))
    assert len(calls) == L.LLM_STREAM_EMPTY_RETRIES + 1
    assert len(events) == L.LLM_STREAM_EMPTY_RETRIES + 1


def test_done_returns_immediately(monkeypatch):
    _stub_once(monkeypatch, [[("done", "ok"), ("token", "should-not-appear")]])
    events = list(L.stream_llm_events({"name": "stub"}, []))
    assert events == [("done", "ok")]


# ---------- ③ 硬停止：socket 注册表必须是强引用 ----------

class _Sock(object):
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Req(object):
    def __init__(self):
        self.sock = _Sock()


def test_active_registry_is_strong_set():
    assert type(L._ACTIVE_RESPONSES) is set, "改回 WeakSet 会让集合恒空、硬停止永不生效"
    req = _Req()
    with L._ACTIVE_LOCK:
        L._ACTIVE_RESPONSES.add(req)
    assert L.llm_active_count() == 1


def test_abort_closes_socket_and_reports_count():
    req = _Req()
    with L._ACTIVE_LOCK:
        L._ACTIVE_RESPONSES.add(req)
    n = L.abort_active_responses()
    assert n == 1, "closesocket 没有真的关到进行中的连接"
    assert req.sock.closed is True


def test_abort_without_active_request_is_zero():
    assert L.abort_active_responses() == 0


def test_source_pins_strong_set_not_weakset():
    src = io.open(os.path.join(ROOT, "llm_client.py"), encoding="utf-8").read()
    assert "_ACTIVE_RESPONSES = set()" in src
    assert "_ACTIVE_RESPONSES = WeakSet()" not in src
