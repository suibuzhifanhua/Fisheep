# -*- coding: utf-8 -*-
"""tests/contract/test_contract_close_guard.py — desktop/close_guard.py 关闭确认链路**契约锁**（评审 P1-2）。

close_guard.py（>1400 行）是 Win32 原生关闭确认窗口。两次用户实锤事故：
  · v1.0.581：apply_web_choice 在 HTTP 线程直接调窗口操作 → CoreWebView2
    跨线程访问弹 .NET 未处理异常。改为写状态 + PostMessage(WM_CLOSE) 投递给 GUI 线程；
  · v1.0.660 F13：写 once/remember 与清 pending 不在同一把锁里 → remember 丢失。
本文件锁「不在本线程执行动作」与「锁内清 pending」，改回旧写法必红。
"""
import inspect
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import desktop.close_guard as CG  # noqa: E402


@pytest.fixture(autouse=True)
def _web_state():
    with CG._WEB_ASK_LOCK:
        saved_ask = dict(CG._WEB_ASK)
    saved_fn = CG._WEB_APPLY.get("fn")
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK.clear()
        CG._WEB_ASK.update({"pending_hwnd": None, "once": None, "remember": False})
    CG._WEB_APPLY["fn"] = None
    yield
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK.clear()
        CG._WEB_ASK.update(saved_ask)
    CG._WEB_APPLY["fn"] = saved_fn


class _User32(object):
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def PostMessageW(self, *args):
        self.calls.append(args)
        if self.fail:
            raise OSError("probe: PostMessage 失败")
        return 1


def test_close_ask_pending_tracks_state():
    assert CG.close_ask_pending() is False
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK["pending_hwnd"] = 4321
    assert CG.close_ask_pending() is True
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK["pending_hwnd"] = None
    assert CG.close_ask_pending() is False


def test_apply_web_choice_posts_message_and_defers_action(monkeypatch):
    """改回「HTTP 线程直接执行窗口操作」→ fn_calls 非空，本用例变红。"""
    u = _User32()
    monkeypatch.setattr(CG, "user32", u)
    fn_calls = []
    CG._WEB_APPLY["fn"] = lambda mn, rm: fn_calls.append((mn, rm))
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK["pending_hwnd"] = 1234
    CG.apply_web_choice(True, True)
    assert len(u.calls) == 1, "必须投递 WM_CLOSE 给 GUI 线程"
    assert u.calls[0][1] == CG.WM_CLOSE
    assert not fn_calls, "动作不得在 HTTP 线程直接执行（CoreWebView2 跨线程会崩）"
    assert CG._WEB_ASK["once"] == "minimize"
    assert CG._WEB_ASK["remember"] is True
    assert CG._WEB_ASK["pending_hwnd"] is None, "pending 必须在同一把锁里清掉"


def test_apply_web_choice_exit_variant(monkeypatch):
    u = _User32()
    monkeypatch.setattr(CG, "user32", u)
    CG._WEB_APPLY["fn"] = lambda *_a: None
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK["pending_hwnd"] = 1
    CG.apply_web_choice(False, False)
    assert CG._WEB_ASK["once"] == "exit"
    assert CG._WEB_ASK["remember"] is False


def test_no_registered_callback_is_a_noop(monkeypatch):
    u = _User32()
    monkeypatch.setattr(CG, "user32", u)
    CG.apply_web_choice(True, False)
    assert u.calls == []


def test_postmessage_failure_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(CG, "user32", _User32(fail=True))
    fn_calls = []
    CG._WEB_APPLY["fn"] = lambda mn, rm: fn_calls.append((mn, rm))
    with CG._WEB_ASK_LOCK:
        CG._WEB_ASK["pending_hwnd"] = 1
    CG.apply_web_choice(True, False)
    assert fn_calls == [(True, False)], "PostMessage 失败必须降级直接执行，不能无响应"


def test_install_signature_keeps_backend_callbacks():
    params = list(inspect.signature(CG.install).parameters)
    for p in ("title", "on_minimize", "on_hotkey"):
        assert p in params, "backend.py 的 install(...) 调用契约被改坏了"


def test_source_shape_keeps_prototypes_and_message_post():
    src = io.open(os.path.join(ROOT, "desktop", "close_guard.py"),
                  encoding="utf-8").read()
    install_body = src.split("def install(", 1)[1].split("\ndef show_window(", 1)[0]
    assert "_setup_prototypes()" in install_body, "漏调会 ctypes 参数错乱"
    choice_body = src.split("def apply_web_choice(", 1)[1].split(
        "\ndef _find_main_replacement(", 1)[0]
    assert "PostMessageW" in choice_body
    assert "WM_CLOSE" in choice_body
