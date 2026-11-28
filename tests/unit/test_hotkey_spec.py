# -*- coding: utf-8 -*-
"""tests/unit/test_hotkey_spec.py — v1.0.788（P2-9）全局热键（唤起/隐藏窗口）可配。

  · parse_hotkey：只接受 Ctrl+Alt（可选 +Shift）+ 单键（A-Z/0-9/Space/F1-F12），顺序不限
  · POST /api/config 的 toggle_hotkey：非空必须可解析（400 锁），空串=显式关闭（存值不删键）
  · apply_toggle_hotkey_change 主窗未挂钩时 no-op（测试/headless 不碰真实热键）
真实 RegisterHotKey 不在测试里跑（会用假句柄污染 _toggle_reg 或意外占键）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from desktop import close_guard  # noqa: E402

MOD_CTRL_ALT = close_guard.MOD_CONTROL | close_guard.MOD_ALT


def test_parse_valid_matrix():
    assert close_guard.parse_hotkey("Ctrl+Alt+Space") == (MOD_CTRL_ALT, 0x20)
    assert close_guard.parse_hotkey("ctrl+alt+space") == (MOD_CTRL_ALT, 0x20)
    assert close_guard.parse_hotkey("ALT+CTRL+K") == (MOD_CTRL_ALT, ord("K"))
    assert close_guard.parse_hotkey("Ctrl+Alt+1") == (MOD_CTRL_ALT, ord("1"))
    assert close_guard.parse_hotkey("Ctrl+Alt+0") == (MOD_CTRL_ALT, ord("0"))
    assert close_guard.parse_hotkey("Ctrl+Alt+F5") == (MOD_CTRL_ALT, 0x70 + 4)
    want = MOD_CTRL_ALT | close_guard.MOD_SHIFT
    assert close_guard.parse_hotkey("Ctrl+Shift+Alt+Q") == (want, ord("Q"))
    # 段间空格宽容：手输形态与无空格同义
    assert close_guard.parse_hotkey("Ctrl + Alt + A") == (MOD_CTRL_ALT, ord("A"))


def test_parse_invalid_matrix():
    for bad in (None, "", 123, "Ctrl+A", "Alt+A", "Shift+A", "Ctrl",
                "Ctrl+Alt", "Ctrl+Alt+A+B", "Ctrl+Alt+F13", "Ctrl+Alt+F0",
                "Ctrl+Alt+!", "Ctrl+Alt+中"):
        assert close_guard.parse_hotkey(bad) is None, repr(bad)


def test_current_toggle_spec_default_and_disabled(app_client):
    import json as _json
    import core.config_store as cs
    c, cfg_path = app_client
    # 磁盘缺键 → 默认
    assert close_guard._current_toggle_spec() == "Ctrl+Alt+Space"
    # 显式关闭（空串）必须读得出——这是「关」与「默认开」的分界
    p = _json.loads(cfg_path.read_text(encoding="utf-8"))
    p.setdefault("ui", {})["toggle_hotkey"] = ""
    cfg_path.write_text(_json.dumps(p), encoding="utf-8")
    cs._CFG_CACHE["cfg"] = None
    try:
        assert close_guard._current_toggle_spec() == ""
    finally:
        cs._CFG_CACHE["cfg"] = None


def test_apply_noop_without_hooked_window():
    """主窗未挂钩（测试/headless）：必须 no-op 返回 False，绝不碰 Win32。"""
    close_guard._toggle_reg["hwnd"] = None
    close_guard._toggle_reg["vk"] = None
    assert close_guard.apply_toggle_hotkey_change() is False


def test_route_save_valid_and_read_back(app_client):
    c, _ = app_client
    r = c.post("/api/config", json={"toggle_hotkey": "Ctrl+Alt+K"})
    assert r.status_code == 200, r.get_json()
    d = c.get("/api/config").get_json()
    assert (d.get("ui") or {}).get("toggle_hotkey") == "Ctrl+Alt+K"


def test_route_empty_string_disables(app_client):
    c, _ = app_client
    # 先存一个非空值，再存空串 = 显式关闭；空串必须**留在磁盘**（删键会回落默认开）
    assert c.post("/api/config", json={"toggle_hotkey": "Ctrl+Alt+M"}).status_code == 200
    r = c.post("/api/config", json={"toggle_hotkey": ""})
    assert r.status_code == 200, r.get_json()
    d = c.get("/api/config").get_json()
    assert (d.get("ui") or {}).get("toggle_hotkey") == ""


def test_route_invalid_spec_400(app_client):
    c, _ = app_client
    for bad in ("Ctrl+A", "Alt+A", "Ctrl+Alt+A+B", "Ctrl+Alt+F13", "Ctrl+Alt+!", "Ctrl+Alt"):
        r = c.post("/api/config", json={"toggle_hotkey": bad})
        assert r.status_code == 400, (bad, r.status_code)
        assert r.get_json()["ok"] is False
