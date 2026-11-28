# -*- coding: utf-8 -*-
"""v1.0.687 回归锁：CDP 自旋栈抓取 + 渲染器子进程恢复 + 看门狗接线。

覆盖：
1. capture_stack 对 mock CDP 服务器（Debugger.pause 路径）能抓回调用栈；
2. capture_stack 的 Profiler 采样退化路径（pause 不响应时）；
3. format_stack 行号 0-based→1-based 换算；
4. kill_spinner_renderers 对不存在的 user-data-dir 返回 -1 且不抛；
5. respawn 环守卫（无标记→允许；写标记后短间隔内→拒绝）；
6. backend.py 看门狗接线：自愈前抓栈 + 第 2 次起杀渲染器 + 耗尽后受控自重启。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import cdp_capture  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(name):
    with open(os.path.join(_BASE, name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- mock CDP


def _readn(conn, n):
    buf = b""
    while len(buf) < n:
        c = conn.recv(n - len(buf))
        if not c:
            return buf
        buf += c
    return buf


def _recv_frame(conn):
    """读一个（可分片的）文本帧；close 返回 None。"""
    parts = []
    while True:
        _raw = _readn(conn, 2)
        if len(_raw) < 2:
            return None
        b0, b1 = _raw
        fin = b0 & 0x80
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        ln = b1 & 0x7F
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
        if opcode == 9:  # ping → pong
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


def _send_event(conn, method, params):
    _send_frame(conn, 0x1, json.dumps({"method": method, "params": params}).encode())


def _send_response(conn, mid, result):
    _send_frame(conn, 0x1, json.dumps({"id": mid, "result": result}).encode())


def _handle_ws(conn, port, mode):
    """处理一条已升级的 CDP 会话。mode: pause=立即回 paused；profiler=pause 装死。"""
    try:
        while True:
            msg = _recv_frame(conn)
            if msg is None:
                return
            m = json.loads(msg)
            method = m.get("method")
            mid = m.get("id")
            if method == "Debugger.enable":
                _send_response(conn, mid, {})
                _send_event(conn, "Debugger.enabled", {})
            elif method == "Debugger.pause":
                if mode == "pause":
                    _send_response(conn, mid, {})   # ack，随后发 paused 事件
                    _send_event(conn, "Debugger.paused", {
                        "reason": "PauseRequested",
                        "callFrames": [
                            {"functionName": "trimSettingsRows",
                             "url": "http://127.0.0.1:8787/js/11-settings.js",
                             "lineNumber": 122, "columnNumber": 4, "scriptId": "s1"},
                            {"functionName": "openView",
                             "url": "http://127.0.0.1:8787/js/02-nav-quicklinks.js",
                             "lineNumber": 71, "columnNumber": 9, "scriptId": "s2"},
                        ]})
                else:
                    # 上下文不支持 pause（真实 CDP 报错形态）→ 客户端立即转采样
                    _send_frame(conn, 0x1, json.dumps({
                        "id": mid,
                        "error": {"code": -32601,
                                  "message": "'Debugger.pause' wasn't found"},
                    }).encode())
            elif method == "Profiler.start":
                _send_response(conn, mid, {})
            elif method == "Profiler.stop":
                _send_response(conn, mid, {"profile": {"nodes": [
                    {"callFrame": {"functionName": "idleTick",
                                   "url": "http://127.0.0.1:8787/js/01-core.js",
                                   "lineNumber": 10, "columnNumber": 0},
                     "hitCount": 2},
                    {"callFrame": {"functionName": "hotSpin",
                                   "url": "http://127.0.0.1:8787/js/05-connectors-status.js",
                                   "lineNumber": 87, "columnNumber": 1},
                     "hitCount": 937},
                ]}})
            elif method == "Debugger.resume":
                _send_response(conn, mid, {})
                return
            else:
                _send_response(conn, mid, {})
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("mock handler: %r\n" % e)


def _serve_mock(port_box, mode, stop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port_box[0] = srv.getsockname()[1]
    srv.settimeout(0.5)
    while not stop.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle_one, args=(conn, port_box[0], mode),
                         daemon=True).start()
    try:
        srv.close()
    except OSError as e:  # noqa: BLE001
        sys.stderr.write("mock srv close: %r\n" % e)


def _handle_one(conn, port, mode):
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = conn.recv(4096)
            if not c:
                return
            buf += c
        head = buf.split(b"\r\n\r\n")[0].decode("latin-1")
        if head.startswith("GET /json"):
            body = json.dumps([{
                "type": "page", "id": "TEST", "url": "http://127.0.0.1:8787/",
                "webSocketDebuggerUrl":
                    "ws://127.0.0.1:%d/devtools/page/TEST" % port,
            }]).encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                          b"Content-Length: " + str(len(body)).encode()
                          + b"\r\nConnection: close\r\n\r\n" + body)
            return
        key = [l.split(":", 1)[1].strip() for l in head.split("\r\n")
               if l.lower().startswith("sec-websocket-key")][0]
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                     b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
                     + accept.encode() + b"\r\n\r\n")
        _handle_ws(conn, port, mode)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("mock conn: %r\n" % e)
    finally:
        try:
            conn.close()
        except OSError as e:  # noqa: BLE001
            sys.stderr.write("mock conn close: %r\n" % e)


# ---------------------------------------------------------------- 测试


def test_capture_stack_pause_path():
    port_box = [0]
    stop = threading.Event()
    t = threading.Thread(target=_serve_mock, args=(port_box, "pause", stop),
                         daemon=True)
    t.start()
    for _ in range(50):
        if port_box[0]:
            break
        time.sleep(0.05)
    ok, frames, detail = cdp_capture.capture_stack(
        port=port_box[0], prefer_url="127.0.0.1:8787",
        timeout_s=15, sample_s=0.2)
    stop.set()
    assert ok, "pause 路径应抓到栈（detail=%s）" % detail
    assert frames and len(frames) >= 2
    assert frames[0]["function"] == "trimSettingsRows"
    assert "11-settings.js" in frames[0]["url"]
    assert frames[0]["line0"] == 122        # 保持 0-based
    txt = cdp_capture.format_stack(frames)
    assert "11-settings.js:123:4" in txt     # 展示时 +1


def test_capture_stack_profiler_fallback():
    port_box = [0]
    stop = threading.Event()
    t = threading.Thread(target=_serve_mock, args=(port_box, "profiler", stop),
                         daemon=True)
    t.start()
    for _ in range(50):
        if port_box[0]:
            break
        time.sleep(0.05)
    ok, frames, detail = cdp_capture.capture_stack(
        port=port_box[0], prefer_url="127.0.0.1:8787",
        timeout_s=15, sample_s=0.2)
    stop.set()
    assert ok, "采样路径应抓到 Top1（detail=%s）" % detail
    assert frames and frames[0]["function"] == "hotSpin"
    assert "05-connectors-status.js" in frames[0]["url"]
    assert "05-connectors-status.js:88:1" in cdp_capture.format_stack(frames)


def test_capture_stack_devtools_down():
    # 无监听端口 → 优雅失败，不抛
    ok, frames, detail = cdp_capture.capture_stack(port=1, timeout_s=3)
    assert not ok and frames is None
    assert detail


def test_format_stack_empty():
    assert "空栈" in cdp_capture.format_stack([])


def test_kill_renderers_unknown_dir():
    # 给一个不存在的 user-data-dir：查询本身成功，但没有任何本实例渲染器匹配
    # → 应「杀 0 个」，绝不误杀 Windows Search 等别人家的 webview。
    n = cdp_capture.kill_spinner_renderers("C:\\no-such-dir-lingban-test")
    assert n in (0, -1)   # 0=查到进程但无一匹配；-1=查询失败（两者都代表没乱杀）


def test_respawn_guard(tmp_path):
    marker = str(tmp_path / "respawn_marker.json")
    assert cdp_capture.respawn_allowed(marker)          # 无标记 → 允许
    assert cdp_capture.write_respawn_marker(marker)
    assert not cdp_capture.respawn_allowed(marker, 300)  # 刚重启过 → 拒绝
    assert cdp_capture.respawn_allowed(marker, 0)        # 间隔 0 → 放行


def test_watchdog_wires_cdp_and_escalation():
    s = _src("backend.py")
    assert "from core import cdp_capture" in s, "自愈模块必须接 CDP 取证"
    assert "_cdp.capture_stack(" in s, "自愈前必须先抓自旋栈"
    assert "_cdp.kill_spinner_renderers(" in s, "第 2 次自愈起必须杀自旋渲染器子进程"
    assert "_cdp.respawn_detached(" in s, "自愈耗尽后必须有受控自重启"
    assert "_cdp.respawn_allowed(" in s, "自重启必须有环守卫（防重启死循环）"
    assert "window.load_url(" in s
    assert "window.reload()" not in s
    # 看门狗主体不得超巨型函数阈值（gate 的 GIANT_FUNC_MAX_LINES=100）
    import ast
    tree = ast.parse(s)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in ("_js_liveness_watchdog", "_webview_self_heal",
                                   "_auto_respawn"):
            assert (node.end_lineno - node.lineno + 1) < 100, \
                "%s 超 100 行" % node.name
