# -*- coding: utf-8 -*-
"""v1.0.730 回归锁：CDP 模块审查修复的**行为断言**（不 grep 源码文案）。

覆盖：
1. app_targets.ensure_browser_binding：WMI 查不到命令行（session=None）时，
   未确认必须 needs_kill 且零 Popen；确认后只杀当场枚举的 PID 再拉起。
2. frames.FrameSession.eval：result.exceptionDetails 必须转成结构化错误，
   不再静默返回 None。
3. frames.FrameSession.probe_all：eval 返回 dict（数据/错误）时不得丢弃。
4. core.cdp_capture.CdpSession.recv：deadline 真的约束单次 recv，过期立即返回。
5. routes.wecom.window_diag：CDP 枚举失败时 cdp_pages 仍是 list 且带 cdp_error。
6. cdp_client._cdp_eval：池连接 gettimeout() 返回 None 时仍按本次 timeout 放宽。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wecom_bridge.app_targets as at  # noqa: E402
import wecom_bridge.frames as frames  # noqa: E402
import wecom_bridge.cdp_client as cdp_client  # noqa: E402
from core import cdp_capture  # noqa: E402


class _FakeWS:
    """只记录行为、不联网的 CDP 会话假件。"""

    def __init__(self, recv_payload='{"id":1,"result":{}}', cur_timeout=None):
        self.sent = []
        self.timeouts = []
        self.recv_calls = 0
        self.closed = False
        self._payload = recv_payload
        self._cur = cur_timeout

    def gettimeout(self):
        return self._cur

    def settimeout(self, value):
        self._cur = value
        self.timeouts.append(value)

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        self.recv_calls += 1
        return self._payload

    def close(self):
        self.closed = True


# ---------------------------------------------- 1. session=None 绑定红线
def _bind_env(monkeypatch, tmp_path, session):
    """搭一套 ensure_browser_binding 的隔离桩，返回 (cfg, launched, killed)。

    session=None 模拟 WMI 查不到命令行；[333,444] 模拟当场枚举到的两个 PID。
    """
    exe = tmp_path / "msedge.exe"
    exe.write_text("", encoding="utf-8")
    cfg = {"wecom": {"cdp_port": 9222, "exe_path": str(exe),
                     "auto_launch": True, "auto_cdp": True,
                     "cdp_target": "edge"}}
    launched = []
    killed = []
    state = {"ready": False}

    monkeypatch.setattr(at, "detect_browsers", lambda: {"edge": str(exe), "chrome": None})
    monkeypatch.setattr(at, "cdp_target_kind", lambda port=9222: "edge")
    monkeypatch.setattr(at, "port_bindable", lambda port: True)
    monkeypatch.setattr(at, "cdp_up", lambda port: state["ready"])
    monkeypatch.setattr(at, "_cdp_ws_ok", lambda port: state["ready"])
    monkeypatch.setattr(at, "find_browser_pids", lambda t: [333, 444])
    monkeypatch.setattr(at, "_debug_session_pids", lambda target, pids: session)
    monkeypatch.setattr(at, "browser_profile_dir",
                        lambda t: str(tmp_path / "cdp_profiles" / t))
    monkeypatch.setattr(at, "list_targets", lambda port: [])
    monkeypatch.setattr(at, "window_list_summary", lambda port: [])
    monkeypatch.setattr(at, "windows_signature", lambda tg: "sig")
    monkeypatch.setattr(at, "_logw", lambda msg: None)
    monkeypatch.setattr(at.time, "sleep", lambda s: None)

    def _fake_kill(target, pids, wait_for=None, max_wait_s=5.0):
        killed.append(list(pids))
        return True, ""

    monkeypatch.setattr(at, "_kill_browser_pids", _fake_kill)

    def _fake_popen(argv, creationflags=0):
        launched.append(argv)
        state["ready"] = True

    monkeypatch.setattr(at.subprocess, "Popen", _fake_popen)
    return cfg, launched, killed


def test_binding_wmi_unavailable_requires_confirmation(monkeypatch, tmp_path):
    """session=None（分不清调试实例与日常浏览器）→ 未确认必须 needs_kill，零 Popen。"""
    cfg, launched, killed = _bind_env(monkeypatch, tmp_path, None)
    st = at.ensure_browser_binding(cfg, "edge", force=True, allow_kill=False)
    assert st.get("needs_kill") is True, st
    assert launched == [], "WMI 查不到命令行时绕过确认闸直接弹了浏览器"
    assert killed == [], "未确认就杀了进程"


def test_binding_wmi_unavailable_confirmed_kills_only_enumerated(monkeypatch, tmp_path):
    """确认后走保守路径：杀当场枚举的全部 PID（不按镜像名 /im），再拉起一次。"""
    cfg, launched, killed = _bind_env(monkeypatch, tmp_path, None)
    st = at.ensure_browser_binding(cfg, "edge", force=True, allow_kill=True)
    assert killed == [[333, 444]], killed
    assert len(launched) == 1, launched
    assert st.get("bound") is True, st


# ---------------------------------------------- 2/3. frames iframe 上下文
def _frame_session():
    sess = object.__new__(frames.FrameSession)
    sess._id = 0
    sess.ctxs = [{"id": 7, "frameId": "f1", "name": "", "origin": ""}]
    sess.frames = [{"id": "f1", "name": "", "url": "http://x", "depth": 0}]
    sess.timeout = 10
    return sess


def test_frame_eval_exception_details_is_structured_error():
    """JS 抛错走 result.exceptionDetails，不能当"无 value"静默返回 None。"""
    sess = _frame_session()
    sess._send = lambda method, params=None: 42
    sess._wait = lambda i: {"id": 42, "result": {
        "exceptionDetails": {"text": "TypeError",
                             "exception": {"description": "TypeError: boom"}}}}
    out = sess.eval("1+1", 7)
    assert isinstance(out, dict) and out.get("ok") is False, out
    assert "boom" in out["error"], out


def test_frame_probe_all_keeps_dict_result():
    """probe_all 的探针结果即使以 dict 返回也要并入 info（原表达式语句会整条丢弃）。"""
    sess = _frame_session()
    sess.eval = lambda expr, ctx_id=None, timeout=None: {
        "len": 123, "canvas": 2, "iframe": 0, "video": 0}
    out = sess.probe_all()
    assert out[0]["canvas"] == 2 and out[0]["len"] == 123, out


def test_frame_probe_all_records_eval_error():
    """探针失败要留痕，不能再静默产出只有 ctx/frame 的空洞 info。"""
    sess = _frame_session()
    sess.eval = lambda expr, ctx_id=None, timeout=None: {
        "ok": False, "error": "frame JS 异常：x"}
    out = sess.probe_all()
    assert "probe_error" in out[0] and "x" in out[0]["probe_error"], out


# ---------------------------------------------- 4. 9229 取证会话总时限
def test_cdp_capture_recv_honours_expired_deadline():
    sess = object.__new__(cdp_capture.CdpSession)
    sess.ws = _FakeWS()
    sess.err = ""
    out = sess.recv(time.time() - 1.0)
    assert out is None
    assert sess.ws.recv_calls == 0, "总时限已过却还去 recv（取证线程可能被挂住）"
    assert "总时限" in sess.err, sess.err


def test_cdp_capture_recv_caps_socket_timeout_by_deadline():
    sess = object.__new__(cdp_capture.CdpSession)
    sess.ws = _FakeWS(recv_payload='{"id":1,"result":{}}', cur_timeout=5.0)
    sess.err = ""
    out = sess.recv(time.time() + 1.0)
    assert isinstance(out, dict), out
    assert sess.ws.timeouts and sess.ws.timeouts[0] <= 1.0, sess.ws.timeouts


# ---------------------------------------------- 5. 诊断端点不掩盖 CDP 故障
def test_window_diag_surfaces_cdp_enum_error(app_client, monkeypatch):
    import wecom_bridge as wb
    import routes.wecom as rw
    client = app_client[0]
    monkeypatch.setattr(rw, "_cdp_port", lambda cfg: 9222)
    monkeypatch.setattr(rw, "load_config_view", lambda: {})
    monkeypatch.setattr(wb, "list_targets",
                        lambda port: {"ok": False, "error": "/json 停摆"})
    monkeypatch.setattr(wb, "list_os_window_titles", lambda: [])
    monkeypatch.setattr(wb, "process_session_info", lambda: {})
    monkeypatch.setattr(wb, "_stage_guard", lambda fn, t, name: ("ok", fn()))
    data = client.get("/api/wecom/window_diag").get_json()
    assert isinstance(data["cdp_pages"], list) and data["cdp_pages"] == [], data
    assert "停摆" in (data.get("cdp_error") or ""), data


# ---------------------------------------------- 6. 池连接 gettimeout=None
def test_cdp_eval_raises_recv_timeout_when_socket_has_no_timeout(monkeypatch):
    """gettimeout() 返回 None 时不能再被 TypeError 静默吞掉而不放宽 recv 上限。"""
    ws = _FakeWS(recv_payload='{"id":1,"result":{}}', cur_timeout=None)
    monkeypatch.setattr(cdp_client, "_pool_acquire",
                        lambda url, t: (ws, None, None))
    monkeypatch.setattr(cdp_client, "_recv_until",
                        lambda w, i, d: {"id": 2, "result": {
                            "result": {"type": "string", "value": "ok"}}})
    out = cdp_client._cdp_eval("ws://x", "1+1", timeout=30.0)
    assert out == "ok", out
    assert 30.0 in ws.timeouts, ws.timeouts
