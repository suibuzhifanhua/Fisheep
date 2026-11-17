# -*- coding: utf-8 -*-
"""v1.0.686（2026-11-16「点一个模块就卡死」根治：原生自愈 + 死循环捕获）回归锁。

实锤链路（v1.0.685 现场，见 companion.log + 渲染器 CPU 采样）：
  - 冻结 = 渲染器主线程 ~100% CPU 同步死循环（PID 1896 五秒内烧 5.3s CPU），
    **非**遮挡（CalculateNativeWinOcclusion 已关且看门狗判「窗口可见」）、
    **非**后端（15 个设置接口全 <100ms）、**非**崩溃（无 WER）。
  - v1.0.684 的自愈调 `window.reload()`，但本机 pywebview 版本**没有**该方法
    （日志实锤 `'Window' object has no attribute 'reload'`）→ 自愈是空操作，
    冻结后永不恢复，用户只能手动杀进程。
本版本锁住两件：
  1. 自愈改用 `window.load_url(...)`（WinForms Invoke 走宿主 UI 线程触发 WebView2
     Navigate = 浏览器进程级重载，能打断渲染器主线程死循环），并允许多次重载
     （`_MAX_RELOADS`）+ 120s 间隔防「重载→立即再冻→重载」死循环。
  2. WebView2 开 `--remote-debugging-port`（仅绑本机）——下次冻结直接连 DevTools
     读渲染器正在自旋的那一帧调用栈，一次锁定根因函数（静态阅读未定位到具体
     while/for/递归，端口是把它钉死的可靠手段）。
"""
from conftest import _src


def test_watchdog_uses_native_load_url():
    """自愈必须走 load_url（浏览器进程级），保留当前 URL / 根地址回退，且能多次重载。"""
    s = _src("backend.py")
    assert "window.load_url(" in s, "自愈必须用 load_url（本版本 pywebview 无 window.reload）"
    assert "get_current_url()" in s, "重载目标优先取当前 URL"
    assert '"http://127.0.0.1:%d/" % APP_PORT' in s, "当前 URL 不可用时回退到应用根地址"
    assert "_MAX_RELOADS" in s, "允许多次自愈（数据/操作型死循环重载后未必复发）"
    assert "window.reload()" not in s, "不许回退到不存在的 window.reload()"


def test_watchdog_spaced_reloads_guard():
    """多次重载必须有间隔守卫，否则「重载→立即再冻→重载」会成新的死循环。"""
    s = _src("backend.py")
    assert "_last_reload_ts" in s
    assert ">= 120" in s, "两次自愈间隔 ≥120s"


def test_devtools_port_for_stack_capture():
    """开 DevTools 远程调试端口（仅本机）——死循环根因捕获。"""
    s = _src("backend.py")
    assert "--remote-debugging-port" in s, "需保留渲染器 DevTools 入口以读取自旋栈"
