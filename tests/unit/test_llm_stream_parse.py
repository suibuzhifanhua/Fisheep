# -*- coding: utf-8 -*-
"""tests.test_llm_stream_parse — llm_client **真实流解析层**的行为测试（v1.0.677 新建）。

为什么要有这个文件：项目原有的流式测试走的是 tests/conftest.py 里的 `_done()` 桩——
它直接 yield ("done", {...}) 元组，**完全绕过 llm_client._stream_body 的解析代码**。
于是这条路径上的东西一件都没被测到：

  · delta.tool_calls 的分片装配（index 缺省时的槽位分配）
  · 工具参数 args 的 JSON 解析失败兜底（原先失败 → args={} 照常派发）
  · [DONE] 缺失 / 流在 chunk 边界被截断（原先 EOF 与 [DONE] 同一条 break）
  · finish_reason == "length" 的截断判定

这几处恰好是 v1.0.677 修的 P0 的藏身处。测试基座不换，修完也没有回归护栏。

本文件的做法：**喂原始 SSE 行**（"data: {...}" 文本），走真正的解析路径。
"""

import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import llm_client  # noqa: E402


MODEL = {"name": "test-model", "model": "test-model", "base_url": "http://127.0.0.1:1/v1",
         "api_key": "k", "max_input_tokens": 8192}


class _FakeResp:
    """最小可用的流式响应桩。"""

    def __init__(self, lines, status_code=200, text=""):
        self._lines = list(lines)
        self.status_code = status_code
        self.text = text
        self.encoding = None
        self.closed = False
        # _kill_sock 取 r.raw._fp.fp.raw._sock —— 取不到就抛（_stream_body 里有 try/except）
        class _Raw:
            class _FP:
                class _FP2:
                    raw = None
                fp = _FP2()
            _fp = _FP()
        self.raw = _Raw()

    def iter_lines(self, decode_unicode=True):
        for ln in self._lines:
            yield ln

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp


def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False)


def _chunk(content=None, reasoning=None, tool_calls=None, finish_reason=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    ch = {"choices": [{"delta": delta, "index": 0}]}
    if finish_reason:
        ch["choices"][0]["finish_reason"] = finish_reason
    return _sse(ch)


def _run(lines, monkeypatch, model=None):
    """用给定的原始 SSE 行跑一次 _stream_body，返回事件列表。"""
    resp = _FakeResp(lines)
    sess = _FakeSession(resp)
    monkeypatch.setattr(llm_client, "_http_session", lambda: sess)
    return list(llm_client._stream_body(model or MODEL, [{"role": "user", "content": "hi"}]))


# --------------------------------------------------------------------------
# 1. 正常流
# --------------------------------------------------------------------------

def test_normal_stream_produces_done(monkeypatch):
    """标准流：content 若干 + finish_reason=stop + [DONE] → 产出 done，文本完整。"""
    evs = _run([
        _chunk(content="你好"),
        _chunk(content="世界"),
        _chunk(finish_reason="stop"),
        "data: [DONE]",
    ], monkeypatch)
    kinds = [e[0] for e in evs]
    assert "done" in kinds, kinds
    assert "error" not in kinds, evs
    done = [e for e in evs if e[0] == "done"][0]
    assert done[1]["text"] == "你好世界"
    assert done[1]["finish_reason"] == "stop"
    tokens = [e[1] for e in evs if e[0] == "token"]
    assert "".join(tokens) == "你好世界"


# --------------------------------------------------------------------------
# 2. 流被截断（v1.0.677 P0-2）
# --------------------------------------------------------------------------

def test_truncated_stream_without_done_marker_errors(monkeypatch):
    """未收到 [DONE]、也没有 finish_reason，却已有正文 → 判定流被截断。

    原实现 EOF 与 [DONE] 走同一条 break：半截正文被当成完整回答推给前端并落库，
    半截 tool_calls 会被真实派发。修复后必须产出 stream_truncated 错误、且**没有** done。
    """
    evs = _run([
        _chunk(content="半截回答"),
    ], monkeypatch)
    kinds = [e[0] for e in evs]
    assert "done" not in kinds, "截断的流绝不能产出 done"
    errs = [e for e in evs if e[0] == "error"]
    assert errs, evs
    assert errs[0][2] == "stream_truncated", errs[0]


def test_truncated_stream_with_tool_calls_never_dispatches(monkeypatch):
    """截断且带工具调用 → 同样不得产出 done（否则半截工具调用会被派发）。"""
    evs = _run([
        _chunk(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                            "function": {"name": "read_wecom_page", "arguments": '{"pick"'}}]),
    ], monkeypatch)
    assert [e for e in evs if e[0] == "done"] == [], evs
    errs = [e for e in evs if e[0] == "error"]
    assert errs and errs[0][2] == "stream_truncated", evs


def test_eof_without_done_but_with_finish_reason_is_accepted(monkeypatch):
    """没发 [DONE] 但发了 finish_reason → 服务端正常收尾，接受（不误伤）。"""
    evs = _run([
        _chunk(content="完整回答"),
        _chunk(finish_reason="stop"),
    ], monkeypatch)
    kinds = [e[0] for e in evs]
    assert "done" in kinds and "error" not in kinds, evs
    assert [e for e in evs if e[0] == "done"][0][1]["text"] == "完整回答"


def test_empty_stream_without_done_is_not_an_error(monkeypatch):
    """零产出且无 [DONE] → 不额外报错（走既有的空流重试路径，不误伤）。"""
    evs = _run([], monkeypatch)
    assert [e for e in evs if e[0] == "error"] == [], evs


# --------------------------------------------------------------------------
# 3. 工具参数解析（v1.0.677 P0-3）
# --------------------------------------------------------------------------

def test_broken_tool_args_are_marked_not_silently_emptied(monkeypatch):
    """args JSON 非法 → 该调用必须带 __parse_error__ 标记（供上层拒绝执行）。

    原实现吞掉异常后 args={} 并照常派发——对 close_wecom_page 这类"无必填参数"的
    工具，空参数调用会产生真实副作用（关掉用户正在看的窗口），且返回 ok=True，
    agent 于是认为成功。
    """
    evs = _run([
        _chunk(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                            "function": {"name": "close_wecom_page", "arguments": '{"pick": 0'}}]),
        _chunk(finish_reason="tool_calls"),
        "data: [DONE]",
    ], monkeypatch)
    done = [e for e in evs if e[0] == "done"]
    assert done, evs
    calls = done[0][1]["tool_calls"]
    assert len(calls) == 1
    assert calls[0].get("__parse_error__"), calls[0]
    assert calls[0]["args"] == {}, "参数非法时不得凭空虚构参数"


def test_valid_tool_args_have_no_error_marker(monkeypatch):
    """合法 args 不带标记（别把正常调用也标成失败）。"""
    evs = _run([
        _chunk(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                            "function": {"name": "read_wecom_page", "arguments": '{"pick": 3}'}}]),
        _chunk(finish_reason="tool_calls"),
        "data: [DONE]",
    ], monkeypatch)
    done = [e for e in evs if e[0] == "done"]
    calls = done[0][1]["tool_calls"]
    assert calls[0]["args"] == {"pick": 3}
    assert not calls[0].get("__parse_error__")


def test_tool_call_slicing_without_index_allocates_separate_slots(monkeypatch):
    """服务端不带 index 的多工具调用 → 按出现顺序分槽，args 不能串到同一槽。"""
    evs = _run([
        _chunk(tool_calls=[{"id": "c1", "type": "function",
                            "function": {"name": "read_wecom_page", "arguments": '{"pick": 1}'}}]),
        _chunk(tool_calls=[{"id": "c2", "type": "function",
                            "function": {"name": "capture_wecom_window", "arguments": '{"pick": 2}'}}]),
        _chunk(finish_reason="tool_calls"),
        "data: [DONE]",
    ], monkeypatch)
    done = [e for e in evs if e[0] == "done"]
    calls = done[0][1]["tool_calls"]
    assert len(calls) == 2, calls
    names = sorted(c["name"] for c in calls)
    assert names == ["capture_wecom_window", "read_wecom_page"], calls
    assert sorted(c["args"].get("pick") for c in calls) == [1, 2], calls


def test_tool_call_argument_fragments_are_concatenated(monkeypatch):
    """args 分片（同一 index 多次 delta）必须拼成完整 JSON。"""
    evs = _run([
        _chunk(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                            "function": {"name": "read_wecom_page", "arguments": '{"pi'}}]),
        _chunk(tool_calls=[{"index": 0, "function": {"arguments": 'ck": 5}'}}]),
        _chunk(finish_reason="tool_calls"),
        "data: [DONE]",
    ], monkeypatch)
    done = [e for e in evs if e[0] == "done"]
    calls = done[0][1]["tool_calls"]
    assert calls[0]["args"] == {"pick": 5}, calls


# --------------------------------------------------------------------------
# 4. 上下文超长识别（v1.0.677 P0）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "This model's maximum context length is 8192 tokens",
    "context_length_exceeded",
    "Request too large for input",
    "reduce the length of the messages",
    "输入长度超出模型上下文限制",
])
def test_context_overflow_body_is_recognized(body):
    """上下文超长的各家措辞都要被识别成 context_overflow（可自愈的一类 400）。"""
    assert llm_client._is_context_overflow(body.lower()) is True, body


@pytest.mark.parametrize("body", [
    "invalid parameter: response_format",
    "unknown field parallel_tool_calls",
    "model not found",
])
def test_other_400_bodies_are_not_context_overflow(body):
    """普通 400（参数不支持/模型不存在）不能被误判成上下文超长。"""
    assert llm_client._is_context_overflow(body.lower()) is False, body


# --------------------------------------------------------------------------
# 5. 完成标记结构化（v1.0.677）
# --------------------------------------------------------------------------

def test_agent_loop_emits_structured_final_event(monkeypatch):
    """agent_loop 收尾时必须额外发一条 {"type":"final"} 结构化事件。

    原来最终答复只能靠正文里的「【完成】」字面量定位，四份消费方（team 审查 /
    auto_loop 续跑 / chat 落库 / scheduler）各自实现抽取、口径还分叉。
    """
    import agent.loop as AL
    from tests import conftest as _cf  # noqa: F401（确保 conftest 已加载路径）

    calls = []

    def _fake_stream(*a, **kw):
        def _gen():
            yield ("done", {"text": "", "tool_calls": [
                {"id": "call_1", "name": "finish", "args": {"final_answer": "已完成 3 项"}}],
                "usage": None})
        return _gen()

    monkeypatch.setattr(AL, "stream_llm_events", _fake_stream)

    def _handler(name, args, cfg):
        calls.append(name)
        return {"__finish__": True, "final_answer": args.get("final_answer", "")}

    monkeypatch.setattr(AL, "dispatch_tool", _handler)

    evs = []
    for ev in AL.agent_loop([{"role": "user", "content": "做点事"}], {"models": [MODEL]}, pane_id="sfp1"):
        evs.append(ev)
    finals = [e for e in evs if '"type": "final"' in e or '"type":"final"' in e]
    assert finals, "没有发出结构化 final 事件"
    obj = json.loads(finals[-1].split("data: ", 1)[1].strip())
    assert obj["type"] == "final"
    assert "已完成 3 项" in obj["text"]
