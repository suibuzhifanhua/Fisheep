# -*- coding: utf-8 -*-
"""desktop/shell.py — 桌面壳层：托盘还原 / X 关闭拦截 / 全局热键 / 原生拖拽桥。

v1.0.648 从 backend.py 原样迁出（backend 只留 app 骨架与启动器；这些函数全部是
"窗口创建之后才用"的壳层能力，跟着 close_guard/tray 一起住在 desktop/ 包里）。
迁出原则：代码零改动，backend.py 顶部 re-export 同名符号，所有既有调用方
（backend 的 __main__ 启动块、tests/contract/test_frontend_render.py）无需感知。
唯一例外 _tray_loop：它引用 backend._exit_app，反向依赖入口模块，留在 backend。
"""
import json
import threading

from core.logging import log_event
from desktop import close_guard


# 托盘“打开程序”要还原的主窗口句柄/回调（由 close_guard 安装后回填）
_TRAY_SHOW = {"hwnd": None}


def _tray_show_fn():
    """返回还原主窗口的回调（句柄尚未就绪时返回 None，托盘侧会退回浏览器）。"""
    h = _TRAY_SHOW.get("hwnd")
    return (lambda: close_guard.show_window(h)) if h else None


def _tray_toggle_fn():
    """v1.0.788（P2-9）：托盘「显示/隐藏窗口」——与全局热键唤窗同语义。
    返回 True=已执行切换；False=无主窗句柄（托盘侧退回浏览器打开）。
    隐藏走 ShowWindow(SW_HIDE)（与热键路径一致，不藏任务栏——「最小化到托盘」
    由关闭弹窗那条路负责，这里只做无脑切换）。"""
    h = _TRAY_SHOW.get("hwnd")
    if not h:
        return False
    try:
        import ctypes as _ct
        u = _ct.windll.user32
        u.IsWindowVisible.argtypes = [_ct.c_void_p]
        u.IsWindowVisible.restype = _ct.c_int
        if u.IsWindowVisible(_ct.c_void_p(h)):
            u.ShowWindow(_ct.c_void_p(h), 0)   # SW_HIDE
        else:
            close_guard.show_window(h)
        return True
    except Exception:  # noqa: BLE001
        return False


def _on_tray_minimize():
    """用户在 X 选择「最小化到托盘」时被调用（UI 线程）：窗口已隐藏，这里仅记录。"""
    try:
        log_event("[guard] 已最小化到托盘，应用后台常驻")
    except Exception as _e:# noqa: BLE001
        log_event(f"[warn] backend.py:312 异常已降级继续: {_e}")  # v1.0.405（H3）：原静默 pass 补日志


def _bind_native_drop(window, _retries=20):
    """v1.0.370/373 拖拽真实路径桥：WebView2 的 JS File 对象没有 path 属性（Electron 私有
    扩展），纯前端拖拽只能读到文件名——此前拖文件夹被降级成「读内容上传复制」。
    pywebview 5+ 的 DOM 事件桥在 Python 侧提供 file['pywebviewFullPath']（完整绝对路径），
    此处注册 document 级 drop 处理，把路径 evaluate_js 推回前端 window.__nativeDrop(paths)，
    前端据此走「只引用不复制」chip 通道（登记只读白名单后 agent 直接分析原路径）。

    ⚠️ 绑定时机：必须经 webview.start(_bind_native_drop, window) 在窗口创建后调用——
    v1.0.372 曾误在 create_window 后/start 前同步调用，evaluate_js 等 loaded 事件
    超时抛 'Main window failed to start'（桥永远没绑上，拖拽一直走上传兜底）。
    DOM 桥未就绪时自动重试（20 次 × 1.5s）。"""
    try:
        from webview.dom import DOMEventHandler

        def _on_drop(e):
            try:
                files = ((e or {}).get("dataTransfer") or {}).get("files") or []
                paths = [f.get("pywebviewFullPath") for f in files if isinstance(f, dict)]
                paths = [p for p in paths if isinstance(p, str) and p.strip()]
                if not paths:
                    return   # 无路径（如拖拽页面元素）→ 前端自行处理
                window.evaluate_js(
                    "window.__nativeDrop && window.__nativeDrop("
                    + json.dumps(paths, ensure_ascii=False) + ");"
                )
            except Exception as ex:  # noqa: BLE001
                log_event(f"[webview] 拖拽路径回推失败：{ex}")

        def _attempt(tries=_retries):
            try:
                window.dom.document.events.drop += DOMEventHandler(_on_drop, True, True)
                window.evaluate_js("window.__pywebviewNativeDrop = true;")
                log_event("[webview] 原生拖拽路径桥已绑定（drop → pywebviewFullPath → 前端 chip）")
            except Exception as ex:  # noqa: BLE001
                if tries > 0:
                    threading.Timer(1.5, _attempt, args=(tries - 1,)).start()
                else:
                    log_event(f"[webview] 拖拽路径桥绑定失败（已重试，拖拽将走上传兜底）：{ex}")

        _attempt()
    except Exception as ex:  # noqa: BLE001
        log_event(f"[webview] 拖拽路径桥初始化失败：{ex}")


def _hotkey_stop_all():
    """全局热键 Ctrl+Alt+S：停止所有进行中的生成（v1.0.373 体验B7）。"""
    try:
        from routes.chat import abort_all_tasks
        abort_all_tasks()
        log_event("[hotkey] Ctrl+Alt+S：已停止所有进行中的任务")
    except Exception as e:  # noqa: BLE001
        log_event(f"[hotkey] 停止任务失败：{e}")


def _install_close_guard(window_title, icon_path=None):
    """后台线程：等主窗口句柄出现后子类化 WNDPROC，安装 X 关闭拦截。"""
    try:
        hwnd = close_guard.install(window_title, on_minimize=_on_tray_minimize,
                                   icon_path=icon_path,
                                   on_hotkey={"stop": _hotkey_stop_all},
                                   saved_action=_saved_close_action,
                                   save_action=_save_close_action)
        if hwnd:
            _TRAY_SHOW["hwnd"] = hwnd
            # v1.0.700：带上句柄——.NET 重建句柄后钩子由 close_guard 守护线程重挂
            # 并另记「主窗句柄已失效」日志，两处句柄一对就能定位拦截失守时刻。
            log_event("[guard] 已安装关闭拦截（挂钩主窗 0x%x｜点 X 可选：最小化到托盘 / 退出；"
                      "勾选「下次不再提示」后按记住的动作执行）" % hwnd)
        else:
            # v1.0.698：措辞不再只怪「窗口句柄」——install 可能死于 _setup_prototypes
            # （那会先记一条带原因的日志）或死于寻窗失败，两种都如实说。
            log_event("[guard] 关闭拦截未安装（点 X 将直接退出）——"
                      "_setup_prototypes 失败见上一条日志，或主窗口句柄两种寻窗法均未命中")
    except Exception as e:  # noqa: BLE001
        log_event(f"[guard] 安装关闭拦截失败：{e}")


def _saved_close_action():
    """v1.0.556：读取已记住的关闭动作（ui.close_action："minimize"/"exit"/None）。
    勾选「下次不再提示」后由 close_guard 在点 X 时调用。"""
    try:
        from core.config_store import load_config
        v = (load_config().get("ui") or {}).get("close_action")
        return v if v in ("minimize", "exit") else None
    except Exception:  # noqa: BLE001
        return None


def _save_close_action(minimize):
    """v1.0.556：勾选「下次不再提示」并选择后持久化动作（update_config 事务）。
    v1.0.664：改回/更改走「设置 → 界面偏好 → 关闭窗口时的行为」（三态，POST /api/config）；
    仍可直接手改 config.json 的 ui.close_action（删除或置 null＝每次询问）。"""
    try:
        from core.config_store import update_config
        _val = "minimize" if minimize else "exit"

        def _mutate(cfg):
            cfg.setdefault("ui", {})["close_action"] = _val
            return True

        update_config(_mutate)
        log_event(f"[guard] 已记住关闭动作：{_val}（点 X 不再弹窗；"
                  "恢复提示请删除 config.json 的 ui.close_action）")
    except Exception as e:  # noqa: BLE001
        log_event(f"[guard] 记住关闭动作失败（下次仍会弹窗）：{e}")
