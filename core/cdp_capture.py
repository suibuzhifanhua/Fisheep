# -*- coding: utf-8 -*-
"""v1.0.687：前端冻死时的 CDP 取证与渲染层进程恢复（灵伴）。

背景（11-16 实锤链）：
- 冻结 = 前端 JS 渲染器主线程同步死循环（~100% CPU 单核采样实锤）；
  后端健康、非遮挡误判、非崩溃。
- v1.0.686 实测：页内 Navigate() 救不活自旋渲染器——导航提交要等渲染器
  主线程处理，而它永不让出 → 死锁。整进程重启则必好（13:27 实例实证）。
- WebView2 遵从 --remote-debugging-port（11-16 探针实证 /json + CDP 握手）：
  后端可直接从 127.0.0.1:9229（仅回环）抓渲染器的自旋 JS 调用栈，
  不再需要人肉开 DevTools。

本模块三件事（全部"旁路"，任何失败都不得影响主流程）：
1. capture_stack()          —— CDP 抓冻死前端的 JS 调用栈（Debugger.pause；
                               不响应时退化为 3s 采样 Profiler）；
2. kill_spinner_renderers() —— 杀**我们自己** WebView2 的自旋渲染器子进程
                               （cmdline 含本实例 user-data-dir 才动手，
                               Windows Search 等别人的 webview 一律不碰）；
3. respawn_detached()        —— 最后手段：拉起新实例（独立进程树）+ 环守卫。

CDP 帧 lineNumber 为 0-based（协议规定），展示时 +1。
"""
import json
import os
import subprocess
import time
import urllib.request

try:
    import websocket  # websocket-client 1.9.0（venv 已有；PyInstaller 直连 import 可见）
except Exception:  # noqa: BLE001
    websocket = None   # 冻结包里万一缺失时优雅降级（capture_stack 返回原因）

DEVTOOLS_HOST = "127.0.0.1"
DEVTOOLS_PORT = 9229
# v1.0.731（审查 P2-10）：9229 诊断口握手也必须固定带 Origin（README 6.17 铁律），
# 与 bridge.cdp_client.CDP_CLIENT_ORIGIN 同源。core 层不 import bridge
# （包级循环依赖），这里保留字面量，由 tests/unit/test_v731_cdp_full.py 锁两者一致。
CDP_CLIENT_ORIGIN = "http://127.0.0.1:8787"

# 找进程用的 CIM 快照（只查 msedgewebview2.exe，输出 JSON 供解析）
_PS_EDGE_SNAPSHOT = (
    "Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" "
    "| Select-Object ProcessId,ParentProcessId,CommandLine "
    "| ConvertTo-Json -Depth 3"
)


def get_page_target(port=DEVTOOLS_PORT, prefer_url=None, timeout_s=3.0):
    """GET http://127.0.0.1:<port>/json → page 目标。

    返回 (target|None, detail)：多 page 时优先 url 含 prefer_url 的（本应用页），
    否则取第一个。任何异常都吞成 (None, 原因)——取证不得拖垮主流程。
    """
    try:
        with urllib.request.urlopen(
                "http://%s:%d/json" % (DEVTOOLS_HOST, port), timeout=timeout_s) as _r:
            _targets = json.loads(_r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return None, "GET /json 失败：%r" % e
    if isinstance(_targets, dict):
        _targets = [_targets]
    _pages = [t for t in _targets if isinstance(t, dict)
              and t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not _pages:
        return None, "/json 无 page 目标（共 %d 个目标，9229 可能未开）" % len(_targets)
    for t in _pages:
        if prefer_url and prefer_url in (t.get("url") or ""):
            return t, ""
    return _pages[0], ""


def _frame_from_cdp(f):
    """CDP CallFrame → 本地帧 dict（line 保持 0-based，展示时 +1）。"""
    return {
        "function": f.get("functionName") or "(anonymous)",
        "url": f.get("url") or "(native/builtin)",
        "line0": f.get("lineNumber", 0) or 0,
        "col": f.get("columnNumber", 0) or 0,
    }


def format_stack(frames, limit=15):
    """调用栈 → 多行文本（#00 在最上）。空栈返回占位说明。"""
    if not frames:
        return "(空栈——没抓到任何帧)"
    _lines = []
    for _i, _f in enumerate(frames[:limit]):
        _host = (_f.get("url") or "").split("://")[-1]
        _lines.append("#%02d %s  %s:%d:%d"
                      % (_i, _f.get("function"), _host,
                         (_f.get("line0") or 0) + 1, _f.get("col") or 0))
    if len(frames) > limit:
        _lines.append("...（另有 %d 帧）" % (len(frames) - limit))
    return "\n".join(_lines)


class CdpSession:
    """一次 CDP WebSocket 会话（原始客户端，仅文本帧）。

    send(method) → 消息 id；recv(deadline) → 解析后的消息 dict 或 None
    （失败原因记到 self.err）；close() 尽力 resume+断连。
    所有方法吞异常——取证会话不得把调用方拖下水。
    """

    def __init__(self, ws_url, timeout_s=5.0):
        # v1.0.731（审查 P2-10）：带上 Origin，与 backend 给 9229 配的
        # --remote-allow-origins 白名单配套；不带头时收紧白名单会必 403。
        self.ws = websocket.create_connection(ws_url, timeout=timeout_s,
                                              origin=CDP_CLIENT_ORIGIN)
        self._next = 0
        self.err = ""

    def send(self, method, params=None):
        self._next += 1
        _msg = {"id": self._next, "method": method}
        if params:
            _msg["params"] = params
        try:
            self.ws.send(json.dumps(_msg))
        except Exception as e:  # noqa: BLE001
            self.err = self.err or ("send 失败：%r" % e)
            return self._next - 1
        return self._next

    def recv(self, deadline):
        # v1.0.730（审查 P2-C）：deadline 原先被 `_ = deadline` 直接丢弃，
        # 只靠 create_connection 的 5s 空闲超时——事件风暴下每次 recv 都提前
        # 返回、取证总预算失真（与 cdp_client._recv_until 已修的同款缺陷）。
        # 注意 deadline 是 time.time() 时刻（调用方 `_t_end = time.time()+N`），
        # 不是 monotonic，别混用。
        try:
            _left = deadline - time.time()
            if _left <= 0:
                self.err = self.err or "recv 超过总时限"
                return None
            _cur = self.ws.gettimeout()
            self.ws.settimeout(min(float(_cur) if _cur else _left, _left))
            _m = json.loads(self.ws.recv())
        except Exception as e:  # noqa: BLE001
            self.err = self.err or ("recv 中断：%r" % e)
            return None
        return _m if isinstance(_m, dict) else None

    def close(self):
        try:
            self.send("Debugger.resume")
            self.ws.close()
        except Exception as e:  # noqa: BLE001
            self.err = self.err or ("收尾断连异常（忽略）：%r" % e)


def _try_debugger_pause(sess, timeout_s=10.0):
    """路径 A：Debugger.enable → pause → 等 paused（精确自旋栈）。
    返回 (frames|None, err)；err 非空即本路径没抓到。"""
    sess.send("Debugger.enable")
    _t_end = time.time() + 6
    while time.time() < _t_end:
        _m = sess.recv(_t_end)
        if _m is None:
            return None, "Debugger.enable 等待中断"
        if _m.get("method") == "Debugger.enabled":
            break
    else:
        return None, "Debugger.enable 超时"
    _t_pause = time.time() + timeout_s
    _pid = sess.send("Debugger.pause")
    while time.time() < _t_pause:
        _m = sess.recv(_t_pause)
        if _m is None:
            return None, "Debugger.pause 等待中断"
        if _m.get("method") == "Debugger.paused":
            _cf = (_m.get("params") or {}).get("callFrames") or []
            return [_frame_from_cdp(f) for f in _cf], ""
        if isinstance(_m.get("id"), int) and _m.get("id") == _pid:
            if _m.get("error"):
                # 上下文不支持 pause（真实 CDP 报错形态）→ 交采样路径
                return None, "Debugger.pause 被拒：%s" % \
                    (_m["error"].get("message") or "未知")
            # 普通 ack（真实 Chrome：先 ack 再发 Debugger.paused 事件）→ 继续等
            continue
    return None, "Debugger.pause 超时"


def _try_profiler_sample(sess, sample_s=3.0):
    """路径 B：CPU 采样（不依赖 JS 主线程合作）。返回 (frames|None, err)。"""
    sess.send("Profiler.start")
    time.sleep(sample_s)
    sess.send("Profiler.stop")
    _t_prof = time.time() + 8
    _prof = None
    while time.time() < _t_prof:
        _m = sess.recv(_t_prof)
        if _m is None:
            break
        if _m.get("method") == "Profiler.profileFinished":
            _prof = (_m.get("params") or {}).get("profile") or _prof
        elif isinstance(_m.get("id"), int) and "result" in _m:
            _prof = (_m.get("result") or {}).get("profile") or _prof
            if _prof:
                break
    if not _prof:
        return None, "Profiler 未返回 profile"
    _best = None
    for _n in _prof.get("nodes") or []:
        if isinstance(_n, dict) and (_n.get("hitCount") or 0) > 0:
            if _best is None or _n["hitCount"] > (_best.get("hitCount") or 0):
                _best = _n
    if not (_best and _best.get("callFrame")):
        return None, "Profiler 无有效采样节点"
    return [_frame_from_cdp(_best["callFrame"])], \
        "（采样 Top1，hitCount=%s）" % _best.get("hitCount")


def capture_stack(port=DEVTOOLS_PORT, prefer_url=None,
                  timeout_s=15.0, sample_s=3.0):
    """CDP 抓冻死前端的 JS 调用栈。返回 (ok, frames, detail)。

    路径 A Debugger.pause（精确）；不响应退路径 B 采样（sample_s 秒）。
    任何一步失败都返回 (False, None, 原因)；绝不抛异常、绝不留挂着的连接。
    """
    if websocket is None:
        return False, None, "websocket-client 不可用（import 失败）"
    _target, _detail = get_page_target(port=port, prefer_url=prefer_url)
    if _target is None:
        return False, None, _detail
    try:
        _sess = CdpSession(_target["webSocketDebuggerUrl"])
    except Exception as e:  # noqa: BLE001
        return False, None, "CDP WebSocket 连接失败：%r" % e
    _frames, _err = _try_debugger_pause(_sess)
    if _frames is None:
        _frames, _err = _try_profiler_sample(_sess, sample_s)
    _sess.close()
    if _frames:
        return True, _frames, _sess.err or _err
    return False, None, _sess.err or _err or "未知原因"


# ---------------------------------------------------------------- 进程级恢复


def _terminate_pid(pid):
    """TerminateProcess（仅用于已确认属于本实例的渲染器子进程）。"""
    if not pid:
        return False
    try:
        import ctypes
        _k32 = ctypes.windll.kernel32
        _h = _k32.OpenProcess(0x0001, False, int(pid))   # PROCESS_TERMINATE
        if not _h:
            return False
        try:
            return bool(_k32.TerminateProcess(_h, 1))
        finally:
            _k32.CloseHandle(_h)
    except Exception:  # noqa: BLE001
        return False


def kill_spinner_renderers(user_data_dir, timeout_s=10):
    """杀掉**本实例** WebView2 的自旋渲染器子进程，返回杀掉的个数。

    判据：msedgewebview2.exe 且 cmdline 含本实例 user-data-dir 且含
    --type=renderer。别人的 webview（Windows Search 等，user-data-dir 不同）
    一律不碰。找不到本实例进程 / 查询失败 → 返回 -1（宁可不动）。
    """
    if not user_data_dir:
        return -1
    try:
        _p = subprocess.run(["powershell", "-NoProfile", "-Command", _PS_EDGE_SNAPSHOT],
                            capture_output=True, text=True, timeout=timeout_s)
        _data = json.loads(_p.stdout or "[]")
    except Exception:  # noqa: BLE001
        return -1   # 查询失败：宁可不动，别乱杀
    if isinstance(_data, dict):
        _data = [_data]
    _ours = [d for d in _data if isinstance(d, dict)
             and d.get("CommandLine") and user_data_dir in d["CommandLine"]]
    _renderers = [d for d in _ours if "--type=renderer" in (d.get("CommandLine") or "")]
    _n = 0
    for _d in _renderers:
        if _terminate_pid(_d.get("ProcessId")):
            _n += 1
    return _n


def respawn_detached(exe_path, cwd=None):
    """拉起新实例（独立进程树，当前进程退出后它不跟着死）。成功 True。"""
    try:
        _flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP
        _p = subprocess.Popen([exe_path], cwd=cwd or os.path.dirname(exe_path),
                             creationflags=_flags, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
        return _p.pid > 0
    except Exception:  # noqa: BLE001
        return False


def respawn_allowed(marker_path, min_interval_s=300.0):
    """自重启环守卫：距上次自重启 < min_interval_s → False（防「重启→再冻→重启」）。
    无标记/不可读 → True（首次）。"""
    try:
        with open(marker_path, encoding="utf-8") as _f:
            _last = float(json.load(_f).get("ts", 0))
        return (time.time() - _last) >= min_interval_s
    except Exception:  # noqa: BLE001
        return True


def write_respawn_marker(marker_path):
    """写下自重启时间戳（环守卫用）。成功 True。"""
    try:
        _d = os.path.dirname(marker_path)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as _f:
            json.dump({"ts": time.time()}, _f)
        return True
    except Exception:  # noqa: BLE001
        return False
