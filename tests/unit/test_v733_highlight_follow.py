# -*- coding: utf-8 -*-
"""v1.0.733 — 高亮边框「跟着窗口走，而不是顶置」回归。

用户 2026-11-20 反馈：「高亮应当跟着窗口走 而不是顶置」。v1.0.732 给边框窗口
加了置顶扩展样式、且每次摆位都强行提到最顶层，导致被接管的浏览器被主窗口
遮住后，橙线仍穿透盖在主窗口内容上（截图里把工作台界面切成两半）。

修法：5 个边框窗口用 GWLP_HWNDPARENT 挂成目标窗口的 owned window，Z 序由
Windows 管理；摆位一律 SWP_NOZORDER；跟随 tick 12.5Hz；矩形取 DWM 可见边界。
"""
import ctypes
import io
import os
import sys

import pytest

import bridge as wb
import bridge.highlight as H

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = io.open(os.path.join(ROOT, "bridge", "highlight.py"),
              encoding="utf-8").read()


@pytest.fixture(autouse=True)
def _reset_state():
    """模块级全局状态逐例隔离。

    刻意不在这里打 _bind_owner / _ensure_frames / _read_owner 的桩：本文件
    大量用例测的就是这几个函数本身。只有 _tick_stubs() 的纯逻辑跟随用例
    才固定掉它们，避免假句柄触发真实 Win32 窗口创建。
    """
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
    H._OVERRIDE["enabled"] = None


# ---------------------------------------------------------------- 源码红线锁
def test_no_topmost_anywhere_in_highlight_module():
    """最硬的一条：源码里不许再出现任何置顶样式 / 置顶摆位。"""
    assert "TOPMOST" not in SRC, "边框窗口不得再有任何置顶实现"
    assert "_HWND_TOPMOST" not in SRC
    assert "_WS_EX_TOPMOST" not in SRC
    assert "_SWP_NOZORDER" in SRC
    assert "_GWLP_HWNDPARENT" in SRC
    assert "_bind_owner" in SRC
    assert "DwmGetWindowAttribute" in SRC


def test_ex_style_keeps_click_through_and_no_activate_but_drops_topmost():
    assert not (H._EX_STYLE & 0x00000008), "WS_EX_TOPMOST 位必须清掉"
    assert H._EX_STYLE & H._WS_EX_TRANSPARENT
    assert H._EX_STYLE & H._WS_EX_TOOLWINDOW
    assert H._EX_STYLE & H._WS_EX_NOACTIVATE


def test_place_frames_never_touches_z_order():
    assert "SWP_NOZORDER" in SRC
    assert "SetWindowPos(wintypes.HWND(int(h)), wintypes.HWND(0)" in SRC
    # 两处摆位（4 条边 + 铭牌）都必须带 NOZORDER
    assert SRC.count("_SWP_NOACTIVATE | _SWP_SHOWWINDOW | _SWP_NOZORDER") == 2


def test_follow_interval_is_tight_enough_for_dragging():
    assert H.HIGHLIGHT_INTERVAL_S <= 0.1
    assert H.HIGHLIGHT_INTERVAL_S > 0


# ---------------------------------------------------------------- owner 绑定
class _FakeU32:
    def __init__(self):
        self.calls = []
        self.is_window = True

    def SetWindowLongPtrW(self, hwnd, index, owner):
        self.calls.append((getattr(hwnd, "value", hwnd), index, owner))
        return 0

    def IsWindow(self, hwnd):
        return self.is_window


def test_bind_owner_uses_gwlp_hwndparent_for_every_frame(monkeypatch):
    fake = _FakeU32()
    monkeypatch.setattr(H, "_ensure_api", lambda: fake)
    assert H._bind_owner([11, 22, 33], 4242) == 3
    assert len(fake.calls) == 3
    assert all(c[1] == H._GWLP_HWNDPARENT and c[2] == 4242
               for c in fake.calls)


def test_bind_owner_skips_when_no_target_or_no_frames(monkeypatch):
    fake = _FakeU32()
    monkeypatch.setattr(H, "_ensure_api", lambda: fake)
    assert H._bind_owner([11, 22], None) == 0
    assert H._bind_owner([], 4242) == 0
    assert fake.calls == []


def test_bind_owner_is_fail_soft_on_win32_error(monkeypatch):
    class _Boom(_FakeU32):
        def SetWindowLongPtrW(self, *_a):
            raise OSError("UIPI blocked")

    monkeypatch.setattr(H, "_ensure_api", lambda: _Boom())
    assert H._bind_owner([11, 22], 4242) == 0       # 不抛、不退化成置顶


def test_bind_owner_without_win32_is_noop(monkeypatch):
    monkeypatch.setattr(H, "_ensure_api", lambda: None)
    assert H._bind_owner([11], 4242) == 0


def test_read_owner_returns_bound_target(monkeypatch):
    class _Probe(_FakeU32):
        def GetWindowLongPtrW(self, _hwnd, _index):
            return 4242

    monkeypatch.setattr(H, "_ensure_api", lambda: _Probe())
    assert H._read_owner(7) == 4242


def test_frames_valid_and_ensure_frames_rebuild(monkeypatch):
    destroyed = []
    monkeypatch.setattr(H, "_destroy_frames",
                        lambda frames: destroyed.append(list(frames)))
    monkeypatch.setattr(H, "_create_frames", lambda: [101, 102, 103, 104, 105])
    monkeypatch.setattr(H, "_frames_valid", lambda frames: False)
    with H._STATE_LOCK:
        H._STATE["owner_hwnd"] = 4242
        H._STATE["last_rect"] = (0, 0, 100, 100)
    fresh = H._ensure_frames([1, 2, 3, 4, 5])
    assert fresh == [101, 102, 103, 104, 105]
    assert destroyed == [[1, 2, 3, 4, 5]]
    assert H._STATE["frames"] == fresh
    assert H._STATE["owner_hwnd"] is None
    assert H._STATE["last_rect"] is None

    # 有效时不重建
    monkeypatch.setattr(H, "_frames_valid", lambda frames: True)
    assert H._ensure_frames(fresh) == fresh
    assert len(destroyed) == 1


def test_frames_valid_checks_every_hwnd(monkeypatch):
    fake = _FakeU32()
    monkeypatch.setattr(H, "_ensure_api", lambda: fake)
    assert H._frames_valid([11, 22]) is True
    fake.is_window = False
    assert H._frames_valid([11, 22]) is False
    assert H._frames_valid([]) is False


# ---------------------------------------------------------------- 可见边界
class _FakeDwm:
    def __init__(self, hr=0, rect=(10, 20, 810, 620), boom=False):
        self.hr, self.rect, self.boom = hr, rect, boom

    def DwmGetWindowAttribute(self, _hwnd, _attr, ptr, _size):
        if self.boom:
            raise OSError("dwmapi gone")
        if self.hr != 0:
            return self.hr
        rc = ctypes.cast(ptr, ctypes.POINTER(H.RECT)).contents
        rc.left, rc.top, rc.right, rc.bottom = self.rect
        return 0


class _FakeRectU32:
    def GetWindowRect(self, _hwnd, ptr):
        rc = ptr._obj
        rc.left, rc.top, rc.right, rc.bottom = 100, 100, 900, 700
        return 1


def test_get_target_rect_prefers_dwm_visible_bounds(monkeypatch):
    monkeypatch.setattr(H, "_ensure_api", lambda: _FakeRectU32())
    monkeypatch.setattr(H, "_DWM", _FakeDwm())
    assert H._get_target_rect(4242) == (10, 20, 810, 620)


def test_get_target_rect_falls_back_when_dwm_fails(monkeypatch):
    monkeypatch.setattr(H, "_ensure_api", lambda: _FakeRectU32())
    monkeypatch.setattr(H, "_DWM", _FakeDwm(hr=-2147024809))
    assert H._get_target_rect(4242) == (100, 100, 900, 700)
    monkeypatch.setattr(H, "_DWM", _FakeDwm(boom=True))
    assert H._get_target_rect(4242) == (100, 100, 900, 700)
    monkeypatch.setattr(H, "_DWM", None)
    assert H._get_target_rect(4242) == (100, 100, 900, 700)


def test_get_target_rect_without_win32_returns_none(monkeypatch):
    monkeypatch.setattr(H, "_ensure_api", lambda: None)
    assert H._get_target_rect(4242) is None


# ---------------------------------------------------------------- 跟随 tick
def _tick_stubs(monkeypatch, found_hwnd=4242):
    binds = []
    # 纯逻辑用例用假句柄，固定掉窗口层，避免真的创建/销毁 Win32 窗口。
    monkeypatch.setattr(H, "_ensure_frames", lambda frames: frames)
    monkeypatch.setattr(H, "_bind_owner",
                        lambda frames, hwnd: binds.append(hwnd) or len(frames))
    settings = {"enabled": True, "port": 9222, "color": 0x0095FF, "width": 8}
    monkeypatch.setattr(H, "_read_settings", lambda force=False: dict(settings))
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(H, "_get_target_rect", lambda hwnd: (0, 0, 1000, 700))
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd",
                        lambda port: (found_hwnd, {"port": port}))
    monkeypatch.setattr(H, "_place_frames", lambda frames, rect, width: None)
    monkeypatch.setattr(H, "_show_frames", lambda frames, show: None)
    with H._STATE_LOCK:
        H._STATE["frames"] = [1, 2, 3, 4, 5]
        H._STATE["last_rescan"] = 0.0
    return binds


def test_sync_tick_binds_owner_once_and_rebinds_on_target_change(monkeypatch):
    binds = _tick_stubs(monkeypatch)
    H._sync_tick()
    assert binds == [4242]
    assert H._STATE["owner_hwnd"] == 4242
    assert H._STATE["visible"] is True

    # rect 不变：不重摆、不重绑（12.5Hz 也不能反复抢 Z 序）
    H._sync_tick()
    assert binds == [4242]

    # 目标窗口换了：必须重新挂 owner，否则边框还跟着旧窗口的 Z 序
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: False)
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd",
                        lambda port: (5252, {"port": port}))
    with H._STATE_LOCK:
        H._STATE["last_rescan"] = 0.0
    H._sync_tick()
    assert binds == [4242, 5252]
    assert H._STATE["owner_hwnd"] == 5252


def test_sync_tick_fast_rescan_when_bound_window_disappears(monkeypatch):
    """关掉浏览器再打开不能干等 3s WMI 节流：失效后给 2s 快速重扫窗口。"""
    _tick_stubs(monkeypatch)
    H._sync_tick()
    assert H._STATE["owner_hwnd"] == 4242

    scans = []
    monkeypatch.setattr(H, "_target_valid", lambda hwnd: False)
    monkeypatch.setattr(H, "resolve_cdp_owner_hwnd",
                        lambda port: scans.append(port) or
                        (None, {"reason": "gone"}))
    # 慢扫设成 999s：若没走快速窗口，下面两轮根本不会重扫。
    monkeypatch.setattr(H, "HIGHLIGHT_RESCAN_TTL_S", 999)
    monkeypatch.setattr(H, "HIGHLIGHT_RESCAN_FAST_TTL_S", 0)
    H._sync_tick()
    assert scans, "窗口刚失效必须立刻重扫一次，不能等 3s"
    assert H._STATE["owner_hwnd"] is None
    assert H._STATE["rescan_fast_until"] > 0.0
    H._sync_tick()
    H._sync_tick()
    assert len(scans) >= 3, "2s 快速窗口内应保持高频重扫"


def test_sync_tick_records_incomplete_owner_binding(monkeypatch):
    _tick_stubs(monkeypatch)
    monkeypatch.setattr(H, "_bind_owner", lambda frames, hwnd: 2)
    H._sync_tick()
    assert "绑定不完整" in H._STATE["last_error"]


def test_highlight_state_reports_owner_and_z_order():
    with H._STATE_LOCK:
        H._STATE["owner_hwnd"] = 4242
    st = H.highlight_state()
    assert st["owner_hwnd"] == 4242
    assert st["z_order"] == "owned"
    with H._STATE_LOCK:
        H._STATE["owner_hwnd"] = None
    assert H.highlight_state()["z_order"] == "unbound"


def test_facade_still_exports_highlight_api():
    for name in ("highlight", "start_highlight", "stop_highlight",
                 "highlight_state", "set_highlight_enabled",
                 "highlight_config_changed"):
        assert hasattr(wb, name) and name in wb.__all__


# ---------------------------------------------------------------- 真实 Win32
@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Win32 only")
def test_real_win32_border_follows_owner_z_order():
    """真窗口验证：遮挡窗口盖住目标窗口时，边框必须跟着沉到遮挡窗口之下，
    而不是像 v1.0.732 那样穿透浮在最顶层。"""
    from ctypes import wintypes
    u32 = H._ensure_api()
    if u32 is None or not H._ensure_classes(H.parse_color("#FF9500")):
        pytest.skip("Win32 API 不可用")
    hinst = H._K32.GetModuleHandleW(None)
    target = u32.CreateWindowExW(0, H._FRAME_CLASS, None, H._WS_POPUP,
                                 120, 120, 900, 650, None, None, hinst, None)
    if not target:
        pytest.skip("当前会话无法创建 Win32 窗口（无交互桌面）")
    frames, occluder = [], None
    try:
        u32.ShowWindow(wintypes.HWND(int(target)), H._SW_SHOWNOACTIVATE)
        frames = H._create_frames()
        assert len(frames) == 5
        assert H._bind_owner(frames, target) == 5
        assert H._read_owner(frames[0]) == int(target)
        rect = H._get_target_rect(target)
        assert rect is not None
        H._place_frames(frames, rect, 8)
        H._show_frames(frames, True)

        occluder = u32.CreateWindowExW(0, H._FRAME_CLASS, None, H._WS_POPUP,
                                       300, 200, 700, 500, None, None,
                                       hinst, None)
        assert occluder
        u32.ShowWindow(wintypes.HWND(int(occluder)), H._SW_SHOWNOACTIVATE)
        u32.SetWindowPos(wintypes.HWND(int(occluder)), wintypes.HWND(0),
                         300, 200, 700, 500,
                         H._SWP_NOACTIVATE | H._SWP_SHOWWINDOW)

        order = []
        ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)
        ref = ENUMPROC(lambda h, _l: (order.append(int(h)), True)[1])
        u32.EnumWindows(ctypes.cast(ref, ctypes.c_void_p), 0)
        i_o = order.index(int(occluder)) if int(occluder) in order else -1
        i_t = order.index(int(target)) if int(target) in order else -1
        i_f = order.index(frames[0]) if frames[0] in order else -1
        assert min(i_o, i_t, i_f) >= 0, (i_o, i_t, i_f)
        assert i_o < i_f < i_t, (i_o, i_f, i_t)
    finally:
        H._destroy_frames(frames)
        if occluder:
            u32.DestroyWindow(wintypes.HWND(int(occluder)))
        u32.DestroyWindow(wintypes.HWND(int(target)))
