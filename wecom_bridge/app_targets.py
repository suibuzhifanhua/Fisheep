# -*- coding: utf-8 -*-
"""wecom_bridge.app_targets — 多目标 CDP 绑定（企微 / Edge / Chrome / 自定义 CDP 软件）
====================================================================
v1.0.723（用户需求：软件不再仅围绕 CDP 绑定企业微信——「绑定企微」按钮升级成
「选择绑定目标」：企业微信 / 本机 Edge / Chrome / 其它支持 CDP 接管的应用）。

CDP 传输层（cdp_client / windows / read / act）本来就与具体应用无关——只要端口上
有标准 Chromium DevTools 端点，窗口列表/读页/点击/上传全链路直接可用。本模块只补
「绑谁」这一层：
  · detect_browsers      本机 Edge / Chrome exe 探测（常见路径 + 注册表兜底）
  · cdp_target_kind      端口上的 CDP 是谁（/json/version 品牌特征：wxwork/edg/chrome）
  · pick_cdp_port_for    端口择优（本目标已占口→复用；被别的 CDP 占→避让；真空闲→用）
  · ensure_browser_binding  浏览器版幂等绑定（语义与 wecom_proc.ensure_binding 对齐：
                            已绑复用 / 运行未带口 needs_kill 闸门 / 带 CDP flag 拉起）
  · open_bound_target    绑定成功后的「打开」按钮（恒 allow_kill=False，绝不杀/重启）
  · bind_custom_cdp      接管「已带调试端口启动」的任意 CDP 软件（只探测，不动进程）

v1.0.725：浏览器目标改用**专用 profile 目录**（cdp_profiles/<target>）启动——
Edge/Chrome 136+ 安全加固把默认 user-data-dir 的 --remote-debugging-port 静默
忽略了（本机 Edge 157 实跑定位：带 flag 启动默认 profile，进程活着但端口永不开）。

与企微流程的边界（刻意不做的事）：
  · 不最小化浏览器窗口——企微的静默收纳是对 Electron 壳的定制，浏览器窗口用户
    正在用，收走是打扰；
  · 浏览器「运行中未带调试端口」时不给任何免确认路径——CDP 无法对已跑实例补开端口，
    唯一解是重启（关全部标签页），必须走 needs_kill 人工确认（同企微红线）。
"""
import json
import os
import time
import subprocess

from .cdp_client import (
    DEFAULT_PORT, CDP_FLAGS, CREATE_NO_WINDOW, _logw, _run_hidden,
    cdp_up, port_bindable, _cdp_ws_ok, cdp_ws_ok_cached,
)
from .wecom_proc import _enum_pids_named
from .windows import list_targets, window_list_summary
from .wecom_proc import windows_signature

# ---------- 目标定义 ----------
# exe_name：进程枚举用的镜像名（小写）；exe_candidates：安装探测候选（按序）。
BROWSERS = {
    "edge": {
        "exe_name": "msedge.exe",
        "label": "Microsoft Edge",
        "exe_candidates": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        ],
    },
    "chrome": {
        "exe_name": "chrome.exe",
        "label": "Google Chrome",
        "exe_candidates": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
    },
}

# 展示名（后端 → 前端顶栏/侧栏/设置页统一取这里，禁止前端再手抄一份）。
TARGET_LABELS = {
    "wecom": "企业微信",
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "custom": "自定义 CDP 软件",
}
# 侧栏短标签（sb-dot-label 是固定窄位，长标签放 title 里）。
# v1.0.724：wecom 短标签改全称「企业微信」——用户要求 UI 不再出现「企微」缩写字样。
TARGET_SHORT = {"wecom": "企业微信", "edge": "Edge", "chrome": "Chrome", "custom": "CDP"}

CDP_PORT_SCAN_SPAN = 8   # 与企微同款：避让时最多往后试几个口


def normalize_target(raw):
    """入参归一：未知目标一律回退 wecom（向后兼容旧配置/旧前端）。"""
    t = str(raw or "wecom").strip().lower()
    if t in ("wecom", "edge", "chrome", "custom"):
        return t
    return "wecom"


# ---------- 1. 浏览器 exe 探测 ----------
def detect_browsers():
    """返回 {"edge": 路径|None, "chrome": 路径|None}。常见路径优先，注册表卸载项兜底。"""
    out = {}
    for key, spec in BROWSERS.items():
        exe = None
        for c in spec["exe_candidates"]:
            if c and os.path.exists(c):
                exe = c
                break
        if not exe:
            exe = _find_browser_from_registry(spec)
        out[key] = exe
    return out


def _find_browser_from_registry(spec):
    """注册表卸载项兜底（同 detect_wecom_exe 的路数）：按 DisplayName 找 InstallLocation。"""
    try:
        import winreg
    except ImportError:
        return None
    name_key = "edge" if spec["exe_name"] == "msedge.exe" else "chrome"
    markers = (("Microsoft Edge", "edge") if name_key == "edge"
               else ("Google Chrome", "chrome"))
    try:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.EnumKey(key, i)
                    sk = winreg.OpenKey(key, sub)
                    disp = winreg.QueryValueEx(sk, "DisplayName")[0]
                    if any(m.lower() in disp.lower() for m in markers):
                        try:
                            loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                        except OSError:
                            continue
                        if loc:
                            cand = os.path.join(loc, spec["exe_name"])
                            if os.path.exists(cand):
                                return cand
                except OSError:
                    continue
    except Exception as e:  # noqa: BLE001
        _logw(f"[targets] 注册表探测 {spec['exe_name']} 异常，按未安装处理：{e}")
    return None


# ---------- 2. 端口上的 CDP 是谁 ----------
def cdp_target_kind(port=DEFAULT_PORT):
    """端口上的 CDP 属于谁：返回 "wecom"|"edge"|"chrome"|"unknown"|None。
    None = 端口没活（探测失败/没响应）——调用方不要据此断言"被别家占用"
    （v1.0.555 P1-12 同款三态纪律：繁忙抖动 ≠ 占用）。
    "unknown" = 活着但品牌特征认不出（代理劫持/非 Chromium CDP，如 Electron 自研壳）
    ——对「本目标已在端口上」判 False，但对「被别家占用」也不断言。
    判序：wxwork/wecom 先于 chrome 系（企微 UA 也带 Chrome 字样）；edg 先于 chrome
    （Edge UA 同时含 Chrome 与 Edg）。"""
    from core.logging import _lazy_requests as requests
    try:
        r = requests.get(f"http://127.0.0.1:{int(port)}/json/version", timeout=2)
    except Exception:  # noqa: BLE001
        return None
    blob = (r.text or "").lower()
    if not blob.strip():
        return None
    if ("wxwork" in blob) or ("wecom" in blob):
        return "wecom"
    if ("edg/" in blob) or ("microsoftedge" in blob) or ("edg:" in blob):
        return "edge"
    if "chrome" in blob:
        return "chrome"
    return "unknown"


# ---------- 3. 端口择优 ----------
def pick_cdp_port_for(target, start, span=CDP_PORT_SCAN_SPAN):
    """给目标挑端口，返回 (port, reason)；port=None 表示整段不可用。
    优先级：本目标的调试端口（原样复用，重启它的代价最大）> 真空闲（port_bindable
    实测定，同企微 v1.0.651 口径）。被别的 CDP 占着（含 unknown）→ 往后避让，
    不硬挤也不断言对方身份。本函数只读不改，落盘/重启由调用方决定。"""
    try:
        start = int(start)
    except (TypeError, ValueError):
        start = DEFAULT_PORT
    span = int(span)
    for cand in [start] + list(range(start + 1, start + 1 + span)):
        kind = cdp_target_kind(cand)
        if kind == target:
            return cand, "端口上已是该目标的调试端口，原样复用"
        if kind in (None, "unknown"):
            # 没活 / 认不出 → 以能否真实 bind 为准（v1.0.651：404 空响应占用者
            # 在 cdp_up 眼里"不死活"，必须用 bind 判定才躲得开）
            if port_bindable(cand):
                return cand, ("配置端口可用" if cand == start
                              else f"{start} 不可用，改用空闲端口")
            continue
        # kind 是明确的"别家"（wecom/edge/chrome 之一且 ≠ 本目标）→ 避让
        continue
    return None, f"{start} 及其后 {span} 个候选端口都被其他程序占用"


# ---------- 4. 浏览器进程 ----------
# v1.0.725（实跑根因定位）：Edge/Chrome 136+ 安全加固——**默认 user-data-dir 下
# --remote-debugging-port 被静默忽略**（带 flag 启动默认 profile，进程活得好好的
# 但 CDP 端口永不监听；本机 Edge 157 实锤）。CDP 调试端口只对**非默认** profile
# 生效，故绑定浏览器一律改用专用 profile 目录（产物库/cdp_profiles/<target>）：
# 调试实例与用户日常浏览 profile 互不占用单实例锁，重启绑定也不再动日常浏览数据。
# v1.0.738：原先直接落在应用目录顶层 cdp_profiles/，与 config/产物库/ocr 并列一堆文件夹；
# 现统一收进产物库，走 core.paths.runtime_dir（写入口径，自动开首启闸门）。
def browser_profile_dir(target):
    """绑定浏览器的专用 profile 目录（产物库/cdp_profiles/<target>），幂等创建。"""
    from core.paths import runtime_dir
    d = os.path.join(runtime_dir("cdp_profiles"), target)
    os.makedirs(d, exist_ok=True)
    return d


def find_browser_pids(target):
    """返回目标浏览器正在运行的 PID 列表（Win32 枚举，失败返回 []）。"""
    spec = BROWSERS.get(target)
    if not spec:
        return []
    try:
        return _enum_pids_named((spec["exe_name"],))
    except Exception as e:  # noqa: BLE001
        _logw(f"[targets] {target} 进程枚举异常（按无进程处理）：{e}")
        return []


def _debug_session_pids(target, pids):
    """v1.0.725：运行中的浏览器 PID 里哪些是本软件拉起的「调试 session」。
    专用 profile（产物库/cdp_profiles/<target>）让调试实例与用户日常浏览器成为两个独立
    session——杀/重启只该动调试 session。按进程命令行判据（一次 WMI 查询全量）：
    · 含 cdp_profiles/<target> → v1.0.725 起的专用 profile 调试实例；
    · 含 --remote-debugging-port → v1.0.724 及更早也是本软件带 flag 拉起的调试实例。
    返回：None = 查不到命令行（WMI 不可用）→ 调用方走保守旧路径（确认+全杀）；
    [] = 运行中的全都不是调试 session（用户日常浏览器）→ 不杀，直接另起独立实例；
    [pid, ...] = 调试 session 的 PID 子集。"""
    spec = BROWSERS[target]
    marker = "cdp_profiles"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter 'Name='%s' | "
             "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
             % json.dumps(spec["exe_name"])],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW)
        data = json.loads(r.stdout or "null")
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
        cmd_by_pid = {int(d.get("ProcessId") or 0): (d.get("CommandLine") or "")
                      for d in data}
    except Exception as e:  # noqa: BLE001（WMI 失败=分不清 session，交回调用方保守处理）
        _logw(f"[targets] {target} 进程命令行查询失败（无法区分调试 session）：{e}")
        return None
    out = []
    for pid in pids:
        cl = cmd_by_pid.get(pid, "")
        if (f"{marker}{os.sep}{target}" in cl or f"{marker}/{target}" in cl
                or "--remote-debugging-port" in cl):
            out.append(pid)
    return out


def _kill_browser_pids(target, pids, max_wait_s=5.0, wait_for=None):
    """只杀**传入的** PID（含子进程树）；退不干净返回 (False, 原因)。
    与企微 v1.0.649 同款红线：绝不按镜像名全杀兜底——浏览器进程树里可能有
    用户正用的 GPU/插件子进程，按名全杀的误伤面比企微大。
    v1.0.725：wait_for=真正要等它消失的 PID 集合（默认=全部传入 PID）。专用
    profile 下只杀调试 session 时，用户的日常浏览器还在跑——等「全部 msedge
    消失」会把共存判成「没退干净」，必须只等被杀的那批。"""
    spec = BROWSERS[target]
    for t in sorted(pids):
        # /t：连子进程树一起收（同企微 v1.0.194 口径）。浏览器主进程被杀后
        # 渲染器/GPU/单实例子进程会残留，占着 user-data-dir 锁不放——新实例
        # 会被「单实例提示」挡住，CDP 端口起不来（v1.0.724 实跑定位的根因）。
        # 仍只针对当场枚举到的每个 PID 的子树，不按镜像名全杀（v1.0.649 红线不变）。
        _run_hidden(["taskkill", "/f", "/t", "/pid", str(t)])
    watch = set(wait_for if wait_for is not None else pids)
    for _ in range(int(max_wait_s * 2)):
        if not (watch & set(find_browser_pids(target))):
            return True, ""
        time.sleep(0.5)
    return False, (f"{spec['label']} 没能完全退出（可能有窗口仍在），本次**没有**强杀它。"
                   f"请手动完全退出 {spec['label']} 后再点绑定。")


# ---------- 5. 浏览器幂等绑定 ----------
def ensure_browser_binding(cfg, target, force=False, allow_kill=True, max_wait_s=15.0,
                           allow_launch=True):
    """浏览器目标（edge/chrome）的幂等 CDP 绑定。直接改 cfg['wecom']（同 ensure_binding
    契约：落盘由调用方走 make_bind_merger 按键合并）。
    返回 status dict，字段与 wecom 版对齐（bound/changed/reason/exe_path/port/pid/
    auto_launch/needs_kill/windows），另带 target/target_label 供前端展示。
    v1.0.727（用户：「启动软件直接弹出浏览器这个设定取消，要让用户自己去打开，
    不然很多用户感觉像是广告就关闭了」）：allow_launch=False 时（启动自动绑定专用）
    绝不 Popen 浏览器——浏览器窗口不许在软件启动时凭空弹出。拉起只走用户显式动作
    （「CDP 绑定」按钮 /「打开」按钮，两者默认 allow_launch=True）。"""
    target = normalize_target(target)
    if target not in BROWSERS:
        return {"ok": False, "bound": False, "changed": True,
                "reason": f"未知绑定目标：{target}", "target": target}
    spec = BROWSERS[target]
    label = spec["label"]
    w = cfg.setdefault("wecom", {})
    # v1.0.728（实跑日志定位）：exe 缓存的守卫必须拿**覆盖前**的旧目标比较——
    # 原实现先 w["cdp_target"]=target 再比 w.get("cdp_target")==target，恒真，
    # 导致换绑目标时把上一个目标的 exe 路径缓存污染过来（17:21 实锤：绑 chrome
    # 起的是 msedge.exe，端口特征 edge 被拒绑）。
    prev_target = str(w.get("cdp_target") or "")
    w["cdp_target"] = target
    from core.config_store import cdp_port as _cfg_cdp_port
    port = _cfg_cdp_port(cfg)
    # 上一轮绑定的 exe 缓存（同目标且路径仍有效才复用），否则重新探测
    exe = w.get("exe_path") if prev_target == target else None
    if not (exe and os.path.exists(exe)):
        exe = detect_browsers().get(target)
    if exe:
        w["exe_path"] = exe
    auto_launch = w.get("auto_launch", True)

    def _save(pid, refresh_started=True):
        w["bound_pid"] = pid
        if refresh_started:
            w["bound_started_ts"] = time.time()
        try:
            w["bound_sig"] = windows_signature(list_targets(port))
        except Exception as e:  # noqa: BLE001
            _logw(f"[targets] 计算 {target} 绑定签名失败：{e}")
            w["bound_sig"] = None

    def _resp(**kw):
        base = {"bound": False, "changed": True, "exe_path": exe, "port": port,
                "pid": None, "auto_launch": auto_launch, "windows": 0,
                "target": target, "target_label": label}
        base.update(kw)
        return base

    # ① exe 都没有：直接说清（不做任何进程操作）
    if not (exe and os.path.exists(exe)):
        return _resp(reason=f"未检测到 {label}（常见安装路径与注册表都没找到）。"
                            f"请先安装 {label}，或选「其他 CDP 软件」接管已带调试端口的应用。")

    # ② 端口择优（本目标已在端口上→复用；被别家 CDP 占→避让）
    _chosen, _why = pick_cdp_port_for(target, port)
    if _chosen is None:
        return _resp(reason=f"CDP 端口 {port} 及其后 {CDP_PORT_SCAN_SPAN} 个候选都被"
                            f"其他程序占用。请关掉占用端口的程序，或在设置里手填空闲端口。")
    if _chosen != port:
        _logw(f"[targets] {target} CDP 端口自动避让：{port} → {_chosen}（{_why}）")
        w["cdp_port"] = _chosen
        port = _chosen
    pids = find_browser_pids(target)

    # ③ 本目标 CDP 已在线：记录即绑定（force 也走这里——重启由用户显式按钮触发）
    up = cdp_up(port)
    if up:
        kind = cdp_target_kind(port)
        if kind not in (None, "unknown", target):
            # 择优与连口之间端口状态漂移（别人刚把调试端口开在这个口上）：
            # 不绑错家，直接说明，下轮 pick_cdp_port_for 会避让。
            return _resp(reason=f"端口 {port} 上出现的不是 {label}（特征：{kind}），"
                                f"本次未绑定。请重试（会自动换空闲端口）。")
        ws_ok = _cdp_ws_ok(port)
        if ws_ok:
            new_pid = pids[0] if pids else None
            if not force and new_pid:
                sig = windows_signature(list_targets(port))
                changed = (sig != w.get("bound_sig"))
                if changed:
                    _save(new_pid)
                return _resp(bound=True, changed=changed,
                             reason="reuse" if not changed else "windows changed",
                             pid=new_pid, windows=len(window_list_summary(port)))
            _save(new_pid)
            return _resp(bound=True, changed=True,
                         reason="cdp already up, recorded", pid=new_pid,
                         windows=len(window_list_summary(port)))
        # WS 被 origin 拒（403）→ 落到 ④ 带白名单 origin 重启

    # ④ CDP 没起（或在线但 WS 被 origin 拒）：需要带 flag（重）启动
    if pids and not w.get("auto_cdp", True):
        return _resp(reason=f"{label} 正在运行但未带 CDP flag，且已关闭自动注入"
                            f"（auto_cdp=false）。请手动带 --remote-debugging-port 启动。",
                     pid=pids[0])
    # v1.0.725：专用 profile 目录——默认 profile 的 CDP 端口在 136+ 被静默关闭
    profile = browser_profile_dir(target)
    if pids:
        # v1.0.725：只动调试 session，用户日常浏览器不碰（专用 profile 下两者可共存）。
        # v1.0.730（审查 P1-A）：原实现 `if session:` 把 None（WMI 查不到命令行）
        # 与 []（确认全是日常浏览器）一并判假，于是 None 落进「全是日常浏览器」
        # 分支——绕过 needs_kill 闸门直接 Popen，与 docstring 承诺的保守路径相反；
        # 且 `kill_set = pids if session is None else session` 的 None 分支是死代码。
        # 现显式三分支：None=分不清→人工确认/杀全部枚举 PID；非空=调试 session
        # 子集→只杀这批；[]=确认全是日常浏览器→不动，另起独立调试实例。
        session = _debug_session_pids(target, pids)
        if session is None:
            kill_set = list(pids)
            if not allow_kill:
                return _resp(
                    reason=(f"{label} 正在运行但未带 CDP 调试端口，且本次读不到进程命令行"
                            f"（无法区分调试实例与你的日常浏览器）。CDP 无法对已运行的"
                            f"浏览器补开端口，保守起见需重启 {label}——重启将关闭它的"
                            f"**全部窗口与标签页**（未保存的表单内容可能丢失）。"
                            f"请在界面确认后手动触发「重启并绑定」。"),
                    pid=kill_set[0], needs_kill=True)
            _ok, _why = _kill_browser_pids(target, kill_set, wait_for=kill_set)
            if not _ok:
                return _resp(reason=_why, pid=kill_set[0])
        elif session:
            kill_set = list(session)
            if not allow_kill:
                return _resp(
                    reason=(f"{label} 的调试实例（专用 profile）正在运行但未带 CDP 调试端口。"
                            f"需重启该调试实例——将关闭它的窗口与标签页"
                            f"（未保存的内容可能丢失；你的日常浏览器不受影响）。"
                            f"请在界面确认后手动触发「重启并绑定」。"),
                    pid=kill_set[0], needs_kill=True)
            _ok, _why = _kill_browser_pids(target, kill_set, wait_for=kill_set)
            if not _ok:
                return _resp(reason=_why, pid=kill_set[0])
        elif not allow_launch:
            # v1.0.727（用户：「启动软件直接弹出浏览器这个设定取消，要让用户自己去
            # 打开，不然很多用户感觉像是广告就关闭了」）：日常浏览器在跑但调试实例
            # 没有——启动时**不**自动另起调试实例：浏览器窗口在软件启动时凭空弹出
            # 会被当广告直接关掉（还顺手带走调试实例，绑定白做）。拉起只走显式动作。
            return _resp(reason=f"{label} 未带调试端口运行（在跑的是你的日常浏览器，"
                                f"不会动它）；为避免启动时意外弹窗，本次不自动拉起。"
                                f"点顶栏「CDP 绑定」会自动带调试端口拉起并绑定，"
                                f"或你自己打开 {label} 后再绑定。")
        else:
            # session == []：运行中的全是用户日常浏览器（不带调试 flag）——不杀，
            # 专用 profile 与之独立共存，直接另起调试实例即可。
            _logw(f"[targets] {label} 运行进程均为用户日常浏览器（非调试 session），"
                   f"不动它——直接启动独立调试实例（专用 profile）")
        subprocess.Popen([exe, f"--remote-debugging-port={port}",
                         f"--user-data-dir={profile}", "--no-first-run"]
                         + CDP_FLAGS, creationflags=CREATE_NO_WINDOW)
        _logw(f"[targets] 已带 CDP flag 启动/重启 {label}（{exe} port={port} profile={profile}）")
    elif auto_launch and allow_launch:
        subprocess.Popen([exe, f"--remote-debugging-port={port}",
                         f"--user-data-dir={profile}", "--no-first-run"]
                         + CDP_FLAGS, creationflags=CREATE_NO_WINDOW)
        _logw(f"[targets] 已带 CDP flag 启动 {label}（{exe} port={port} profile={profile}）")
    elif not allow_launch:
        return _resp(reason=f"未检测到 {label} 运行进程；为避免启动时意外弹窗，本次不自动"
                            f"拉起。点顶栏「CDP 绑定」会自动带调试端口拉起并绑定，"
                            f"或你自己打开 {label} 后重新绑定。")
    else:
        return _resp(reason=f"未检测到 {label} 运行进程，且 auto_launch 已关闭——"
                            f"请手动打开 {label} 后重新绑定。")

    # ⑤ 等 CDP 就绪（HTTP + WebSocket 双确认，max_wait_s 预算）
    # v1.0.731（审查 P1-5 / P2-6）：WS 握手结果在函数内局部缓存里复用，不再每轮
    # 新建连接；同时给循环加轮数上限——sleep 被测试桩掉时也不会空转到预算耗尽。
    _ws_cache = {}
    deadline = time.time() + max_wait_s
    _rounds = max(1, int(float(max_wait_s) / 0.5) + 1)
    for _ in range(_rounds):
        if time.time() >= deadline:
            break
        if cdp_up(port) and cdp_ws_ok_cached(port, _ws_cache, probe=_cdp_ws_ok):
            break
        time.sleep(0.5)
    _pids = find_browser_pids(target)
    new_pid = _pids[0] if _pids else None
    _save(new_pid)
    # 收尾判据用一次真实探测（新 cache，不吃循环里的陈旧结果）
    if cdp_up(port) and cdp_ws_ok_cached(port, {}, probe=_cdp_ws_ok):
        kind = cdp_target_kind(port)
        if kind not in (None, "unknown") and kind != target:
            return _resp(reason=f"端口 {port} 上起来的不是 {label}（特征：{kind}），"
                                f"本次未绑定。请换一个空闲端口后重试。", pid=new_pid)
        return _resp(bound=True, changed=True,
                     reason="launched/relaunched with CDP", pid=new_pid,
                     windows=len(window_list_summary(port)))
    if _pids and cdp_up(port):
        return _resp(reason=(f"{label} 已启动、CDP 端口已开但 WebSocket 不可用（多为 403 origin "
                             f"限制）。请点「重启并绑定」让其带白名单 origin 重新启动。"),
                     pid=new_pid)
    if _pids:
        return _resp(reason=f"{label} 进程已启动但 CDP 调试端口不可用。绑定用的是专用 profile"
                            f"（产物库/cdp_profiles/{target}），最常见原因是上一轮调试实例没退干净、"
                            f"端口还被占着：请在任务管理器里确认 {spec['exe_name']} 的所有进程"
                            f"都已退出（若开了「启动增强/后台运行」，先在 {label} 设置里关掉），"
                            f"再点「CDP 绑定」。若仍不行，可能是端口被别的程序占用或浏览器策略"
                            f"限制——在设置里换一个空闲端口重试。",
                     pid=new_pid)
    return _resp(reason=f"{label} 启动后进程未出现（启动失败）。请手动打开 {label} 后重试。")


# ---------- 6.5 打开已绑定目标（v1.0.725「打开」按钮） ----------
def open_bound_target(cfg, max_wait_s=15.0):
    """绑定成功后点「打开」：拉起已绑定的目标应用（已在运行则不动它）。
    红线：绝不杀进程/重启——恒 allow_kill=False；目标在运行但未带 CDP 端口时
    原样返回（needs_kill 提示），重启只走「CDP 绑定」入口的确认链。
    custom 目标是接管模式（应用不归本软件管）：不拉起，只回当前连通状态。"""
    w = cfg.setdefault("wecom", {})
    target = normalize_target(w.get("cdp_target") or "wecom")
    if target == "custom":
        try:
            port = int(w.get("custom_cdp_port") or w.get("cdp_port") or 0)
        except (TypeError, ValueError):
            port = 0
        up = bool(port) and cdp_up(port)
        return {"ok": True, "bound": up, "target": "custom",
                "target_label": TARGET_LABELS["custom"],
                "reason": "目标正在运行（CDP 可达）" if up else
                         "自定义 CDP 软件由本软件接管而非启动——请手动带 "
                         "--remote-debugging-port 启动它，再到「设置 → CDP 绑定」重新绑定。"}
    if target == "wecom":
        from .wecom_proc import ensure_binding
        st = ensure_binding(cfg, force=False, allow_kill=False)
    else:
        st = ensure_browser_binding(cfg, target, force=False,
                                    allow_kill=False, max_wait_s=max_wait_s)
    st["ok"] = True
    return st


# ---------- 7. 自定义 CDP 软件（接管） ----------
def bind_custom_cdp(cfg, port, max_wait_s=0.0):
    """接管「已经带 --remote-debugging-port 启动」的任意 CDP 应用：只探测、只记录，
    绝不碰进程（它不归本软件管，用户怎么启的我们就怎么连）。
    max_wait_s>0 时先轮询等待（启动竞态），默认 0=立即探测。"""
    w = cfg.setdefault("wecom", {})
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "bound": False, "changed": True,
                "reason": "自定义 CDP 端口必须是数字", "port": None,
                "target": "custom", "target_label": TARGET_LABELS["custom"]}
    w["cdp_target"] = "custom"
    w["custom_cdp_port"] = port
    w["cdp_port"] = port
    label = TARGET_LABELS["custom"]

    def _resp(**kw):
        base = {"bound": False, "changed": True, "exe_path": None, "port": port,
                "pid": None, "auto_launch": w.get("auto_launch", True),
                "windows": 0, "target": "custom", "target_label": label}
        base.update(kw)
        return base

    deadline = time.time() + max(0.0, float(max_wait_s))
    while True:
        if cdp_up(port) and _cdp_ws_ok(port):
            try:
                sig = windows_signature(list_targets(port))
            except Exception as e:  # noqa: BLE001
                _logw(f"[targets] 计算自定义 CDP 签名失败：{e}")
                sig = None
            w["bound_sig"] = sig
            w["bound_pid"] = None
            w["bound_started_ts"] = time.time()
            return _resp(bound=True, changed=True, reason="attached to existing CDP endpoint",
                          windows=len(window_list_summary(port)))
        if time.time() >= deadline:
            break
        time.sleep(0.5)
    return _resp(reason=(f"端口 {port} 上没有可用的 CDP 端点。确认目标应用已带 "
                         f"--remote-debugging-port={port} 启动（如 Chrome/Edge 可加该参数，"
                         f"或它自带的调试开关），再点一次绑定。"))
