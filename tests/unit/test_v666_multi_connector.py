# -*- coding: utf-8 -*-
"""v1.0.667（用户："连接器功能只能添加保存一个 优化一下"）：多连接器回归锁。

覆盖：
  1. POST /api/connectors 接受 user-* 实例（id 白名单正则 + name 落盘）
  2. 非法 user- id（超长 / 特殊字符 / 内置 id 伪造）不得写入
  3. GET /api/connectors 返回用户连接器（user:true + api_key 脱敏 + name 回显）
  4. DELETE /api/connectors/<cid> 只删用户连接器，内置不可删
  5. build_agent_tools 给启用且已配置的用户连接器注入 {id}_call 工具 + prompt 渲染
  6. dispatch_tool 直调用户连接器工具走 run_connector（mock requests 验证 URL/Key）
  7. 功能闸（connectors=False）对用户连接器同样拦截
"""
import copy

import pytest

from agent_tools import (build_agent_tools, dispatch_tool, user_connector_def,
                         render_connector_prompt)


def _fake_update_config(saved, base_cfg):
    def _mutate(cfg):
        if cfg["connectors"]:
            saved.update(cfg)
        return True
    def _wrap(mutate):
        cfg = copy.deepcopy(base_cfg)
        if mutate(cfg):
            saved.update(cfg)
        return True
    return _wrap


# ---------- 1. POST 接受 user-* ----------

def test_post_accepts_user_connector(monkeypatch):
    import backend  # noqa: F401（确保 app 组装）
    from routes import b_admin
    saved = {}
    monkeypatch.setattr(b_admin, "update_config", _fake_update_config(saved, {}))
    client = backend.app.test_client()
    resp = client.post("/api/connectors", json={"connectors": {
        "user-oa": {"enabled": True, "name": "内部OA",
                    "base_url": "https://oa.example.com", "api_key": "sk-oa-1",
                    "headers": "{}"}}})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    c = saved["connectors"]["user-oa"]
    assert c["base_url"] == "https://oa.example.com"
    assert c["name"] == "内部OA"
    assert c["enabled"] is True


def test_post_rejects_malformed_user_ids(monkeypatch):
    import backend  # noqa: F401
    from routes import b_admin
    saved = {}
    monkeypatch.setattr(b_admin, "update_config", _fake_update_config(saved, {}))
    client = backend.app.test_client()
    resp = client.post("/api/connectors", json={"connectors": {
        # 超长 id（>64）
        "user-" + "a" * 70: {"enabled": True, "base_url": "https://x"},
        # 特殊字符
        "user-..%$": {"enabled": True, "base_url": "https://x"},
        # 非 dict
        "user-ok": "notadict",
    }})
    assert resp.status_code == 200
    assert "user-" + "a" * 70 not in saved.get("connectors", {})
    assert "user-..%$" not in saved.get("connectors", {})
    assert "user-ok" not in saved.get("connectors", {})


def test_post_user_connector_empty_password_keeps(monkeypatch):
    """用户连接器同样吃到 P1-13 空串保留原凭据的守卫（同一代码路径，锁死防回归）。"""
    import backend  # noqa: F401
    from routes import b_admin
    base = {"connectors": {"user-oa": {"enabled": True, "name": "OA",
                                       "base_url": "https://oa.example.com",
                                       "api_key": "sk-keep"}}}
    saved = {}
    monkeypatch.setattr(b_admin, "update_config", _fake_update_config(saved, base))
    client = backend.app.test_client()
    resp = client.post("/api/connectors", json={"connectors": {
        "user-oa": {"enabled": True, "name": "OA", "api_key": ""}}})
    assert resp.status_code == 200
    assert saved["connectors"]["user-oa"]["api_key"] == "sk-keep"


# ---------- 3. GET 返回用户连接器 ----------

def test_get_lists_user_connector_masked(monkeypatch):
    import backend  # noqa: F401
    from routes import b_admin
    cfg = {"connectors": {"user-oa": {"enabled": True, "name": "内部OA",
                                       "base_url": "https://oa.example.com",
                                       "api_key": "sk-secret", "headers": ""}}}
    monkeypatch.setattr(b_admin, "load_config", lambda: copy.deepcopy(cfg))
    client = backend.app.test_client()
    resp = client.get("/api/connectors")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = {c["id"] for c in data["connectors"]}
    assert "user-oa" in ids and "generic-http" in ids
    uc = next(c for c in data["connectors"] if c["id"] == "user-oa")
    assert uc["user"] is True
    assert uc["name"] == "内部OA"
    # 脱敏口径不变：api_key 打码，name 回显
    assert uc["config"].get("api_key") == "******"
    assert uc["config"].get("name") == "内部OA"
    assert uc["configured"] is True


# ---------- 4. DELETE ----------

def test_delete_user_connector(monkeypatch):
    import backend  # noqa: F401
    from routes import b_admin
    base = {"connectors": {"user-oa": {"enabled": True, "name": "OA",
                                        "base_url": "https://x"},
                           "generic-http": {"enabled": True, "base_url": "https://y"}}}
    saved = {}
    monkeypatch.setattr(b_admin, "update_config", _fake_update_config(saved, base))
    client = backend.app.test_client()
    resp = client.delete("/api/connectors/user-oa")
    assert resp.status_code == 200
    assert "user-oa" not in saved["connectors"]
    assert "generic-http" in saved["connectors"]  # 内置不受影响


@pytest.mark.parametrize("bad", ["generic-http", "user-", "user-a b", "user-a/b"])
def test_delete_rejects_non_user_or_bad(monkeypatch, bad):
    import backend  # noqa: F401
    from routes import b_admin
    saved = {}
    monkeypatch.setattr(b_admin, "update_config", _fake_update_config(saved,
        {"connectors": {"generic-http": {"enabled": True}}}))
    client = backend.app.test_client()
    resp = client.delete("/api/connectors/" + bad)
    assert resp.status_code == 400, f"{bad} 应被拒绝"
    assert not saved.get("connectors") or "generic-http" in saved.get("connectors", {})


# ---------- 5/6. 工具注入与执行 ----------

def _cfg_with_user_conn():
    return {"enabled_skills": None, "skill_overrides": {}, "custom_skills": [],
            "models": [], "feature_flags": {"connectors": True},
            "connectors": {"user-oa": {"enabled": True, "name": "内部OA",
                                         "base_url": "https://oa.example.com",
                                         "api_key": "sk-oa", "headers": "{}"}}}


def test_build_injects_user_connector_tool():
    cfg = _cfg_with_user_conn()
    tools, prompts = build_agent_tools(cfg)
    names = {t.get("function", {}).get("name") for t in tools}
    assert "user-oa_call" in names, names
    assert "内部OA" in prompts  # prompt 占位符已按实例渲染


def test_build_skips_disabled_or_unconfigured_user_conn():
    cfg = _cfg_with_user_conn()
    cfg["connectors"]["user-oa"]["enabled"] = False
    tools, _ = build_agent_tools(cfg)
    names = {t.get("function", {}).get("name") for t in tools}
    assert "user-oa_call" not in names

    cfg2 = _cfg_with_user_conn()
    del cfg2["connectors"]["user-oa"]["base_url"]  # 必填缺失 → 未配置
    tools2, _ = build_agent_tools(cfg2)
    names2 = {t.get("function", {}).get("name") for t in tools2}
    assert "user-oa_call" not in names2


def test_user_connector_feature_gate():
    cfg = _cfg_with_user_conn()
    cfg["feature_flags"] = {"connectors": False}
    tools, _ = build_agent_tools(cfg)
    names = {t.get("function", {}).get("name") for t in tools}
    assert "user-oa_call" not in names
    out = dispatch_tool("user-oa_call", {}, cfg)
    assert out.get("ok") is False and "停用" in str(out.get("error", "")), out


def test_dispatch_routes_user_connector(monkeypatch):
    """dispatch 直调 user-oa_call → run_connector 同执行器：URL = base+path，Key 自动附带。"""
    from agent_tools import connectors as conn_mod
    captured = {}

    class _FakeR:
        status_code = 200
        text = '{"ok":1}'

    def _fake_request(method, url, **kw):
        captured.update(method=method, url=url, headers=kw.get("headers"))
        return _FakeR()

    monkeypatch.setattr(conn_mod, "requests", type("R", (), {"request": staticmethod(_fake_request)})())
    out = dispatch_tool("user-oa_call", {"path": "/v1/items", "method": "POST", "body": {"a": 1}},
                        _cfg_with_user_conn())
    assert out.get("status") == 200, out
    assert captured["url"] == "https://oa.example.com/v1/items"
    assert captured["headers"].get("Authorization") == "Bearer sk-oa"


def test_run_connector_unknown_id_still_errors():
    """内置 generic-http 分支判定改过之后，未知非 user 连接器仍走错误分支（防误放行）。"""
    out = dispatch_tool("nope_call", {}, _cfg_with_user_conn())
    assert out.get("ok") is False, out


def test_prompt_placeholder_rendering():
    """内置 generic-http 新占位符 prompt 渲染后不再是未替换的 {tool}。"""
    c = next(c for c in __import__("agent_tools").CONNECTORS if c["id"] == "generic-http")
    p = render_connector_prompt(c, {"name": ""})
    assert "{tool}" not in p and "generic_http_call" in p, p
