"""routes/security — 安全中心（v1.0.320）：配置/审计/审批令牌。蓝图 url_prefix=/api。"""

import json

from flask import Blueprint, request, jsonify

from core.config_store import load_config, update_config, _save_config_safe  # noqa: F401（_save_config_safe 兼容旧导入）
# v1.0.660（B7）：只读配置快照（只读路径用，省掉每次深拷贝整份 config）
from core.config import load_config_view
from core.logging import log_event
import security_ops

bp = Blueprint("security", __name__, url_prefix="/api")



@bp.route("/security/config", methods=["GET"])
def api_sec_config():
    # v1.0.660（B7）：只读路径 → 只读快照
    cfg = load_config_view()
    sec = security_ops.sec_cfg(cfg)
    # v1.0.660（审查修复 L4）：把密钥保护强度一并回传——非 Windows 平台或 DPAPI
    # 失败时敏感字段会降级成 base64 弱保护，原实现只有一行日志、设置页无从提示，
    # 用户以为密钥已加密（实测凭证可被 base64 直接还原）。前端据此显示"未加密"警示。
    from core.secrets import encryption_status
    return jsonify({"ok": True, "security": sec,
                    "audit": security_ops.audit_read(20),
                    "audit_path": security_ops.audit_jsonl(),
                    "encryption": encryption_status()})


@bp.route("/security/config", methods=["POST"])
def api_sec_config_post():
    data = request.get_json(force=True, silent=True) or {}
    sec_in = data.get("security")
    if not isinstance(sec_in, dict):
        return jsonify({"ok": False, "error": "缺少 security 对象"}), 400
    # v1.0.555（P0-10）：输入校验纯函数化（不依赖 cfg）→ 通过后进 update_config
    # 锁内读-改-写事务。原 load→改→save 无整体锁：两个并发写（或一个慢写与一个
    # 用户写交叉）即后写覆盖先写——安全策略/模型/连接器凭据静默丢失
    # （config.json 是凭据与安全策略的唯一事实源，此类丢失属于数据事故）。
    if "command_mode" in sec_in and sec_in["command_mode"] not in ("block", "ask", "pass"):
        return jsonify({"ok": False, "error": "command_mode 只能是 block/ask/pass"}), 400
    # v1.0.590 权限级别（询问范围轴）：必须校验后落盘——本路由按白名单逐键写入，
    # 漏加就会「保存成功但设置被静默丢弃」（v1.0.555 P0-10 同型坑）
    if "permission_level" in sec_in and sec_in["permission_level"] not in security_ops.PERMISSION_LEVELS:
        return jsonify({"ok": False, "error": "permission_level 只能是 ask_every/auto/full"}), 400
    for k in ("cmd_blacklist", "cmd_whitelist", "protected_paths"):
        if k in sec_in:
            v = sec_in[k]
            if isinstance(v, str):
                v = [l.strip() for l in v.splitlines() if l.strip()]
            if not isinstance(v, list):
                return jsonify({"ok": False, "error": f"{k} 应为字符串数组"}), 400
            # v1.0.616（审查 P1）：黑白名单是**正则**，但命中处写的是
            # `try: re.compile(pat) except re.error: continue`（security_ops.py:346/372）
            # —— 一字写错的规则从不生效、也从不报错，安全策略呈现"保存成功"假象
            # （与 kb_auto 写错键名同型）。保存前逐条试编译，指出第几行。
            if k != "protected_paths":
                import re as _re
                for _i, _p in enumerate(v, 1):
                    try:
                        _re.compile(str(_p), _re.I)
                    except _re.error as e:
                        return jsonify({"ok": False,
                                        "error": f"{k} 第 {_i} 行不是合法正则：{str(_p)[:60]}（{e}）"}), 400
    if "delete_confirm_limit" in sec_in:
        try:
            int(sec_in["delete_confirm_limit"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "删除审批阈值应为数字"}), 400
    if "backup_limit_mb" in sec_in:
        try:
            int(sec_in["backup_limit_mb"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "备份上限应为数字（MB）"}), 400
    if "backup_retention_days" in sec_in:
        try:
            int(sec_in["backup_retention_days"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "备份保留天数应为数字（0=关闭）"}), 400

    def _mutate(cfg):
        sec = security_ops.sec_cfg(cfg)
        if "command_mode" in sec_in:
            sec["command_mode"] = sec_in["command_mode"]
        if "permission_level" in sec_in:
            sec["permission_level"] = sec_in["permission_level"]
        for k in ("cmd_blacklist", "cmd_whitelist", "protected_paths"):
            if k in sec_in:
                v = sec_in[k]
                if isinstance(v, str):
                    v = [l.strip() for l in v.splitlines() if l.strip()]
                sec[k] = [str(x)[:200] for x in v[:50]]
        if "delete_confirm_limit" in sec_in:
            sec["delete_confirm_limit"] = max(1, min(100, int(sec_in["delete_confirm_limit"])))
        if "auto_backup" in sec_in:
            sec["auto_backup"] = bool(sec_in["auto_backup"])
        if "backup_limit_mb" in sec_in:
            sec["backup_limit_mb"] = max(50, min(10240, int(sec_in["backup_limit_mb"])))
        if "backup_retention_days" in sec_in:
            sec["backup_retention_days"] = max(0, min(3650, int(sec_in["backup_retention_days"])))
        cfg["security"] = sec
        return True

    if not update_config(_mutate):
        return jsonify({"ok": False, "error": "保存失败：配置写入异常（详见日志）"}), 500
    log_event("[security] 配置已更新（update_config 事务）")
    return jsonify({"ok": True})


@bp.route("/security/grant", methods=["POST"])
def api_security_grant():
    """审批批准：发放一次性令牌（agent 原样重试时安全层放行）。
    v1.0.652（审计 P0-1）：必须出示弹窗 SSE 事件里的 nonce——hash 是可自算的，
    单凭 hash 发令牌等于允许模型/本机进程自铸通行证绕过审批闸。"""
    data = request.get_json(force=True, silent=True) or {}
    h = str(data.get("hash") or "").strip()
    nonce = str(data.get("nonce") or "").strip()
    if not h:
        return jsonify({"ok": False, "error": "缺少 hash"}), 400
    if not security_ops.grant(h, nonce):
        return jsonify({"ok": False,
                        "error": "审批凭据无效或已过期（需从审批弹窗「批准」按钮提交）"}), 400
    log_event(f"[security] 用户批准操作 {h}")
    return jsonify({"ok": True})


@bp.route("/security/audit/clear", methods=["POST"])
def api_sec_audit_clear():
    security_ops.audit_clear()
    return jsonify({"ok": True})


@bp.route("/security/retention/run", methods=["POST"])
def api_sec_retention_run():
    """v1.0.788（P2-8）：立即执行一次保留归档（不受每日节流管；0=关闭时为空报告）。"""
    from core.paths import workspace_root
    from services import retention
    days = int((security_ops.sec_cfg(load_config()) or {}).get("backup_retention_days") or 0)
    rep = retention.archive_expired(workspace_root(), days)
    log_event("[retention] 手动归档：超期目录 %s，跳过冲突 %d 个"
              % (rep["moved"] or "无", len(rep["skipped"])))
    return jsonify({"ok": True, "days": days, **rep})


# ---------- 后台任务审批待办队列（v1.0.660 功能 B1） ----------
# 后台会话（定时任务/跑课）没有弹窗可点，命中 ALWAYS_ASK 的不可撤销写操作会被直接拒绝。
# 原实现下这条拒绝只在流里闪一下就消失，用户事后完全不知道发生过什么。
# 这里把被拒的操作收进内存队列，用户可以逐条查看/批准/驳回。


@bp.route("/security/pending-approvals", methods=["GET"])
def api_sec_pending_approvals():
    """列出待审批队列（只含脱敏摘要与参数指纹，不含命令原文）。"""
    from services import approval_queue
    items = approval_queue.list_pending()
    return jsonify({"ok": True, "count": len(items), "items": items})


@bp.route("/security/pending-approvals/<item_id>/approve", methods=["POST"])
def api_sec_pending_approve(item_id):
    """批准一条待审批。

    批准时签发**一次性放行令牌**（复用 security_ops 的 args_hash + grant 机制）——
    用户重跑同一个操作时闸门直接放行，不必再弹一次窗。令牌是一次性的、10 分钟过期。
    """
    from services import approval_queue
    rec = approval_queue.resolve(item_id, True)
    if not rec:
        return jsonify({"ok": False, "error": "该待审批项不存在或已处理"}), 404
    log_event(f"[security] 用户批准后台待审批项 {item_id}（工具={rec.get('tool')}，"
              f"已签发一次性放行令牌={bool(rec.get('granted'))}）")
    return jsonify({"ok": True, "item": rec, "granted": bool(rec.get("granted"))})


@bp.route("/security/pending-approvals/<item_id>/reject", methods=["POST"])
def api_sec_pending_reject(item_id):
    """驳回一条待审批（记录审计，队列出队）。"""
    from services import approval_queue
    rec = approval_queue.resolve(item_id, False)
    if not rec:
        return jsonify({"ok": False, "error": "该待审批项不存在或已处理"}), 404
    log_event(f"[security] 用户驳回后台待审批项 {item_id}（工具={rec.get('tool')}）")
    return jsonify({"ok": True, "item": rec})


# ---------- 会话级"记住该命令"免审批（v1.0.660 功能 B2） ----------
# 现状：审批令牌是一次性 args hash，同一条良性命令每轮都要问一次。
# 这里让用户能对**当前会话**记住某条命令，之后同命令直接放行。
# 存储只在内存（不落盘）+ 有过期时间（默认 30 分钟）+ 只在本会话内有效。

@bp.route("/security/session-allow", methods=["GET"])
def api_sec_session_allow_list():
    """列出当前会话记住的免审批规则。"""
    pane_id = str(request.args.get("pane_id") or "")
    from agent.session import list_session_allow
    items = list_session_allow(pane_id)
    return jsonify({"ok": True, "count": len(items), "items": items})


@bp.route("/security/session-allow/all", methods=["GET"])
def api_sec_session_allow_all():
    """v1.0.667（v1.0.666 审查 P0-2）：列出**全部会话**的免审批规则（安全中心用）。

    原 /session-allow GET 只能按单个 pane_id 查——用户不知道规则落在哪个会话下，
    「记住了哪些操作、能不能撤」事实上不可见。这里聚合所有会话的未过期规则，
    每条带 pane_id，供前端逐条清除或一键全清。
    """
    import time as _time
    from agent.session import _SESSION_ALLOW, _allow_lock
    now = _time.time()
    items = []
    with _allow_lock:
        for pid, rules in _SESSION_ALLOW.items():
            for rid, r in rules.items():
                if r["expire"] > now:
                    items.append({"pane_id": pid, "id": rid, "prefix": r["prefix"],
                                  "tool": r["tool"], "mode": r["mode"],
                                  "expire": r["expire"], "created": r.get("created", 0)})
    items.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return jsonify({"ok": True, "count": len(items), "items": items})


@bp.route("/security/session-allow/all", methods=["DELETE"])
def api_sec_session_allow_clear_all():
    """v1.0.667：一键清掉**全部会话**的免审批规则（安全中心「全清」用）。"""
    from agent.session import _SESSION_ALLOW, _allow_lock
    with _allow_lock:
        n = sum(len(v) for v in _SESSION_ALLOW.values())
        _SESSION_ALLOW.clear()
    log_event(f"[security] 用户清除全部会话免审批规则 {n} 条（安全中心全清）")
    return jsonify({"ok": True, "cleared": n})


@bp.route("/security/session-allow", methods=["POST"])
def api_sec_session_allow_add():
    """记住一条命令：本会话内该命令（或命中该正则）不再逐次询问。

    body: {pane_id, prefix|pattern, tool?, mode?: "prefix"|"regex", ttl?}
    """
    data = request.get_json(force=True, silent=True) or {}
    pane_id = str(data.get("pane_id") or "").strip()
    prefix = str(data.get("prefix") or data.get("pattern") or "").strip()
    tool = str(data.get("tool") or "").strip() or None
    mode = str(data.get("mode") or "prefix").strip().lower()
    try:
        ttl = int(data.get("ttl")) if data.get("ttl") is not None else None
    except (TypeError, ValueError):
        ttl = None
    if not pane_id:
        return jsonify({"ok": False, "error": "缺少 pane_id"}), 400
    if not prefix:
        return jsonify({"ok": False, "error": "缺少 prefix（要记住的命令文本）"}), 400
    from agent.session import add_session_allow
    rid = add_session_allow(pane_id, prefix, tool=tool, ttl=ttl, mode=mode)
    if not rid:
        return jsonify({"ok": False, "error": "规则无效（正则编译失败或参数缺失）"}), 400
    log_event(f"[security] 用户记住会话免审批规则 {rid}（pane={pane_id}，mode={mode}）")
    return jsonify({"ok": True, "id": rid})


@bp.route("/security/session-allow", methods=["DELETE"])
def api_sec_session_allow_clear():
    """清掉当前会话的免审批规则（带 rule_id 则只清那一条）。"""
    data = request.get_json(force=True, silent=True) or {}
    pane_id = str(data.get("pane_id") or request.args.get("pane_id") or "").strip()
    rule_id = str(data.get("rule_id") or request.args.get("rule_id") or "").strip() or None
    if not pane_id:
        return jsonify({"ok": False, "error": "缺少 pane_id"}), 400
    from agent.session import clear_session_allow
    n = clear_session_allow(pane_id, rule_id)
    log_event(f"[security] 用户清除会话免审批规则 {n} 条（pane={pane_id}）")
    return jsonify({"ok": True, "cleared": n})


@bp.route("/security/audit/export", methods=["POST"])
def api_sec_audit_export():
    p = security_ops.audit_export()
    return jsonify({"ok": True, "path": p})

