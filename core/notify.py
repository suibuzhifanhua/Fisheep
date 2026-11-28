# -*- coding: utf-8 -*-
"""core.notify — Windows 原生 Toast（自 core_utils 拆分 v1.0.402）

v1.0.498：通知中心文件簇（通知.json + push/list/mark）整簇删除——写入方唯一
（scheduler 定时任务完成），读取方（list/mark + 前端 UI）自 v1.0.402 起从未存在，
纯堆垃圾数据；送达通道收敛为 native_toast 单通道。
"""
import contextlib
import os
import sys
from core.logging import log_event  # noqa


# ---------------- v1.0.373（用户需求5）Windows 原生 Toast 推送 ----------------
# v1.0.556（用户截图实锤）：原 Toast 借 PowerShell 的 AUMID 发送，通知横幅来源
# 显示成「Windows PowerShell」而非软件名。注册自有 AppUserModelId（HKCU 注册表，
# 免管理员）后来源=软件当前显示名；冻结态顺带把 exe 图标挂到横幅上。
# v1.0.680（改名）：AUMID 改名。Windows 用 AUMID 把通知与图标/快捷方式关联——
# 改名后新 AUMID 需要重新注册（否则横幅不显示图标），老 AUMID 的注册项
# 由 _sweep_legacy_aumids 清扫（通知中心来源名跟着 DisplayName 走，留着就
# 一直显示老品牌名——v1.0.703 用户报障的根因之一）。
from core import branding as _branding
_AUMID = _branding.AUMID
_AUMID_STATE = {"done": False, "ok": False}


def _sweep_legacy_aumids(winreg):
    """v1.0.703（用户报障：通知来源还是老品牌名）：清扫历代 AUMID 注册项。
    通知中心按 AUMID→DisplayName 显示来源名，老代键残留会让历史通知
    永远顶着老名字。护栏：只删 DisplayName 命中本软件品牌链的键——同名键
    若被别的应用占用（DisplayName 不在链内）一律不动。"""
    for name in _branding.ALL_AUMIDS + (_branding.LEGACY_APP_ID,):
        if name == _AUMID:
            continue
        key_path = r"SOFTWARE\Classes\AppUserModelId\{}".format(name)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                 winreg.KEY_READ) as k:
                disp, _t = winreg.QueryValueEx(k, "DisplayName")
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001
            log_event(f"[notify] AUMID 清扫探测失败 {name}：{e}")
            continue
        if str(disp) not in _branding.ALL_DISPLAY_TITLES:
            log_event(f"[notify] 保留 AUMID {name}（DisplayName={disp}，非本软件品牌链）")
            continue
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            log_event(f"[notify] 已清扫老 AUMID 注册项：{name}（DisplayName={disp}）")
        except Exception as e:  # noqa: BLE001
            log_event(f"[notify] 清扫 AUMID {name} 失败：{e}")


def _ensure_aumid():
    """注册自有 AUMID（进程内只写一次）。返回是否成功；失败回退 PowerShell AUMID。
    v1.0.703：DisplayName 取品牌常量（原硬编码老显示名，通知来源长期顶老名）；
    注册成功后顺带清扫老代 AUMID 注册项。"""
    if _AUMID_STATE["done"]:
        return _AUMID_STATE["ok"]
    _AUMID_STATE["done"] = True
    if os.name != "nt":
        return False
    try:
        import winreg
        key_path = r"SOFTWARE\Classes\AppUserModelId\{}".format(_AUMID)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, _branding.APP_TITLE)
            if getattr(sys, "frozen", False):   # 开发态是 python.exe，图标无意义
                try:
                    exe = sys.executable or ""
                    if exe.lower().endswith(".exe"):
                        winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, exe)
                except Exception:  # noqa: BLE001
                    pass
        _AUMID_STATE["ok"] = True
    except Exception as e:  # noqa: BLE001
        try:
            log_event(f"[notify] 自有 AUMID 注册失败（Toast 来源将显示 PowerShell）：{e}")
        except Exception:  # noqa: BLE001
            pass
        return _AUMID_STATE["ok"]
    else:
        _sweep_legacy_aumids(winreg)   # 清扫失败不影响注册结果（内部逐键兜底）
    return _AUMID_STATE["ok"]


def native_toast(title, msg):
    """Windows 10/11 系统原生通知（右下角 Toast）。经 PowerShell 加载 WinRT，
    无第三方依赖；失败静默（托盘气泡 tray_notify 是兜底通道）。异步不阻塞调用方。
    v1.0.556：通知来源优先用自有 AUMID（横幅显示软件显示名，原显示
    「Windows PowerShell」）；未注册成功时回退旧通道。
    v1.0.703：发送前落一行日志——此前 toast 无任何留痕，通知中心冒出内容时
    查不到谁发的、何时发的（排障盲区）。"""
    try:
        import subprocess
        if os.name != "nt":
            return False
        esc = lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
        t, m = esc(title)[:60], esc(msg)[:180]
        notifier_id = (_AUMID if _ensure_aumid()
                       else "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe")
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] | Out-Null; "
            "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$t = $x.GetElementsByTagName('text'); "
            f"$t.Item(0).AppendChild($x.CreateTextNode('{t}')) | Out-Null; "
            f"$t.Item(1).AppendChild($x.CreateTextNode('{m}')) | Out-Null; "
            "$n = [Windows.UI.Notifications.ToastNotification]::new($x); "
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            f"'{notifier_id}').Show($n);"
        )
        with contextlib.suppress(Exception):   # 留痕失败不挡发送（裸 except-pass 棘轮）
            log_event(f"[notify] toast「{t}」：{m}")   # v1.0.703：发送前留痕
        # v1.0.533：提权链下 -WindowStyle Hidden 不可靠（用户实测控制台窗口可见）——
        # 叠加 startupinfo SW_HIDE 双保险，控制台窗口彻底不可见。
        si = subprocess.STARTUPINFO()
        try:
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0   # SW_HIDE
        except Exception:  # noqa: BLE001
            pass
        subprocess.Popen(["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         startupinfo=si)
        return True
    except Exception as e:  # noqa: BLE001
        try:
            log_event(f"[notify] 原生 Toast 发送失败（忽略）：{e}")
        except Exception:  # noqa: BLE001
            pass
        return False
