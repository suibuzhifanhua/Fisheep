"""
close_guard.py — 拦截主窗口 X 关闭，弹「最小化到托盘 / 退出程序」选择
====================================================================
pywebview(EdgeChromium/WinForms) 的 X 关闭会直接销毁窗口并让 webview.start() 返回、
进程退出，没有公开的"取消关闭"钩子。这里在 Windows 上用 ctypes 子类化主窗口的
WNDPROC，只拦截 WM_CLOSE：
  - 选「最小化到托盘」 → ShowWindow(SW_HIDE) 隐藏窗口并吞掉 WM_CLOSE（进程常驻托盘）
  - 选「退出程序」      → 把 WM_CLOSE 转发给原 WinForms 处理程序，正常关闭→进程退出
除 WM_CLOSE 外的所有消息一律转发给"原来的" WNDPROC（WinForms 托管处理程序，
而不是 DefWindowProcW），从而不会破坏 WinForms 自身的消息处理。

自定义按钮的关闭选择对话框是纯 Win32 自绘的（CreateWindowEx + 内置 BUTTON/STATIC 类，
无 comctl32 v6 manifest 依赖；按钮文字完全自定义：最小化到托盘 / 退出程序）。
字体用 GetStockObject(DEFAULT_GUI_FONT)（微软系统默认 GUI 字体），与资源管理器一致。

【关键防御】WM_CLOSE 回调内任何异常都不允许导致窗口直接关闭：
  - _ask_close_choice 全链路 try/except，任何失败降级 MessageBoxW（文字"是/否"）
  - MessageBoxW 也失败 → 返回 True（最小化到托盘），进程不退出
  - proc 外层再包一层 try，未捕获异常默认最小化（不转发 WM_CLOSE）
历史教训：
  - v1.0.181 曾因 WM_CREATE 内 SendMessageW(WM_SETFONT) 未包 try，
    CreateWindowExW 创建窗口时回调异常未被捕获，proc 异常后 WM_CLOSE 被默认处理直接关窗。
  - v1.0.276 实测：选「最小化到托盘」后窗口隐藏成功但进程秒退——根因是
    经典嵌套循环用 PostQuitMessage(0) 退出，而 pywebview 6.x 主循环是【无参
    Application.Run()】，对 quit 状态敏感，外层 .NET 泵退出 → webview.start()
    返回 → backend 进程退出。v1.0.277 起对话框改 PeekMessageW 非阻塞泵 +
    MsgWaitForMultipleObjects 超时轮询 choice，全程零 WM_QUIT/PostQuitMessage。
"""
import ctypes
import os
import time
# v1.0.660（审查修复 F13）：_WEB_ASK 的跨线程读写需要锁——本模块原先只局部导入
# threading（函数体内），模块级锁拿不到它。
import threading

user32 = ctypes.windll.user32
# GetStockObject 属于 gdi32.dll（user32 里没有！v1.0.181 误用 user32.GetStockObject
# 导致 _setup_prototypes 抛 AttributeError → install 失败 → 点 X 直接关窗）
gdi32 = ctypes.windll.gdi32

# 消息
WM_CLOSE = 0x0010
WM_CREATE = 0x0001
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_KEYDOWN = 0x0100
WM_SETFONT = 0x0030
# 通用窗口样式
GWLP_WNDPROC = -4
SW_HIDE = 0
SW_SHOW = 5
DEFAULT_GUI_FONT = 13  # GetStockObject(13) 微软系统默认 GUI 字体
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_TABSTOP = 0x00010000
WS_GROUP = 0x00020000
WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_TOPMOST = 0x00000008
# 按钮样式
BS_PUSHBUTTON = 0x00000000
BS_DEFPUSHBUTTON = 0x00000001
BS_AUTOCHECKBOX = 0x00000003   # v1.0.556「下次不再提示」复选框样式（漏定义曾致 WM_CREATE 抛 NameError）
# 静态文本样式
SS_LEFT = 0x00000000
SS_NOPREFIX = 0x00000080
# 系统颜色/键
COLOR_3DFACE = 15
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
# 消息等待掩码（MsgWaitForMultipleObjects：等待任意输入事件）
QS_ALLINPUT = 0x04FF
# DrawTextW 标志
DT_WORDBREAK = 0x0010
DT_CALCRECT = 0x0400
DT_NOPREFIX = 0x0800
# CreateFontW
FW_NORMAL = 400
FW_BOLD = 700
DEFAULT_CHARSET = 1
CLEARTYPE_QUALITY = 5
# 控件 ID
IDC_BTN_MINIMIZE = 101
IDC_BTN_EXIT = 102
IDC_CHK_REMEMBER = 103        # v1.0.556：「下次不再提示」复选框
BM_GETCHECK = 0x00F0          # 按钮消息：读复选框状态
BST_CHECKED = 0x0001
# 窗口类名
_DIALOG_CLASS = "WeComCloseDialog"
_BTN_CLASS = "BUTTON"
_STATIC_CLASS = "STATIC"
# MessageBoxW（降级兜底）
MB_YESNO = 0x0004
# v1.0.660（审查修复 U2）：兜底 MessageBox 加「取消」——MB_YESNO 时按 Esc 直接
# 返回 IDNO（=退出程序），与新语义（Esc=取消关闭、窗口保持打开）矛盾，
# 极端情况下按一下 Esc 就把软件退了。改用 MB_YESNOCANCEL。
MB_YESNOCANCEL = 0x0003
MB_ICONQUESTION = 0x0020
IDYES = 6
IDNO = 7
IDCANCEL = 2
# ---------- v1.0.682（08:30 事故 UX）：原生兜底对话框品牌化 ----------
# 08:30 实锤：前端轮询链断（后端停摆）时，只有这条降级链能问出"最小化还是退出"，
# 而它当时弹的是 Windows 系统默认 COLOR_3DFACE 灰白框——与软件恒深侧栏+品牌绿
# 完全两个世界，用户报障原话"弹窗也不是适配原来颜色的了"。现在对话框改用软件
# 自己的视觉语言：底=侧栏恒深 #1d232e、文字浅 #e8edf4、主按钮=品牌绿 #07c160
# （owner-draw，深绿字保证对比度）。全部绘制动作 try/except 包裹，任何一步失败
# 退回默认外观——兜底对话框宁可不漂亮，不能不可用。
_BRAND_BG = 0x2E231D          # #1d232e（BGR）——侧栏恒深底
_BRAND_FG = 0xF4EDE8          # #e8edf4（BGR）——深底浅字
_BRAND_BTN = 0x493C33         # #333c49（BGR）——次按钮（退出）填充
_BRAND_PRIM = 0x60C107        # #07c160（BGR）——主按钮（最小化到托盘）品牌绿
_BRAND_PRIM_FG = 0x162B06     # #062b16（BGR）——品牌绿底上的深绿字（对比度≥7:1）
_WM_CTLCOLORSTATIC = 0x0138
_WM_CTLCOLORBTN = 0x0134
_WM_CTLCOLOREDIT = 0x0135
_WM_DRAWITEM = 0x002B
BS_OWNERDRAW = 0x0000000B
DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0200
TRANSPARENT_BKMODE = 1

_BRAND_BRUSH = None           # 长寿命：窗口类背景 + CTLCOLOR 返回值，进程级不销毁


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class _DRAWITEM(ctypes.Structure):
    """v1.0.682：owner-draw 按钮的 WM_DRAWITEM 参数（取到按钮矩形与 ID 来绘制）。"""
    _fields_ = [("CTLTYPE", ctypes.c_uint), ("hwndItem", ctypes.c_void_p),
                ("itemID", ctypes.c_int), ("itemAction", ctypes.c_uint),
                ("itemState", ctypes.c_uint), ("itemRect", RECT)]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
    ctypes.c_size_t, ctypes.c_ssize_t)


# 防 GC（ctypes 回调必须长期存活）
_CBS = []


def _setup_prototypes():
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.SetActiveWindow.argtypes = [ctypes.c_void_p]
    user32.SetActiveWindow.restype = ctypes.c_void_p
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_int
    user32.EnableWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.EnableWindow.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = ctypes.c_int
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = ctypes.c_void_p
    user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    user32.PeekMessageW.restype = ctypes.c_int
    user32.MsgWaitForMultipleObjects.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.MsgWaitForMultipleObjects.restype = ctypes.c_uint
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.DestroyWindow.restype = ctypes.c_int
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEX)]
    user32.RegisterClassExW.restype = ctypes.c_ushort
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    gdi32.GetStockObject.restype = ctypes.c_void_p
    # v1.0.341：对话框排版所需（DPI 感知 + 文字实测 + 真字体）
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.DrawTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int,
                                 ctypes.POINTER(RECT), ctypes.c_uint]
    user32.DrawTextW.restype = ctypes.c_int
    user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(RECT), ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_uint]
    user32.AdjustWindowRectEx.restype = ctypes.c_int
    user32.SetFocus.argtypes = [ctypes.c_void_p]
    user32.SetFocus.restype = ctypes.c_void_p
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [ctypes.c_wchar_p]
    gdi32.CreateFontW.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_int
    # v1.0.682：品牌色对话框绘制所需
    user32.FillRect.argtypes = [ctypes.POINTER(RECT), ctypes.c_void_p]
    user32.FillRect.restype = ctypes.c_int
    gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint]
    gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    # v1.0.698（#8 最深根因）：这三个是 **gdi32** 函数，682 起误写 user32——
    # user32 不导出它们，_setup_prototypes 在此抛 AttributeError → install() 恒
    # return None → 关闭拦截从未装上（点 X 直接退出、_TRAY_SHOW 不回填、08:30
    # 灰色兜底框全是这一个坑）。user32 只有 FillRect 是例外（转发导出）。
    gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.SetBkMode.restype = ctypes.c_int
    gdi32.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    gdi32.SetTextColor.restype = ctypes.c_uint
    gdi32.SetBkColor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    gdi32.SetBkColor.restype = ctypes.c_uint
    # v1.0.698：按 PID 找主窗口（绕开被安全软件拦截的 GetWindowTextW）
    user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t]
    user32.EnumWindows.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_uint
    # v1.0.700：主窗句柄重建守护——死窗检测（IsWindow）+ 替代窗选区（GetWindowRect）
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = ctypes.c_int


# ---------- 自绘最小化/退出选择对话框 ----------
# 模态对话框全局状态：None=未选择 True=最小化 False=退出；
# remember=「下次不再提示」勾选状态（v1.0.556），由 install 的关闭拦截读取后持久化
# v1.0.660（审查修复 U2）：新增第三态 _CHOICE_CANCEL=取消关闭（窗口保持打开）。
# 原先只有"最小化/退出"两态，Esc 与点 X 都被硬塞进"最小化"——用户按 Esc（通用预期
# =取消本次操作）却把窗口藏进托盘，行为与预期相反。"取消"必须是一等状态。
_CHOICE_CANCEL = "cancel"
_DIALOG_RESULT = {"choice": None, "remember": False}
# 复选框控件句柄（WM_CREATE 时写入，按钮点击时 BM_GETCHECK 读取）
_DIALOG_CTRL = {"chk": None}

# v1.0.341：对话框文案与布局全局状态。
# 旧版布局写死 96-DPI 像素坐标、字体用 DEFAULT_GUI_FONT（中文系统上度量不匹配）、
# 说明文字控件高度不随实际换行数变——高 DPI / 实际字体更宽时文字互相重叠、
# 按钮越界出窗（v1.0.340 用户截图实锤）。新版：
#   1. 按 GetDpiForSystem 缩放全部坐标（DPI 不感知进程返回 96，DWM 拉伸同样成立）
#   2. 显式创建「Microsoft YaHei UI」9pt 字体（失败回退 DEFAULT_GUI_FONT）
#   3. 说明文字用 DrawTextW(DT_CALCRECT) 实测高度，窗口按内容自适应
#   4. 按钮水平居中、贴底排布，不再硬编码绝对坐标
_Q_TEXT = "关闭窗口后想做什么？"
# v1.0.660（审查修复 U2）：说明文字补一行键盘语义——此前 Esc 被绑成"最小化"，
# 用户按 Esc（通用预期=取消本次操作）却把窗口藏进了托盘，行为与预期相反。
# 现在 Esc / 右上角 × = 真正取消关闭（窗口保持打开）；回车 = 最小化到托盘。
_D_TEXT = ("「最小化到托盘」会让程序保留在后台运行，下次从系统托盘图标双击打开；\n"
           "「退出程序」则会完全关闭，下次需要手动重新启动。\n"
           "回车 = 最小化到托盘；Esc 或右上角 × = 取消关闭（窗口保持打开）。")
_LAYOUT = {"dpi": 96, "win_w": 480, "win_h": 240, "client_w": 452, "client_h": 228,
           "q": (24, 15, 404, 24), "d": (24, 45, 404, 52),
           "b1": (100, 170, 148, 34), "b2": (264, 170, 148, 34),
           "font": None, "font_bold": None, "fonts_owned": False}


def _system_dpi():
    """系统 DPI（Win10 1607+ 用 GetDpiForSystem；老系统回退 96）。"""
    try:
        f = user32.GetDpiForSystem
    except AttributeError:
        return 96
    try:
        return int(f()) or 96
    except Exception:  # noqa: BLE001
        return 96


def _make_gui_font(dpi, bold=False):
    """创建「Microsoft YaHei UI」9pt 字体；失败回退 DEFAULT_GUI_FONT（不可 Delete）。
    返回 (字体句柄, 是否需要 DeleteObject)。"""
    try:
        h = -round(9 * dpi / 72)  # 9pt：96dpi 时 -12
        f = gdi32.CreateFontW(
            h, 0, 0, 0, FW_BOLD if bold else FW_NORMAL,
            0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
            0, "Microsoft YaHei UI")
        if f:
            return f, True
    except Exception:  # noqa: BLE001
        pass
    try:
        return gdi32.GetStockObject(DEFAULT_GUI_FONT), False
    except Exception:  # noqa: BLE001
        return None, False


def _measure_text(text, width_px, dpi, bold=False):
    """DrawTextW(DT_CALCRECT) 实测：text 在 width_px 宽内按实际字体需要多高。"""
    try:
        hdc = user32.GetDC(None)
        if not hdc:
            return 0
        font, owned = _make_gui_font(dpi, bold)
        prev = gdi32.SelectObject(hdc, font)
        r = RECT(0, 0, int(width_px), 0)
        user32.DrawTextW(hdc, text, -1, ctypes.byref(r),
                         DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX)
        gdi32.SelectObject(hdc, prev)
        if owned:
            gdi32.DeleteObject(font)
        user32.ReleaseDC(None, hdc)
        return max(0, r.bottom - r.top)
    except Exception:  # noqa: BLE001
        return 0


def _compute_layout():
    """按当前 DPI + 实测文字高度计算对话框全部布局，写入 _LAYOUT。"""
    dpi = _system_dpi()
    d = dpi / 96.0
    margin = max(12, round(24 * d))
    client_w = max(300, round(432 * d))
    btn_w = max(96, round(148 * d))
    btn_h = max(26, round(34 * d))
    gap_btn = max(10, round(16 * d))
    content_w = client_w - 2 * margin
    q_h = _measure_text(_Q_TEXT, content_w, dpi, bold=True)
    if q_h <= 0:
        q_h = round(22 * d)  # 实测失败兜底
    q_h += max(2, round(4 * d))
    desc_h = _measure_text(_D_TEXT, content_w, dpi)
    if desc_h <= 0:
        desc_h = round(52 * d)
    gap1 = max(4, round(8 * d))
    gap_chk = max(6, round(12 * d))   # 说明文字与复选框间距
    chk_h = max(16, round(20 * d))    # 「下次不再提示」复选框行高
    gap2 = max(16, round(30 * d))
    bottom = max(10, round(22 * d))
    qy = margin
    dy = qy + q_h + gap1
    cy = dy + desc_h + gap_chk        # 复选框 y
    client_h = cy + chk_h + gap2 + btn_h + bottom
    total = 2 * btn_w + gap_btn
    bx = max(margin, (client_w - total) // 2)
    by = client_h - bottom - btn_h
    _LAYOUT.update(
        dpi=dpi, client_w=client_w, client_h=client_h,
        q=(margin, qy, content_w, q_h),
        d=(margin, dy, content_w, desc_h),
        c=(margin, cy, content_w, chk_h),
        b1=(bx, by, btn_w, btn_h),
        b2=(bx + btn_w + gap_btn, by, btn_w, btn_h),
    )


def _brand_brush():
    """v1.0.682：品牌深底画刷（进程级长寿命——窗口类注册时就要用，不能按对话框
    生命周期销毁）。创建失败返回 0，调用方退回系统默认外观。"""
    global _BRAND_BRUSH
    if not _BRAND_BRUSH:
        try:
            _BRAND_BRUSH = gdi32.CreateSolidBrush(_BRAND_BG)
        except Exception:  # noqa: BLE001
            return 0
    return _BRAND_BRUSH


def _draw_brand_button(hwnd_btn, x, y, w, h, text, primary, font):
    """v1.0.682：owner-draw 按钮绘制（品牌绿主按钮 / 深灰次按钮，浅字）。
    任何一步失败只影响观感——命中测试与消息分发不依赖绘制，按钮仍可点。"""
    try:
        hdc = user32.GetDC(hwnd_btn)
        if not hdc:
            return
        try:
            br = gdi32.CreateSolidBrush(_BRAND_PRIM if primary else _BRAND_BTN)
            r = RECT(x, y, x + w, y + h)
            user32.FillRect(ctypes.byref(r), br)
            gdi32.DeleteObject(br)
            prev = gdi32.SelectObject(hdc, font)
            gdi32.SetBkMode(hdc, TRANSPARENT_BKMODE)   # v1.0.698：gdi32（原 user32 取不到，绘制静默退回系统默认色）
            gdi32.SetTextColor(hdc, _BRAND_PRIM_FG if primary else _BRAND_FG)
            rr = RECT(x + 2, y + 2, x + w - 2, y + h - 2)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(rr),
                             DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)
            gdi32.SelectObject(hdc, prev)
        finally:
            user32.ReleaseDC(hwnd_btn, hdc)
    except Exception:  # noqa: BLE001
        pass  # silent-ok: 绘制失败退回系统默认按钮外观，命中测试与消息分发不依赖绘制


def _msgbox_fallback(hwnd):
    """最后防线：任何自绘失败都退回系统 MessageBoxW（是/否/取消）。
    返回 True=最小化到托盘，False=退出程序，_CHOICE_CANCEL=取消关闭（窗口保持打开）。
    v1.0.660（审查修复 U2）：用 MB_YESNOCANCEL —— MB_YESNO 时按 Esc 等于「否」
    （=退出程序），与「Esc=取消关闭」的新语义直接矛盾。
    MessageBoxW 本身失败时默认 True（不退出，更安全）。"""
    try:
        r = user32.MessageBoxW(
            hwnd, "要最小化到托盘继续后台运行，还是退出程序？\n"
                  "（取消 = 关闭窗口到托盘前的取消操作，窗口保持打开）",
            "Fisheep", MB_YESNOCANCEL | MB_ICONQUESTION)   # v1.0.691 改名（显示名与 core.branding.APP_TITLE 同值）
        if r == IDCANCEL:
            return _CHOICE_CANCEL
        return r == IDYES
    except Exception:  # noqa: BLE001
        return True  # 极端情况：默认最小化（进程不退出更安全）


def _brand_theme_lparam(msg, wparam, lparam):
    """v1.0.682：品牌深色主题的绘制消息分发（CTLCOLOR* / WM_DRAWITEM）。
    返回非 0=已处理（调用方直接以其值作为 wndproc 返回值）；0=不处理，交给默认过程。
    全 try/except：绘制失败退回系统默认外观——命中测试与消息分发不依赖绘制。"""
    if msg in (_WM_CTLCOLORSTATIC, _WM_CTLCOLORBTN, _WM_CTLCOLOREDIT):
        try:
            _child = ctypes.c_void_p(lparam)
            _chk = _DIALOG_CTRL.get("chk")
            if not (_chk and _child == _chk):
                # 复选框例外：深色底上系统勾选框图形会不可辨，保留系统默认外观
                gdi32.SetTextColor(ctypes.c_void_p(wparam), _BRAND_FG)   # v1.0.698：gdi32（原 user32 抛异常→整段退回系统默认外观）
                gdi32.SetBkColor(ctypes.c_void_p(wparam), _BRAND_BG)
                _bb = _brand_brush()
                if _bb:
                    return ctypes.c_ssize_t(_bb)
        except Exception:  # noqa: BLE001
            pass  # silent-ok: 绘制失败退回系统默认外观，对话框仍完整可用（观感降级非功能失效）
        return 0
    if msg == _WM_DRAWITEM:
        try:
            di = _DRAWITEM.from_address(lparam)
            font = _LAYOUT.get("font")
            if font and di.itemID in (IDC_BTN_MINIMIZE, IDC_BTN_EXIT):
                r = di.itemRect
                _draw_brand_button(di.hwndItem, r.left, r.top,
                                   r.right - r.left, r.bottom - r.top,
                                   "最小化到托盘" if di.itemID == IDC_BTN_MINIMIZE else "退出程序",
                                   di.itemID == IDC_BTN_MINIMIZE, font)
            return 1
        except Exception:  # noqa: BLE001
            return 0  # silent-ok: 按钮绘制失败退回系统默认按钮外观，按钮仍可点击
    return 0


def _dialog_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_CREATE:
        try:
            L = _LAYOUT
            # 字体在 _ask_close_choice 里创建好（含 DPI 缩放）；缺失则现场兜底
            font = L.get("font")
            if not font:
                font, owned = _make_gui_font(L.get("dpi", 96))
                L["font"], L["fonts_owned"] = font, owned
            qx, qy, qw, qh = L["q"]
            dx, dy, dw, dh = L["d"]
            cx, cy, cw, chh = L.get("c", (dx, dy + dh + 10, dw, 20))
            b1x, b1y, b1w, b1h = L["b1"]
            b2x, b2y, b2w, b2h = L["b2"]
            h1 = user32.CreateWindowExW(
                0, _STATIC_CLASS, _Q_TEXT,
                WS_CHILD | WS_VISIBLE | SS_LEFT | SS_NOPREFIX,
                qx, qy, qw, qh, hwnd, None, 0, None,
            )
            h2 = user32.CreateWindowExW(
                0, _STATIC_CLASS, _D_TEXT,
                WS_CHILD | WS_VISIBLE | SS_LEFT | SS_NOPREFIX,
                dx, dy, dw, dh, hwnd, None, 0, None,
            )
            # v1.0.682：两按钮改 owner-draw（品牌色自绘，观感与软件一致）；
            # 焦点/回车默认按钮语义不变（BS_OWNERDRAW 保留 WS_TABSTOP + SetFocus 给主按钮）
            h3 = user32.CreateWindowExW(
                0, _BTN_CLASS, "最小化到托盘",
                WS_CHILD | WS_VISIBLE | BS_OWNERDRAW | WS_TABSTOP,
                b1x, b1y, b1w, b1h, hwnd, IDC_BTN_MINIMIZE, 0, None,
            )
            h4 = user32.CreateWindowExW(
                0, _BTN_CLASS, "退出程序",
                WS_CHILD | WS_VISIBLE | BS_OWNERDRAW | WS_TABSTOP,
                b2x, b2y, b2w, b2h, hwnd, IDC_BTN_EXIT, 0, None,
            )
            # v1.0.556：「下次不再提示」复选框（勾选 + 点击任一按钮 = 记住该动作，
            # 之后点 X 直接执行不再弹窗；由 install 的关闭拦截持久化到 ui.close_action）
            h5 = user32.CreateWindowExW(
                0, _BTN_CLASS, "下次不再提示（记住本次选择）",
                WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX | WS_TABSTOP,
                cx, cy, cw, chh, hwnd, IDC_CHK_REMEMBER, 0, None,
            )
            _DIALOG_CTRL["chk"] = h5
            for h in (h1, h2, h3, h4, h5):
                if h:
                    user32.SendMessageW(h, WM_SETFONT, font, 1)  # lParam=1 立即重绘
            if h3:
                try:
                    user32.SetFocus(h3)  # 焦点给默认按钮：回车=最小化（防误退）
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass  # 字体/子控件创建失败不致命：窗口本体已在显示，按钮缺失仅影响交互
        return 0
    # v1.0.682：品牌深色主题（绘制消息抽到 _brand_theme_lparam，守住本函数 100 行上限）
    _themed = _brand_theme_lparam(msg, wparam, lparam)
    if _themed:
        return _themed
    if msg == WM_COMMAND:
        cid = wparam & 0xFFFF
        # v1.0.556：按钮点击时读取复选框状态，随 choice 一起交给关闭拦截持久化
        _remember = False
        try:
            chk = _DIALOG_CTRL.get("chk")
            if chk and user32.SendMessageW(chk, BM_GETCHECK, 0, 0) == BST_CHECKED:
                _remember = True
        except Exception:  # noqa: BLE001
            pass
        _DIALOG_RESULT["remember"] = _remember
        if cid == IDC_BTN_MINIMIZE:
            # 【v1.0.277】只置 choice，绝不调 PostQuitMessage——
            # PostQuitMessage 的 quit 状态会被外层 pywebview 的无参 Application.Run()
            # （.NET 消息泵）感知 → GUI 线程退出 → webview.start() 返回 → 进程 os._exit。
            # 嵌套循环改由 _ask_close_choice 的 PeekMessageW 泵直接轮询 choice 退出。
            _DIALOG_RESULT["choice"] = True
            return 0
        if cid == IDC_BTN_EXIT:
            _DIALOG_RESULT["choice"] = False
            return 0
    if msg == WM_KEYDOWN:
        # 回车=最小化（更安全，避免误退）
        # v1.0.660（审查修复 U2）：Esc 改为**真正取消关闭**——窗口保持打开。
        # 原实现 Esc=最小化，用户按 Esc（通用预期=取消本次操作）却把窗口藏进托盘，
        # 行为与预期相反；"取消关闭"本就该什么都不做。
        if wparam == VK_RETURN:
            _DIALOG_RESULT["choice"] = True
            return 0
        if wparam == VK_ESCAPE:
            _DIALOG_RESULT["choice"] = _CHOICE_CANCEL
            return 0
    if msg == WM_CLOSE:
        # v1.0.660（审查修复 U2）：点 X 关闭对话框 = 取消关闭（窗口保持打开），
        # 与 Esc 同义——"关掉这个对话框"不等于"替用户选了最小化"。
        _DIALOG_RESULT["choice"] = _CHOICE_CANCEL
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_DIALOG_WNDPROC = WNDPROC(_dialog_wndproc)
_CBS.append(_DIALOG_WNDPROC)  # 防 GC


_dialog_class_registered = False


def _register_dialog_class():
    global _dialog_class_registered
    if _dialog_class_registered:
        return
    wc = WNDCLASSEX()
    wc.cbSize = ctypes.sizeof(wc)
    wc.lpfnWndProc = ctypes.cast(_DIALOG_WNDPROC, ctypes.c_void_p)
    wc.hInstance = 0
    # v1.0.682：窗口类背景改品牌深底（画刷长寿命；失败退回系统 3DFACE 灰）
    _bb = _brand_brush()
    wc.hbrBackground = ctypes.c_void_p(_bb if _bb else (COLOR_3DFACE + 1))
    wc.lpszClassName = _DIALOG_CLASS
    user32.RegisterClassExW(ctypes.byref(wc))
    _dialog_class_registered = True


def _ask_close_choice(hwnd):
    """弹自绘模态对话框。返回 True=最小化到托盘，False=退出程序，
    _CHOICE_CANCEL（"cancel"）=取消关闭（窗口保持打开，v1.0.660 U2 新增）。
    全链路防御：任何一步失败都不抛异常（降级 MessageBoxW 或默认最小化）。"""
    fonts_owned = [False]
    try:
        _setup_prototypes()
        try:
            _register_dialog_class()
        except Exception:  # noqa: BLE001
            pass  # 注册失败继续走 CreateWindowEx（大概率也失败，会走 fallback）
        # v1.0.341：先按 DPI + 实测文字算布局，再经 AdjustWindowRectEx 反推窗口外框尺寸
        try:
            _compute_layout()
            font, owned = _make_gui_font(_LAYOUT["dpi"])
            font_b, owned_b = _make_gui_font(_LAYOUT["dpi"], bold=True)
            _LAYOUT["font"], _LAYOUT["font_bold"] = font, font_b
            _LAYOUT["fonts_owned"] = bool(owned or owned_b)
            fonts_owned[0] = _LAYOUT["fonts_owned"]
        except Exception:  # noqa: BLE001
            pass  # 布局失败沿用 _LAYOUT 里的静态兜底值
        try:
            rc = RECT(0, 0, _LAYOUT["client_w"], _LAYOUT["client_h"])
            user32.AdjustWindowRectEx(
                ctypes.byref(rc), WS_CAPTION | WS_SYSMENU, 0, WS_EX_DLGMODALFRAME)
            w = rc.right - rc.left
            h = rc.bottom - rc.top
        except Exception:  # noqa: BLE001
            w, h = 480, 260  # AdjustWindowRectEx 失败兜底
        w = max(280, w)
        h = max(180, h)
        # 按主显示器屏幕居中（父窗口 rect 在多屏/缩放下可能越界或不准）
        sw = user32.GetSystemMetrics(0x00)  # SM_CXSCREEN
        sh = user32.GetSystemMetrics(0x01)  # SM_CYSCREEN
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        # 禁用父窗口实现模态
        user32.EnableWindow(hwnd, False)
        _DIALOG_RESULT["choice"] = None
        _DIALOG_RESULT["remember"] = False
        hdlg = user32.CreateWindowExW(
            WS_EX_DLGMODALFRAME | WS_EX_TOPMOST,
            _DIALOG_CLASS, "Fisheep",   # v1.0.691 改名（显示名与 core.branding.APP_TITLE 同值）
            WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
            x, y, w, h,
            hwnd, None, 0, None,
        )
        if not hdlg:
            user32.EnableWindow(hwnd, True)
            return _msgbox_fallback(hwnd)
        user32.SetActiveWindow(hdlg)
        # 进入消息循环（模态阻塞到 choice 被设置）。
        # 【v1.0.277 根治】禁用 GetMessageW + PostQuitMessage 的经典嵌套循环模式：
        # pywebview 6.x 主循环是【无参 Application.Run()】，对线程 quit 状态敏感，
        # 对话框 PostQuitMessage(0) 即使被本循环的 GetMessageW 消费，外层 .NET 泵
        # 仍会退出 → webview.start() 返回 → backend._exit_app() 进程死亡
        # （v1.0.276 实测：选「最小化到托盘」窗口隐藏成功但进程秒退）。
        # 改为非阻塞 PeekMessageW 泵 + MsgWaitForMultipleObjects 100ms 超时：
        # 按钮点击在 DispatchMessageW 内直接置 choice，循环条件随之退出，全程零 WM_QUIT。
        msg = MSG()
        while _DIALOG_RESULT["choice"] is None:
            try:
                user32.MsgWaitForMultipleObjects(0, None, False, 100, QS_ALLINPUT)
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            except Exception:  # noqa: BLE001
                break  # 消息泵异常：跳出循环，按未选择处理
        try:
            user32.DestroyWindow(hdlg)
        except Exception:  # noqa: BLE001
            pass
        # v1.0.341：回收自建字体（stock 字体不可删，fonts_owned 只在含自建时为 True）
        if fonts_owned[0]:
            try:
                for k in ("font", "font_bold"):
                    if _LAYOUT.get(k):
                        gdi32.DeleteObject(_LAYOUT[k])
                _LAYOUT["font"] = _LAYOUT["font_bold"] = None
                _LAYOUT["fonts_owned"] = False
            except Exception:  # noqa: BLE001
                pass
        try:
            user32.EnableWindow(hwnd, True)
            user32.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
        return _DIALOG_RESULT["choice"] if _DIALOG_RESULT["choice"] is not None else True
    except Exception:  # noqa: BLE001
        # 兜底：任何未预期异常都回退 MessageBoxW（再失败默认最小化）
        try:
            user32.EnableWindow(hwnd, True)
        except Exception:  # noqa: BLE001
            pass
        return _msgbox_fallback(hwnd)


def find_main_hwnd(title, retries=120, delay=0.25):
    """按窗口标题找主窗口句柄（WinForms 表单创建后即可找到，含尚未 Show 的情况）。
    重试 120 次 × 0.25s = 30s：pywebview 窗口初始化可能较慢，给足时间避免
    install 线程先跑完导致关闭拦截没装上（点 X 直接退出）。"""
    for _ in range(retries):
        try:
            h = user32.FindWindowW(None, title)
        except Exception:  # noqa: BLE001
            h = None
        if h:
            return h
        time.sleep(delay)
    return None


# v1.0.698（用户需求：选了「每次询问」点 X 还是直接关 + 托盘双击打开的是网页版）
# 根因有**两层**（本机 companion.log 11-16 10:01 起每次启动都是「未找到主窗口句柄」）：
#   层1（最深，v1.0.682→697 潜伏）：_setup_prototypes 把三个 gdi32 函数误挂 user32
#       （SetBkMode/SetTextColor/SetBkColor，user32 不导出）→ 函数体第一行就抛
#       AttributeError → install() 恒 return None，根本没走到寻窗。698 已修（改挂
#       gdi32）+ install 失败路径单独记日志 + tests 真跑锁。
#   层2：本机安全软件拦截 GetWindowTextW（window-tucker 早为此弃用标题路径），
#       而 FindWindowW(None, title) 内部正是靠 GetWindowTextW 逐个比对标题 → 永远
#       匹配不上。即使层1修好，标题法在本机仍必败。
# 层2 修法：按**属主 PID** 找窗口——EnumWindows + GetWindowThreadProcessId 只碰
# 「窗口枚举 + 属主进程」，完全不走标题 API，安全软件拦不到这条路上。
_GWL_STYLE = -16
_WS_VISIBLE = 0x10000000


def find_main_hwnd_by_pid(retries=120, delay=0.25):
    """v1.0.698：按属主 PID 找本进程唯一的顶层主窗口（不走 GetWindowTextW）。
    本进程（pywebview 单进程）顶层窗口只有 WinForms 主表单一个；若枚举到多个
    （极端情况），优先取带 WS_VISIBLE 的。窗口尚未创建时按 find_main_hwnd 同款
    节奏重试（120 × 0.25s = 30s）。

    v1.0.701：install 跑得太早时命中的可能全是**隐藏**窗（.NET/pywebview 先建
    的辅助窗），真可见主窗随后才以另一句柄出现——此时不能急着返回隐藏窗
    （探针 11-17 14:23 实锤：钩挂隐藏窗 0x6b0e50，可见主窗 0x800968 晚到；
    旧逻辑直接 return 隐藏窗 → 点 X 直达 .NET 退出）。修法：命中全隐藏时
    继续等约 10s（40 × 0.25）让可见主窗出现；始终没出现（托盘启动态）才
    挂第一个命中窗，由 _rehook_watchdog 的「隐藏挂点」触发器兜底。"""
    try:
        _setup_prototypes()
        own_pid = os.getpid()
    except Exception:  # noqa: BLE001
        return None
    hidden_streak = 0   # v1.0.701：连续「只枚举到隐藏窗」的轮数
    for _ in range(retries):
        hits = []

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
        def _cb(h, _u):
            pid = ctypes.c_ulong()
            try:
                # 返回值是**创建窗口的线程 ID**；进程 ID 写在 pid 指针里——
                # 拿返回值比 own_pid 恒不等（698 实跑首测抓到的真 bug）
                user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == own_pid:
                    hits.append(h)
            except Exception:  # noqa: BLE001
                pass  # silent-ok: 单个窗口查询失败就跳过该窗口，不影响其余窗口枚举
            return 1

        try:
            user32.EnumWindows(_cb, 0)
        except Exception:  # noqa: BLE001
            pass  # silent-ok: 本轮枚举失败不致命——窗口还没建出来时按 delay 下轮重试
        if hits:
            try:
                vis = [h for h in hits
                        if user32.GetWindowLongPtrW(h, _GWL_STYLE) & _WS_VISIBLE]
            except Exception:  # noqa: BLE001
                vis = []
            if vis:
                return vis[0]
            # v1.0.701：全隐藏 → 不急着返回，等可见主窗（上限 40 轮≈10s）
            hidden_streak += 1
            if hidden_streak >= 40:
                return hits[0]
        else:
            hidden_streak = 0
        time.sleep(delay)
    return None


_WM_SETICON = 0x80
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x10


def _apply_icon(hwnd, icon_path):
    """给窗口设置小/大图标（任务栏按钮、标题栏、Alt-Tab）。
    注意：HICON 是 64 位句柄——必须显式 restype=c_void_p，否则 ctypes 默认
    32 位截断导致图标静默失效（v1.0.324 首版踩坑）。用独立 user32 实例，
    不污染 close_guard 全局原型。"""
    import ctypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.LoadImageW.restype = ctypes.c_void_p
    u32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u32.GetSystemMetrics.restype = ctypes.c_int
    u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    u32.SendMessageW.restype = ctypes.c_void_p
    u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p]
    sm_cxsm = u32.GetSystemMetrics(49) or 16   # SM_CXSMICON
    sm_cx = u32.GetSystemMetrics(11) or 32     # SM_CXICON
    hicon_sm = u32.LoadImageW(None, icon_path, _IMAGE_ICON, sm_cxsm, sm_cxsm, _LR_LOADFROMFILE)
    hicon_big = u32.LoadImageW(None, icon_path, _IMAGE_ICON, sm_cx, sm_cx, _LR_LOADFROMFILE)
    sent = 0
    if hicon_sm:
        u32.SendMessageW(hwnd, _WM_SETICON, ctypes.c_void_p(0), ctypes.c_void_p(hicon_sm))
        sent += 1
    if hicon_big:
        u32.SendMessageW(hwnd, _WM_SETICON, ctypes.c_void_p(1), ctypes.c_void_p(hicon_big))
        sent += 1
    return sent


# ---------- v1.0.573：web 层关闭确认（HTML 弹窗，软件同款字体/风格） ----------
# 点 X 时设置 pending 标志并吞掉 WM_CLOSE；前端经既有轮询（/api/close_ask）发现
# pending 后在网页内弹确认层，选择 POST /api/close_ask/choice 回传执行。
# v1.0.580 教训：不要在 WM_CLOSE 钩子线程里同步调 evaluate_js——钩子跑在
# pywebview GUI 主线程上，evaluate_js 内部再等主线程 = 自我死锁（界面冻结、
# 确认层点不动的实锤）。HTTP 轮询桥完全绕开跨线程调用。
# 降级链：8s 内前端未回传选择（页面崩了/轮询失效）→ 后台线程弹品牌色自绘对话框。
# v1.0.682（08:30 事故）：原 15s——用户体感"按了 X 很久才有反应"。8s 是"前端活着
# 就早回传"与"页面慢启动误降级"之间的平衡；真降级的场景里对话框观感同样重要。
_WEB_ASK = {"pending_hwnd": None, "once": None, "remember": False}   # v1.0.581：web 关闭确认状态
# v1.0.660（审查修复 F13）：_WEB_ASK 同时被两个线程读写——apply_web_choice（HTTP 线程）
# 写、proc 钩子（GUI 线程）读。原实现「读取 → 清空」是两步，中间可能被对方插进来：
# 极端情况下 once 被读走但 remember 还没读到（读到 remember=False），
# 「下次不再提示」的勾选丢失；更糟的是两次 WM_CLOSE 会读到同一个 once 而重复执行动作。
# 加锁把「读 once + 读 remember + 清 pending」收成一个原子步骤。
_WEB_ASK_LOCK = threading.Lock()
_WEB_APPLY = {"fn": None}              # install 闭包注册的执行回调
_WEB_ASK_FALLBACK_S = 8.0              # 前端未响应的降级时限（v1.0.682：15s→8s，08:30 事故"按 X 很久才弹窗"）


def close_ask_pending():
    """供 /api/close_ask 查询：是否存在等待用户确认的关闭请求。"""
    with _WEB_ASK_LOCK:
        return _WEB_ASK.get("pending_hwnd") is not None


def apply_web_choice(minimize, remember):
    """选择回传入口（/api/close_ask/choice POST 调这里，HTTP 线程）。
    v1.0.581：**不在此线程直接执行窗口操作**——CoreWebView2 只能在 UI 线程访问，
    跨线程调 prev_fn/ShowWindow/window.hide() 会弹 .NET 未处理异常（用户截图实锤）。
    改为：把 {once 动作, remember} 存入 _WEB_ASK → PostMessage 投递 WM_CLOSE 给
    本窗口 → 钩子在 GUI 线程收到后按 once 执行（持久化 + 动作），天然线程安全。
    v1.0.660（审查修复 F13）：写入与清 pending 放进同一把锁里，避免与 GUI 线程
    的「读 once」交叉，出现半写状态（remember 丢失 / once 被重复消费）。"""
    fn = _WEB_APPLY.get("fn")
    if not fn:
        return
    with _WEB_ASK_LOCK:
        hwnd = _WEB_ASK.get("pending_hwnd")
        _WEB_ASK["once"] = "minimize" if minimize else "exit"
        _WEB_ASK["remember"] = bool(remember)
        _WEB_ASK["pending_hwnd"] = None            # 清 pending（前端弹层自动收起）
    try:
        import ctypes as _ct
        user32.PostMessageW(_ct.c_void_p(hwnd or 0), WM_CLOSE, 0, 0)
    except Exception as e:  # noqa: BLE001
        # PostMessage 失败（极端）→ 降级直接执行（可能跨线程异常，总比无响应好）
        try:
            from core_utils import log_event
            log_event(f"[close-guard] PostMessage 失败，降级直接执行：{e}")
        except Exception:  # noqa: BLE001
            pass
        fn(bool(minimize), bool(remember))


def _find_main_replacement():
    """v1.0.700：被挂钩窗口失效后，为本进程挑当前主窗（重挂目标）。
    优先可见窗口（常态下只有一个可见 WinForms 主表单）；不可见时（最小化到
    托盘期间 .NET 重建句柄的极端情况）取**窗口面积最大**者——.NET parking 窗、
    pystray 托盘助手窗面积 0 或极小，WinForms 主表单必最大。找不到返回 None。"""
    try:
        _setup_prototypes()
        own_pid = os.getpid()
    except Exception:  # noqa: BLE001
        return None
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    def _cb(h, _u):
        pid = ctypes.c_ulong()
        try:
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == own_pid:
                hits.append(h)
        except Exception:  # noqa: BLE001
            pass  # silent-ok: 单个窗口查询失败跳过该窗，EnumWindows 继续枚举其余
        return 1

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:  # noqa: BLE001
        return None
    if not hits:
        return None
    try:
        vis = [h for h in hits
               if user32.GetWindowLongPtrW(h, _GWL_STYLE) & _WS_VISIBLE]
    except Exception:  # noqa: BLE001
        vis = []
    pool = vis or hits
    if len(pool) == 1:
        return pool[0]
    best, best_area = pool[0], -1
    for h in pool:
        r = RECT()
        area = 0
        try:
            if user32.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r)):
                area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
        except Exception:  # noqa: BLE001
            area = 0
        if area > best_area:
            best, best_area = h, area
    return best


def _sync_tray_hwnd(hwnd):
    """v1.0.700：重挂后同步 shell._TRAY_SHOW——托盘「打开程序」与
    /api/show_window（单实例二次启动弹窗）都走这个句柄，旧句柄死了它们就失效。"""
    try:
        from desktop import shell as _shell
        _shell._TRAY_SHOW["hwnd"] = hwnd
    except Exception:  # noqa: BLE001
        pass  # silent-ok: shell 未加载/属性缺失时托盘退回浏览器路径，不阻断重挂


def _rehook_watchdog(state, attach):
    """v1.0.700：主窗句柄重建守护（用户报「选了每次询问点 X 还是直接关了」的根治）。

    根因：install 时挂钩的 WNDPROC 绑在**当时那个句柄**上；.NET WinForms 在 DPI/
    首次显示阶段会重建表单句柄（旧句柄 Destroy、新句柄 Create），旧 WNDPROC 随旧
    句柄一起失效——此后点 X 直达 .NET 处理器退出：无 pending、无 8s 降级、无任何
    [close-guard] 日志（11-17 13:12/13:15 两次实跑 + PostMessage 探针实锤）。
    守护：两类失守都重挂并留痕——
    ① 被挂钩窗死亡（.NET 重建句柄：旧 Destroy、新 Create）→ 在新主窗重挂；
    ② v1.0.701 被挂钩窗还活着但是**隐藏**、且本进程另有可见窗（install 挂到
       .NET 辅助窗、可见主窗晚到）→ 在可见主窗重挂。托盘隐藏态主窗不可见
       但无其它可见窗，两类误触都不会发生。节奏：前 2 分钟 1s（句柄重建集中
    在窗口创建/首显阶段），之后 5s 终身（DPI 变化等晚发重建也覆盖）。"""
    import time as _t

    stop = threading.Event()   # 测试钩子：stop_rehook_watchdog() 可停（防测试窗口串扰）

    def _loop():
        t0 = _t.time()
        while not stop.is_set():
            try:
                if stop.wait(1.0 if _t.time() - t0 < 120.0 else 5.0):
                    break
                hooked = state.get("hwnd")
                if not hooked:
                    continue
                if user32.IsWindow(ctypes.c_void_p(hooked)):
                    # v1.0.701：被挂钩窗还活着**不等于**钩子还在正确窗上——
                    # install 可能挂到 .NET 辅助窗（隐藏），可见主窗以另一句柄
                    # 晚到（探针 11-17 14:23 实锤：挂隐藏窗 0x6b0e50，可见主窗
                    # 0x800968 后建，旧守护只探「死亡」恒不触发 → 点 X 秒退）。
                    # 触发条件：被挂钩窗不可见 **且** 本进程另有可见窗 → 重挂。
                    # 托盘隐藏态：主窗不可见但无其它可见窗 → 不动，两态可分。
                    try:
                        hooked_hidden = not (
                            user32.GetWindowLongPtrW(
                                ctypes.c_void_p(hooked), _GWL_STYLE) & _WS_VISIBLE)
                    except Exception:  # noqa: BLE001
                        hooked_hidden = False   # 风格读取失败按「不动」保守处理
                    if hooked_hidden:
                        cur = _find_main_replacement()
                        if cur and cur != hooked:
                            try:
                                cur_vis = bool(user32.GetWindowLongPtrW(
                                    ctypes.c_void_p(cur), _GWL_STYLE) & _WS_VISIBLE)
                            except Exception:  # noqa: BLE001
                                cur_vis = False
                            if cur_vis:
                                if attach(cur):
                                    try:
                                        from core_utils import log_event
                                        log_event("[guard] 挂钩点为隐藏窗，可见主窗 "
                                                  "0x%x 已出现，关闭拦截重新挂钩" % cur)
                                    except Exception:  # noqa: BLE001
                                        pass  # silent-ok: 留痕失败不影响重挂结果
                                    _sync_tray_hwnd(cur)
                                continue
                    continue   # 可见 / 隐藏但无其它可见窗（托盘态）→ 无需动作
                cur = _find_main_replacement()
                if not cur or cur == hooked:
                    continue   # 新主窗还没建出来 → 下轮再查
                if not attach(cur):
                    continue   # 新句柄 WNDPROC 读取失败 → 下轮再试
                try:
                    from core_utils import log_event
                    log_event("[guard] 主窗句柄已失效（0x%x→0x%x，.NET 重建句柄），"
                              "关闭拦截已重新挂钩" % (hooked, cur))
                except Exception:  # noqa: BLE001
                    pass  # silent-ok: 留痕失败不影响重挂结果
                _sync_tray_hwnd(cur)
            except Exception:  # noqa: BLE001
                pass  # silent-ok: 守护线程任何异常都不得中断循环（否则守护本身失守）

    th = threading.Thread(target=_loop, daemon=True, name="guard-rehook-watchdog")
    th.start()
    return th, stop


_WATCHDOG_STOP = None   # v1.0.700：当前守护线程的停止开关（install 时回填）


def stop_rehook_watchdog():
    """v1.0.700 测试入口：停掉句柄重建守护线程（测试自建窗口用完即毁，
    不停的话守护会去挂钩同进程其它测试建的窗口，造成跨用例串扰）。"""
    if _WATCHDOG_STOP is not None:
        _WATCHDOG_STOP.set()


def install(title, on_minimize, icon_path=None, on_hotkey=None,
            saved_action=None, save_action=None):
    """安装关闭拦截。on_minimize() 在用户选「最小化到托盘」时于 UI 线程被调用。
    成功返回安装的 hwnd；失败返回 None。建议在独立线程中调用以免阻塞启动。
    icon_path（v1.0.324）：传入 .ico 路径时用 WM_SETICON 设置窗口大小图标——
    pywebview 的 start(icon=) 在 Windows/WinForms 后端被忽略，任务栏/Alt-Tab
    图标必须自行设置。
    on_hotkey（v1.0.373）：dict{"stop": fn}——Ctrl+Alt+S 全局热键触发的停止回调。
    安装成功后自动添加托盘图标（左键还原）+ 注册全局热键。
    v1.0.556（用户需求）：关闭对话框加「下次不再提示」复选框——
    saved_action：callable → None/"minimize"/"exit"，返回已记住的关闭动作
    （非 None 时点 X 直接执行，不再弹窗）；
    save_action：callable(minimize: bool)——用户勾选「下次不再提示」并选择后，
    把动作持久化（由调用方写 config，本模块不碰配置层）。"""
    try:
        _setup_prototypes()
    except Exception as e:  # noqa: BLE001
        # v1.0.698：原型失败与「窗口没找到」必须分开记——v1.0.682→697 期间
        # user32 误挂 gdi32 函数（SetBkMode 等）就在这里炸，而调用方只记
        # 「未找到主窗口句柄」，把排查带偏了一整天（#8 的误导源）。
        try:
            from core_utils import log_event
            log_event(f"[guard] _setup_prototypes 失败（关闭拦截未安装，点 X 将直接退出）：{e}")
        except Exception:  # noqa: BLE001
            pass  # silent-ok: 留痕自身失败不影响降级路径——install 照样 return None
        return None
    # v1.0.698：PID 寻窗优先（安全软件拦 GetWindowTextW 时标题法必败，见
    # find_main_hwnd_by_pid 顶部注释）；标题法留作兜底（PID 法异常时退回旧路）。
    hwnd = find_main_hwnd_by_pid()
    if not hwnd:
        hwnd = find_main_hwnd(title)
    if not hwnd:
        return None
    try:
        # v1.0.700：共享 state + attach 闭包——.NET 重建句柄后对**新句柄**再执行一次
        # attach（prev/prev_fn 每次现取；proc WNDPROC 每次重建，杜绝陈旧闭包引用）。
        state = {"hwnd": hwnd, "prev": None, "prev_fn": None,
                 "on_min": on_minimize}

        def _do_minimize(_h):
            """最小化+藏任务栏+通知回调（v1.0.654 B9：原三处逐字复制的收尾动作）。"""
            try:
                user32.ShowWindow(_h, SW_HIDE)
            except Exception:  # noqa: BLE001
                pass
            try:
                hide_from_taskbar(_h)
            except Exception:  # noqa: BLE001
                pass
            try:
                on_minimize()
            except Exception:  # noqa: BLE001
                pass

        def _do_exit(_h, _msg, _wp, _lp):
            """退出：转发原处理程序（v1.0.654 B9：原三处逐字复制）。"""
            try:
                return state["prev_fn"](_h, _msg, _wp, _lp)
            except Exception:  # noqa: BLE001
                return 0

        # v1.0.573：web 层关闭确认的选择回调（js_api apply_close_choice → 模块级
        # apply_web_choice → 本闭包）。执行动作与原自绘对话框的选择路径一致。
        def _apply_web_choice(minimize, remember):
            try:
                if remember and save_action:
                    try:
                        save_action(bool(minimize))
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
            if minimize:
                _do_minimize(state["hwnd"])
            else:
                # 退出：转发 WM_CLOSE 给原处理程序（正常关闭 → 进程退出）
                _do_exit(state["hwnd"], WM_CLOSE, 0, 0)
        _WEB_APPLY["fn"] = _apply_web_choice

        def attach(hwnd):
            """v1.0.700：把 WNDPROC 挂钩（或重挂）到指定主窗；失败返回 False。
            .NET 重建句柄后旧 WNDPROC 随旧句柄一起死掉——必须对新句柄重跑本闭包
            （prev 现取、proc 重建）；共享 state 延续，pending/once 不受影响。"""
            prev = user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
            if not prev:
                return False
            state["prev"] = prev
            state["prev_fn"] = WNDPROC(prev)  # 原 WinForms 托管处理程序
            state["hwnd"] = hwnd
            if icon_path:
                try:
                    _apply_icon(hwnd, icon_path)
                except Exception:  # noqa: BLE001
                    pass  # 图标失败不影响关闭拦截

            @WNDPROC
            def proc(h, msg, wp, lp):
                if msg == WM_CLOSE:
                    # v1.0.653 瘦身：state["busy"] 死机制删除——全部赋值点恒 False（v1.0.582
                    # 起注释明令"严禁置 True"），恒不命中的拦截分支连同状态位一并移除。
                    # v1.0.556：已记住动作（勾选过「下次不再提示」）→ 直接执行不弹窗
                    try:
                        _act = saved_action() if saved_action else None
                    except Exception:  # noqa: BLE001
                        _act = None
                    if _act == "minimize":
                        _do_minimize(h)
                        return 0
                    if _act == "exit":
                        return _do_exit(h, msg, wp, lp)
                    # v1.0.581：web 确认层的一次性动作回放——apply_web_choice（HTTP
                    # 线程）不直接执行窗口操作（CoreWebView2 只能在 UI 线程访问，
                    # 跨线程直接调 prev_fn/ShowWindow 会弹 .NET 未处理异常），改为把
                    # 动作存 _WEB_ASK["once"] 并 PostMessage 投递 WM_CLOSE 回 GUI
                    # 线程，在此处按 once 执行——天然在 UI 线程，零跨线程异常。
                    # v1.0.660（审查修复 F13）：读 once + 读 remember + 清 pending 收进
                    # 同一把锁——与 HTTP 线程的 apply_web_choice 互斥，杜绝"读到 once
                    # 但 remember 还没写进去"的半写状态，也杜绝同一个 once 被消费两次。
                    _once = None
                    _remember = False
                    try:
                        with _WEB_ASK_LOCK:
                            _once = _WEB_ASK.get("once")
                            if _once:
                                _remember = bool(_WEB_ASK.get("remember"))
                                _WEB_ASK["once"] = None
                                _WEB_ASK["pending_hwnd"] = None
                                _WEB_ASK["remember"] = False
                    except Exception:  # noqa: BLE001
                        pass  # silent-ok: 取不到 once 标记即按"本次不弹窗"处理
                    if _once:
                        # v1.0.581：勾选「不再询问」→ 在 GUI 线程持久化（save_action 写 config）
                        if _remember and save_action:
                            try:
                                save_action(_once == "minimize")
                            except Exception:  # noqa: BLE001
                                pass
                        if _once == "minimize":
                            _do_minimize(h)
                            return 0
                        if _once == "exit":
                            return _do_exit(h, msg, wp, lp)
                    # v1.0.573→582（用户反馈：原生弹窗字体不一致 + web 层死锁 + 退出无反应）：
                    # 设置 pending 标志并吞掉本次 WM_CLOSE——前端 500ms 常驻轮询发现后在
                    # 网页内弹确认层，选择经 /api/close_ask/choice 回传，PostMessage 投回
                    # WM_CLOSE 由本钩子的 once 回放执行。
                    # **严禁在此线程调 evaluate_js**（死锁，v1.0.580 实锤）。
                    # 防重复用 pending 标志：确认层已弹出时再点 X 直接忽略。
                    with _WEB_ASK_LOCK:
                        if _WEB_ASK.get("pending_hwnd") is not None:
                            return 0   # 确认层已弹，忽略重复点 X
                        _WEB_ASK["pending_hwnd"] = h

                    def _web_ask_fallback(_h=h):
                        import time as _t
                        from core_utils import log_event  # 本模块不顶层导入，与 :617 同款局部导入
                        deadline = _t.time() + _WEB_ASK_FALLBACK_S
                        while _t.time() < deadline:
                            with _WEB_ASK_LOCK:
                                if _WEB_ASK.get("pending_hwnd") != _h:
                                    return   # 前端已回传选择，无需降级
                            _t.sleep(0.5)
                        with _WEB_ASK_LOCK:
                            still = _WEB_ASK.get("pending_hwnd") == _h
                        if not still:
                            return
                        try:
                            log_event("[close-guard] web 确认层 %.0fs 未响应，降级自绘对话框"
                                      % _WEB_ASK_FALLBACK_S)
                            minimize = _ask_close_choice(_h)
                            # v1.0.660（审查修复 U2）：取消关闭 = 什么都不做，窗口保持打开。
                            # 清掉 pending 让下一次点 X 能重新弹层（否则会被"已弹"守卫吞掉）。
                            if minimize == _CHOICE_CANCEL:
                                with _WEB_ASK_LOCK:
                                    if _WEB_ASK.get("pending_hwnd") == _h:
                                        _WEB_ASK["pending_hwnd"] = None
                                try:
                                    log_event("[close-guard] 用户取消关闭（Esc / 关闭对话框）——窗口保持打开")
                                except Exception:  # noqa: BLE001
                                    pass
                                return
                            apply_web_choice(minimize, bool(_DIALOG_RESULT.get("remember")))
                        except Exception as e:  # noqa: BLE001
                            try:
                                log_event(f"[close-guard] 降级对话框异常（默认最小化）：{e}")
                            except Exception:  # noqa: BLE001
                                pass

                    import threading as _th_fb
                    _th_fb.Thread(target=_web_ask_fallback, daemon=True,
                                  name="close-ask-fallback").start()
                    return 0   # 吞掉本次关闭；等前端回传或 _WEB_ASK_FALLBACK_S（8s）降级弹品牌色对话框
                if msg == WM_HOTKEY:
                    # v1.0.373 全局热键：Ctrl+Alt+Space 唤起/隐藏；Ctrl+Alt+S 停止所有任务
                    if wp == _HK_TOGGLE:
                        try:
                            u = _tray_user32()
                            if u.IsWindowVisible(h):
                                user32.ShowWindow(h, SW_HIDE)
                            else:
                                show_window(h)
                        except Exception:  # noqa: BLE001
                            pass
                    elif wp == _HK_STOP:
                        fn = _on_hotkey_cb.get("stop")
                        if fn:
                            try:
                                fn()
                            except Exception:  # noqa: BLE001
                                pass
                    return 0
                # 其它所有消息：原样转发给原 WinForms 处理程序
                try:
                    return state["prev_fn"](h, msg, wp, lp)
                except Exception:  # noqa: BLE001
                    return 0

            state["cb"] = proc
            _CBS.append(state)  # 防 GC
            user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, proc)
            register_hotkeys(hwnd, (on_hotkey or {}).get("stop"))
            return True

        if not attach(hwnd):
            return None
        # v1.0.700：句柄重建守护——没有它，.NET 第一次重建句柄就静默让钩子失守
        # （用户报「选了每次询问点 X 还是直接关了」的根因，见 _rehook_watchdog 注释）
        global _WATCHDOG_STOP
        _WATCHDOG_STOP = _rehook_watchdog(state, attach)[1]
        # v1.0.373 收尾（双图标根治）：托盘图标统一由 tray.py（pystray）负责。
        # 此前两套并存——pystray 的「企业微信伴侣 vX」+ 本模块的「企业微信伴侣
        # （双击打开）」——任务栏永远两个图标（用户 hover 截图确认，2026-09-25）。
        # pystray 成熟库自带 TaskbarCreated 重建与右键菜单，统一归它；
        # 本模块仅保留：X 关闭拦截 / 最小化隐藏 / 全局热键 / 气泡通知（委托 tray.py）。
        return hwnd
    except Exception:  # noqa: BLE001
        return None


def show_window(hwnd):
    """把隐藏的主窗口重新显示并置于前台（托盘「打开程序」/ 全局热键调用）。"""
    try:
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass


# ==================== v1.0.373 托盘图标 + 气泡通知 + 全局热键 ====================
# 此前「最小化到托盘」只是隐藏窗口（无真托盘图标），用户只能重启找回——补齐：
#   Shell_NotifyIconW 托盘图标（左键还原）+ NIF_INFO 气泡通知（任务完成提醒）
#   + RegisterHotKey 全局热键（Ctrl+Alt+Space 唤起/隐藏、Ctrl+Alt+S 停止所有任务）。
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x1, 0x2, 0x4
VK_SPACE = 0x20
_HK_TOGGLE, _HK_STOP = 1, 2


_on_hotkey_cb = {"stop": None}      # backend 注入：停止所有任务（Ctrl+Alt+S）
_user32_tray = None


def _tray_user32():
    """独立的 user32 实例并配好 RegisterHotKey 原型（不污染全局）。
    v1.0.498：Shell_NotifyIconW 原型随 ctypes 自注册托盘簇删除（托盘统一归 tray.py/pystray）。"""
    global _user32_tray
    if _user32_tray is not None:
        return _user32_tray
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    u.RegisterHotKey.restype = ctypes.c_int
    u.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.IsWindowVisible.restype = ctypes.c_int
    _user32_tray = u
    return u


def tray_notify(title, msg):
    """托盘气泡通知（任务完成/失败提醒）。
    v1.0.373 收尾：托盘统一归 tray.py（pystray），气泡委托其 notify——
    此前本模块用自注册托盘的 NIM_MODIFY 发气泡，双图标根源已移除。"""
    try:
        from desktop import tray
        return tray.notify(title, msg)
    except Exception:  # noqa: BLE001
        return False


def remove_tray_icon():
    """进程退出前清理托盘图标（防 os._exit 硬杀残留）。
    v1.0.373 收尾：托盘统一归 tray.py（pystray），委托其 shutdown；
    本模块的 ctypes 自注册托盘已移除（双图标根治，2026-09-25）。"""
    try:
        from desktop import tray
        tray.shutdown()
    except Exception:  # noqa: BLE001
        pass


def activate_existing(title):
    """把已在运行实例的主窗口激活到前台（单实例二次启动时调用，
    避免『以为没开再点一次』造成多开困惑）。
    v1.0.698 双路查找：
      ① 标题法 FindWindowW —— 无安全软件的机器上可用（也能命中隐藏窗口）；
      ② HTTP 让运行中实例自己弹窗（/api/show_window，backend.py 路由）——
         本机安全软件拦 GetWindowTextW 时标题法必败（与 install 装不上是同一
         根因，见 find_main_hwnd_by_pid 顶部注释），第二实例 PID 未知用不了
         PID 法，但第一实例**知道自己的 hwnd**，走 HTTP 完全绕开窗口枚举。"""
    try:
        _setup_prototypes()
        h = user32.FindWindowW(None, title)
        if h:
            show_window(h)
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:8787/api/show_window", data=b"{}", method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            if r.status == 200:
                return True
    except Exception:  # noqa: BLE001
        pass  # silent-ok: 第一实例 HTTP 未就绪/端口被占：退回 False，由调用方 toast 引导
    return False


def hide_from_taskbar(hwnd):
    """v1.0.373（用户需求7）：窗口隐藏时强制从任务栏移除按钮——WinForms 隐藏后
    任务栏按钮偶有残留（用户实测显示两个图标）。经 ITaskbarList::DeleteTab（COM
    vtable 直调：槽 3=HrInit，5=DeleteTab）。失败静默。"""
    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitialize.argtypes = [ctypes.c_void_p]
        ole32.CoInitialize.restype = ctypes.c_int
        ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = ctypes.c_int
        ole32.CoUninitialize.restype = None
        CLSCTX_INPROC_SERVER = 1
        clsid = (ctypes.c_ubyte * 16)(0x44, 0xF3, 0xFD, 0x56, 0x6D, 0xFD, 0xD0, 0x11,
                                      0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
        iid = (ctypes.c_ubyte * 16)(0x42, 0xF3, 0xFD, 0x56, 0x6D, 0xFD, 0xD0, 0x11,
                                    0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
        ole32.CoInitialize(None)
        taskbar = ctypes.c_void_p()
        if ole32.CoCreateInstance(clsid, None, CLSCTX_INPROC_SERVER, iid, ctypes.byref(taskbar)) != 0 or not taskbar:
            ole32.CoUninitialize()
            return
        vtbl = ctypes.c_void_p.from_address(taskbar.value).value
        if not vtbl:
            ole32.CoUninitialize()
            return
        slot = ctypes.sizeof(ctypes.c_void_p)
        hr_init = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(
            ctypes.c_void_p.from_address(vtbl.value + 3 * slot).value)
        del_tab = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
            ctypes.c_void_p.from_address(vtbl.value + 5 * slot).value)
        release = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(
            ctypes.c_void_p.from_address(vtbl.value + 2 * slot).value)
        hr_init(taskbar)
        del_tab(taskbar, hwnd)
        release(taskbar)
        ole32.CoUninitialize()
    except Exception:  # noqa: BLE001
        pass


# ---------- v1.0.788（P2-9）：唤窗热键组合键可配（ui.toggle_hotkey） ----------
# 口径：字符串 "Ctrl+Alt+Space"（= v1.0.373 起硬编码值，磁盘缺键即此默认，
# 存量用户行为不变）；空串 = 显式关闭（**存值不删键**——删键会回落默认"开"）；
# 只接受 Ctrl+Alt（可选 +Shift）+ 单键，单键 = A-Z / 0-9 / Space / F1-F12。
# Ctrl+Alt+S（停止任务）固定内置，不可配。
_HK_MODS = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL,
            "ALT": MOD_ALT, "SHIFT": MOD_SHIFT}
_HK_EXTRA_VK = {"SPACE": VK_SPACE}


def parse_hotkey(spec):
    """组合键字符串 → (mods, vk)；非法/空/非字符串 → None。修饰键顺序不限。"""
    if not isinstance(spec, str):
        return None
    # 段间空格宽容（"Ctrl + Alt + A" 与 "Ctrl+Alt+A" 同义——用户手输最常见形态）
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if len(parts) < 2 or len(parts) > 4:   # 2~3 个修饰键 + 1 个主键
        return None
    mods, keys = 0, []
    for p in parts:
        if p in _HK_MODS:
            mods |= _HK_MODS[p]
        else:
            keys.append(p)
    # Ctrl+Alt 双必需（纯 Shift 组合会被系统级快捷键大量冲突，不做）
    if (mods & MOD_CONTROL) != MOD_CONTROL or (mods & MOD_ALT) != MOD_ALT:
        return None
    if len(keys) != 1:
        return None
    k = keys[0]
    vk = _HK_EXTRA_VK.get(k)
    if vk is None and len(k) == 1 and ("A" <= k <= "Z" or "0" <= k <= "9"):
        vk = ord(k)
    if vk is None and len(k) == 2 and k[0] == "F" and k[1].isdigit() and 1 <= int(k[1]) <= 12:
        vk = 0x70 + int(k[1]) - 1   # VK_F1..VK_F12
    if vk is None:
        return None
    return (mods, vk)


_toggle_reg = {"hwnd": None, "vk": None}   # 当前已注册的唤窗热键（变更时先卸旧再注册）


def _current_toggle_spec():
    """读当前唤窗热键配置（磁盘缺键/坏类型 → 默认 Ctrl+Alt+Space；空串=关闭）。"""
    try:
        from core.config_store import load_config
        ui = load_config().get("ui")
        v = ui.get("toggle_hotkey") if isinstance(ui, dict) else None
        return v if isinstance(v, str) else "Ctrl+Alt+Space"
    except Exception:  # noqa: BLE001
        return "Ctrl+Alt+Space"


def _register_toggle(u, hwnd):
    """按当前配置注册（重注册）唤窗热键，返回是否生效。
    变更/关闭 = 先 UnregisterHotKey 旧组合再注册新的（ID 相同但键码变化时，
    同 ID 覆盖只换 handler 不换键码，必须先卸旧键）。重复调用安全
    （install 重挂路径会再次进入，卸-注幂等）。"""
    try:
        _toggle_reg["hwnd"] = hwnd
        spec = _current_toggle_spec()
        parsed = parse_hotkey(spec) if spec.strip() else None
        if _toggle_reg["vk"] is not None:
            try:
                u.UnregisterHotKey(ctypes.c_void_p(hwnd), _HK_TOGGLE)
            except Exception:  # noqa: BLE001
                pass   # 卸旧失败不挡注册：RegisterHotKey 同 ID 会覆盖
            _toggle_reg["vk"] = None
        if not parsed:
            return True   # 显式关闭 = 目标状态达成（日志由调用方记）
        ok = u.RegisterHotKey(ctypes.c_void_p(hwnd), _HK_TOGGLE, parsed[0], parsed[1])
        if ok:
            _toggle_reg["vk"] = parsed[1]
        return bool(ok)
    except Exception as e:  # noqa: BLE001
        try:
            from core_utils import log_event
            log_event(f"[hotkey] 唤窗热键注册异常（其余功能正常）：{e}")
        except Exception:  # noqa: BLE001
            pass   # silent-ok: 留痕自身失败不放大——注册结果按失败上抛
        return False


def apply_toggle_hotkey_change():
    """v1.0.788（P2-9）：设置保存后即时生效——卸旧键+注册新键，无需重启/重挂。
    主窗尚未挂钩（headless/测试）时 no-op 返回 False。"""
    h = _toggle_reg.get("hwnd")
    if not h:
        return False
    u = _tray_user32()
    ok = _register_toggle(u, h)
    try:
        from core_utils import log_event
        spec = _current_toggle_spec()
        log_event("[hotkey] 全局热键（唤起/隐藏）已按新配置重注册：%s" % (spec or "（已关闭）"))
    except Exception:  # noqa: BLE001
        pass   # silent-ok: 留痕失败不影响重注册结果
    return ok


def register_hotkeys(hwnd, on_stop):
    """注册全局热键：唤起/隐藏主窗口（组合键可配 ui.toggle_hotkey，默认 Ctrl+Alt+Space）
    + Ctrl+Alt+S 停止所有任务（固定内置）。
    v1.0.660（审查修复 F14）：注册失败（ok 为 0，通常是被别的软件抢占了
    组合键）此前只 return False、无任何日志——用户抱怨"快捷键没反应"时无从查证。
    现在按本模块既有写法（局部 from core_utils import log_event，见 :631/:773）
    记录失败原因与成功项。
    v1.0.788（P2-9）：唤窗组合键改读配置（_register_toggle），Ctrl+Alt+S 不变。
    """
    try:
        u = _tray_user32()
        _on_hotkey_cb["stop"] = on_stop
        ok_tog = _register_toggle(u, hwnd)
        ok_stop = u.RegisterHotKey(hwnd, _HK_STOP, MOD_CONTROL | MOD_ALT, ord("S"))
        if ok_tog and ok_stop:
            try:
                from core_utils import log_event
                log_event("[hotkey] 全局热键已注册：%s（唤起/隐藏）、Ctrl+Alt+S（停止任务）"
                          % (_current_toggle_spec() or "（唤起/隐藏已关闭）"))
            except Exception:  # noqa: BLE001
                pass
            return True
        # 失败：逐条记录是哪一个没抢到
        failed = []
        if not ok_tog:
            failed.append("唤起/隐藏（配置 %r）" % _current_toggle_spec())
        if not ok_stop:
            failed.append("Ctrl+Alt+S（停止任务）")
        try:
            from core_utils import log_event
            log_event("[hotkey] 全局热键注册失败：" + "、".join(failed)
                      + "——多半是被其它软件占用了同一组合键；"
                      + "本软件其余功能不受影响，可在「设置→界面偏好」里改组合键或清空关闭")
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception as e:  # noqa: BLE001
        try:
            from core_utils import log_event
            log_event(f"[hotkey] 全局热键注册异常（快捷键不可用，其余功能正常）：{e}")
        except Exception:  # noqa: BLE001
            pass
        return False