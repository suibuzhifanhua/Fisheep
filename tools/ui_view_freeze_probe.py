# -*- coding: utf-8 -*-
"""tools/ui_view_freeze_probe.py — 「点某个视图会不会把渲染器搞死」实测探针

要回答的问题
------------
进软件点某个模块（设置/工作台/…）之后，整个窗口是不是**真卡死了**？
——不是「页面看起来没反应」，而是**可证伪的判定**：渲染器主线程还活着吗？

为什么需要它
------------
v1.0.685~687 连续三个版本在这件事上打转：用户报「点一个模块就卡死」，
686 实锤了「渲染器主线程 100% CPU 同步死循环」，但**没定位到是哪个函数**——
静态阅读穷尽 while(true)/do-while/for(;;)/rAF/全局定时器 全都没命中，
因为真凶是「MutationObserver 回调自己改 class 属性 → 观察器再回调自己」
这种**微任务自激**（静态看只是两行正常的代码）。

本探针把这套「证据链」固化成可重复执行的工具：
  ① 起一个只读桩后端（前端静态文件 + 真实 API 快照），**不碰真实后端/企微**
  ② 用 Edge headless 打开页面，CDP 接管
  ③ 逐个视图调 openView(name)
  ④ 每秒往页面塞一个 `1+1` 表达式并计时
     · 1.5s 内返回 → 该视图没把渲染器搞死
     · 超时/无响应 → **主线程已死**（就是用户看到的卡死），并立刻抓 JS 调用栈
  ⑤ 抓栈走 Debugger.pause（拿得到就打印函数名:行号），失败退化为 Profiler 采样

用法
----
    python tools/ui_view_freeze_probe.py                     # 全部视图
    python tools/ui_view_freeze_probe.py --views settings     # 只测设置页
    python tools/ui_view_freeze_probe.py --keep-server        # 桩后端常驻（调试用）

退出码：0=全部视图渲染器均存活；1=有视图把渲染器搞死了；2=环境问题（没 Edge 等）。
"""
import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

# 侧栏视图 id（index.html 里 .view 的 id 与 openView() 的入参一致）
ALL_VIEWS = ["work", "chat", "knowledge", "study", "skills", "experiences",
              "connectors", "workspace", "schedules", "components", "help", "settings"]

# 每个视图进页时会被 openView 调用的**最后一个**脚本里的函数。
# 必须等它就位再点——否则 openView 里那句 initSettingsSideNav() 会撞上
# 「函数未定义」，而 openView 的 try/catch 会把异常吞掉 → 视图看着「正常」，
# 探针却啥也没测到（假阴性）。v1.0.688 实测踩过这个坑：
# openView 定义在 js/02（先加载），initSettingsSideNav 在 js/11（后加载），
# 只等 openView 会在 js/11 就位前就点进去 → 永远测不到设置页的真凶。
VIEW_READY_FN = {
    "settings": "initSettingsSideNav",
}

PROBE_TIMEOUT_S = 1.5      # 表达式超过这个秒数就算「主线程被占死」
SAMPLE_WINDOW = 6          # 每个视图观察多少秒


def log(msg):
    print("[freeze-probe] %s" % msg, flush=True)


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


# ------------------------------------------------------------------ 桩后端

STUB_SERVER_SRC = r'''
# -*- coding: utf-8 -*-
"""只读桩后端：静态服务 index.html/js/css + 应答所有 /api/*。
不连真实后端、不碰企微、不写任何业务数据。

/api/* 的应答有两种来源：
  ① 若项目根下有 _api_data/ 快照目录 → 用快照（更贴近真实响应形状）
  ② 没有快照 → 用内置的最小应答（探针只需要「接口别报错」，不校验业务数据）
快照目录不进版本库（含开发机路径），所以 ② 必须能独立跑。
"""
import json
import os
import sys
from flask import Flask, Response, request, send_from_directory

ROOT = sys.argv[1]
API_DIR = os.path.join(ROOT, "_api_data")
PORT = int(sys.argv[2])

app = Flask(__name__, static_folder=None)


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def _static(path):
    full = os.path.normpath(os.path.join(ROOT, path))
    if not full.startswith(ROOT):
        return Response("denied", status=403)
    if os.path.isfile(full):
        return send_from_directory(ROOT, path)
    return Response("not found", status=404)


# 无快照时的兜底应答：前端各 loader 只要拿到「形状对」的对象就不会抛。
FALLBACK = {
    "config": {"ui": {}, "quick_links": [], "security": {}, "close_action": ""},
    "status": {"ok": True, "wecom": {}, "windows": []},
    "admin/status": {"is_admin": False, "elevatable": True},
    "autostart": {"enabled": False},
    "personal/config": {"ok": True, "reimburse": {}, "dsh": {}, "background": {}, "budget": {}},
    "security/config": {"security": {}, "audit": []},
    "versions": {"current": "", "versions": []},
    "conversations": {"conversations": []},
    "connectors": {"connectors": []},
    "models": {"models": []},
    "workspace": {"root": ""},
    "knowledge": {"files": []},
    "dashboard": {"ok": True},
    "experiences": {"experiences": []},
    "schedules": {"schedules": []},
    "ocr/status": {"installed": False},
    "asr/status": {"installed": False},
    "close_ask": {"pending": False},
    "ping": {"ok": True},
}


def _load(name):
    p = os.path.join(API_DIR, "api_" + name.replace("/", "_") + ".json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/api/<path:rest>", methods=["GET", "POST", "PUT", "DELETE"])
def _api(rest):
    body = _load(rest)
    if body is None:
        parts = rest.split("/")
        for i in range(len(parts) - 1, 0, -1):
            body = _load("/".join(parts[:i]))
            if body is not None:
                break
    if body is None:
        hit = FALLBACK.get(rest)
        if hit is None:
            for k, v in FALLBACK.items():
                if rest.startswith(k):
                    hit = v
                    break
        body = json.dumps(hit if hit is not None else {"ok": True})
    return Response(body, mimetype="application/json")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, threaded=True)
'''


def start_stub_server(port):
    """起只读桩后端；返回 (proc, api_data_dir 或 None)。"""
    api_dir = os.path.join(ROOT, "_api_data")
    has_snap = os.path.isdir(api_dir)
    if not has_snap:
        log("没有 _api_data 快照目录 → 用内置最小应答（不影响探针判定）")
    src = os.path.join(HERE, "_freeze_probe_stub_server.py")
    with open(src, "w", encoding="utf-8") as f:
        f.write(STUB_SERVER_SRC)
    proc = subprocess.Popen([sys.executable, src, ROOT, str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.25)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2) as r:
                r.read()
            return proc, api_dir
        except Exception:  # noqa: BLE001 —— 桩后端起不来（端口占用/缺 flask）
            proc.kill()
            return None, None


# ------------------------------------------------------------------ CDP

class Page:
    """最小 CDP 客户端：只够本探针用（Runtime.evaluate / Debugger.pause / Profiler）。"""

    def __init__(self, ws_url, timeout=8):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self.i = 0

    def send(self, method, params=None, timeout=8):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        self.ws.settimeout(timeout)
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                if "error" in r:
                    raise RuntimeError("%s -> %s" % (method, r["error"]))
                return r.get("result", {})

    def eval(self, expr, timeout=8):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True}, timeout=timeout)
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(str(res.get("description", "eval error"))[:200])
        return res.get("value")

    def grab_stack(self):
        """抓自旋时的 JS 栈：Debugger.pause 优先，失败退化为 Profiler 采样。"""
        try:
            self.send("Debugger.enable", timeout=4)
            self.send("Debugger.pause", timeout=4)
            deadline = time.time() + 6
            while time.time() < deadline:
                self.ws.settimeout(2)
                r = json.loads(self.ws.recv())
                if r.get("method") == "Debugger.paused":
                    frames = r.get("params", {}).get("callFrames", [])
                    log("=== 自旋栈（Debugger.pause，%d 帧）===" % len(frames))
                    for f in frames[:20]:
                        cf = f.get("callFrame", f)
                        fn = cf.get("functionName") or "(anonymous)"
                        url = str(cf.get("url", "")).split("/")[-1]
                        line = cf.get("location", {}).get("lineNumber", -1) + 1
                        log("   %-30s %s:%s" % (fn[:30], url, line))
                    # resume 失败无所谓（渲染器已卡死，拿不到栈也一样）
                    with contextlib.suppress(Exception):
                        self.send("Debugger.resume", timeout=3)
                    return
        except Exception as e:
            log("Debugger.pause 失败（%s）—— 退化 Profiler 采样" % type(e).__name__)
        try:
            self.send("Profiler.enable", timeout=4)
            self.send("Profiler.start", {"samplingInterval": 100}, timeout=4)
            time.sleep(3)
            prof = self.send("Profiler.stop", timeout=6)
            nodes = prof.get("profile", {}).get("nodes", [])
            counts = {}
            for s in prof.get("profile", {}).get("samples", []):
                counts[s] = counts.get(s, 0) + 1
            log("=== Profiler 采样 Top10 ===")
            for nid, c in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
                nd = next((n for n in nodes if n.get("id") == nid), None)
                if not nd:
                    continue
                cf = nd.get("callFrame", {})
                url = str(cf.get("url", "")).split("/")[-1]
                line = cf.get("lineNumber", -1) + 1
                log("   %-30s %s:%s  (%d samples)"
                    % ((cf.get("functionName") or "(anon)")[:30], url, line, c))
        except Exception as e:
            log("Profiler 采样也失败：%s" % type(e).__name__)

    def close(self):
        # 关连接失败无所谓（探针收尾）
        with contextlib.suppress(Exception):
            self.ws.close()


def http_json(port, path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def probe_view(page, view, window_s):
    """点进一个视图，返回 (是否把渲染器搞死, 说明)。"""
    # 进页前先确认该视图要用到的函数已就位（见 VIEW_READY_FN 的说明）
    ready_fn = VIEW_READY_FN.get(view)
    if ready_fn:
        try:
            ok = page.eval("typeof %s === 'function' ? 1 : 0" % ready_fn, timeout=5)
        except Exception as e:
            return True, "检查 %s 是否就位时页面已无响应：%s" % (ready_fn, type(e).__name__)
        if ok != 1:
            return False, ("⚠ %s 尚未就位，本视图未真正测到（openView 里的异常被"
                           "try/catch 吞了 → 假阴性）" % ready_fn)
    try:
        page.eval("openView(%r)" % view, timeout=8)
    except Exception as e:
        # openView 本身超时 = 主线程在调用期间就被占死了
        return True, "openView(%s) 本身超时：%s" % (view, type(e).__name__)
    deadline = time.time() + window_s
    worst = 0.0
    while time.time() < deadline:
        time.sleep(0.5)
        t0 = time.time()
        try:
            page.eval("1+1", timeout=3)
        except Exception as e:
            return True, "表达式无响应：%s" % type(e).__name__
        worst = max(worst, time.time() - t0)
    if worst > PROBE_TIMEOUT_S:
        return True, "表达式最慢 %.2fs（>%.1fs）" % (worst, PROBE_TIMEOUT_S)
    return False, "存活（最慢 %.2fs）" % worst


def launch_browser(edge, cdp_port, url):
    """起 headless 浏览器并等 CDP 就位，返回 (proc, page)。"""
    ud = os.path.join(os.environ.get("LOCALAPPDATA", "C:////Windows////Temp"),
                      "edge-freeze-probe-%d" % cdp_port)
    if os.path.isdir(ud):
        shutil.rmtree(ud, ignore_errors=True)
    proc = subprocess.Popen(
        [edge, "--headless=new", "--remote-debugging-port=%d" % cdp_port,
         "--user-data-dir=" + ud, "--no-first-run", "--no-default-browser-check",
         "--disable-features=Translate,CalculateNativeWinOcclusion", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(50):
        time.sleep(0.5)
        tg = http_json(cdp_port, "/json")
        if tg:
            for t in tg:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    ws_url = t["webSocketDebuggerUrl"]
        if ws_url:
            break
    if not ws_url:
        log("CDP 未就绪")
        return proc, None
    return proc, Page(ws_url)


def wait_frontend_ready(page, views):
    """等首屏落定：openView 只是「入口」，还得等各视图自己要用的函数就位。

    js/02 定义 openView，js/11 才定义 initSettingsSideNav——只等 openView 的话，
    会在 js/11 就位前就点进设置页，撞上 undefined 被 openView 的 try/catch 吞掉 →
    探针啥也没测到 → **假阴性**（明明有 bug 却报「存活」，v1.0.688 实测踩过）。
    """
    need = ["openView"] + [VIEW_READY_FN[v] for v in views if v in VIEW_READY_FN]
    probe = " && ".join("typeof %s === 'function'" % n for n in need)
    for _ in range(60):
        time.sleep(0.5)
        try:
            if page.eval("(%s) ? 1 : 0" % probe, timeout=5) == 1:
                return True
        except Exception:  # noqa: BLE001 —— 页面还没起来，继续等下一轮
            continue
    log("首屏 JS 没活（%s 未就位）—— 启动期就有问题，与具体视图无关" % "/".join(need))
    return False


def parse_args():
    ap = argparse.ArgumentParser(description="实测各视图会不会把渲染器主线程搞死")
    ap.add_argument("--views", default="", help="逗号分隔的视图 id（默认全部）")
    ap.add_argument("--port", type=int, default=8799, help="桩后端端口")
    ap.add_argument("--cdp-port", type=int, default=9400, help="CDP 端口")
    ap.add_argument("--keep-server", action="store_true", help="结束后保留桩后端")
    ap.add_argument("--live-url", default="",
                    help="直接测已在跑的服务（如 http://127.0.0.1:8787/ 跑着的正式 exe）"
                         "，不另起桩后端 —— 测的是 exe 里真正在跑的那份前端")
    return ap.parse_args()


def main():
    args = parse_args()
    views = [v.strip() for v in args.views.split(",") if v.strip()] or ALL_VIEWS

    edge = find_edge()
    if not edge:
        log("找不到 Edge/Chrome —— 本探针需要 Chromium 内核实测渲染器")
        return 2
    try:
        import websocket  # noqa: F401
    except ImportError:
        log("需要 websocket-client：pip install websocket-client")
        return 2

    srv = None
    if args.live_url:
        url = args.live_url
        log("直接测已在跑的服务：%s" % url)
    else:
        srv, api_dir = start_stub_server(args.port)
        if srv is None:
            return 2
        log("桩后端已起：127.0.0.1:%d（只读，不连真实后端）" % args.port)
        url = "http://127.0.0.1:%d/" % args.port

    proc, page = launch_browser(edge, args.cdp_port, url)
    dead = []
    try:
        if page is None:
            return 2
        log("CDP 已接管")
        if not wait_frontend_ready(page, views):
            return 2
        for v in views:
            spun, detail = probe_view(page, v, SAMPLE_WINDOW)
            log("%-12s %s  (%s)" % (v, "⚠ 主线程死了" if spun else "✓ 存活", detail))
            if spun:
                dead.append(v)
                log("→ 抓栈定位根因函数…")
                page.grab_stack()
                break   # 主线程已死，后面没法再测
        if dead:
            log("判定：视图 %s 把渲染器主线程搞死了（复现成功，根因见上面调用栈）" % dead[0])
            return 1
        log("判定：所测视图均未把渲染器主线程搞死")
        return 0
    finally:
        if page:
            page.close()
        with contextlib.suppress(Exception):   # 浏览器已退出
            proc.kill()
        if srv is not None and not args.keep_server:
            with contextlib.suppress(Exception):   # 桩后端已退出
                srv.kill()
            with contextlib.suppress(Exception):   # 临时文件删不掉不影响探针结论
                os.remove(os.path.join(HERE, "_freeze_probe_stub_server.py"))




if __name__ == "__main__":
    sys.exit(main())
