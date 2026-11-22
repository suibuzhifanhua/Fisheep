# -*- coding: utf-8 -*-
"""tests/test_model_test_speed.py — v1.0.691「测试连接」出字速度契约锁。

用户："软件模型测试 增加测试出字速度"。后端 /api/models/test 成功响应必须带
speed:{elapsed_s, tokens, tok_per_s}——字段名是前后端契约（前端 js/05 testModel
按名取数显示「✅ 连接成功 · x.x tok/s」，一字不差）。

真实网络不打：把 b_models 模块里的 requests 代理整体换成 fake
（模块级 `from core.logging import _lazy_requests as requests`，patch 对象
必须是 b_models.requests，patch llm_client 无效）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from routes import b_models  # noqa: E402


class _FakeResp:
    def __init__(self, code, body, text=""):
        self.status_code = code
        self._body = body
        self.text = text

    def json(self):
        return self._body


class _FakeReq:
    def __init__(self, code=200, body=None, exc=None):
        self.code = code
        self.body = body if body is not None else {}
        self.exc = exc
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return _FakeResp(self.code, self.body, text="boom" if self.code != 200 else "")


@pytest.fixture()
def fake_model(monkeypatch):
    """让 resolve_model 直接命中一个假模型，并固定 URL/请求头构造。"""
    import llm_client
    monkeypatch.setattr(b_models, "resolve_model",
                       lambda cfg, model_id=None: {"name": "假模型", "model": "fake-14b"})
    monkeypatch.setattr(llm_client, "_chat_url", lambda m: "http://fake.local/v1/chat/completions")
    monkeypatch.setattr(llm_client, "build_headers", lambda m: {"Authorization": "Bearer t"})
    yield


def _ok_body(reply="你好，我是假模型。", comp_tokens=80):
    body = {"choices": [{"message": {"content": reply}}]}
    if comp_tokens is not None:
        body["usage"] = {"completion_tokens": comp_tokens}
    return body


def test_speed_fields_present_and_consistent(app_client, fake_model, monkeypatch):
    """usage 正常 → speed 三字段齐全，tok_per_s 与 tokens/elapsed 自洽（容差 0.2）。"""
    client, _cfg = app_client
    fake = _FakeReq(code=200, body=_ok_body(comp_tokens=80))
    monkeypatch.setattr(b_models, "requests", fake)
    d = client.post("/api/models/test", json={}).get_json()
    assert d["ok"] is True and d["model"] == "假模型"
    sp = d["speed"]
    assert set(sp) == {"elapsed_s", "tokens", "tok_per_s"}
    assert sp["tokens"] == 80
    assert isinstance(sp["elapsed_s"], (int, float)) and sp["elapsed_s"] >= 0
    expect = round(80 / sp["elapsed_s"], 1) if sp["elapsed_s"] > 0 else 0
    assert abs(sp["tok_per_s"] - expect) <= 0.2, (sp["tok_per_s"], expect)


def test_speed_zero_tokens_when_usage_missing(app_client, fake_model, monkeypatch):
    """usage 缺失（部分本地模型不回 usage）→ tokens=0、tok_per_s=0，
    连接仍算成功——前端见 0 只报「✅ 连接成功」，不显示假速度。"""
    client, _cfg = app_client
    fake = _FakeReq(code=200, body=_ok_body(comp_tokens=None))
    monkeypatch.setattr(b_models, "requests", fake)
    d = client.post("/api/models/test", json={}).get_json()
    assert d["ok"] is True
    assert d["speed"]["tokens"] == 0
    assert d["speed"]["tok_per_s"] == 0


def test_http_error_shape_unchanged_no_speed(app_client, fake_model, monkeypatch):
    """失败响应结构不变（{ok, error}），不带 speed——前端失败分支只读 error。"""
    client, _cfg = app_client
    fake = _FakeReq(code=500)
    monkeypatch.setattr(b_models, "requests", fake)
    d = client.post("/api/models/test", json={}).get_json()
    assert d["ok"] is False
    assert "500" in d["error"]
    assert "speed" not in d


def test_network_error_shape_unchanged(app_client, fake_model, monkeypatch):
    """网络层异常（超时/拒连）→ ok False，无 speed。"""
    client, _cfg = app_client
    fake = _FakeReq(exc=ConnectionError("connect timeout"))
    monkeypatch.setattr(b_models, "requests", fake)
    d = client.post("/api/models/test", json={}).get_json()
    assert d["ok"] is False
    assert "speed" not in d


def test_payload_has_enough_ouput_budget(app_client, fake_model, monkeypatch):
    """行为锁：速度要有统计意义——提示语不能是 ping、max_tokens 必须放开到
    160（v1.0.691 把 ping/8 换成长输出句；回退成 ping 会让 tok/s 变成噪声）。"""
    client, _cfg = app_client
    fake = _FakeReq(code=200, body=_ok_body())
    monkeypatch.setattr(b_models, "requests", fake)
    client.post("/api/models/test", json={})
    pl = fake.calls[0]["json"]
    assert pl["max_tokens"] == 160
    content = pl["messages"][0]["content"]
    assert content != "ping" and len(content) >= 10
    assert pl["stream"] is False
