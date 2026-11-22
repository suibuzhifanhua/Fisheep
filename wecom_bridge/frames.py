# -*- coding: utf-8 -*-
"""wecom_bridge.frames — iframe 上下文（v1.0.230）

背景：企业学习平台（知鸟）的课件渲染在**同源 iframe** 内。
同源 iframe 不会在 /json/list 里单独出现（只有 OOPIF 才会），
主 frame 的 document.querySelectorAll 也看不到 iframe 内部 DOM，
所以 read_page / click_element 对课件一律失效（表现为"只采到第 1 页"）。

解决：一条 WebSocket 会话内
  1) Runtime.enable → 收集 Runtime.executionContextCreated 得到 frameId→contextId
  2) Page.getFrameTree → 得到 frameId→url/name 树
  3) Runtime.evaluate 带 contextId → 在指定 iframe 内执行 JS

本模块把这套流程封装成可复用 API，供 learn_capture 等使用。
"""
import contextlib
import json
import threading
import time   # v1.0.652：_wait/_enable 总时限用 monotonic

from .cdp_client import _open_ws, _close_ws_quiet, _logw
from core.result import err_msg

# ---------- v1.0.731（审查 P1-3）：专用会话的并发边界 ----------
# FrameSession **刻意不复用 _WS_POOL**：它要在一条全新 WS 上发 Runtime.enable 并
# 收全量 executionContextCreated 事件，池化连接被 _cdp_eval 共用会互相污染事件流
# （且池化连接只 enable 一次，不会重推上下文）。代价是同页面可能并存池内 + 直连
# 两条会话，多占 devtools 会话数——所以显式标出边界，并限制同时存活的专用会话数。
FRAME_SESSION_MAX = 4
_FRAME_SESSION_LIVE = set()          # 存 id(self)，不持有强引用（不阻碍 GC）
_FRAME_SESSION_LOCK = threading.Lock()


def _frame_session_register(obj):
    with _FRAME_SESSION_LOCK:
        if len(_FRAME_SESSION_LIVE) >= FRAME_SESSION_MAX:
            raise RuntimeError(f"FrameSession 并发已达上限 {FRAME_SESSION_MAX}——"
                               f"请先用 close() / with 归还上一条专用会话再开新的")
        _FRAME_SESSION_LIVE.add(id(obj))
        return len(_FRAME_SESSION_LIVE)


def _frame_session_unregister(obj):
    with _FRAME_SESSION_LOCK:
        _FRAME_SESSION_LIVE.discard(id(obj))
        return len(_FRAME_SESSION_LIVE)


def active_frame_sessions():
    """当前存活的专用会话数（诊断 / 回归用）。"""
    with _FRAME_SESSION_LOCK:
        return len(_FRAME_SESSION_LIVE)

# 探针：返回该 frame 的内容特征，用于判断"课件在哪一层"
PROBE_JS = (
    "(() => {"
    " const b = document.body ? document.body.innerText : '';"
    " return JSON.stringify({"
    "  len: b.length,"
    "  canvas: document.querySelectorAll('canvas').length,"
    "  iframe: document.querySelectorAll('iframe').length,"
    "  video: document.querySelectorAll('video').length,"
    "  head: b.slice(0, 160)"
    " });"
    "})()"
)


class FrameSession(object):
    """一条**专用** CDP WebSocket 会话，管理该页面的所有 frame 上下文。

    v1.0.731（审查 P1-3）：刻意不走 _WS_POOL（理由见模块头注释）。会话在构造时
    登记、close() 时归还，忘关由 __del__ 兜底；超过 FRAME_SESSION_MAX 直接拒绝，
    避免无人值守跑课累积耗尽 devtools 会话数。可用 with FrameSession(...) as s。
    """

    def __init__(self, ws_url, timeout=10):
        self._closed = False
        self._frame_registered = False
        self.ws = _open_ws(ws_url, timeout)
        if isinstance(self.ws, dict):  # 连接失败
            raise RuntimeError(err_msg(self.ws))
        _frame_session_register(self)
        self._frame_registered = True
        self.timeout = timeout
        self._id = 0
        self.ctxs = []        # [{id, frameId, name, origin}]
        self.frames = []      # [{id, name, url, depth}]
        try:
            self._enable()
        except Exception:
            self.close()      # 登记后初始化失败也要归还槽位
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        # 兜底：调用方漏了 close 也不能把 devtools 会话永久占住。
        # contextlib.suppress 而非裸 except-pass（门禁棘轮口径）。
        with contextlib.suppress(Exception):
            self.close()

    # ---------- 底层收发 ----------
    def _send(self, method, params=None):
        self._id += 1
        i = self._id
        msg = {"id": i, "method": method}
        if params is not None:
            msg["params"] = params
        self.ws.send(json.dumps(msg))
        return i

    def _wait(self, i):
        # v1.0.652（审计 P1-10）：补总时限——ws 空闲超时挡不住事件流不断（与
        # cdp_client._recv_until 同款缺陷），课件采集页事件多时会永久挂住。
        deadline = time.monotonic() + float(getattr(self, "timeout", 25) or 25)
        while True:
            _left = deadline - time.monotonic()
            if _left <= 0:
                return None
            try:
                self.ws.settimeout(min(float(self.ws.gettimeout() or _left), _left))
                m = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001
                return None
            if m.get("id") == i:
                return m

    def _enable(self):
        """Runtime.enable + 收集已存在的执行上下文（含所有同源 iframe）。"""
        i = self._send("Runtime.enable")
        deadline = time.monotonic() + float(getattr(self, "timeout", 25) or 25)   # v1.0.652
        while True:
            if time.monotonic() >= deadline:
                break
            try:
                m = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001
                break
            if m.get("id") == i:
                break
            if m.get("method") == "Runtime.executionContextCreated":
                c = m["params"]["context"]
                if c.get("type") != "other":
                    self.ctxs.append({
                        "id": c.get("id"),
                        "frameId": (c.get("auxData") or {}).get("frameId"),
                        "name": c.get("name") or "",
                        "origin": c.get("origin") or "",
                    })
        self._load_tree()

    def _load_tree(self):
        i = self._send("Page.getFrameTree")
        m = self._wait(i)
        if not m or "result" not in m:
            return

        def walk(node, dep=0):
            f = node.get("frame") or node
            self.frames.append({
                "id": f.get("id"),
                "name": f.get("name") or "",
                "url": f.get("url") or "",
                "depth": dep,
            })
            for c in (node.get("childFrames") or []):
                walk(c, dep + 1)

        walk(m["result"].get("frameTree") or {})

    # ---------- 对外 API ----------
    def eval(self, expr, ctx_id=None, timeout=None):
        """在指定 contextId（默认主 frame）内执行 JS，返回 Python 值。"""
        params = {"expression": expr, "returnByValue": True, "awaitPromise": True}
        if ctx_id is not None:
            params["contextId"] = ctx_id
        i = self._send("Runtime.evaluate", params)
        m = self._wait(i)
        if not m:
            return {"ok": False, "error": "evaluate 超时"}
        if "error" in m:
            return {"ok": False, "error": str(m["error"])}
        _res = m.get("result") or {}
        # v1.0.730（审查 P1-B）：JS 抛错不走顶层 msg["error"]，而是
        # result.exceptionDetails —— 原实现漏检，异常被当成"没有 value"静默返回
        # None，probe_all 再一丢，课件采集就选错 frame 且全程无痕。
        # 口径与 cdp_client._cdp_eval（503-514 行）保持一致。
        if _res.get("exceptionDetails"):
            _ed = _res["exceptionDetails"]
            _desc = ((_ed.get("exception") or {}).get("description")
                     or _ed.get("text") or "frame JS 异常")
            return {"ok": False, "error": "frame JS 异常：%s" % str(_desc)[:500]}
        r = _res.get("result") or {}
        if r.get("type") == "undefined":
            return None
        if r.get("type") == "object" and r.get("subtype") == "error":
            return {"ok": False, "error": "frame JS 异常：%s" % str(r.get("description") or "")[:500]}
        return r.get("value")

    def frame_of(self, ctx_id):
        for c in self.ctxs:
            if c["id"] == ctx_id:
                for f in self.frames:
                    if f["id"] == c["frameId"]:
                        return f
                return {"id": c["frameId"], "url": "", "name": c["name"], "depth": -1}
        return None

    def probe_all(self):
        """对所有 frame 跑探针，返回 [{ctx, frame, len, canvas, iframe, video, head}]。"""
        out = []
        for c in self.ctxs:
            v = self.eval(PROBE_JS, c["id"])
            info = {"ctx": c["id"], "frame": self.frame_of(c["id"]) or {}}
            # v1.0.730（审查 P1-B）：原为 `info.update(...) if isinstance(v, str)
            # else None` 表达式语句——eval 返回 dict（结构化错误 / returnByValue
            # 直接给对象）时探针结果被整条丢弃，info 只剩 ctx/frame，
            # pick_content_frame 必然选错 frame。改为显式分支，错误也要留痕。
            if isinstance(v, str):
                try:
                    info.update(json.loads(v))
                except Exception as e:  # noqa: BLE001
                    info["probe_error"] = "探针 JSON 解析失败：%s" % e
            elif isinstance(v, dict):
                if v.get("ok") is False or "error" in v or "__error__" in v:
                    info["probe_error"] = str(v.get("error") or v.get("__error__") or "探针失败")
                else:
                    info.update(v)
            elif v is not None:
                info["probe_error"] = "探针返回非预期类型：%s" % type(v).__name__
            out.append(info)
        return out

    def pick_content_frame(self, want_canvas=False, want_video=False, min_len=0):
        """挑出"最像课件"的 frame context：优先 canvas/video 数量多的，其次正文长的。"""
        best, best_score = None, -1
        for info in self.probe_all():
            score = 0
            if want_canvas:
                score += (info.get("canvas") or 0) * 100
            if want_video:
                score += (info.get("video") or 0) * 100
            score += min(info.get("len") or 0, 2000) / 100.0
            if (info.get("len") or 0) < min_len:
                score -= 500
            if score > best_score:
                best, best_score = info, score
        return best

    def screenshot(self, fmt="png", front=None):
        """在本会话连接的窗口上截图，返回 PNG 字节（失败返回 b''）。

        为什么不复用 wecom_bridge.capture_screenshot：它带 4s 超时 + DOM 绘制降级，
        课件（canvas 渲染）超时降级后会返回不含最新画布的画面，表现为"多页 OCR 内容重复"。
        课件采集要求"截到的一定是当前帧"，宁可失败也不降级（v1.0.230 实测）。

        v1.0.523：front=None（默认）时按静默模式自动判定——静默模式（wecom.silent_mode
        默认开）下不 bringToFront（不抢前台焦点），课件采集可能拿到旧帧或空帧，
        由调用方 _take_shot 的既有失败处理兜底；显式传 True 强制前置（旧行为）。
        """
        if front is None:
            try:
                from core.config_store import silent_mode
                front = not silent_mode()
            except Exception:  # noqa: BLE001
                front = True
        if front:
            i = self._send("Page.bringToFront")
            self._wait(i)
        i = self._send("Page.captureScreenshot", {"format": fmt, "captureBeyondViewport": False})
        m = self._wait(i)
        if not m or "error" in m:
            return b""
        import base64
        data = (m.get("result") or {}).get("data") or ""
        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            return b""

    def close(self):
        """幂等断连 + 归还并发槽位（v1.0.731）。"""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.ws.close()
        except Exception as e:  # noqa: BLE001
            _logw(f"[frames] 关闭专用会话失败（忽略）：{type(e).__name__}: {e}")
        if getattr(self, "_frame_registered", False):
            self._frame_registered = False
            _frame_session_unregister(self)


def iframe_targets(tg):
    """从原始 /json 列表里取 OOPIF（type=iframe）目标——**不能**走 _pages()。"""
    if isinstance(tg, dict):
        return []
    return [t for t in tg if isinstance(t, dict)
            and t.get("type") == "iframe" and t.get("webSocketDebuggerUrl")]


def open_session(port=None, pick=0, allow_iframe=False):
    """按窗口序号打开 FrameSession。
    v1.0.364 审查修复（H3）：port=None 时从配置读取 cdp_port（原写死 9222，
    用户改端口后此路径失联）。v1.0.588：口径收敛到 core.config_store.cdp_port。

    v1.0.731（审查 P1-2）：allow_iframe=True 时按 README 6.13 的要求直连 OOPIF——
    从 list_targets 的**原始列表**里筛 type=iframe 的独立 WS 地址。旧实现无论怎么
    调都走 _pages()（只留 type=page），跨域 OOPIF 直连方案在当前代码里根本不可达，
    只剩 HTTP 拉 SVG 一条兜底。
    """
    if port is None:
        from core.config_store import cdp_port
        port = cdp_port()
    from .windows import list_targets, _pages, pick_window
    tg = list_targets(port)
    if isinstance(tg, dict):
        raise RuntimeError(err_msg(tg))
    if allow_iframe:
        oopifs = iframe_targets(tg)
        if not oopifs:
            raise RuntimeError("没有可直连的 OOPIF iframe 目标（/json 里 type=iframe）")
        if pick is None:
            t = oopifs[0]
        elif isinstance(pick, int) and 0 <= pick < len(oopifs):
            t = oopifs[pick]
        elif isinstance(pick, str) and pick.strip().isdigit() \
                and 0 <= int(pick.strip()) < len(oopifs):
            t = oopifs[int(pick.strip())]
        elif isinstance(pick, str) and pick.strip():
            sl = pick.strip().lower()
            t = next((x for x in oopifs
                      if sl in (x.get("url") or "").lower()
                      or sl in (x.get("title") or "").lower()), None)
        else:
            t = None
        if not t or not t.get("webSocketDebuggerUrl"):
            raise RuntimeError(f"OOPIF iframe 序号/匹配无效（pick={pick}，共 {len(oopifs)} 个）")
        return FrameSession(t["webSocketDebuggerUrl"])
    # pick 语义与 read_page / click_element 一致：_pages() 过滤后的序号
    t = pick_window(tg, pick) if pick is not None else None
    if not t:
        pages = _pages(tg)
        if not pages:
            raise RuntimeError("没有可连接的窗口")
        t = pages[0]
    if not t.get("webSocketDebuggerUrl"):
        raise RuntimeError("该窗口无调试地址")
    return FrameSession(t["webSocketDebuggerUrl"])
