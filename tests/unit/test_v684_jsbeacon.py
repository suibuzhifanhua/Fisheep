# -*- coding: utf-8 -*-
"""v1.0.684（2026-11-16「设置/学习中心点不开」修复）回归锁。

实例形态：页面渲染正常，但 WebView2 里的 JS 停执行（渲染器冻结/窗口遮挡误判）——
侧栏点击无响应（"各个模块点不开"）、点 X 关闭确认的前端弹层不出（只有 8s 原生
兜底）、兜底框退回系统默认色（"弹窗颜色不适配"）。后端心跳全 200：HTTP 活着 ≠
JS 活着，只有页面自己打的信标能证明 JS 活着。本文件锁住三件事：
  1. /api/jsbeacon 信标端点 + /api/ping 携带 js_silent_s（看门狗调用即现场）
  2. 关 CalculateNativeWinOcclusion（托盘常驻/VDI 遮挡误判 → 后台渲染器被挂起）
  3. 前端信标（01-core.js）+ 后端自愈线程（窗口可见 + 静默 90s → 自动重载一次）
"""
from conftest import _src


def test_jsbeacon_endpoint(app_client):
    """信标端点：200 + ok；打完信标后 /api/ping 的 js_silent_s 应是小数（非 None）。"""
    c, _ = app_client
    r = c.get("/api/ping")
    assert r.status_code == 200
    d0 = r.get_json()
    assert "js_silent_s" in d0, "/api/ping 必须携带 JS 存活（js_silent_s）"
    r = c.post("/api/jsbeacon", json={"view": "settings"})
    assert r.status_code == 200
    assert r.get_json().get("ok") is True
    d1 = c.get("/api/ping").get_json()
    assert d1["js_silent_s"] is not None and d1["js_silent_s"] <= 5, \
        "打完信标 js_silent_s 应是小数：%r" % d1["js_silent_s"]


def test_occlusion_disabled():
    """CalculateNativeWinOcclusion 必须关掉：托盘常驻/VDI 下 Chromium 把窗口误判为
    被遮挡 → 后台渲染器被挂起 → 窗口一显示 JS 还是死的（11-16 三症状实例的根因
    假设；09:44 日志「托盘隐藏时 15s 未响应」是旁证）。"""
    s = _src("backend.py")
    assert "--disable-features=CalculateNativeWinOcclusion" in s


def test_frontend_beacon_present():
    """前端信标必须在场：页面 JS 停执行时，后端唯一证据就是它。"""
    s = _src("js", "01-core.js")
    assert "/api/jsbeacon" in s
    assert "keepalive: true" in s, "信标必须 fire-and-forget（keepalive），失败不得报警"


def test_self_heal_thread_present():
    """自愈线程必须在场：窗口可见 + 信标静默 → 原生 load_url 重载页面（HTTP 200 救不活 JS）。

    v1.0.686：684 那次自愈调 `window.reload()`，但本机 pywebview 版本根本没有该
    方法（日志实锤 `'Window' object has no attribute 'reload'`）→ 自愈是空操作。
    现改用 `window.load_url(...)`（浏览器进程级 Navigate，能打断渲染器主线程死循环）。
    这里锁住正确 API：不许回退到不存在的 window.reload()。"""
    s = _src("backend.py")
    assert "_js_liveness_watchdog" in s
    assert "window.load_url(" in s
    assert "window.reload()" not in s, "本版本 pywebview 无 window.reload()，自愈必须走 load_url"
