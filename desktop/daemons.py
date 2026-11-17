# -*- coding: utf-8 -*-
"""desktop/daemons.py — 后台件启动函数（10 件）。

v1.0.661（「全做」审查）从 backend.py 原样迁出：backend 是 PyInstaller 单文件
冻结入口，__main__ 启动编排与模块级 respawn 标志核销必须留在 backend（二次 import
会重执行模块级代码），但这 10 个 `_start_*_daemon` 是纯函数（docstring + 函数内
局部 import + 返回线程/句柄，无模块级状态），可安全外移。
迁出原则与 desktop/shell.py（v1.0.648）一致：代码零改动，backend.py 原位
re-export 同名符号，所有既有调用方（backend 的 __main__ 编排、tests）无需感知。

# 每个函数只负责"启动自己那一个件"，异常由 core.daemon.start_all() 统一隔离
# （单个件失败只记它的 error，不影响其他件——与原逐个 try/except 的行为一致）。
# 抽成函数而不是内联 lambda：让 import 失败（模块缺失/循环导入）也走同一条隔离路径，
# 且读代码时一眼知道每个件是干什么的。
"""
import threading

import wecom_bridge as wb
from core.config_store import load_config


def _start_scheduler_daemon():
    """v1.0.313：定时自动化调度线程（30s tick；到点任务直接走 agent_loop，结果落产物库）。"""
    from services import scheduler as _sched
    return _sched.start_daemon()


def _start_self_repair_daemon():
    """v1.0.348：每日体检线程（开关 feature_flags.self_repair_daily；距上次 ≥24h
    且新增错误达阈值才真正调用 LLM，平时静默空转）。"""
    from services import self_repair as _sr
    th = threading.Thread(target=_sr.daily_loop, daemon=True, name="self-repair")
    th.start()
    return th


def _start_window_tucker_daemon():
    """v1.0.274：新学习窗口自动最小化——agent 点开的课程/视频/考试独立窗口
    不再弹到用户工作面上方。仅处理 zhi-niao 新 target，存量窗口与用户 OA 窗口不动。"""
    from agent_tools import _cdp_port as _cdp_port_tucker
    return wb.start_window_tucker(_cdp_port_tucker(load_config()))


def _start_save_dialog_daemon():
    """v1.0.413：另存为对话框自动应答；v1.0.604 默认关闭（见设置页开关说明）。"""
    from desktop import save_dialog_auto
    _flags = (load_config().get("feature_flags") or {})
    return save_dialog_auto.start_daemon(enabled=bool(_flags.get("auto_save_dialogs", False)))


def _start_ocr_selfcheck_daemon():
    """v1.0.608：开机后台自检 OCR「真能不能识别」（延迟 20 秒避开启动高峰）。"""
    from core.ocr import ocr_selfcheck_async
    return ocr_selfcheck_async(delay=20.0)


def _start_ocr_update_check_daemon():
    """v1.0.611：预热「检查更新」结果（镜像往返实测 34.6 秒，进后台不挡界面）。"""
    from core.ocr import ocr_update_check_async
    return ocr_update_check_async(delay=35.0)


def _start_kb_ops_daemon():
    """v1.0.301：知识库后台维护（60s tick：空闲整理 INDEX+查重 + 每日 AI 消化）。"""
    from services import knowledge_ops
    return knowledge_ops.keeper.start()


def _start_daily_digest_daemon():
    """v1.0.523-3：每日工作小结 daemon（默认 18:30，静默生成 产物库/日报/）。"""
    from services import daily_digest
    return daily_digest.start_daemon()


def _start_todo_watch_daemon():
    """v1.0.523-4：待办变更监视线程（默认关，config todo_watch.enabled 开启）。"""
    from services import todo_watch
    return todo_watch.start_daemon()


def _start_wecom_archive_daemon():
    """v1.0.477：企微聊天文件静默分析（明文附件/企业网盘 → 知识库/企微文件/）。"""
    from services import wecom_archive
    return wecom_archive.start_archive_daemon()
