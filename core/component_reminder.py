# -*- coding: utf-8 -*-
"""core.component_reminder — v1.0.704 组件提醒：需求事件 + 提醒开关。

两类提醒（都受 component_reminder.enabled 总开关控制，「运行组件」页可改）：
  ① 首次使用软件 → 提醒体检并安装组件（first_run_seen 只提示一次）；
  ② 有任务需要某组件而组件未安装 → 提醒用户安装。

需求事件的单一入口是 record_demand()：各组件的「任务调用点发现未安装」分支
（OCR 自动下载触发 / 语音组件加载失败 / dsh 委托入口）记一条；前端经
GET /api/components/reminder 取走展示。事件只存内存（软件重启即清空——
缺组件的事实还躺在「运行组件」体检里，重启后首跑检查/下次任务会再提醒，
所以不落盘不丢信息）。
"""
import threading
import time

from core.logging import log_event

_lock = threading.Lock()
_demands = []          # [{id, key, name, msg, ts, seen}]，新→旧
_DEMAND_CAP = 50       # 上限：界面只摆最近的，早先的被新事件挤出
_DEMAND_DEDUP_S = 900   # 同一 (key,msg) 未读事件 15 分钟内的重复触发不重复记账


def record_demand(key, name, msg, dedup_s=_DEMAND_DEDUP_S):
    """记一条「任务需要组件 key 但未安装」需求事件。

    返回 True=已记账，False=被去重挡掉（同 key+msg 的未读事件还在冷却期内）。
    调用方必须容忍本函数抛异常（调用点用 contextlib.suppress 包住）——
    提醒是增强，记账失败绝不能挡主任务。
    """
    now = time.time()
    with _lock:
        for d in _demands:
            if d["key"] == key and d["msg"] == msg and not d["seen"] and now - d["ts"] < dedup_s:
                return False
        _demands.insert(0, {"id": now, "key": key, "name": name, "msg": msg,
                            "ts": now, "seen": False})
        del _demands[_DEMAND_CAP:]
    log_event(f"[components] 组件需求事件：{name}（{key}）——{msg}")
    return True


def pending_demands():
    """未读需求事件（新→旧）。GET 端点展示用。"""
    with _lock:
        return [dict(d) for d in _demands if not d["seen"]]


def mark_all_seen():
    """用户点「关闭」：全部标记已读（不改总开关，下次缺组件还会提醒）。"""
    with _lock:
        for d in _demands:
            d["seen"] = True


def _patch_section(patch):
    """合并写 component_reminder 段并落盘。必须走 load→改→save 的正规路径——
    顶层段已在 DEFAULT_CONFIG 声明，save 不会抹段。"""
    from core.config_store import load_config, save_config
    cfg = load_config() or {}
    cr = dict(cfg.get("component_reminder") or {})
    cr.update(patch)
    cfg["component_reminder"] = cr
    save_config(cfg)
    return cr


def get_state():
    """返回 {enabled, first_run_seen}（缺省按 DEFAULT_CONFIG：提醒开、未提示过）。"""
    from core.config_store import load_config
    cr = (load_config() or {}).get("component_reminder") or {}
    return {"enabled": bool(cr.get("enabled", True)),
            "first_run_seen": bool(cr.get("first_run_seen", False))}


def set_enabled(on):
    """总开关：True=每次遇到上述情况提醒 / False=不再提醒。"""
    _patch_section({"enabled": bool(on)})


def mark_first_seen():
    """首跑提醒已弹出/已关闭——之后不再弹首跑体检弹窗（任务缺组件提醒不受影响）。"""
    _patch_section({"first_run_seen": True})


def reset():
    """测试用：清空内存事件。"""
    with _lock:
        _demands.clear()
