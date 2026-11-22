# -*- coding: utf-8 -*-
"""v1.0.731「全修」回归锁：CDP 协议层 / 绑定链路 / 架构建议。

v1.0.730 报告里 11 条未改建议 + 4 条架构建议全部落地后，用**行为断言**锁住：
不 grep 源码文案当作验证，优先真跑函数、断言返回值与副作用（AGENTS.md 纪律 2）。

分层：
  A. 协议层 —— 全局递增消息 id / 单次 Runtime.enable / base_timeout / sweeper /
     WS 探测复用与内容页评分。
  B. 页面生命周期 —— StaleTargets / close_page 拒绝陈旧序号 / 等待预算封顶。
  C. iframe / OOPIF —— 专用会话并发边界 / allow_iframe 独立 WS 直连。
  D. 架构 —— frames 门面导出 / 9229 Origin / frozen 真实 HTTP 冒烟 / 文件软告警。
  E. **真实 WebSocket 端点**：本地起一个真 WS server（非 monkeypatch），让
     _cdp_eval 真发帧、真收帧，验证「enable 只一次 + id 永不复用」。
     这条直接补上 v1.0.730 报告「4 份 CDP 测试全是 mock」的遗留风险。
     headless Edge/Chrome 集成测试默认 opt-in（WECOM_RUN_HEADLESS_CDP=1），
     避免把外部浏览器启动的波动带进全量套件。

注意：本文件刻意不写任何裸 `except: pass`——check_silent_pass 会扫 tests/，
新增静默点会把棘轮基线顶红。
"""
import ast
import base64
import contextlib
import hashlib
import io
import json
import os
import socket
import struct
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wecom_bridge as wb                      # noqa: E402
import wecom_bridge.cdp_client as CC           # noqa: E402
import wecom_bridge.frames as FR               # noqa: E402
import wecom_bridge.targets as TG              # noqa: E402
import wecom_bridge.windows as WIN             # noqa: E402
from core import cdp_capture as CAP            # noqa: E402
from core import smoke                         # noqa: E402
import buildlib.gates as G                     # noqa: E402
import build as B                              # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---------------------------------------------------------------- 通用假件
class _FakeWS:
    """记录行为、不联网的 CDP 会话假件。"""

    def __init__(self, cur_timeout=None):
        self.sent = []
        self.timeouts = []
        self.closed = False
        self._cur = cur_timeout

    def gettimeout(self):
        return self._cur

    def settimeout(self, value):
        self._cur = value
        self.timeouts.append(value)

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        return '{"id":1,"result":{}}'

    def close(self):
        self.closed = True


def _pool_entry(ws, base_timeout=5.0):
    """造一条「已被 acquire、持锁中」的池槽位，配对 _pool_release 用。"""
    lock = threading.Lock()
    lock.acquire()
    return {"lock": lock, "ws": ws, "ts": time.time(),
            "base_timeout": base_timeout, "enabled_for": None}


def _resp_for(i):
    """_recv_until 的桩：按实际 id 回一条合法的 Runtime.evaluate 响应。"""
    return {"id": i, "result": {"result": {"type": "string", "value": "ok"}}}


# ============================================================ A. 协议层
def test_msg_ids_are_unique_across_threads():
    """全局递增 id：并发取号也不许出现重复（池化常驻连接上迟到响应的根因）。"""
    got = []
    lock = threading.Lock()

    def _take():
        local = [CC._next_msg_id() for _ in range(60)]
        with lock:
            got.extend(local)

    ts = [threading.Thread(target=_take) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(got) == 240
    assert len(set(got)) == 240, "消息 id 复用：迟到响应会被 _recv_until 当成别人的结果"


def test_cdp_eval_enables_runtime_once_with_unique_ids(monkeypatch):
    """同一条池化连接上：Runtime.enable 只发一次，evaluate 每次发，id 全不复用。"""
    ws = _FakeWS(cur_timeout=1.0)
    entry = _pool_entry(ws)
    monkeypatch.setattr(CC, "_pool_acquire", lambda url, t: (ws, entry, None))
    monkeypatch.setattr(CC, "_recv_until", lambda w, i, d: _resp_for(i))

    assert CC._cdp_eval("ws://x", "1+1", timeout=3.0) == "ok"
    assert CC._cdp_eval("ws://x", "2+2", timeout=3.0) == "ok"
    methods = [m["method"] for m in ws.sent]
    assert methods.count("Runtime.enable") == 1, ws.sent
    assert methods.count("Runtime.evaluate") == 2, ws.sent
    ids = [m["id"] for m in ws.sent]
    assert len(set(ids)) == len(ids), ids
    assert entry["enabled_for"] is ws, "enable 标记没绑定到 ws 对象身份"


def test_cdp_eval_reenables_after_broken_release(monkeypatch):
    """broken 重建后 ws 换新对象：一次性状态必须作废，下次自动重新 enable。"""
    ws = _FakeWS(cur_timeout=1.0)
    entry = _pool_entry(ws)
    entry["enabled_for"] = ws
    monkeypatch.setattr(CC, "_pool_acquire", lambda url, t: (ws, entry, None))
    monkeypatch.setattr(CC, "_recv_until", lambda w, i, d: _resp_for(i))
    CC._cdp_eval("ws://x", "1+1", timeout=3.0)
    # 模拟 _pool_release(broken=True) 的清理
    CC._pool_release(entry, ws, True)
    assert entry["ws"] is None
    assert entry["enabled_for"] is None and entry["base_timeout"] is None
    # 新连接对象（同样这个假件）再进来，必须重新 enable
    entry["ws"] = ws
    entry["lock"].acquire()
    before = len(ws.sent)
    CC._cdp_eval("ws://x", "1+1", timeout=3.0)
    assert [m["method"] for m in ws.sent[before:]] == [
        "Runtime.enable", "Runtime.evaluate"], ws.sent[before:]


def test_cdp_command_uses_unique_ids(monkeypatch):
    """_cdp_command 的 id 也走全局递增，不再固定 1。"""
    ws = _FakeWS(cur_timeout=1.0)
    monkeypatch.setattr(CC, "_pool_acquire", lambda url, t: (ws, None, None))
    monkeypatch.setattr(CC, "_recv_until", lambda w, i, d: {"id": i, "result": {}})
    assert CC._cdp_command("ws://x", "Browser.setDownloadBehavior") == {}
    assert CC._cdp_command("ws://x", "Browser.setDownloadBehavior") == {}
    ids = [m["id"] for m in ws.sent]
    assert len(set(ids)) == 2, ids
    assert all(m["method"] == "Browser.setDownloadBehavior" for m in ws.sent)


def test_pool_release_restores_base_timeout_not_pool_default():
    """归还时按建连值恢复 socket 超时，而不是一律压回 _POOL_SOCK_TIMEOUT=10。"""
    ws = _FakeWS(cur_timeout=30.0)
    entry = _pool_entry(ws, base_timeout=1.5)
    CC._pool_release(entry, ws, False)
    assert ws.gettimeout() == 1.5, ws.timeouts
    assert CC._POOL_SOCK_TIMEOUT not in ws.timeouts


def test_pool_release_broken_clears_one_shot_state():
    ws = _FakeWS(cur_timeout=30.0)
    entry = _pool_entry(ws, base_timeout=1.5)
    entry["enabled_for"] = ws
    CC._pool_release(entry, ws, True)
    assert ws.closed is True
    assert entry["ws"] is None
    assert entry["enabled_for"] is None and entry["base_timeout"] is None


def test_pool_sweeper_start_is_idempotent(monkeypatch):
    """首次用池懒启动后台回收；重复 start 不重复起线程，stop 后可再拉起。"""
    monkeypatch.setattr(CC, "_pool_enabled", lambda: True)
    monkeypatch.setattr(CC, "_POOL_SWEEPER_THREAD", None)
    try:
        assert CC.start_pool_sweeper() is True
        assert CC.start_pool_sweeper() is False, "幂等失效：每次 acquire 都起一条线程"
        assert CC._POOL_SWEEPER_THREAD.is_alive()
    finally:
        CC.stop_pool_sweeper()
        monkeypatch.setattr(CC, "_POOL_SWEEPER_THREAD", None)


def test_ws_probe_picks_content_page_and_carries_origin(monkeypatch):
    """绑定硬判据不能是第一个空白页；且握手必须固定带 Origin（README 6.17）。"""
    calls = []

    class _Ws:
        def close(self):
            pass

    def _create(url, **kw):
        calls.append((url, kw))
        return _Ws()

    monkeypatch.setattr(CC.websocket, "create_connection", _create)
    monkeypatch.setattr(WIN, "list_targets", lambda port: [
        {"type": "page", "id": "A", "url": "about:blank", "title": "",
         "webSocketDebuggerUrl": "ws://blank"},
        {"type": "page", "id": "B", "url": "https://work.weixin.qq.com/?x=1",
         "title": "企微", "webSocketDebuggerUrl": "ws://content"},
    ])
    monkeypatch.setattr(WIN, "_get_auto_pick", lambda: None)
    assert CC._cdp_ws_ok(9222) is True
    assert calls and calls[0][0] == "ws://content", calls
    assert calls[0][1].get("origin") == CC.CDP_CLIENT_ORIGIN, calls


def test_ws_probe_fails_closed_on_enum_error(monkeypatch):
    """枚举返回结构化错误时不能让 .get 抛 AttributeError，也不能假装连通。"""
    calls = []
    monkeypatch.setattr(CC.websocket, "create_connection",
                        lambda *a, **k: calls.append(1))
    monkeypatch.setattr(WIN, "list_targets",
                        lambda port: {"ok": False, "error": "/json 停摆"})
    assert CC._cdp_ws_ok(9222) is False
    assert calls == [], "枚举都失败了还去开 WebSocket"


def test_ws_probe_cache_reuses_within_ttl():
    """绑定等待循环：probe 可注入（保住 monkeypatch 语义）、ttl 内复用、过期重探。"""
    hits = []

    def _probe(port):
        hits.append(port)
        return True

    cache = {}
    assert CC.cdp_ws_ok_cached(9222, cache, ttl=100.0, probe=_probe) is True
    assert CC.cdp_ws_ok_cached(9222, cache, ttl=100.0, probe=_probe) is True
    assert CC.cdp_ws_ok_cached(9222, cache, ttl=100.0, probe=_probe) is True
    assert len(hits) == 1, hits
    assert CC.cdp_ws_ok_cached(9222, cache, ttl=0.0, probe=_probe) is True
    assert len(hits) == 2, "ttl 过期后没有重新握手"


# ============================================================ B. 页面生命周期
def test_stale_targets_is_list_and_pages_still_work():
    """stale 用 list 子类而不是 dict：_pages() 对 dict 会返回 [] 让全链路误判。"""
    raw = [{"type": "page", "id": "X", "url": "https://x",
            "webSocketDebuggerUrl": "ws://x"}]
    s = TG.StaleTargets(raw)
    assert isinstance(s, list) and s.stale is True
    assert WIN._pages(s) == raw
    assert TG.StaleTargets([]).stale is True


def test_close_page_refuses_stale_index_but_allows_tid(monkeypatch):
    """/json 停摆期按陈旧序号关页会关错窗口；#tid: 精确寻址不受影响。"""
    raw = [{"type": "page", "id": "exact", "title": "旧页",
            "url": "https://x", "webSocketDebuggerUrl": "ws://x"}]
    gets = []

    class _Resp:
        status_code = 200
        text = ""

    class _Req:
        @staticmethod
        def get(url, **kw):
            gets.append(url)
            return _Resp()

    monkeypatch.setattr(WIN, "list_targets", lambda port: TG.StaleTargets(raw))
    monkeypatch.setattr(WIN, "requests", _Req())
    r = TG.close_page(0, port=9222)
    assert r.get("ok") is False and "不是最新" in r["error"], r
    assert gets == [], "已经拒绝关页却还是打了 /json/close"
    r2 = TG.close_page("#tid:exact", port=9222)
    assert r2.get("ok") is True, r2
    assert gets and "/json/close/exact" in gets[0], gets


def test_wait_page_load_caps_budget_at_shared_constant(monkeypatch):
    """工具层不会再承诺 25s：wait_page_load 必须按 WAIT_PAGE_LOAD_MAX_S 封顶。"""
    class _Clock:
        def __init__(self):
            self.t = 100.0

        def time(self):
            return self.t

        def sleep(self, s):
            self.t += s

    clk = _Clock()
    monkeypatch.setattr(TG, "time", clk)
    monkeypatch.setattr(WIN, "_page_ws", lambda pick=None, port=None: ("ws://x", {}))
    monkeypatch.setattr(WIN, "_cdp_eval", lambda *a, **k: "loading")
    out = TG.wait_page_load(timeout=25.0)
    assert out["ok"] is True, out
    assert clk.t - 100.0 <= TG.WAIT_PAGE_LOAD_MAX_S + 1.0, \
        "没按共享常量封顶，仍在按调用方传的 25s 空等：%s" % (clk.t - 100.0)


# ============================================================ C. iframe / OOPIF
@pytest.fixture()
def frame_slots(monkeypatch):
    """隔离专用会话登记表，并让 FrameSession 不真连 WS。"""
    monkeypatch.setattr(FR, "_FRAME_SESSION_LIVE", set())
    monkeypatch.setattr(FR, "_open_ws", lambda url, timeout: _FakeWS())
    monkeypatch.setattr(FR.FrameSession, "_enable", lambda self: None)
    made = []
    yield made
    for s in made:
        with contextlib.suppress(Exception):
            s.close()
    FR._FRAME_SESSION_LIVE.clear()


def test_frame_session_slots_are_bounded(frame_slots):
    """专用会话刻意不走 _WS_POOL，必须靠并发上限兜住 devtools 会话数。"""
    made = frame_slots
    for _ in range(FR.FRAME_SESSION_MAX):
        made.append(FR.FrameSession("ws://x"))
    assert FR.active_frame_sessions() == FR.FRAME_SESSION_MAX
    with pytest.raises(RuntimeError) as ei:
        FR.FrameSession("ws://x")
    assert "上限" in str(ei.value)
    assert FR.active_frame_sessions() == FR.FRAME_SESSION_MAX


def test_frame_session_context_manager_and_del_release_slot(frame_slots):
    made = frame_slots
    with FR.FrameSession("ws://x") as s:
        made.append(s)
        assert FR.active_frame_sessions() == 1
    assert FR.active_frame_sessions() == 0, "with 退出没归还槽位"
    t = FR.FrameSession("ws://x")
    made.append(t)
    assert FR.active_frame_sessions() == 1
    t.__del__()          # 漏关兜底；再调一次验证幂等
    t.__del__()
    assert FR.active_frame_sessions() == 0


def test_iframe_targets_only_oopif():
    """只认 type=iframe 且有独立 WS 的目标；dict（枚举错误）返回空。"""
    tg = [
        {"type": "page", "webSocketDebuggerUrl": "ws://page"},
        {"type": "iframe", "id": "F", "webSocketDebuggerUrl": "ws://oopif"},
        {"type": "iframe", "id": "G"},
        {"type": "other", "webSocketDebuggerUrl": "ws://other"},
    ]
    assert FR.iframe_targets(tg) == [
        {"type": "iframe", "id": "F", "webSocketDebuggerUrl": "ws://oopif"}]
    assert FR.iframe_targets({"ok": False}) == []


def test_open_session_allow_iframe_bypasses_pages_filter(monkeypatch):
    """README 6.13 的 OOPIF 直连必须可达：allow_iframe=True 不走 _pages()。"""
    targets = [
        {"type": "page", "id": "P", "url": "https://x",
         "webSocketDebuggerUrl": "ws://page"},
        {"type": "iframe", "id": "F", "url": "https://cdn/frame.html",
         "title": "课件", "webSocketDebuggerUrl": "ws://oopif"},
    ]
    monkeypatch.setattr(WIN, "list_targets", lambda port: targets)
    monkeypatch.setattr(WIN, "_pages",
                        lambda tg: (_ for _ in ()).throw(AssertionError("不该走 _pages()")))
    monkeypatch.setattr(FR, "FrameSession", lambda url: ("SESSION", url))
    assert FR.open_session(9222, 0, allow_iframe=True) == ("SESSION", "ws://oopif")
    assert FR.open_session(9222, "课件", allow_iframe=True) == ("SESSION", "ws://oopif")
    monkeypatch.setattr(WIN, "list_targets", lambda port: targets[:1])
    with pytest.raises(RuntimeError) as ei:
        FR.open_session(9222, 0, allow_iframe=True)
    assert "OOPIF" in str(ei.value)


# ============================================================ D. 架构
def test_bridge_exports_frames_facade():
    """README 6.12 说的门面必须真的存在（此前只有 learn_capture 直接 import 子模块）。"""
    assert wb.frames is FR
    for name in ("FrameSession", "open_session", "iframe_targets",
                 "active_frame_sessions", "FRAME_SESSION_MAX"):
        assert hasattr(wb, name), name
    assert name in wb.__all__
    assert callable(wb.active_frame_sessions)


def test_wait_page_load_constant_is_single_source():
    from agent_tools.registry import TOOLS
    assert CC.WAIT_PAGE_LOAD_MAX_S == 8.0
    assert wb.WAIT_PAGE_LOAD_MAX_S == CC.WAIT_PAGE_LOAD_MAX_S
    tool = next(t for t in TOOLS
                if t["function"]["name"] == "wait_wecom_page_load")
    desc = tool["function"]["parameters"]["properties"]["timeout"]["description"]
    assert "封顶 8" in desc, desc
    esrc = io.open(os.path.join(ROOT, "agent_tools", "executors.py"),
                   encoding="utf-8").read()
    assert "wb.WAIT_PAGE_LOAD_MAX_S" in esrc, "工具层默认值没读共享常量"


def test_9229_origin_is_consistent_and_sent_on_handshake(monkeypatch):
    """9229 取证口：白名单与握手 Origin 同源，且握手真的把 origin 发出去。"""
    import backend  # noqa: F401  确认包级符号可用
    assert CAP.CDP_CLIENT_ORIGIN == wb.CDP_CLIENT_ORIGIN
    bsrc = io.open(os.path.join(ROOT, "backend.py"), encoding="utf-8").read()
    assert "--remote-allow-origins=" in bsrc and "wb.CDP_CLIENT_ORIGIN" in bsrc
    calls = []

    class _Ws:
        def settimeout(self, t):
            pass

        def close(self):
            pass

    def _create(url, **kw):
        calls.append((url, kw))
        return _Ws()

    monkeypatch.setattr(CAP.websocket, "create_connection", _create)
    CAP.CdpSession("ws://127.0.0.1:9229/devtools/page/X", timeout_s=2.0)
    assert calls and calls[0][1].get("origin") == CAP.CDP_CLIENT_ORIGIN, calls


def _ping_app(status=200, nosniff=True):
    from flask import Flask, jsonify
    app = Flask(__name__)
    if nosniff:
        @app.after_request
        def _head(r):
            r.headers.setdefault("X-Content-Type-Options", "nosniff")
            return r
    @app.route("/api/ping")
    def _ping():
        return jsonify({"ok": status == 200}), status
    return app


def test_smoke_http_ping_checks_status_body_and_security_header():
    """frozen 冒烟必须验「只有真实请求才会跑」的三件事：状态/body/安全头。"""
    assert smoke.run_http_ping(None) == 1, "拿不到 app 必须 fail-closed"
    assert smoke.run_http_ping(_ping_app(), None) == 0
    assert smoke.run_http_ping(_ping_app(nosniff=False), None) == 1, \
        "after_request 没跑却报绿"
    assert smoke.run_http_ping(_ping_app(status=503), None) == 1, \
        "非 200 却报绿"


def test_smoke_http_ping_restores_auth_flag():
    """临时放开鉴权只在冒烟进程内，finally 必须还回去。"""
    class _Mod:
        _AUTH_DISABLED = False

    mod = _Mod()
    assert smoke.run_http_ping(_ping_app(), mod) == 0
    assert mod._AUTH_DISABLED is False, "鉴权闸门被冒烟永久打开了"


def test_smoke_live_server_really_binds_and_serves():
    """再进一层：真 bind socket 起 WSGI，用 urllib 打 /api/ping。"""
    assert smoke.run_live_server(None) == 1, "拿不到 app 必须 fail-closed"
    assert smoke.run_live_server(_ping_app(), None) == 0


def test_smoke_run_is_wired_to_http_layers():
    """接线锁：run() 必须真的调 _run_http_layers，backend --smoke 必须传 app。"""
    src = io.open(os.path.join(ROOT, "core", "smoke.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    called = {getattr(n.func, "id", None) for n in ast.walk(fn)
              if isinstance(n, ast.Call)}
    assert "_run_http_layers" in called, "helper 写了却没接进 run()"
    bsrc = io.open(os.path.join(ROOT, "backend.py"), encoding="utf-8").read()
    assert "_smoke_run(app=app, main_mod=" in bsrc


def test_repo_hygiene_is_soft_and_reports_large_files(tmp_path, monkeypatch, capsys):
    """软告警：真造一个大文件必须被报出来，但恒返回 0 不拦打包。"""
    big = tmp_path / "huge.py"
    big.write_text("\n".join("x = %d" % i for i in range(G.LARGE_FILE_LINES + 5)),
                   encoding="utf-8")
    monkeypatch.setattr(G, "BASE", str(tmp_path))
    rows = G._large_file_rows()
    assert any(rel.endswith("huge.py") and n > G.LARGE_FILE_LINES
               for n, rel in rows), rows
    assert G.check_repo_hygiene() == 0
    out = capsys.readouterr().out
    assert "huge.py" in out and "软提醒" in out, out


def test_repo_hygiene_gate_is_wired_into_build():
    src = io.open(os.path.join(ROOT, "build.py"), encoding="utf-8").read()
    body = src[src.find("def main("):]
    assert "check_repo_hygiene()" in body, "软告警没接进打包流程"
    assert "check_repo_hygiene" in B.__all__
    assert G.LARGE_FILE_LINES == 1200 and G.TOOLS_PY_SOFT_LIMIT == 60


def test_learn_capture_oopif_ws_fallback_is_wired():
    """OOPIF WS 直连必须是 SVG 直连失败后的第二选择，不能只写个 helper 晾着。"""
    src = io.open(os.path.join(ROOT, "services", "learn_capture.py"),
                  encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "capture_course")
    called = {getattr(n.func, "id", None) for n in ast.walk(fn)
              if isinstance(n, ast.Call)}
    assert "_capture_svg_pages" in called and "_capture_oopif_via_ws" in called
    assert "def _capture_oopif_via_ws(" in src


# ============================================================ E. 真实 WebSocket 端点
def _readn(conn, n):
    buf = b""
    while len(buf) < n:
        c = conn.recv(n - len(buf))
        if not c:
            return buf
        buf += c
    return buf


def _recv_frame(conn):
    parts = []
    while True:
        raw = _readn(conn, 2)
        if len(raw) < 2:
            return None
        b0, b1 = raw
        fin, opcode, masked, ln = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _readn(conn, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _readn(conn, 8))[0]
        mask = _readn(conn, 4) if masked else b"\x00" * 4
        payload = bytearray(_readn(conn, ln))
        if masked:
            for i in range(len(payload)):
                payload[i] ^= mask[i & 3]
        if opcode == 8:
            return None
        if opcode == 9:
            _send_frame(conn, 0xA, bytes(payload))
        if fin:
            return b"".join(parts + [bytes(payload)]).decode("utf-8", "replace")


def _send_frame(conn, opcode, payload):
    hdr = bytes([0x80 | opcode])
    if len(payload) < 126:
        hdr += bytes([len(payload)])
    elif len(payload) < 65536:
        hdr += b"\x7e" + struct.pack(">H", len(payload))
    else:
        hdr += b"\x7f" + struct.pack(">Q", len(payload))
    conn.sendall(hdr + payload)


class _RealCdpWsServer:
    """本地**真实** WebSocket CDP 端点：真握手、真收发帧、记录客户端消息。"""

    def __init__(self):
        self.received = []
        self.errors = []
        self._stop = threading.Event()
        self._srv = None
        self.port = 0

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self._srv.settimeout(0.3)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True,
                         name="v731-cdp-ws-mock").start()
        return self

    @property
    def ws_url(self):
        return "ws://127.0.0.1:%d/devtools/page/TEST" % self.port

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError as e:
                self.errors.append("accept: %s" % e)
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                c = conn.recv(4096)
                if not c:
                    return
                buf += c
            head = buf.split(b"\r\n\r\n")[0].decode("latin-1")
            keys = [l.split(":", 1)[1].strip() for l in head.split("\r\n")
                    if l.lower().startswith("sec-websocket-key")]
            accept = base64.b64encode(
                hashlib.sha1((keys[0] + WS_GUID).encode()).digest()).decode()
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                         b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
                         + accept.encode() + b"\r\n\r\n")
            while True:
                msg = _recv_frame(conn)
                if msg is None:
                    return
                m = json.loads(msg)
                self.received.append(m)
                mid, method = m.get("id"), m.get("method")
                if method == "Runtime.evaluate":
                    result = {"result": {"type": "string", "value": "pong"}}
                else:
                    result = {}
                _send_frame(conn, 0x1,
                            json.dumps({"id": mid, "result": result}).encode())
        except Exception as e:  # noqa: BLE001
            self.errors.append("%s: %s" % (type(e).__name__, e))
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def stop(self):
        self._stop.set()
        with contextlib.suppress(OSError):
            self._srv.close()


@pytest.fixture()
def real_ws_server():
    CC._WS_POOL.clear()
    srv = _RealCdpWsServer().start()
    yield srv
    CC.stop_pool_sweeper()
    CC._WS_POOL.clear()
    srv.stop()


def test_real_ws_runtime_enable_once_and_unique_ids(real_ws_server):
    """报告中「4 份 CDP 测试全是 mock」的补齐：真 WS 上验证协议层改造。"""
    srv = real_ws_server
    assert CC._cdp_eval(srv.ws_url, "1+1", timeout=4.0) == "pong"
    assert CC._cdp_eval(srv.ws_url, "2+2", timeout=4.0) == "pong"
    methods = [m.get("method") for m in srv.received]
    assert methods.count("Runtime.enable") == 1, srv.received
    assert methods.count("Runtime.evaluate") == 2, srv.received
    ids = [m.get("id") for m in srv.received]
    assert len(set(ids)) == len(ids) and None not in ids, ids
    assert srv.errors == [], srv.errors


def test_real_ws_cdp_command_roundtrip(real_ws_server):
    """同一条真连接上 _cdp_command 也走全局递增 id，能拿回 result。"""
    srv = real_ws_server
    assert CC._cdp_command(srv.ws_url, "Browser.setDownloadBehavior",
                           {"behavior": "allow"}, timeout=4.0) == {}
    assert CC._cdp_command(srv.ws_url, "Page.enable", timeout=4.0) == {}
    ids = [m.get("id") for m in srv.received]
    assert len(set(ids)) == len(ids), ids
    assert srv.errors == [], srv.errors


# ---------------------------------------------------------------- headless（opt-in）
def _find_browser():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.isfile(p):
            return p
    return None


_BROWSER = _find_browser()
_RUN_HEADLESS = os.environ.get("WECOM_RUN_HEADLESS_CDP") == "1"


@pytest.mark.skipif(not (_RUN_HEADLESS and _BROWSER),
                    reason="真实 headless 浏览器集成需 WECOM_RUN_HEADLESS_CDP=1 且本机装了 Edge/Chrome")
def test_headless_browser_real_cdp_eval(tmp_path):
    """可选的真浏览器集成：真起 headless Chromium，用本项目 CDP 客户端读页面标题。

    默认不进全量套件（外部进程启动有波动），但本机验证时用环境变量单独实跑一次，
    证明协议层在真实 Chromium 上同样成立。
    """
    import subprocess
    import urllib.request
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    profile = tmp_path / "profile"
    proc = subprocess.Popen([
        _BROWSER, "--headless=new", "--remote-debugging-port=%d" % port,
        "--remote-allow-origins=*", "--user-data-dir=%s" % profile,
        "--no-first-run", "--disable-gpu", "--no-sandbox",
        "data:text/html,<title>v731-headless</title><p>hello</p>",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ok = False
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/json/version" % port, timeout=1) as r:
                    ok = r.status == 200
                if ok:
                    break
            except Exception:  # noqa: BLE001  浏览器还没起来，继续等
                time.sleep(0.2)
        assert ok, "headless 浏览器 60×0.2s 内没起来"
        targets = wb.list_targets(port, force_refresh=True)
        pages = wb._pages(targets)
        assert pages, targets
        url = pages[0]["webSocketDebuggerUrl"]
        assert wb._cdp_eval(url, "document.title", timeout=6.0) == "v731-headless"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=8)
