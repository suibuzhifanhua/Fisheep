# -*- coding: utf-8 -*-
"""test_cdp_write_idempotency — 写类 CDP 表达式不得被自动重试。

背景（v1.0.767 B6）：_cdp_eval_retry 对「评估超时 / 通信异常 / 空响应」自动重试。
对**只读**表达式这没问题；但对 click / insertText / 下载点击这类**写**表达式，
「超时」发生在 Runtime.evaluate 的 send **之后**——页面可能已经把动作执行完、
只是响应在半死连接上丢了。此时重试 = 同一业务动作执行两次，而企微的审批提交 /
下载是不可撤销的。故 _cdp_eval_retry 加 idempotent 开关：写类只对「页面正忙」
（_pool_acquire 在发送**之前**就拒绝，动作铁定没跑）重试，其余错误与空响应直接上报。

行为锁（真跑 _cdp_eval_retry，只桩掉 _cdp_eval，按调用顺序喂返回值）：
  · 只读（默认 idempotent=True）：错误/空响应按 retries 重放，最终命中；
  · 写类（idempotent=False）：非「忙」的结构化错误 → 只调 1 次立即上报；
  · 写类：空响应 → 只调 1 次立即上报（绝不空转重放）；
  · 写类：「页面正忙」→ 仍按 retries 重放（可证安全）；
  · 三个真实写调用点确实传了 idempotent=False。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bridge import cdp_client  # noqa: E402
import bridge.act as ACT     # noqa: E402
import bridge.read as RD     # noqa: E402
import bridge.download as DL  # noqa: E402


def _drive(monkeypatch, results, idempotent):
    """把 _cdp_eval 换成按序取值的记录器，跑 _cdp_eval_retry，返回 (raw, 调用次数)。"""
    calls = {"n": 0}
    it = iter(results)

    def _fake_eval(ws_url, expr, timeout=None, await_promise=False):
        calls["n"] += 1
        try:
            return next(it)
        except StopIteration:
            return results[-1]

    monkeypatch.setattr(cdp_client, "_cdp_eval", _fake_eval)
    raw = cdp_client._cdp_eval_retry("ws://x", "expr", retries=3,
                                     retry_delay=0, idempotent=idempotent)
    return raw, calls["n"]


def test_read_retries_through_errors_and_none(monkeypatch):
    err = {"ok": False, "error": "CDP 评估超时：页面未在限定时间内响应"}
    raw, n = _drive(monkeypatch, [err, None, '{"ok":true}'], idempotent=True)
    assert raw == '{"ok":true}' and n == 3


def test_write_does_not_replay_ambiguous_error(monkeypatch):
    err = {"ok": False, "error": "CDP 评估超时：页面未在限定时间内响应"}
    raw, n = _drive(monkeypatch, [err, err, err], idempotent=False)
    assert raw is err and n == 1, "写类超时=送达不可证明，必须只跑一次"


def test_write_does_not_replay_comm_error(monkeypatch):
    err = {"ok": False, "error": "CDP 通信异常：broken pipe"}
    raw, n = _drive(monkeypatch, [err, err, err], idempotent=False)
    assert raw is err and n == 1


def test_write_does_not_replay_none(monkeypatch):
    raw, n = _drive(monkeypatch, [None, None, None], idempotent=False)
    assert raw is None and n == 1, "写类空响应=动作可能已执行，绝不空转重放"


def test_write_still_replays_page_busy(monkeypatch):
    busy = {"ok": False, "error": "页面正忙（另一 CDP 操作超 13s 未完成），请稍后重试"}
    raw, n = _drive(monkeypatch, [busy, busy, '{"ok":true}'], idempotent=False)
    assert raw == '{"ok":true}' and n == 3, "「页面正忙」在发送前就拒绝，可安全重试"


def test_write_returns_success_on_first_shot(monkeypatch):
    raw, n = _drive(monkeypatch, ['{"ok":true}'], idempotent=False)
    assert raw == '{"ok":true}' and n == 1


# ---- 真实写调用点确实标记为写（改回默认 idempotent 必红）----
def _has_kwarg(module, kw):
    import ast
    import io
    src = io.open(os.path.abspath(module.__file__), encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_cdp_eval_retry":
            for k in node.keywords:
                if k.arg == kw and isinstance(k.value, ast.Constant) and k.value.value is False:
                    return True
    return False


def test_write_call_sites_pass_idempotent_false():
    # 用 AST 精确到「该模块里存在 _cdp_eval_retry(..., idempotent=False)」，
    # 避免 grep 到注释/字符串里的一句 idempotent=False 造成假绿。
    assert _has_kwarg(ACT, "idempotent"), "type_document（写）未标 idempotent=False"
    assert _has_kwarg(DL, "idempotent"), "download 点击（写）未标 idempotent=False"
    assert _has_kwarg(RD, "idempotent"), "click（写）未标 idempotent=False"