# -*- coding: utf-8 -*-
"""services.doctor — 一键自诊报告（v1.0.788，P1-5）
=================================================================
出问题时用户要自己翻一堆页面拼状态（管理员状态/组件体检/模型探测/磁盘/
配置…），报告发出来也没法一键复制。doctor 一个 GET 全聚合：
每项独立 try/except（单项失败不拖垮整份报告）、带耗时、统一
{name, ok, detail, ms} 结构，前端渲染成单页报告 + 一键复制文本。

口径纪律：
  · 全部**进程内直调**，不起 HTTP 自请求（/api/ping 之类自己打自己是循环依赖）；
  · 模型探测复用 llm_client.probe_model_reachable（2.5s 预算，零 token 消耗）；
  · 组件探测复用 b_ocr_admin._probe_component（与「运行组件」页同一实现，
    不另写一份口径漂移的探测）。
"""
import os
import platform
import shutil
import sys
import time


def _item(name, fn):
    """跑单项检查：异常统一降级 ok=False + 留痕，绝不抛给调用方。"""
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:   # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(e).__name__, str(e)[:120])
    return {"name": name, "ok": bool(ok), "detail": str(detail)[:300],
            "ms": int((time.time() - t0) * 1000)}


def _chk_runtime():
    frozen = bool(getattr(sys, "frozen", False))
    return True, "Python %s · %s · %s %s" % (
        platform.python_version(), "打包版" if frozen else "开发版",
        platform.system(), platform.release())


def _chk_workspace():
    from core.paths import workspace_root
    r = workspace_root()
    subs = [d for d in ("产出", "台账", "会话", "备份", "系统", "版本")
            if os.path.isdir(os.path.join(r, d))]
    return os.path.isdir(r), "%s · 子目录 %s" % (r, "、".join(subs) or "（尚未创建）")


def _chk_disk():
    from core.paths import workspace_root
    try:
        root = workspace_root()
    except Exception:   # noqa: BLE001
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    du = shutil.disk_usage(root)
    free_gb = du.free / 1024 ** 3
    return free_gb >= 1.0, "总 %.0f GB / 可用 %.1f GB%s" % (
        du.total / 1024 ** 3, free_gb, "（剩余空间偏低！）" if free_gb < 1.0 else "")


def _chk_config():
    import core.config_store as cs
    p = cs.config_path()
    cfg = cs.load_config() or {}
    port = cs.cdp_port()
    if not os.path.isfile(p):
        return True, "%s（尚未创建，走默认值）· CDP 端口 %s" % (p, port)
    return True, "%s · 顶层键 %d · CDP 端口 %s" % (p, len(cfg), port)


def _chk_wecom():
    from core.config_store import cdp_port
    from bridge.cdp_client import cdp_up
    port = cdp_port()
    up = cdp_up(port)
    return up, ("CDP %s 可达（企微可操作）" % port if up
                else "CDP %s 不可达（企微没开调试端口？或企微未启动）" % port)


def _chk_model():
    from core.config_store import load_config
    from llm_client import resolve_model, probe_model_reachable
    m = resolve_model(load_config())
    if not m:
        return False, "未配置默认模型（设置 → 模型管理）"
    r = probe_model_reachable(m)
    name = str(m.get("name") or "?")
    if r.get("state") == "ok":
        return True, "%s 在线（%s）" % (name, r.get("detail") or "")
    return False, "%s 不可用：%s" % (name, r.get("detail") or "未知")


def _chk_components():
    from routes.b_ocr_admin import _probe_component
    keys = ("ocr", "asr", "git", "node", "dsh")
    comps = [_probe_component(k) for k in keys]
    bad = [c["key"] for c in comps if c["status"] in ("missing", "unknown")]
    return (not bad), " / ".join("%s:%s" % (c["key"], c["status"]) for c in comps)


def _chk_version():
    from version import get as vget
    from services import version_store as vs
    cur = vget()
    try:
        n = len(vs._load())
        latest_reg = (vs._load() or [{}])[0].get("version") or "?"
    except Exception:   # noqa: BLE001
        n, latest_reg = 0, "?"
    return True, "当前 v%s · 登记 %d 版（最新登记 v%s，可一键回退）" % (cur, n, latest_reg)


def run():
    """跑全量自诊，返回单页报告（永不抛）。"""
    checks = [
        ("运行环境", _chk_runtime),
        ("工作区", _chk_workspace),
        ("磁盘空间", _chk_disk),
        ("配置文件", _chk_config),
        ("企微 CDP", _chk_wecom),
        ("默认模型", _chk_model),
        ("运行组件", _chk_components),
        ("版本登记", _chk_version),
    ]
    items = [_item(n, f) for n, f in checks]
    bad = [it["name"] for it in items if not it["ok"]]
    return {"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "all_ok": not bad, "bad": bad,
            "total_ms": sum(it["ms"] for it in items),
            "items": items}
