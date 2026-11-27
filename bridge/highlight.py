# -*- coding: utf-8 -*-
"""bridge/highlight.py — CDP 接管窗口的 OS 层高亮边框（v1.0.732）。

需求（用户 2026-11-20）：「cdp接管的浏览器没有高亮边框 优化一下提高识别度」。
v1.0.727 曾在**绑定弹窗卡片**上加亮色边框（3px + 实心绿 chip），但那只是软件
界面；用户这次要的是**桌面上那个被 CDP 接管的浏览器/企微窗口本身**一眼可辨，
免得和自己的日常浏览器混在一起、误手动操作自动化窗口。

为什么不用 CDP 往页面注入 CSS 边框（最容易想到、但这里是错的）：
  本项目大量用 Page.captureScreenshot 做实时窗口预览、OCR 与 LLM
  视觉判断（cdp_client.CDP_SCREENSHOT_TRY_TIMEOUT）。
  页面内注入的边框会被**原样截进每一张图**，污染 OCR 文本与模型判断。
  故走 OS 层：4 条彩色边框窗口 + 1 条标识铭牌，包住整个窗口（含标题栏 /
  标签栏），由 DWM 合成，Chrome 的截图 API 完全看不到。

Win32 窗口样式红线：
  WS_EX_TRANSPARENT  —— 鼠标穿透，绝不挡自动化点击 / 用户操作
  WS_EX_NOACTIVATE   —— 不抢前台焦点（静默模式红线）
  WS_EX_TOOLWINDOW   —— 不进任务栏 / Alt-Tab
  纯色画刷交 DefWindowProcW 处理 WM_ERASEBKGND，不长期持有额外 GDI 句柄。

v1.0.733（用户：「高亮应当跟着窗口走 而不是顶置」）：
  旧版给边框窗口加了置顶扩展样式、且每次摆位都强行提到最顶层。结果被接管的
  浏览器一旦被主窗口遮住，橙线仍穿透盖在主窗口内容上，看起来是一个悬空的框，
  而不是「框住那个窗口」。现改为 **owned window**：5 个边框窗口用
  GWLP_HWNDPARENT 挂到目标窗口下，Windows 负责：
    · owned 永远在 owner 之上（边框看得见）
    · owner 被别的窗口盖住 / Z 序下降时 owned 一起下降（不再顶置穿透）
    · owner 最小化时 owned 自动隐藏
  摆位一律 SWP_NOZORDER，代码彻底不碰 Z 序；跟随用 DWM 可见边界而不是
  GetWindowRect（后者含 DWM 不可见阴影，边框会外扩一圈显得没贴住）。
  跟随 tick 提到 12.5Hz，拖影 <10px；rect 未变时不重摆，不闪也不抖。

目标定位红线（绝不框错用户的日常浏览器）：
  只认命令行里带 `--remote-debugging-port=<当前端口>` 的进程，再按 PID 枚举
  可见顶层窗口取面积最大的主窗口。这正是「CDP 接管」的定义。WMI / Win32
  不可用时 fail-soft 隐藏边框，绝不退化成「随便框一个窗口」。
  定位结果按 3s 节流缓存，前台跟随 tick 只做取矩形 + SetWindowPos。

v1.0.734（用户 2026-11-20 四项反馈）：
  ① 状态识别：新增 cdp_owner_attached()，把守护线程「确实框住了窗口」这一
     事实公开给 bind_status。此前框只凭命令行证据、界面只信 CDP HTTP 探活，
     两套判据各说各话 → 出现「黄框亮着但显示未绑定」。现在两者同源：框亮
     ⇔ owner_hwnd 非空 ⇔ attached=True；界面区分「已绑定 / 已接管·连接中 /
     未绑定」三态，bound 仍严格等于 cdp_up（不撒谎让 agent 操作不可达页面）。
  ② 颜色/粗细可在设置页选：窗口过程改 WM_ERASEBKGND 动态画刷读当前色，
     改色即时重绘（旧版类画刷只注册一次 → 必须重启）；粗细变化强制重摆，
     否则 rect 不变时线宽根本不更新。
  ③ 企业微信不做边框：WMI 结果按进程名黑名单排除企微系，并只认主进程
     （跳过命令行带 --type= 的 renderer / gpu / utility 子进程）。
  ④ 跟随提速：静止 20Hz，一旦检测到窗口在移动立刻切 ~62Hz，静止 0.25s
     后退回，拖拽时不再明显拖影。
"""
import ctypes
import json
import locale
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes

from .cdp_client import CREATE_NO_WINDOW, _logw

_IS_WIN32 = (os.name == "nt")

# ---- 可调外观（配置键：wecom.cdp_highlight*） ----
HIGHLIGHT_ENABLED_DEFAULT = True
HIGHLIGHT_COLOR_DEFAULT = "#FF9500"      # 琥珀橙：与软件黑白中性皮肤强对比，语义=「注意，自动化窗口」
HIGHLIGHT_WIDTH_DEFAULT = 8               # px，比系统 1px 边框醒目得多
HIGHLIGHT_WIDTH_MIN = 3
HIGHLIGHT_WIDTH_MAX = 20
HIGHLIGHT_LABEL = "Fisheep · CDP 接管中"
HIGHLIGHT_LABEL_W = 260
HIGHLIGHT_LABEL_H = 24
# v1.0.734：自适应跟随频率。静止 20Hz 足够且省 CPU；一旦窗口在移动，
# 立刻切到 ~62Hz，拖拽时人眼几乎看不到框落后；静止 0.25s 后自动退回。
HIGHLIGHT_INTERVAL_S = 0.05               # idle：20Hz
HIGHLIGHT_INTERVAL_ACTIVE_S = 0.016       # 移动中：~62Hz
HIGHLIGHT_MOVE_SETTLE_S = 0.25            # 最后一次位置变化后，多久退回 idle
HIGHLIGHT_CONFIG_TTL_S = 1.5              # 配置/端口重读节流
HIGHLIGHT_RESCAN_TTL_S = 3.0              # 未找到属主窗口时，重按命令行解析的最小间隔
HIGHLIGHT_RESCAN_FAST_S = 2.0             # v1.0.733：窗口刚失效后的快速重扫窗口
HIGHLIGHT_RESCAN_FAST_TTL_S = 0.2         # 该窗口内的重扫间隔（WMI 查询较贵，2s 后回 3s）

_PORT_RE = re.compile(r"--remote-debugging-port\s*[= ]\s*(\d+)")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
# v1.0.734 ③：企业微信系进程一律不做边框。匹配的是 WMI 的 Name（进程名），
# 不是命令行，所以不会误伤路径里恰好带 wecom 字样的其它软件。
_EXCLUDE_OWNER_NAME_RE = re.compile(
    r"(wxwork|wecom|wework|qywx|企业微信)", re.IGNORECASE)
# Chromium 的 renderer/gpu/utility 子进程会继承主进程的调试端口参数，
# 但它们没有可见主窗口；只认主进程让 pids 集合干净且定位更快。
_CHILD_PROC_RE = re.compile(r"--type\s*[= ]", re.IGNORECASE)

# ---- Win32 常量 ----
# v1.0.733：**不再置顶**。边框窗口挂成目标窗口的 owned window，Z 序完全跟着
# 目标窗口走；代码只用 SWP_NOZORDER 摆位，一次都不抢 Z 序。
_WS_POPUP = 0x80000000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_EX_STYLE = (_WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
_GWLP_HWNDPARENT = -8                    # WS_POPUP 下该索引设置的是 owner
_SWP_NOZORDER = 0x0004                   # 摆位不动 Z 序（交给 owner 语义）
_DWMWA_EXTENDED_FRAME_BOUNDS = 9         # 可见边界，不含 DWM 阴影
_CS_HREDRAW_VREDRAW = 0x0003
_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_PM_REMOVE = 0x0001
_WM_QUIT = 0x0012
_WM_PAINT = 0x000F
_WM_ERASEBKGND = 0x0014          # v1.0.734：动态画刷在这里填当前配置色
_DT_CENTER = 0x00000001
_DT_VCENTER = 0x00000004
_DT_SINGLELINE = 0x00000020
_DT_NOPREFIX = 0x00000800
_TRANSPARENT_BK = 1
_FRAME_CLASS = "FisheepCdpFrame"
_LABEL_CLASS = "FisheepCdpLabel"


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", ctypes.c_void_p), ("fErase", ctypes.c_int),
                ("rcPaint", RECT), ("fRestore", ctypes.c_int),
                ("fIncUpdate", ctypes.c_int), ("rgbReserved", ctypes.c_byte * 32)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint), ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                             ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)

# 模块级持有：ctypes 回调 / 画刷 / 已注册类，防 GC（回调被回收会直接崩进程）
_U32 = None
_G32 = None
_K32 = None
_DWM = None
_BRUSH = None
_CLASS_READY = False
_FRAME_PROC = None
_LABEL_PROC = None
# v1.0.734 ②：当前生效色。窗口过程每次擦背景都读它，改色无需重建窗口类 /
# 无需重启进程。None 表示还没初始化，回退默认色。
_CURRENT_COLORREF = None

_STATE_LOCK = threading.Lock()
_STATE = {"thread": None, "stop": None, "frames": [], "hwnd": None,
          "owner_hwnd": None, "port": None, "visible": False,
          "last_rect": None, "last_rescan": 0.0, "rescan_fast_until": 0.0,
          "last_width": None, "last_move_ts": 0.0,
          "last_cfg": 0.0, "cfg": {}, "last_enabled": False, "last_error": ""}
_OVERRIDE = {"enabled": None}   # set_highlight_enabled() 运行时覆盖；None=跟随配置


# ---------------------------------------------------------------- 纯逻辑（可单测）
def parse_color(value, default=HIGHLIGHT_COLOR_DEFAULT):
    """'#RRGGBB' → COLORREF(0x00BBGGRR)。非法值回退默认，绝不抛。"""
    s = str(value or "").strip()
    if not _HEX_RE.match(s):
        s = default
    r, g, b = int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
    return (b << 16) | (g << 8) | r


def clamp_width(value, default=HIGHLIGHT_WIDTH_DEFAULT):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(HIGHLIGHT_WIDTH_MIN, min(HIGHLIGHT_WIDTH_MAX, n))


def frame_rects(rect, width=HIGHLIGHT_WIDTH_DEFAULT,
                label_w=HIGHLIGHT_LABEL_W, label_h=HIGHLIGHT_LABEL_H):
    """把目标窗口 RECT 摊成 5 个边框窗口的 (x, y, w, h)：上/下/左/右 4 条边
    + 顶部居中的标识铭牌。窗口太窄放不下铭牌时只返回 4 条边。纯函数。"""
    left, top, right, bottom = (int(v) for v in rect)
    w = max(1, right - left)
    h = max(1, bottom - top)
    px = clamp_width(width)
    out = [(left, top, w, px),
           (left, bottom - px, w, px),
           (left, top, px, h),
           (right - px, top, px, h)]
    room = w - 2 * px - 40
    if room >= 60:
        lw = min(int(label_w), room)
        lh = min(int(label_h), max(8, h - 2 * px))
        out.append((left + (w - lw) // 2, top, lw, lh))
    return out


def highlight_enabled(cfg=None):
    """读 wecom.cdp_highlight（默认 True）；运行时覆盖优先。"""
    if _OVERRIDE["enabled"] is not None:
        return bool(_OVERRIDE["enabled"])
    c = cfg if cfg is not None else _load_cfg()
    w = (c.get("wecom") or {}) if isinstance(c, dict) else {}
    return bool(w.get("cdp_highlight", HIGHLIGHT_ENABLED_DEFAULT))


def set_highlight_enabled(value):
    """运行时开关（不落盘）。value=None 恢复跟随配置。保存设置后即时生效，不必重启。

    必须同时让配置缓存失效：_read_settings 有 1.5s 节流，否则关掉开关后
    最多 1.5s 内边框还挂着（真实端到端实跑抓到过）。
    """
    _OVERRIDE["enabled"] = None if value is None else bool(value)
    highlight_config_changed()
    return _OVERRIDE["enabled"]


def highlight_config_changed():
    """配置落盘后调用：让守护线程下一 tick（≤0.35s）立刻重读，不等 1.5s 缓存过期。"""
    with _STATE_LOCK:
        _STATE["last_cfg"] = 0.0
    return True


def cdp_owner_attached():
    """v1.0.734 ①：守护线程是否**真的框住**了一个被 CDP 接管的窗口。

    只做内存读取：零 WMI、零 Win32、零配置读盘，/api/status 每 8s 调也无害。
    这是「黄框亮着」的同一份事实源 —— owner_hwnd 只在 _bind_owner 成功后
    写入，visible 只在边框真正显示后为 True。bind_status 拿它做接管证据，
    界面就不会再出现「框亮着却说未绑定」这种两套判据打架的情况。

    注意与 bound 的区别：attached 只证明「进程带调试端口 + 有个可见主窗口」，
    不证明 CDP HTTP 端点可达。所以 bound 仍严格等于 cdp_up，不能拿它替代。
    """
    with _STATE_LOCK:
        return bool(_STATE.get("owner_hwnd") and _STATE.get("visible"))


def highlight_state():
    """诊断快照（只读），供 /api/status 排查「边框怎么没出现」。"""
    with _STATE_LOCK:
        return {"win32": _IS_WIN32, "class_ready": _CLASS_READY,
                "running": bool(_STATE["thread"] and _STATE["thread"].is_alive()),
                "enabled": highlight_enabled(_STATE["cfg"] or None),
                "override": _OVERRIDE["enabled"], "port": _STATE["port"],
                "target_hwnd": _STATE["hwnd"],
                "owner_hwnd": _STATE.get("owner_hwnd"),
                "z_order": "owned" if _STATE.get("owner_hwnd") else "unbound",
                "visible": _STATE["visible"], "frames": len(_STATE["frames"]),
                "color": _STATE["cfg"].get("color"),
                "width": _STATE["cfg"].get("width"),
                # 不能调 cdp_owner_attached()：它自己也要拿 _STATE_LOCK，
                # threading.Lock 不可重入，这里会直接死锁。
                "attached": bool(_STATE.get("owner_hwnd") and _STATE["visible"]),
                "moving": (time.monotonic() -
                           float(_STATE.get("last_move_ts", 0.0))) < HIGHLIGHT_MOVE_SETTLE_S,
                "error": _STATE["last_error"]}


def _load_cfg():
    from core.config_store import load_config
    return load_config()


def _read_port(cfg):
    """与 agent_tools._cdp_port 同口径（含自动避让）；函数内延迟导入避免包级循环。"""
    try:
        from agent_tools import _cdp_port
        return int(_cdp_port(cfg))
    except Exception:  # noqa: BLE001 - 兜底口径，留痕不静默
        _logw("[cdp-highlight] _cdp_port 不可用，回落 wecom.cdp_port")
    w = (cfg.get("wecom") or {}) if isinstance(cfg, dict) else {}
    try:
        return int(w.get("cdp_port") or 9222)
    except (TypeError, ValueError):
        return 9222


def _read_settings(force=False):
    """节流重读配置（端口会随自动避让变化）。返回 dict。"""
    now = time.monotonic()
    with _STATE_LOCK:
        if not force and now - float(_STATE["last_cfg"]) < HIGHLIGHT_CONFIG_TTL_S:
            return dict(_STATE["cfg"])
    try:
        cfg = _load_cfg()
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] 配置读取失败（沿用上轮）：{type(e).__name__}: {e}")
        with _STATE_LOCK:
            return dict(_STATE["cfg"])
    w = (cfg.get("wecom") or {}) if isinstance(cfg, dict) else {}
    settings = {"enabled": highlight_enabled(cfg), "port": _read_port(cfg),
                "color": parse_color(w.get("cdp_highlight_color")),
                "width": clamp_width(w.get("cdp_highlight_width"))}
    with _STATE_LOCK:
        _STATE["last_cfg"] = now
        _STATE["cfg"] = settings
        _STATE["port"] = settings["port"]
    return dict(settings)


# ---------------------------------------------------------------- 属主窗口定位
def _cdp_owner_pids(port):
    """一次 WMI 查询找出命令行带 `--remote-debugging-port=<port>` 的**主进程** PID。
    PowerShell 侧先 like 过滤，输出极小。任何异常返回空集（fail-soft）。

    v1.0.734 ③「企业微信不做边框」：
      · 只认主进程：renderer / gpu / utility 子进程会继承主进程的调试端口参数，
        但它们没有可见主窗口，混进 pids 只会让枚举变慢、诊断变脏。
      · 按 WMI 的 Name 排除企微系（WXWork.exe 等）。匹配进程名而非命令行，
        避免误伤路径里恰好带 wecom 字样的其它软件。
      · 老数据 / mock 里没有 Name 字段时按「未知」放行，保持既有测试契约。
    """
    if not _IS_WIN32:
        return set()
    ps = ("Get-CimInstance Win32_Process | "
          "Where-Object { $_.CommandLine -like '*--remote-debugging-port*' } | "
          "Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        enc = locale.getpreferredencoding(False) or "utf-8"
        raw = (r.stdout or b"").decode(enc, "replace").strip()
        data = json.loads(raw or "null")
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] CDP 属主进程查询失败（本轮不显示边框）："
              f"{type(e).__name__}: {e}")
        return set()
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return set()
    need = int(port)
    out = set()
    for d in data:
        if not isinstance(d, dict):
            continue
        cmdline = str(d.get("CommandLine") or "")
        if _CHILD_PROC_RE.search(cmdline):
            continue                       # 子进程，不是属主
        name = str(d.get("Name") or "")
        if name and _EXCLUDE_OWNER_NAME_RE.search(name):
            continue                       # 企业微信系，不做边框
        m = _PORT_RE.search(cmdline)
        if m and int(m.group(1)) == need:
            try:
                pid = int(d.get("ProcessId") or 0)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                out.add(pid)
    return out


def _visible_windows_for_pids(pids):
    """按 PID 枚举可见、非最小化的顶层窗口，返回 [(hwnd, area, rect)] 面积降序。
    过滤掉 <240x180 的小窗（托盘/气泡/子窗口），避免框到调试器的小工具窗。"""
    if not _IS_WIN32 or not pids:
        return []
    u32 = _ensure_api()
    if u32 is None:
        return []
    found = []
    ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(h, _l):
        try:
            if not u32.IsWindowVisible(h) or u32.IsIconic(h):
                return True
            pid = wintypes.DWORD(0)
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            rc = RECT()
            if not u32.GetWindowRect(h, ctypes.byref(rc)):
                return True
            w = int(rc.right - rc.left)
            hh = int(rc.bottom - rc.top)
            if w >= 240 and hh >= 180:
                found.append((int(h), w * hh,
                              (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))))
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] 窗口枚举单项异常（忽略）：{type(e).__name__}: {e}")
        return True

    ref = ENUMPROC(_cb)   # 持引用至 EnumWindows 返回
    try:
        # argtypes 在 _ensure_api 里一次性声明成 c_void_p；回调 cast 成地址传入，
        # 运行时绝不再改共享原型（并发下会互踩）。
        u32.EnumWindows(ctypes.cast(ref, ctypes.c_void_p), 0)
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] EnumWindows 失败：{type(e).__name__}: {e}")
        return []
    found.sort(key=lambda it: it[1], reverse=True)
    return found


def resolve_cdp_owner_hwnd(port, cfg=None):
    """定位「当前 CDP 端口接管的进程」的主窗口。返回 (hwnd|None, info dict)。
    严格按命令行筛选；失败一律返回 None，绝不猜窗口。"""
    pids = _cdp_owner_pids(port)
    # 回退：配置里记的 bound_pid（绑定成功时写入），仅当它属于同一批 CDP 进程时可用；
    # WMI 挂了就不冒险框错窗口——直接返回 None。
    info = {"port": int(port), "pids": sorted(pids), "source": "command-line",
            "exclude_wecom": True}
    if not pids:
        info["reason"] = ("没有命令行带该调试端口的非企微主进程，或 WMI 不可用"
                          "（企业微信按需求不做边框）")
        return None, info
    wins = _visible_windows_for_pids(pids)
    info["windows"] = len(wins)
    if not wins:
        info["reason"] = "属主进程没有可见的大窗口（可能最小化或未创建窗口）"
        return None, info
    # 自己按面积取最大，不依赖 _visible_windows_for_pids 的返回顺序
    # （那个函数排序只是诊断友好；重构成迭代器/被 mock 时不能悄悄框错窗口）。
    hwnd, _area, rect = max(wins, key=lambda it: it[1])
    info["rect"] = rect
    return hwnd, info


# ---------------------------------------------------------------- Win32 窗口层
def _declare_user32(u32):
    """user32 原型表。单独成函数：v1.0.734 加动态画刷两个声明后 _ensure_api
    涨到 102 行触发巨型函数棘轮，按 DLL 分组拆开（每个 helper 都远低于 100 行）。"""
    u32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                   ctypes.c_size_t, ctypes.c_ssize_t]
    u32.DefWindowProcW.restype = ctypes.c_ssize_t
    u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEX)]
    u32.RegisterClassExW.restype = ctypes.c_ushort
    u32.CreateWindowExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p,
                                    ctypes.c_wchar_p, ctypes.c_uint,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.HWND,
                                    ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_uint]
    u32.SetWindowPos.restype = wintypes.BOOL
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.ShowWindow.restype = wintypes.BOOL
    u32.DestroyWindow.argtypes = [wintypes.HWND]
    u32.DestroyWindow.restype = wintypes.BOOL
    u32.IsWindow.argtypes = [wintypes.HWND]
    u32.IsWindow.restype = wintypes.BOOL
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsWindowVisible.restype = wintypes.BOOL
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.IsIconic.restype = wintypes.BOOL
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    u32.GetWindowRect.restype = wintypes.BOOL
    # v1.0.734 ②：WM_ERASEBKGND 自绘当前配置色（类画刷只注册一次，
    # 靠它改色必须重启；自绘则随时可变）。
    u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    u32.GetClientRect.restype = wintypes.BOOL
    u32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT),
                             ctypes.c_void_p]
    u32.FillRect.restype = ctypes.c_int
    # v1.0.733：把边框窗口挂成目标窗口的 owned window，Z 序跟着目标走。
    u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                      ctypes.c_ssize_t]
    u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    u32.EnumWindows.restype = wintypes.BOOL
    u32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                 ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    u32.PeekMessageW.restype = wintypes.BOOL
    u32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    u32.TranslateMessage.restype = wintypes.BOOL
    u32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    u32.DispatchMessageW.restype = ctypes.c_ssize_t
    u32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u32.BeginPaint.restype = ctypes.c_void_p
    u32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    u32.EndPaint.restype = wintypes.BOOL
    u32.DrawTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                              ctypes.c_int, ctypes.POINTER(RECT), ctypes.c_uint]
    u32.DrawTextW.restype = ctypes.c_int
    u32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p,
                                   wintypes.BOOL]
    u32.InvalidateRect.restype = wintypes.BOOL


def _declare_gdi32(g32):
    g32.CreateSolidBrush.argtypes = [ctypes.c_uint]
    g32.CreateSolidBrush.restype = ctypes.c_void_p
    g32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    g32.SetBkMode.restype = ctypes.c_int
    g32.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    g32.SetTextColor.restype = ctypes.c_uint
    g32.DeleteObject.argtypes = [ctypes.c_void_p]
    g32.DeleteObject.restype = wintypes.BOOL


def _declare_kernel32(k32):
    k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    k32.GetModuleHandleW.restype = ctypes.c_void_p


def _open_dwm():
    """DWM 可见边界：拿不到就回退 GetWindowRect，绝不让整个功能禁用。"""
    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        return dwm
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] dwmapi 不可用（回退 GetWindowRect）："
              f"{type(e).__name__}: {e}")
        return None


def _ensure_api():
    """一次性声明 prototypes。失败返回 None（后续全部 fail-soft）。"""
    global _U32, _G32, _K32, _DWM
    if _U32 is not None:
        return _U32
    if not _IS_WIN32:
        return None
    try:
        # 私有 WinDLL 实例而不是 ctypes.windll.user32 单例：argtypes/restype 存在
        # 各自实例 __dict__ 里，不会和 close_guard / os_windows / tucker 等模块
        # 并发互踩（同仓 test_shared_windll_prototype_writes_ratchet 的硬红线）。
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        g32 = ctypes.WinDLL("gdi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _declare_user32(u32)
        _declare_gdi32(g32)
        _declare_kernel32(k32)
        _U32, _G32, _K32, _DWM = u32, g32, k32, _open_dwm()
        return u32
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] Win32 API 声明失败（本功能禁用）：{type(e).__name__}: {e}")
        return None


def _fill_current_color(hwnd, hdc, u32=None):
    """v1.0.734 ②：WM_ERASEBKGND 用**当前配置色**填满客户区。

    旧版把颜色写死在窗口类的 hbrBackground 里，而窗口类一进程只注册一次 →
    改颜色必须重启。这里改成每次擦背景都读 _CURRENT_COLORREF 现造现删画刷，
    设置页改色后一次 InvalidateRect 就换色，不用重启、不用重建窗口。
    失败返回 False，由调用方交回 DefWindowProcW（退回类画刷，颜色不更新但不崩）。
    """
    api = u32 if u32 is not None else _ensure_api()
    if api is None or not hdc:
        return False
    try:
        cr = _CURRENT_COLORREF
        if cr is None:
            cr = parse_color(HIGHLIGHT_COLOR_DEFAULT)
        brush = _G32.CreateSolidBrush(ctypes.c_uint(int(cr)).value)
        if not brush:
            return False
        try:
            rc = RECT()
            api.GetClientRect(hwnd, ctypes.byref(rc))
            api.FillRect(ctypes.c_void_p(int(hdc)), ctypes.byref(rc),
                         ctypes.c_void_p(int(brush)))
        finally:
            _G32.DeleteObject(ctypes.c_void_p(int(brush)))
        return True
    except Exception as e:  # noqa: BLE001 - 回调里绝不抛，退回系统默认
        _logw(f"[cdp-highlight] 动态填色失败（退化类画刷）：{type(e).__name__}: {e}")
        return False


def _install_procs():
    """注册两个窗口过程回调（模块级引用防 GC）。"""
    global _FRAME_PROC, _LABEL_PROC
    u32 = _ensure_api()
    if u32 is None:
        return False

    @WNDPROC
    def _frame_proc(hwnd, msg, wparam, lparam):
        if msg == _WM_ERASEBKGND and wparam and _fill_current_color(hwnd, wparam, u32):
            return 1
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    @WNDPROC
    def _label_proc(hwnd, msg, wparam, lparam):
        try:
            if (msg == _WM_ERASEBKGND and wparam
                    and _fill_current_color(hwnd, wparam, u32)):
                return 1
            if msg == _WM_PAINT:
                ps = PAINTSTRUCT()
                hdc = u32.BeginPaint(hwnd, ctypes.byref(ps))
                if hdc:
                    try:
                        _G32.SetBkMode(hdc, _TRANSPARENT_BK)
                        _G32.SetTextColor(hdc, 0x00FFFFFF)   # 白字（COLORREF）
                        rc = RECT(ps.rcPaint.left, ps.rcPaint.top,
                                  ps.rcPaint.right, ps.rcPaint.bottom)
                        u32.DrawTextW(hdc, HIGHLIGHT_LABEL, -1, ctypes.byref(rc),
                                      _DT_CENTER | _DT_VCENTER |
                                      _DT_SINGLELINE | _DT_NOPREFIX)
                    finally:
                        u32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
        except Exception as e:  # noqa: BLE001 - 画字失败也要退回系统默认，绝不崩回调
            _logw(f"[cdp-highlight] 铭牌绘制异常（退化纯色）：{type(e).__name__}: {e}")
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _FRAME_PROC, _LABEL_PROC = _frame_proc, _label_proc
    return True


def _ensure_classes(colorref):
    """进程内只注册一次窗口类；**颜色不写死在类里**，每次都同步给自绘用的当前值。

    v1.0.734 ②：即使 _CLASS_READY 已为 True 也要刷新 _CURRENT_COLORREF，
    否则重启后的第一次改色会读到旧的默认色。
    """
    global _CLASS_READY, _BRUSH, _CURRENT_COLORREF
    _CURRENT_COLORREF = int(colorref)
    if _CLASS_READY:
        return True
    u32 = _ensure_api()
    if u32 is None or not _install_procs():
        return False
    try:
        brush = _G32.CreateSolidBrush(ctypes.c_uint(colorref).value)
        hinst = _K32.GetModuleHandleW(None)
        for name, proc in ((_FRAME_CLASS, _FRAME_PROC),
                           (_LABEL_CLASS, _LABEL_PROC)):
            wc = WNDCLASSEX()
            wc.cbSize = ctypes.sizeof(WNDCLASSEX)
            wc.style = _CS_HREDRAW_VREDRAW
            wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
            wc.hInstance = hinst
            wc.hbrBackground = brush
            wc.lpszClassName = name
            if not u32.RegisterClassExW(ctypes.byref(wc)):
                _logw(f"[cdp-highlight] RegisterClassExW({name}) 失败，本功能禁用")
                return False
        _BRUSH = brush
        _CLASS_READY = True
        return True
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] 窗口类注册失败：{type(e).__name__}: {e}")
        return False


def apply_highlight_color(colorref):
    """v1.0.734 ②：运行时换色，即时生效，无需重启进程 / 重建窗口。

    更新 _CURRENT_COLORREF 后对所有边框窗口 InvalidateRect(..., True) 触发
    WM_ERASEBKGND 自绘。返回成功触发重绘的窗口数；Win32 不可用 / 类未注册时
    只更新颜色值（下次创建窗口自然用新色），fail-soft 不抛。
    """
    global _CURRENT_COLORREF
    _CURRENT_COLORREF = int(colorref)
    u32 = _ensure_api()
    if u32 is None or not _CLASS_READY:
        return 0
    with _STATE_LOCK:
        frames = list(_STATE["frames"])
    n = 0
    for h in frames:
        try:
            if u32.InvalidateRect(wintypes.HWND(int(h)), None, True):
                n += 1
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] 改色重绘失败（忽略）：{type(e).__name__}: {e}")
    return n


def _create_frames():
    u32 = _ensure_api()
    if u32 is None:
        return []
    hinst = _K32.GetModuleHandleW(None)
    out = []
    for i in range(5):
        cls = _LABEL_CLASS if i == 4 else _FRAME_CLASS
        try:
            h = u32.CreateWindowExW(_EX_STYLE, cls, None, _WS_POPUP,
                                    0, 0, 1, 1, None, None, hinst, None)
            if h:
                out.append(int(h))
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] 第 {i} 个边框窗口创建失败：{type(e).__name__}: {e}")
    return out


def _destroy_frames(frames):
    u32 = _ensure_api()
    if u32 is None:
        return
    for h in frames:
        try:
            u32.DestroyWindow(wintypes.HWND(int(h)))
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] 销毁边框窗口失败（忽略）：{type(e).__name__}: {e}")


def _read_owner(frame_hwnd):
    """读回某个边框窗口的 owner（真实 Win32 探针 / 诊断用）。"""
    u32 = _ensure_api()
    if u32 is None or not frame_hwnd:
        return None
    try:
        v = u32.GetWindowLongPtrW(wintypes.HWND(int(frame_hwnd)),
                                  _GWLP_HWNDPARENT)
        return int(v) if v else None
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] 读取 owner 失败（忽略）：{type(e).__name__}: {e}")
        return None


def _bind_owner(frames, hwnd):
    """v1.0.733 核心：把边框窗口挂成目标窗口的 owned window。

    Windows 保证 owned 永远在 owner 之上，且 owner 被遮挡 / Z 序下降 / 最小化 /
    销毁时 owned 一起跟着走 —— 这正是「跟着窗口走，而不是顶置」。跨进程 owner
    合法；UIPI 或 Win32 拒绝时 fail-soft 只留痕，绝不退化成置顶穿透。
    """
    u32 = _ensure_api()
    if u32 is None or not hwnd or not frames:
        return 0
    bound = 0
    for h in frames:
        try:
            u32.SetWindowLongPtrW(wintypes.HWND(int(h)),
                                  _GWLP_HWNDPARENT, int(hwnd))
            bound += 1
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] 绑定 owner 失败（忽略）："
                  f"{type(e).__name__}: {e}")
    return bound


def _frames_valid(frames):
    """owner 窗口销毁时 Windows 会连带销毁 owned 窗口；失效必须重建，
    否则目标窗口关掉再打开后边框会永久消失。"""
    u32 = _ensure_api()
    if u32 is None or not frames:
        return False
    try:
        return all(bool(u32.IsWindow(wintypes.HWND(int(h)))) for h in frames)
    except Exception as e:  # noqa: BLE001
        _logw(f"[cdp-highlight] 边框窗口有效性检查失败：{type(e).__name__}: {e}")
        return False


def _ensure_frames(frames):
    """返回可用边框窗口列表；失效则销毁（幂等）并重建、重新绑定 owner。"""
    if _frames_valid(frames):
        return frames
    _destroy_frames(frames)
    fresh = _create_frames()
    with _STATE_LOCK:
        _STATE["frames"] = list(fresh)
        _STATE["owner_hwnd"] = None
        _STATE["last_rect"] = None
        _STATE["last_error"] = "" if fresh else "边框窗口重建失败"
    return fresh


def _show_frames(frames, show):
    u32 = _ensure_api()
    if u32 is None:
        return
    cmd = _SW_SHOWNOACTIVATE if show else _SW_HIDE
    for h in frames:
        try:
            u32.ShowWindow(wintypes.HWND(int(h)), cmd)
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] ShowWindow 失败（忽略）：{type(e).__name__}: {e}")


def _place_frames(frames, rect, width):
    """按目标 RECT 摆放 5 个窗口；铭牌放不下时隐藏第 5 个。"""
    u32 = _ensure_api()
    if u32 is None:
        return
    rects = frame_rects(rect, width)
    for i, h in enumerate(frames[:4]):
        if i >= len(rects):
            break
        x, y, w, hh = rects[i]
        try:
            # SWP_NOZORDER：绝不抢 Z 序。边框窗口是目标窗口的 owned window，
            # Windows 自己保证它贴在 owner 上方、随 owner 一起被遮挡。
            u32.SetWindowPos(wintypes.HWND(int(h)), wintypes.HWND(0),
                             x, y, w, hh,
                             _SWP_NOACTIVATE | _SWP_SHOWWINDOW | _SWP_NOZORDER)
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] SetWindowPos 失败（忽略）：{type(e).__name__}: {e}")
    if len(frames) >= 5:
        h = frames[4]
        if len(rects) >= 5:
            x, y, w, hh = rects[4]
            try:
                u32.SetWindowPos(wintypes.HWND(int(h)), wintypes.HWND(0),
                                 x, y, w, hh,
                                 _SWP_NOACTIVATE | _SWP_SHOWWINDOW | _SWP_NOZORDER)
                u32.InvalidateRect(wintypes.HWND(int(h)), None, True)
            except Exception as e:  # noqa: BLE001
                _logw(f"[cdp-highlight] 铭牌摆放失败（忽略）：{type(e).__name__}: {e}")
        else:
            _show_frames([h], False)


# ---------------------------------------------------------------- 守护线程
def _pump_messages(stop):
    """窗口线程自己泵消息（WM_PAINT 才会真正刷出颜色 / 文字）。"""
    u32 = _ensure_api()
    if u32 is None:
        return True
    msg = MSG()
    for _ in range(64):   # 单轮最多处理 64 条，避免繁忙时饿死跟随 tick
        try:
            if not u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                return True
            if msg.message == _WM_QUIT:
                return False
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] 消息泵异常（忽略）：{type(e).__name__}: {e}")
            return True
    return True


def _target_valid(hwnd):
    u32 = _ensure_api()
    if u32 is None or not hwnd:
        return False
    try:
        h = wintypes.HWND(int(hwnd))
        return bool(u32.IsWindow(h) and u32.IsWindowVisible(h) and not u32.IsIconic(h))
    except Exception:  # noqa: BLE001
        return False


def _get_target_rect(hwnd):
    """目标窗口的**可见边界**。优先 DwmGetWindowAttribute(EXTENDED_FRAME_BOUNDS)。

    GetWindowRect 在 Win10/11 上含约 7px 的不可见 resize 阴影，直接用会让边框
    比窗口大一圈（看起来「没贴住窗口」）。DWM 不可用 / 返回空矩形一律回退。
    """
    u32 = _ensure_api()
    if u32 is None:
        return None
    h = wintypes.HWND(int(hwnd))
    dwm = _DWM
    if dwm is not None:
        try:
            rc = RECT()
            hr = dwm.DwmGetWindowAttribute(
                h, wintypes.DWORD(_DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.cast(ctypes.byref(rc), ctypes.c_void_p),
                wintypes.DWORD(ctypes.sizeof(RECT)))
            if hr == 0 and rc.right > rc.left and rc.bottom > rc.top:
                return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))
        except Exception as e:  # noqa: BLE001
            _logw(f"[cdp-highlight] DWM 可见边界读取失败（回退）："
                  f"{type(e).__name__}: {e}")
    rc = RECT()
    if not u32.GetWindowRect(h, ctypes.byref(rc)):
        return None
    return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))


def _hide_frames(frames, visible):
    """统一隐藏收尾：只在确实显示过时才动窗口 / 状态，12.5Hz 下不空转。"""
    if not visible:
        return
    _show_frames(frames, False)
    with _STATE_LOCK:
        _STATE["visible"] = False
        _STATE["last_rect"] = None


def _rescan_owner(port, last_rescan):
    """窗口失效时重解析属主窗口，返回 (hwnd|None, info|None, did_scan)。

    v1.0.733：之前确实框过窗口、现在它没了（关闭 / 最小化 / 系统回收）时，
    给 2s 快速重扫窗口（0.2s 一次），用户马上重开浏览器能立刻重新框上；
    2s 后自动回到 3s 慢扫，避免 PowerShell / WMI 常驻开销。
    """
    now = time.monotonic()
    with _STATE_LOCK:
        fast_until = float(_STATE.get("rescan_fast_until", 0.0))
        had_owner = _STATE.get("owner_hwnd") is not None
    if had_owner:
        with _STATE_LOCK:
            _STATE["owner_hwnd"] = None
            _STATE["last_rect"] = None
            _STATE["last_rescan"] = 0.0
            _STATE["rescan_fast_until"] = now + HIGHLIGHT_RESCAN_FAST_S
        last_rescan = 0.0
        fast_until = now + HIGHLIGHT_RESCAN_FAST_S
    ttl = (HIGHLIGHT_RESCAN_FAST_TTL_S if now < fast_until
           else HIGHLIGHT_RESCAN_TTL_S)
    if now - float(last_rescan) < ttl:
        return None, None, False
    with _STATE_LOCK:
        _STATE["last_rescan"] = now
    found, info = resolve_cdp_owner_hwnd(port)
    return found, info, True


def _sync_tick(stop=None):
    """一轮跟随：读设置 → 校验/重解析属主窗口 → 摆位 → 显隐。"""
    st = _read_settings()
    with _STATE_LOCK:
        frames = list(_STATE["frames"])
        hwnd = _STATE["hwnd"]
        visible = _STATE["visible"]
        last_rect = _STATE["last_rect"]
        last_rescan = _STATE["last_rescan"]
        last_enabled = _STATE["last_enabled"]
        last_width = _STATE.get("last_width")
        owner_hwnd = _STATE.get("owner_hwnd")

    enabled = bool(st.get("enabled", True))
    if enabled != last_enabled:
        # 开关刚切换（含设置页保存 / 首次启动）：清节流与旧窗口，这一轮立刻重定位，
        # 「关→开」不必等 3s 重扫窗口期；「开→关」下面直接隐藏。
        with _STATE_LOCK:
            _STATE["last_enabled"] = enabled
            _STATE["last_rescan"] = 0.0
            _STATE["hwnd"] = None
            _STATE["owner_hwnd"] = None
            _STATE["rescan_fast_until"] = 0.0
            _STATE["last_rect"] = None
        hwnd = None
        owner_hwnd = None
        last_rescan = 0.0

    if not enabled:
        _hide_frames(frames, visible)
        return

    # owner 窗口销毁时 Windows 会连带销毁 owned 边框窗口；这里兜底重建，
    # 否则「关掉浏览器再打开」之后边框会永久消失。
    frames = _ensure_frames(frames)
    if not frames:
        return

    if not _target_valid(hwnd):
        found, info, did_scan = _rescan_owner(st["port"], last_rescan)
        if did_scan:
            hwnd = found
            with _STATE_LOCK:
                _STATE["hwnd"] = found
                _STATE["last_rect"] = None
                _STATE["last_error"] = ("" if found else
                                        str((info or {}).get("reason") or ""))
                owner_hwnd = _STATE.get("owner_hwnd")
        else:
            hwnd = None   # 节流期内不反复查 WMI

    if not hwnd:
        _hide_frames(frames, visible)
        return

    # 目标窗口换了（或首次找到）：重新挂 owner。之后 Z 序完全由 Windows 管理，
    # 每轮只更新位置，不再有任何「置顶」动作。
    if hwnd != owner_hwnd:
        bound = _bind_owner(frames, hwnd)
        with _STATE_LOCK:
            _STATE["owner_hwnd"] = hwnd
            if bound < len(frames):
                _STATE["last_error"] = ("owned window 绑定不完整 "
                                        f"({bound}/{len(frames)})")

    rect = _get_target_rect(hwnd)
    if not rect or rect[2] - rect[0] < 240 or rect[3] - rect[1] < 180:
        _hide_frames(frames, visible)
        return

    # v1.0.734 ②④：线宽变化也要重摆——否则窗口没动时用户改粗细根本不生效；
    # 位置变化则打时间戳，让 _loop 立刻切到高频跟随（拖拽不掉队）。
    width = st.get("width", HIGHLIGHT_WIDTH_DEFAULT)
    if rect != last_rect or width != last_width:
        _place_frames(frames, rect, width)
        _show_frames(frames, True)
        with _STATE_LOCK:
            if rect != last_rect:
                _STATE["last_move_ts"] = time.monotonic()
            _STATE["last_rect"] = rect
            _STATE["last_width"] = width
            if not _STATE["visible"]:
                _STATE["visible"] = True


def _loop(interval, colorref, stop):
    frames = []
    try:
        if not _ensure_classes(colorref):
            with _STATE_LOCK:
                _STATE["last_error"] = "Win32 窗口类注册失败（本机可能拦截 Win32 API）"
            return
        frames = _create_frames()
        with _STATE_LOCK:
            _STATE["frames"] = list(frames)
            _STATE["last_error"] = "" if frames else "边框窗口创建失败"
        if not frames:
            return
        while not stop.is_set():
            alive = _pump_messages(stop)
            if not alive:
                break
            try:
                # v1.0.734 ②：配置色变了就即时重绘（不重启、不重建窗口）。
                # 读的是节流后的 settings，但保存设置会调 highlight_config_changed()
                # 清缓存，所以下一轮必定读到新色。
                _st = _read_settings()
                if int(_st.get("color", colorref)) != _CURRENT_COLORREF:
                    apply_highlight_color(int(_st["color"]))
                _sync_tick(stop)
            except Exception as e:  # noqa: BLE001
                _logw(f"[cdp-highlight] 跟随 tick 异常（忽略继续）：{type(e).__name__}: {e}")
                with _STATE_LOCK:
                    _STATE["last_error"] = f"{type(e).__name__}: {e}"
            # v1.0.734 ④：窗口一动就切高频，静止 0.25s 后自动退回省 CPU。
            with _STATE_LOCK:
                _moving = (time.monotonic() -
                           float(_STATE.get("last_move_ts", 0.0))) < HIGHLIGHT_MOVE_SETTLE_S
            stop.wait(HIGHLIGHT_INTERVAL_ACTIVE_S if _moving else interval)
    finally:
        # _ensure_frames 可能重建过边框窗口；销毁必须取最新列表，否则新建的会漏掉。
        with _STATE_LOCK:
            live_frames = list(_STATE["frames"])
        _destroy_frames(live_frames)
        with _STATE_LOCK:
            _STATE["frames"] = []
            _STATE["hwnd"] = None
            _STATE["owner_hwnd"] = None
            _STATE["rescan_fast_until"] = 0.0
            _STATE["visible"] = False
            _STATE["last_rect"] = None
            _STATE["last_width"] = None
            _STATE["last_move_ts"] = 0.0
            _STATE["thread"] = None


def start_highlight(port=None, interval=HIGHLIGHT_INTERVAL_S):
    """启动高亮守护线程（幂等）。非 Windows / 已启动 / Win32 不可用返回 None 或现有线程。

    port 只作为首轮诊断值，实际端口由线程每 1.5s 从配置重读（支持自动避让换端口）。
    """
    if not _IS_WIN32:
        _logw("[cdp-highlight] 非 Windows 平台，跳过窗口高亮")
        return None
    with _STATE_LOCK:
        th = _STATE["thread"]
        if th is not None and th.is_alive():
            return th
        stop = threading.Event()
        _STATE["stop"] = stop
        _STATE["port"] = port
    st = _read_settings(force=True)
    colorref = st["color"]
    th = threading.Thread(target=_loop, args=(float(interval), colorref, stop),
                          name="cdp-highlight", daemon=True)
    with _STATE_LOCK:
        _STATE["thread"] = th
    th.start()
    _logw(f"[cdp-highlight] 已启动（port={st['port']} width={st['width']} "
          f"idle={interval}s active={HIGHLIGHT_INTERVAL_ACTIVE_S}s，"
          f"owned 随窗跟随、不抢 Z 序；排除企业微信，仅框带调试端口的主进程）")
    return th


def stop_highlight(timeout=2.0):
    """请求停止；窗口在**创建它的线程内**销毁（跨线程 DestroyWindow 会挂）。"""
    with _STATE_LOCK:
        stop = _STATE["stop"]
        th = _STATE["thread"]
    if stop is None:
        return False
    stop.set()
    if th is not None and th is not threading.current_thread():
        th.join(timeout=float(timeout))
    return True
