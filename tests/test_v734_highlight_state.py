# -*- coding: utf-8 -*-
"""v1.0.734 — CDP 接管状态识别 / 颜色粗细 / 企微排除 / 跟随提速 回归。

用户 2026-11-20 四项反馈：
  ① 有接管的黄色框但软件还是显示未绑定 → 优化状态识别
  ② cdp 接管框可以在设置中选择颜色和粗细
  ③ 高亮框仅对除企业微信以外的软件有效，企业微信不做边框
  ④ 提高边框跟随速度（拖动网页时框跟不上）

核心设计：状态与高亮框**共用同一份接管证据**（守护线程的 owner_hwnd），
不再「框只凭命令行、界面只信 HTTP」各说各话。
"""
import ctypes
import io
import json
import os
import sys
import time

import pytest

import wecom_bridge as wb
import wecom_bridge.highlight as H
import routes.wecom as RW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HSRC = io.open(os.path.join(ROOT, "wecom_bridge", "highlight.py"),
               encoding="utf-8").read()
JS = io.open(os.path.join(ROOT, "js", "05-connectors-status.js"),
             encoding="utf-8").read()
HTML = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


@pytest.fixture(autouse=True)
def _reset_state():
    """模块级全局状态逐例隔离（含 v1.0.734 新增键与当前色）。"""
    old_color = H._CURRENT_COLORREF
    H._OVERRIDE["enabled"] = None
    with H._STATE_LOCK:
        H._STATE.update({"thread": None, "stop": None, "frames": [],
                         "hwnd": None, "owner_hwnd": None, "port": None,
                         "visible": False, "last_rect": None,
                         "last_rescan": 0.0, "rescan_fast_until": 0.0,
                         "last_width": None, "last_move_ts": 0.0,
                         "last_cfg": 0.0, "cfg": {},
                         "last_enabled": False, "last_error": ""})
    yield
    H._CURRENT_COLORREF = old_color
    H._OVERRIDE["enabled"] = None


# ---------------------------------------------------------------- ① 状态识别
def _bind_status(monkeypatch, up, attached, *, evidence_raises=False):
    """把 bind_status 的所有外部探测打桩，只测「三态融合」这一段逻辑。"""
    monkeypatch.setattr(RW, "_BS_CACHE", {"ts": 0.0, "data": None})
    monkeypatch.setattr(RW.wb, "find_wecom_pids", lambda: [])
    monkeypatch.setattr(RW.wb, "cdp_up", lambda port: up)
    monkeypatch.setattr(RW.wb, "list_targets", lambda port: [])
    monkeypatch.setattr(RW.wb, "window_list_summary", lambda port: [])
    monkeypatch.setattr(RW.wb, "windows_signature", lambda targets: "sig")
    if evidence_raises:
        def _boom():
            raise OSError("old bundle without facade")
        monkeypatch.setattr(RW.wb, "cdp_owner_attached", _boom)
    else:
        monkeypatch.setattr(RW.wb, "cdp_owner_attached", lambda: attached)
    return RW.bind_status({"wecom": {"cdp_port": 9222}})


def test_bound_true_is_bound_detail(monkeypatch):
    d = _bind_status(monkeypatch, up=True, attached=False)
    assert d["bound"] is True
    assert d["attached"] is True
    assert d["bound_detail"] == "bound"


def test_owner_attached_but_cdp_down_is_middle_state(monkeypatch):
    """用户截图场景：黄框亮着（进程在）/ CDP 端点超时。必须显示「已接管·连接中」，
    且 bound 仍为 False —— 绝不把连不上的端点说成可用，否则 agent 会去操作页面。"""
    d = _bind_status(monkeypatch, up=False, attached=True)
    assert d["bound"] is False
    assert d["attached"] is True
    assert d["bound_detail"] == "attaching"


def test_no_owner_no_cdp_is_unbound(monkeypatch):
    d = _bind_status(monkeypatch, up=False, attached=False)
    assert d["bound"] is False
    assert d["attached"] is False
    assert d["bound_detail"] == "unbound"


def test_evidence_facade_failure_is_fail_closed(monkeypatch):
    """门面不可用（老包 / 非 Win32）时保守降级，不让 /api/status 500。"""
    d = _bind_status(monkeypatch, up=False, attached=True, evidence_raises=True)
    assert d["bound"] is False
    assert d["attached"] is False
    assert d["bound_detail"] == "unbound"


def test_cdp_owner_attached_reads_only_in_memory_state():
    """纯内存读取：owner_hwnd + visible 才算「框亮着」。最小化时 visible=False。"""
    assert H.cdp_owner_attached() is False
    with H._STATE_LOCK:
        H._STATE["owner_hwnd"] = 4242
    assert H.cdp_owner_attached() is False       # 还没显示
    with H._STATE_LOCK:
        H._STATE["visible"] = True
    assert H.cdp_owner_attached() is True
    st = H.highlight_state()                     # 不该死锁（Lock 不可重入）
    assert st["attached"] is True
    assert st["owner_hwnd"] == 4242


def test_facade_exports_v734_api():
    for name in ("cdp_owner_attached", "apply_highlight_color",
                 "HIGHLIGHT_INTERVAL_ACTIVE_S"):
        assert hasattr(wb, name) and name in wb.__all__, name


# ---------------------------------------------------------------- ② 颜色 / 粗细
class _FakeCompleted:
    def __init__(self, payload):
        self.stdout = payload


def _wmi(payload):
    return lambda *a, **k: _FakeCompleted(payload)


def test_apply_highlight_color_updates_current_and_repaints(monkeypatch):
    calls = []

    class _U:
        def InvalidateRect(self, hwnd, _rc, erase):
            # 生产里 ctypes 把 HWND 交给 Win32；桩只记录值，别对 c_void_p 调 int()
            calls.append((getattr(hwnd, "value", hwnd), bool(erase)))
            return 1

    monkeypatch.setattr(H, "_ensure_api", lambda: _U())
    monkeypatch.setattr(H, "_CLASS_READY", True)
    with H._STATE_LOCK:
        H._STATE["frames"] = [11, 22, 33]
    cr = H.parse_color("#00A0FF")
    assert H.apply_highlight_color(cr) == 3
    assert H._CURRENT_COLORREF == cr
    assert calls == [(11, True), (22, True), (33, True)]


def test_apply_highlight_color_without_win32_only_sets_value(monkeypatch):
    monkeypatch.setattr(H, "_ensure_api", lambda: None)
    cr = H.parse_color("#112233")
    assert H.apply_highlight_color(cr) == 0
    assert H._CURRENT_COLORREF == cr


def test_fill_current_color_uses_current_value(monkeypatch):
    """WM_ERASEBKGND 自绘必须读 _CURRENT_COLORREF，而不是写死的类画刷。"""
    made, filled, deleted = [], [], []

    class _G:
        def CreateSolidBrush(self, cr):
            made.append(int(cr))
            return 0x1234          # restype=c_void_p 实际返回 Python int，不是 ctypes 对象

        def DeleteObject(self, b):
            deleted.append(getattr(b, "value", b))

    class _U:
        def GetClientRect(self, _hwnd, ptr):
            rc = ptr._obj
            rc.left, rc.top, rc.right, rc.bottom = 0, 0, 10, 10
            return 1

        def FillRect(self, hdc, ptr, brush):
            filled.append((getattr(hdc, "value", hdc),
                           getattr(brush, "value", brush)))

    monkeypatch.setattr(H, "_G32", _G())
    cr = H.parse_color("#ABCDEF")
    H._CURRENT_COLORREF = cr
    assert H._fill_current_color(7, 0xDC, _U()) is True
    assert made == [cr]
    assert filled == [(0xDC, 0x1234)]
    assert deleted == [0x1234]


def test_fill_current_color_fails_soft_without_hdc():
    assert H._fill_current_color(7, 0) is False


def test_erase_background_is_handled_by_both_window_procs():
    """红线：颜色不能只靠类画刷（那样改色必须重启）。"""
    assert "_WM_ERASEBKGND" in HSRC
    assert HSRC.count("_fill_current_color(hwnd, wparam, u32)") == 2
    assert "apply_highlight_color" in HSRC
    assert "_loop" in HSRC and "_CURRENT_COLORREF" in HSRC


def test_sync_tick_repositions_when_only_width_changes(monkeypatch):
    """用户改粗细时窗口没动 —— 若只比 rect，线宽根本不会更新。"""
    placed = []
    settings = {"enabled": True, "port": 9222,
                "color": H.parse_color("#FF9500"), "width": 8}
    monkeypatch.setattr(H, "_read_settings", lambda force=False: dict(settings))
    monkeypatch.setattr(H, "_ensure_frames", lambda frames: frames)
    monkeypatch.setattr(H, "_bind_owner", lambda frames, hwnd: len(frames))
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(H, "_get_target_rect", lambda hwnd: (0, 0, 1000, 700))
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd",
                        lambda port: (4242, {"port": port}))
    monkeypatch.setattr(H, "_place_frames",
                        lambda frames, rect, width: placed.append((rect, width)))
    monkeypatch.setattr(H, "_show_frames", lambda frames, show: None)
    with H._STATE_LOCK:
        H._STATE["frames"] = [1, 2, 3, 4, 5]
        H._STATE["last_rescan"] = 0.0

    H._sync_tick()
    assert len(placed) == 1 and placed[0][1] == 8
    H._sync_tick()                       # rect 与 width 都没变：不重摆
    assert len(placed) == 1
    settings["width"] = 14
    H._sync_tick()                       # 只改粗细：必须重摆
    assert len(placed) == 2 and placed[1][1] == 14
    assert H._STATE["last_width"] == 14


def test_frontend_has_color_and_width_controls():
    for needle in ('id="opt_cdp_highlight_color"',
                   'id="opt_cdp_highlight_width"',
                   'id="cdpHlColorNote"', 'id="cdpHlWidthNote"'):
        assert needle in HTML, needle
    for needle in ("cdpHlWidthPreview", "_cdpHlDirty",
                   "payload.cdp_highlight_color", "payload.cdp_highlight_width",
                   "w.cdp_highlight_color", "w.cdp_highlight_width"):
        assert needle in JS, needle


def test_frontend_three_state_status_text():
    for needle in ("bound_detail", "w.attached", "已接管 · 连接中",
                   "dot warn", "已接管·连接中"):
        assert needle in JS, needle


# ---------------------------------------------------------------- ③ 企业微信不做边框
def test_owner_pids_excludes_wecom_and_child_processes(monkeypatch):
    payload = json.dumps([
        {"ProcessId": 1, "Name": "msedge.exe",
         "CommandLine": "msedge.exe --remote-debugging-port=9223"},
        {"ProcessId": 2, "Name": "msedge.exe",
         "CommandLine": "msedge.exe --type=renderer --remote-debugging-port=9223"},
        {"ProcessId": 3, "Name": "WXWork.exe",
         "CommandLine": "WXWork.exe --remote-debugging-port=9223"},
        {"ProcessId": 4, "Name": "企业微信.exe",
         "CommandLine": "企业微信.exe --remote-debugging-port=9223"},
        {"ProcessId": 5, "Name": "WeCom.exe",
         "CommandLine": "WeCom.exe --remote-debugging-port 9223"},
        {"ProcessId": 6, "Name": "chrome.exe",
         "CommandLine": "chrome.exe --remote-debugging-port 9223"},
        {"ProcessId": 7, "Name": "chrome.exe",
         "CommandLine": "chrome.exe --remote-debugging-port=9333"},
    ]).encode("utf-8")
    monkeypatch.setattr(H, "_IS_WIN32", True)
    monkeypatch.setattr(H.subprocess, "run", _wmi(payload))
    assert H._cdp_owner_pids(9223) == {1, 6}
    assert H._cdp_owner_pids(9333) == {7}
    assert H._cdp_owner_pids(9444) == set()


def test_owner_pids_with_name_field_missing_still_allows(monkeypatch):
    """老数据 / 旧 WMI 输出没有 Name 时按「未知」放行，兼容既有契约。"""
    payload = json.dumps([
        {"ProcessId": 111,
         "CommandLine": "msedge.exe --remote-debugging-port=9222 --user-data-dir=x"},
        {"ProcessId": 222, "CommandLine": "chrome.exe --remote-debugging-port 9222"},
    ]).encode("utf-8")
    monkeypatch.setattr(H, "_IS_WIN32", True)
    monkeypatch.setattr(H.subprocess, "run", _wmi(payload))
    assert H._cdp_owner_pids(9222) == {111, 222}


def test_exclude_name_regex_matches_wecom_family_only():
    for name in ("WXWork.exe", "wecom.exe", "WeWork.exe", "qywx.exe", "企业微信.exe"):
        assert H._EXCLUDE_OWNER_NAME_RE.search(name), name
    for name in ("msedge.exe", "chrome.exe", "Fisheep.exe", "explorer.exe", ""):
        assert not H._EXCLUDE_OWNER_NAME_RE.search(name), name
    assert H._CHILD_PROC_RE.search("--type=renderer")
    assert H._CHILD_PROC_RE.search("--type gpu")
    assert not H._CHILD_PROC_RE.search("--remote-debugging-port=9223")


def test_resolve_owner_reports_exclude_wecom_flag(monkeypatch):
    monkeypatch.setattr(H, "_cdp_owner_pids", lambda port: set())
    monkeypatch.setattr(H, "_visible_windows_for_pids", lambda pids: [])
    _hwnd, info = H.resolve_cdp_owner_hwnd(9222)
    assert info["exclude_wecom"] is True
    assert "企业微信" in info["reason"]


# ---------------------------------------------------------------- ④ 跟随提速
def test_adaptive_interval_is_faster_while_moving():
    assert 0 < H.HIGHLIGHT_INTERVAL_ACTIVE_S < H.HIGHLIGHT_INTERVAL_S <= 0.1
    assert H.HIGHLIGHT_MOVE_SETTLE_S > 0
    assert "HIGHLIGHT_INTERVAL_ACTIVE_S if _moving else interval" in HSRC
    assert "last_move_ts" in HSRC


def test_sync_tick_records_move_timestamp(monkeypatch):
    settings = {"enabled": True, "port": 9222,
                "color": H.parse_color("#FF9500"), "width": 8}
    rect = {"v": (0, 0, 1000, 700)}
    monkeypatch.setattr(H, "_read_settings", lambda force=False: dict(settings))
    monkeypatch.setattr(H, "_ensure_frames", lambda frames: frames)
    monkeypatch.setattr(H, "_bind_owner", lambda frames, hwnd: len(frames))
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(H, "_get_target_rect", lambda hwnd: rect["v"])
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd",
                        lambda port: (4242, {"port": port}))
    monkeypatch.setattr(H, "_place_frames", lambda frames, r, width: None)
    monkeypatch.setattr(H, "_show_frames", lambda frames, show: None)
    with H._STATE_LOCK:
        H._STATE["frames"] = [1, 2, 3, 4, 5]
        H._STATE["last_move_ts"] = 0.0
    H._sync_tick()
    assert H._STATE["last_move_ts"] > 0
    first = H._STATE["last_move_ts"]
    H._sync_tick()                      # rect 不变：时间戳不刷新，会退回 idle
    assert H._STATE["last_move_ts"] == first
    time.sleep(0.002)
    rect["v"] = (10, 20, 1010, 720)
    H._sync_tick()
    assert H._STATE["last_move_ts"] > first


# ---------------------------------------------------------------- 真实 Win32
@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Win32 only")
def test_real_win32_color_change_is_visible_without_restart():
    """真读像素：改色后 GetPixel 必须读到新 COLORREF。

    这是「设置里改颜色即时生效」的硬证据 —— 旧版类画刷只注册一次，
    不重启根本换不了色。
    """
    from ctypes import wintypes
    u32 = H._ensure_api()
    if u32 is None:
        pytest.skip("Win32 API 不可用")
    u32.GetDC.argtypes = [wintypes.HWND]
    u32.GetDC.restype = ctypes.c_void_p
    u32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    u32.ReleaseDC.restype = ctypes.c_int
    H._G32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    H._G32.GetPixel.restype = ctypes.c_uint

    def _pump(n=40):
        msg = H.MSG()
        for _ in range(n):
            if not u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, H._PM_REMOVE):
                time.sleep(0.004)
                continue
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    cr0 = H.parse_color("#FF9500")
    if not H._ensure_classes(cr0):
        pytest.skip("窗口类注册失败")
    hinst = H._K32.GetModuleHandleW(None)
    h = u32.CreateWindowExW(0, H._FRAME_CLASS, None, H._WS_POPUP,
                            100, 100, 200, 120, None, None, hinst, None)
    if not h:
        pytest.skip("当前会话无法创建 Win32 窗口（无交互桌面）")

    def _pixel():
        hdc = u32.GetDC(wintypes.HWND(int(h)))
        try:
            return int(H._G32.GetPixel(ctypes.c_void_p(int(hdc)), 5, 5)) & 0xFFFFFF
        finally:
            u32.ReleaseDC(wintypes.HWND(int(h)), ctypes.c_void_p(int(hdc)))

    try:
        u32.ShowWindow(wintypes.HWND(int(h)), H._SW_SHOWNOACTIVATE)
        with H._STATE_LOCK:
            H._STATE["frames"] = [int(h)]
        u32.InvalidateRect(wintypes.HWND(int(h)), None, True)
        _pump()
        assert _pixel() == cr0, "初始色不是配置色"

        cr1 = H.parse_color("#00A0FF")
        assert H.apply_highlight_color(cr1) == 1
        _pump()
        assert _pixel() == cr1, "改色后像素没变（动态画刷没生效）"
    finally:
        with H._STATE_LOCK:
            H._STATE["frames"] = []
        u32.DestroyWindow(wintypes.HWND(int(h)))
