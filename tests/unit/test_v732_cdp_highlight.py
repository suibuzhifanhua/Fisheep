# -*- coding: utf-8 -*-
"""v1.0.732 — CDP 接管窗口高亮边框（OS 层）回归。

用户反馈：「cdp接管的浏览器没有高亮边框 优化一下提高识别度」。
v1.0.727 只在**绑定弹窗卡片**上加过亮色边框（界面 UI），这次要的是桌面上
那个被接管的浏览器 / 企微窗口本身。实现走 Win32 边框窗口（v1.0.733 起为
owned 随窗跟随、不再置顶）而不是 CDP 注入
页面 CSS —— 后者会被 Page.captureScreenshot 截进每一张图，污染 OCR / LLM 视觉。
"""
import io
import json
import os
import sys

import pytest

import core.config_store as cs
import bridge as wb
import bridge.highlight as H

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """守护线程状态是模块级全局，逐例隔离，避免串味。

    v1.0.733：_sync_tick 新增「边框窗口有效性兜底 + owner 绑定」，纯逻辑用例
    用的是假句柄，这里固定成恒等桩，避免真的去创建/销毁 Win32 窗口。
    """
    H._OVERRIDE["enabled"] = None
    monkeypatch.setattr(H, "_ensure_frames", lambda frames: frames)
    monkeypatch.setattr(H, "_bind_owner", lambda frames, hwnd: len(frames))
    with H._STATE_LOCK:
        H._STATE.update({"thread": None, "stop": None, "frames": [],
                         "hwnd": None, "owner_hwnd": None, "port": None,
                         "visible": False, "last_rect": None,
                         "last_rescan": 0.0, "rescan_fast_until": 0.0,
                         "last_width": None, "last_move_ts": 0.0,
                         "last_cfg": 0.0, "cfg": {},
                         "last_enabled": False, "last_error": ""})
    yield
    H._OVERRIDE["enabled"] = None


# ---------------------------------------------------------------- 纯逻辑
def test_parse_color_is_bgr_and_falls_back():
    assert H.parse_color("#FF9500") == 0x0095FF      # COLORREF = 0x00BBGGRR
    assert H.parse_color("#ff9500") == 0x0095FF
    assert H.parse_color("#000000") == 0
    assert H.parse_color("#FFFFFF") == 0x00FFFFFF
    for bad in ("", None, "orange", "#FFF", "#GGGGGG", 123, "#1234567"):
        assert H.parse_color(bad) == H.parse_color(H.HIGHLIGHT_COLOR_DEFAULT)


def test_clamp_width_bounds():
    assert H.clamp_width(8) == 8
    assert H.clamp_width("12") == 12
    assert H.clamp_width(1) == H.HIGHLIGHT_WIDTH_MIN
    assert H.clamp_width(999) == H.HIGHLIGHT_WIDTH_MAX
    assert H.clamp_width(None) == H.HIGHLIGHT_WIDTH_DEFAULT
    assert H.clamp_width("x") == H.HIGHLIGHT_WIDTH_DEFAULT


def test_frame_rects_four_edges_plus_centered_label():
    r = H.frame_rects((100, 100, 900, 700), 8)
    assert len(r) == 5
    assert r[0] == (100, 100, 800, 8)         # 上边整宽
    assert r[1] == (100, 692, 800, 8)         # 下边
    assert r[2] == (100, 100, 8, 600)         # 左边整高
    assert r[3] == (892, 100, 8, 600)         # 右边
    assert r[4][2] == H.HIGHLIGHT_LABEL_W     # 铭牌宽度
    assert r[4][0] == 370                     # 居中：100 + (800-260)//2
    assert r[4][3] == H.HIGHLIGHT_LABEL_H


def test_frame_rects_supports_negative_multi_monitor_coords():
    r = H.frame_rects((-1920, -100, -920, 500), 6)
    assert r[0] == (-1920, -100, 1000, 6)
    assert r[1][1] == 494
    assert r[3][0] == -926


def test_frame_rects_drops_label_when_window_too_narrow():
    assert len(H.frame_rects((0, 0, 60, 500), 8)) == 4


def test_highlight_enabled_default_and_runtime_override():
    assert H.highlight_enabled({"wecom": {}}) is True
    assert H.highlight_enabled({"wecom": {"cdp_highlight": False}}) is False
    assert H.set_highlight_enabled(False) is False
    assert H.highlight_enabled({"wecom": {"cdp_highlight": True}}) is False
    assert H.highlight_state()["override"] is False
    assert H.set_highlight_enabled(None) is None
    assert H.highlight_enabled({"wecom": {"cdp_highlight": True}}) is True


# ---------------------------------------------------------------- 配置与接口
def test_default_config_has_highlight_keys():
    w = cs.DEFAULT_CONFIG["wecom"]
    assert w["cdp_highlight"] is True
    assert w["cdp_highlight_color"] == H.HIGHLIGHT_COLOR_DEFAULT
    assert w["cdp_highlight_width"] == H.HIGHLIGHT_WIDTH_DEFAULT


def test_config_endpoint_persists_highlight_options(app_client):
    c, _cfg = app_client
    r = c.post("/api/config", json={"cdp_highlight": False,
                                    "cdp_highlight_color": "#00A0FF",
                                    "cdp_highlight_width": 12})
    assert r.status_code == 200
    w = cs.load_config()["wecom"]
    assert w["cdp_highlight"] is False
    assert w["cdp_highlight_color"] == "#00A0FF"
    assert w["cdp_highlight_width"] == 12


def test_config_endpoint_ignores_dirty_highlight_values(app_client):
    c, _cfg = app_client
    c.post("/api/config", json={"cdp_highlight_color": "red",
                                "cdp_highlight_width": "x"})
    w = cs.load_config()["wecom"]
    assert w["cdp_highlight_color"] == H.HIGHLIGHT_COLOR_DEFAULT
    assert w["cdp_highlight_width"] == H.HIGHLIGHT_WIDTH_DEFAULT
    c.post("/api/config", json={"cdp_highlight_width": 999})
    assert cs.load_config()["wecom"]["cdp_highlight_width"] == H.HIGHLIGHT_WIDTH_MAX
    c.post("/api/config", json={"cdp_highlight_width": -1})
    assert cs.load_config()["wecom"]["cdp_highlight_width"] == H.HIGHLIGHT_WIDTH_MIN
    # 脏值不该把同批的其它设置挡掉
    c.post("/api/config", json={"cdp_highlight_color": "red", "auto_launch": False})
    assert cs.load_config()["wecom"]["auto_launch"] is False


def test_runtime_toggle_invalidates_config_cache():
    """真实端到端抓到过：_read_settings 有 1.5s 节流，关开关不清缓存会挂着边框。"""
    with H._STATE_LOCK:
        H._STATE["last_cfg"] = 12345.0
    H.set_highlight_enabled(False)
    assert H._STATE["last_cfg"] == 0.0
    assert H.set_highlight_enabled(None) is None
    assert H.highlight_config_changed() is True


def test_config_endpoint_notifies_highlight_thread(app_client, monkeypatch):
    called = []
    monkeypatch.setattr(wb, "highlight_config_changed",
                        lambda: called.append(1) or True)
    c, _cfg = app_client
    r = c.post("/api/config", json={"cdp_highlight": False})
    assert r.status_code == 200
    assert called, "保存后应通知守护线程立即重读配置"


def test_bind_status_reports_highlight_option(monkeypatch):
    import routes.wecom as RW
    monkeypatch.setattr(RW, "_BS_CACHE", {"ts": 0.0, "data": None})
    monkeypatch.setattr(RW.wb, "find_wecom_pids", lambda: [])
    monkeypatch.setattr(RW.wb, "cdp_up", lambda port: False)
    monkeypatch.setattr(RW.wb, "list_targets", lambda port: [])
    cfg = {"wecom": {"cdp_port": 9222, "cdp_highlight": False,
                     "cdp_highlight_color": "#123456", "cdp_highlight_width": 14}}
    d = RW.bind_status(cfg)
    assert d["cdp_highlight"] is False
    assert d["cdp_highlight_color"] == "#123456"
    assert d["cdp_highlight_width"] == 14
    assert d["bound"] is False


# ---------------------------------------------------------------- 接线源码锁
def test_facade_exports_highlight_api():
    for name in ("start_highlight", "stop_highlight", "highlight_state",
                 "highlight_enabled", "set_highlight_enabled",
                 "highlight_config_changed", "resolve_cdp_owner_hwnd",
                 "frame_rects", "parse_color",
                 "HIGHLIGHT_COLOR_DEFAULT", "HIGHLIGHT_WIDTH_DEFAULT"):
        assert hasattr(wb, name), name
        assert name in wb.__all__, name
    assert wb.highlight is H


def test_daemon_registered_in_backend():
    src = io.open(os.path.join(ROOT, "backend.py"), encoding="utf-8").read()
    assert '_daemon.register("cdp-highlight"' in src
    assert "_start_cdp_highlight_daemon" in src
    from desktop import daemons
    assert callable(daemons._start_cdp_highlight_daemon)
    dsrc = io.open(os.path.join(ROOT, "desktop", "daemons.py"), encoding="utf-8").read()
    assert "wb.start_highlight(" in dsrc


def test_frontend_has_highlight_toggle():
    html = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    js = io.open(os.path.join(ROOT, "js", "05-connectors-status.js"),
                 encoding="utf-8").read()
    assert 'id="opt_cdp_highlight"' in html
    assert "cdp_highlight: _ch ? _ch.checked : undefined" in js
    assert "w.cdp_highlight !== false" in js          # 状态轮询回显（默认开）


def test_no_cdp_css_injection_in_highlight_module():
    """红线：绝不能往页面注入 CSS 边框——会被 Page.captureScreenshot 截进图。"""
    src = io.open(os.path.join(ROOT, "bridge", "highlight.py"),
                  encoding="utf-8").read()
    # 只查「真的会往页面注入/评估」的调用形式；注释里为解释红线提到
    # captureScreenshot 是必要的文档，不算违规。
    for banned in ("addScriptToEvaluateOnNewDocument", "Runtime.evaluate",
                   "document.body.style", "outline:", "wb._cdp_eval(",
                   "wb._cdp_command(", "Page.addScript"):
        assert banned not in src, f"高亮边框不该依赖页面注入：{banned}"


# ---------------------------------------------------------------- 属主定位
def test_resolve_owner_without_wmi_result_never_guesses_window(monkeypatch):
    monkeypatch.setattr(H, "_cdp_owner_pids", lambda port: set())
    called = []
    monkeypatch.setattr(H, "_visible_windows_for_pids",
                        lambda pids: called.append(pids) or
                        [(1, 9999, (0, 0, 800, 600))])
    hwnd, info = H.resolve_cdp_owner_hwnd(9222)
    assert hwnd is None
    assert called == [], "没有属主进程却去枚举窗口 = 可能框错用户日常浏览器"
    assert info["pids"] == []
    assert "reason" in info


def test_resolve_owner_picks_largest_visible_window(monkeypatch):
    monkeypatch.setattr(H, "_cdp_owner_pids", lambda port: {11, 22})
    monkeypatch.setattr(H, "_visible_windows_for_pids",
                        lambda pids: [(101, 1000, (0, 0, 100, 100)),
                                      (202, 9999, (0, 0, 800, 600)),
                                      (303, 5000, (0, 0, 400, 300))])
    hwnd, info = H.resolve_cdp_owner_hwnd(9222)
    assert hwnd == 202
    assert info["windows"] == 3
    assert info["pids"] == [11, 22]
    assert info["rect"] == (0, 0, 800, 600)


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


def test_cdp_owner_pids_parses_both_commandline_forms(monkeypatch):
    payload = json.dumps([
        {"ProcessId": 111, "CommandLine": "msedge.exe --remote-debugging-port=9222 --user-data-dir=x"},
        {"ProcessId": 222, "CommandLine": "chrome.exe --remote-debugging-port 9222"},
        {"ProcessId": 333, "CommandLine": "msedge.exe --remote-debugging-port=9333"},
        {"ProcessId": 444, "CommandLine": "explorer.exe"},
    ]).encode("utf-8")
    monkeypatch.setattr(H, "_IS_WIN32", True)
    monkeypatch.setattr(H.subprocess, "run",
                        lambda *a, **k: _FakeCompleted(payload))
    assert H._cdp_owner_pids(9222) == {111, 222}
    assert H._cdp_owner_pids(9333) == {333}
    assert H._cdp_owner_pids(9444) == set()


def test_cdp_owner_pids_wmi_failure_is_fail_soft(monkeypatch):
    monkeypatch.setattr(H, "_IS_WIN32", True)

    def _boom(*_a, **_k):
        raise OSError("powershell blocked")

    monkeypatch.setattr(H.subprocess, "run", _boom)
    assert H._cdp_owner_pids(9222) == set()


# ---------------------------------------------------------------- 守护线程
def test_start_highlight_non_windows_is_noop(monkeypatch):
    monkeypatch.setattr(H, "_IS_WIN32", False)
    assert H.start_highlight(9222) is None
    assert H.highlight_state()["running"] is False


def test_sync_tick_shows_follows_and_hides(monkeypatch):
    calls = {"place": [], "show": []}
    settings = {"enabled": True, "port": 9222,
                "color": 0x0095FF, "width": 8}
    monkeypatch.setattr(H, "_read_settings", lambda force=False: dict(settings))
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(H, "_get_target_rect", lambda hwnd: (0, 0, 1000, 700))
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd",
                        lambda port: (4242, {"port": port}))
    monkeypatch.setattr(H, "_place_frames",
                        lambda frames, rect, width:
                        calls["place"].append((frames, rect, width)))
    monkeypatch.setattr(H, "_show_frames",
                        lambda frames, show: calls["show"].append((frames, show)))
    with H._STATE_LOCK:
        H._STATE["frames"] = [1, 2, 3, 4, 5]
        H._STATE["last_rescan"] = 0.0

    H._sync_tick()
    assert H._STATE["visible"] is True
    assert calls["place"][-1][1] == (0, 0, 1000, 700)
    assert any(show is True for _f, show in calls["show"])

    # 位置没变不重复 SetWindowPos（不闪、不反复抢 Z 序）
    n = len(calls["place"])
    H._sync_tick()
    assert len(calls["place"]) == n

    # 位置变化才重新摆放
    monkeypatch.setattr(H, "_get_target_rect", lambda hwnd: (10, 20, 1010, 720))
    H._sync_tick()
    assert len(calls["place"]) == n + 1

    # 关开关 → 立刻隐藏
    settings["enabled"] = False
    H._sync_tick()
    assert H._STATE["visible"] is False
    assert calls["show"][-1][1] is False


def test_sync_tick_hides_when_target_window_disappears(monkeypatch):
    shown = []
    monkeypatch.setattr(H, "_read_settings",
                        lambda force=False: {"enabled": True, "port": 9222,
                                             "color": 0x0095FF, "width": 8})
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: False)
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd", lambda port: (None, {"reason": "gone"}))
    monkeypatch.setattr(H, "_show_frames",
                        lambda frames, show: shown.append(show))
    with H._STATE_LOCK:
        H._STATE["frames"] = [1, 2, 3, 4, 5]
        H._STATE["visible"] = True
        H._STATE["last_rescan"] = 0.0
    H._sync_tick()
    assert H._STATE["visible"] is False
    assert shown[-1] is False
    assert "gone" in H._STATE["last_error"]


def test_sync_tick_hides_on_too_small_rect(monkeypatch):
    monkeypatch.setattr(H, "_read_settings",
                        lambda force=False: {"enabled": True, "port": 9222,
                                             "color": 0x0095FF, "width": 8})
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: True)
    monkeypatch.setattr(H, "_get_target_rect", lambda hwnd: (0, 0, 100, 100))
    shown = []
    monkeypatch.setattr(H, "_show_frames", lambda frames, show: shown.append(show))
    with H._STATE_LOCK:
        H._STATE["frames"] = [1, 2, 3, 4, 5]
        H._STATE["hwnd"] = 4242
        H._STATE["visible"] = True
        H._STATE["last_enabled"] = True   # 消除开关上升沿，专测「rect 太小」分支
    H._sync_tick()
    assert shown[-1] is False
    assert H._STATE["visible"] is False


# ---------------------------------------------------------------- 真实 Win32 集成
@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Win32 only")
def test_real_win32_window_roundtrip(monkeypatch):
    """真创建一个可见顶层窗口替身，验证「按 PID 定位 → 摆放 5 个边框 → 销毁」
    全链路，不只是 mock。当前会话无交互桌面时跳过。"""
    import ctypes
    from ctypes import wintypes
    u32 = H._ensure_api()
    if u32 is None or not H._ensure_classes(H.parse_color("#FF9500")):
        pytest.skip("Win32 API 不可用")
    hinst = H._K32.GetModuleHandleW(None)
    h = u32.CreateWindowExW(0, H._FRAME_CLASS, None, H._WS_POPUP,
                            50, 50, 640, 480, None, None, hinst, None)
    if not h:
        pytest.skip("当前会话无法创建 Win32 窗口（无交互桌面）")
    try:
        u32.ShowWindow(wintypes.HWND(int(h)), H._SW_SHOWNOACTIVATE)
        monkeypatch.setattr(H, "_cdp_owner_pids", lambda port: {os.getpid()})
        found, info = H.resolve_cdp_owner_hwnd(9222)
        assert found is not None, info
        frames = H._create_frames()
        assert len(frames) == 5
        H._place_frames(frames, info["rect"], 8)
        H._show_frames(frames, True)
        rects = H.frame_rects(info["rect"], 8)
        assert len(rects) == 5
        assert rects[0][2] >= 240
        H._destroy_frames(frames)
    finally:
        u32.DestroyWindow(wintypes.HWND(int(h)))
