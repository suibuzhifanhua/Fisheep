"""
tray.py — 系统托盘（最小化到后台任务栏 / 通知区域）
====================================================
Fisheep 本身是「Flask 后端 + 浏览器窗口」，没有传统 GUI 主窗。
这里用 pystray 在任务栏通知区域放一个图标：
  - 双击图标 / 「打开程序」 → 还原/唤起原生主窗口（v1.0.698 默认行为；
    此前双击落 webbrowser 打开网页版，是因为 close_guard 拿不到 hwnd——
    安全软件拦 GetWindowTextW 致标题寻窗必败，v1.0.698 改按 PID 寻窗根治）
  - 「网页打开」            → 用系统浏览器打开 http://127.0.0.1:8787（无主窗口的兜底也走这里）
  - 「退出」               → 停止托盘并结束整个进程
这样关掉浏览器也不会杀掉应用，应用常驻后台。

纯 Windows 行为由 pystray 封装（底层 ctypes），打包进单文件 exe 无额外依赖。
"""
import contextlib
import os
import sys
import webbrowser

APP_URL = "http://127.0.0.1:8787/"
# v1.0.691 改名：显示名统一取品牌常量（单一事实源）
from core.branding import APP_TITLE   # noqa: E402
from core.logging import log_event   # noqa: E402（v1.0.703 气泡留痕）
APP_NAME = APP_TITLE   # "Fisheep"

_ICON = None   # pystray Icon 实例（start_tray 运行期可用；托盘唯一来源）


def notify(title, msg):
    """托盘气泡通知（NIF_INFO 经 pystray）。托盘未运行/失败返回 False。
    v1.0.373 收尾：全软件的气泡通知统一走这里（close_guard.tray_notify 委托）。"""
    ic = _ICON
    if ic is None:
        return False
    with contextlib.suppress(Exception):   # v1.0.703：发送前留痕，留痕失败不挡气泡
        log_event(f"[notify] tray「{str(title or '')[:63]}」：{str(msg or '')[:120]}")
    try:
        ic.notify(str(msg or "")[:250], str(title or "")[:63])
        return True
    except Exception:  # noqa: BLE001
        return False


def shutdown():
    """停止托盘图标（进程退出前调用，防 os._exit 硬杀残留图标；跨线程安全）。"""
    ic = _ICON
    if ic is not None:
        try:
            ic.stop()
        except Exception:  # noqa: BLE001
            pass


def _safe_open():
    """用系统浏览器打开伴侣页面（「网页打开」/ 无主窗口句柄时的兜底），异常静默，
    绝不因浏览器缺失而阻塞/崩溃。"""
    try:
        webbrowser.open(APP_URL)
    except Exception:  # noqa: BLE001
        pass


def resource_path(rel):
    """与 core.paths.resource_path 同一口径（v1.0.619 收敛：原先两处各写一遍
    `getattr(sys, "_MEIPASS", 上一级目录)`，改冻结路径规则时要记得改两处）。"""
    from core.paths import resource_path as _rp
    return _rp(rel)


def _make_icon():
    from PIL import Image, ImageDraw
    # 优先用托盘专用标 tray.ico（v1.0.674 起的反相黑白：近白浅灰底 + 近黑标，
    # 与近黑窗口标 app.ico 构成同一单色语言的反相；v1.0.785 起标 = 「AI 鱼&羊」）；
    # 缺失回退 app.ico；再缺失用绿色圆兜底（legacy 兜底路径，保持原样）。
    # 生成：tools/make_brand_icons.py。
    for name in ("tray.ico", "app.ico"):
        p = resource_path(name)
        try:
            if os.path.exists(p):
                return Image.open(p).convert("RGBA")
        except Exception:  # noqa: BLE001
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=(22, 163, 74, 255))
    d.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 230))
    return img


def start_tray(version, on_exit, get_show_fn=None, get_toggle_fn=None):
    """在调用线程里启动托盘图标（阻塞直到退出）。
    保证无论用户点击退出还是 run() 异常，on_exit 都恰好触发一次，避免进程残留/重复清理。
    get_show_fn(): 若提供则返回「还原主窗口」回调；「打开程序/双击」优先还原
    主窗口，拿不到时退回用系统浏览器打开（兼容无主窗口的极端情况）。
    get_toggle_fn（v1.0.788 P2-9）: 返回「主窗显示/隐藏切换」回调；「显示/隐藏窗口」
    菜单项调用它，拿不到句柄或执行失败时退回「打开程序」语义（绝不静默无响应）。
    v1.0.698：菜单改为「打开程序（默认/双击）+ 网页打开 + 退出」——用户需求
    「默认双击是打开软件，右键可选网页打开还是打开程序」。
    """
    import pystray

    exited = {"done": False}

    def _do_exit():
        if not exited["done"]:
            exited["done"] = True
            try:
                on_exit()
            except Exception:  # noqa: BLE001
                pass

    def _show_main():
        fn = None
        try:
            fn = get_show_fn() if get_show_fn else None
        except Exception:  # noqa: BLE001
            fn = None
        if fn:
            try:
                fn()
                return
            except Exception:  # noqa: BLE001
                pass
        _safe_open()  # 兜底：无主窗口句柄时用浏览器打开

    def on_open(icon, item):
        _show_main()

    def on_toggle(icon, item):
        # v1.0.788（P2-9）：显示/隐藏窗口——与全局热键唤窗同语义
        fn = None
        try:
            fn = get_toggle_fn() if get_toggle_fn else None
        except Exception:  # noqa: BLE001
            fn = None
        if fn:
            try:
                if fn():
                    return
            except Exception:  # noqa: BLE001
                pass
        _show_main()   # 兜底：无主窗句柄时按「打开程序」处理

    def on_web(icon, item):
        _safe_open()

    def on_quit(icon, item):
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass
        _do_exit()

    menu = pystray.Menu(
        # v1.0.470 修复（P2）：默认项机制——pystray.Icon 不支持 on_activate 参数
        # （构造时被静默丢弃），且 Windows 上双击行为绑定 default=True 菜单项。
        # v1.0.698（用户需求）：双击默认=「打开程序」（还原原生主窗口），
        # 右键菜单同时提供「网页打开」（浏览器）与「退出」，行为显式可选。
        pystray.MenuItem("打开程序", on_open, default=True),
        pystray.MenuItem("显示/隐藏窗口", on_toggle),   # v1.0.788（P2-9）
        pystray.MenuItem("网页打开", on_web),
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon(
        APP_NAME,   # v1.0.691 改名（原硬编码 "Lingban"）
        _make_icon(),
        f"{APP_NAME} v{version}",
        menu,
    )
    global _ICON
    _ICON = icon   # 气泡通知入口（tray.notify）就绪
    try:
        icon.run()  # 阻塞；退出后返回
    finally:
        _ICON = None
        # run() 异常退出也要兜底触发 on_exit，确保资源释放、进程不残留
        _do_exit()
