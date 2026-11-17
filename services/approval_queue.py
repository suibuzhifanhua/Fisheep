# -*- coding: utf-8 -*-
"""services.approval_queue — 后台任务审批待办队列（v1.0.660 功能 B1）

====================================================================
背景：工具级 ALWAYS_ASK（不可撤销的真实业务写入）在**后台会话**里没有弹窗可点，
security_ops.gate_write_tool 的 background 分支直接拒绝（返回 bg_deny_result）。
原实现下这条拒绝只在 agent 流里发一条 approval_denied 事件就消失——用户
完全不知道发生过什么。定时任务跑一晚，产出里也没有任何"有 N 条操作需要你确认"
的痕迹（用户只看到"✅ 定时任务完成"）。

本模块提供进程内的待办队列：
  · 被后台会话拒绝的不可撤销写操作 → 入队（add_pending）
  · 用户在安全中心看到队列（list_pending）→ 逐条批准/驳回（resolve）
  · 批准时签发**一次性放行令牌**（复用 security_ops 的 args_hash + grant 机制），
    用户重跑同一操作时闸门直接放行，不必再弹一次窗

设计约束：
  · **纯内存**：不落盘（含命令原文的审批待办不该长期留在磁盘上）
  · **参数脱敏摘要**：只存"这条操作大概要干什么"的摘要 + 参数指纹，
    不存命令原文（命令里常有密码/令牌）
  · **有上限**：最多 100 条，超出丢最旧的（防内存无界增长）
  · **线程安全**：Flask 多线程 + agent 后台线程都会碰它
"""

import os
import threading
import time

# v1.0.660（构建门禁）：静默降级必须留痕——本模块的异常分支都影响"用户看到的
# 审批待办描述"，静默吞掉等于队列里出现无法判断内容的条目。
try:
    from core.logging import log_event as _log
except Exception:  # noqa: BLE001
    import sys as _sys

    def _log(msg):
        _sys.stderr.write("%s\n" % msg)

# 队列容量上限：超出丢最旧的一条（内存不无界增长）
MAX_PENDING = 100

_lock = threading.Lock()
# 待办条目：id → 记录。dict 保序（Python 3.7+），插入序即 FIFO 淘汰依据。
_items = {}
# 计数器（生成短 id）：进程内单调递增
_seq = 0

# v1.0.667（v1.0.666 审查 P0-1）：队列补落盘。原「纯内存」是安全取舍（不落命令原文——
# 条目只存脱敏摘要+指纹，不含原文，与落盘不冲突），但重启即丢意味着夜间被拦的操作
# 用户早上查不到、批准通道事实性断头。现落 产物库/系统/待审批队列.json（系统/ 段
# 不在文件树与 /workspace/file 读取名单里，与 api_token 同级防护）。
# 注意：路径不能用 core.paths.ws_sub("系统")——"系统" 不在 _ALLOWED_SUBS，会被静默
# 回退成「产出」（v1.0.294 起已知行为）。
_PENDING_FILE_REL = os.path.join("系统", "待审批队列.json")


def _pending_file():
    try:
        from core.paths import workspace_root
        return os.path.join(workspace_root(), _PENDING_FILE_REL)
    except Exception:  # noqa: BLE001
        return ""


def _load_persisted():
    """启动时把盘上未处理的待审批捞回内存（只回 pending 且结构完整的条目）。"""
    import json as _json
    from core.files import read_json
    p = _pending_file()
    if not p or not os.path.isfile(p):
        return
    data = read_json(p, None)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return
    for r in items:
        if (isinstance(r, dict) and r.get("id")
                and r.get("status") == "pending" and r.get("created_at")):
            _items[str(r["id"])] = r
    while len(_items) > MAX_PENDING:
        _items.pop(next(iter(_items)), None)
    if _items:
        _log(f"[approval_queue] 恢复 {len(_items)} 条未处理待审批（落盘恢复）")


def _persist_locked():
    """全量落盘（调用方已持 _lock）。写失败只留痕不抛出——队列主功能不因盘故障中断。"""
    import json as _json
    from core.config import atomic_write_json
    p = _pending_file()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        atomic_write_json(p, {"items": list(_items.values())})
    except Exception as e:  # noqa: BLE001
        _log(f"[approval_queue] 落盘失败（队列仍在内存中）：{e}")


def load_persisted():
    """backend 启动钩子：把上次进程留下的未处理待审批恢复进队列。"""
    with _lock:
        _load_persisted()

# 放行令牌所用的参数指纹 → 一次性放行键（hash）。批准后签发，重跑同操作时消费。
# 与 security_ops.gate_write_tool 的口径**必须逐字一致**，否则令牌匹配不上：
#   h = args_hash("perm", name, json.dumps(args or {}, ensure_ascii=False,
#                                        sort_keys=True, default=str))
_pending_grants = {}


def _grant_hash(tool_name, args):
    """算 security_ops 口径的一次性放行键（批准后签发令牌用）。"""
    import json as _json
    try:
        from security_ops import args_hash
        return args_hash("perm", tool_name,
                         _json.dumps(args or {}, ensure_ascii=False,
                                     sort_keys=True, default=str))
    except Exception:  # noqa: BLE001
        return ""


def _summarize(tool_name, args, reason=""):
    """生成脱敏摘要——只描述"要做什么"，不含命令原文。

    v1.0.660（B1）：审批待办要能让人看懂"这条是什么"，但命令/参数里常有
    密码、令牌、内网地址（pc_run 的完整命令行尤其）。故只取工具名 +
    少量非敏感标识（路径 basename、长度），不给原文。
    """
    bits = []
    try:
        a = args if isinstance(args, dict) else {}
        for k in ("path", "file", "dir", "url", "target", "name"):
            v = a.get(k)
            if isinstance(v, str) and v.strip():
                # 只留 basename：完整路径带用户名/目录结构，同样是信息泄露面
                base = v.replace("\\", "/").rstrip("/").split("/")[-1][:60]
                bits.append(f"{k}={base}")
        for k in ("flow", "expression", "command", "text"):
            v = a.get(k)
            if isinstance(v, str) and v.strip():
                bits.append(f"{k}({len(v)}字)")
    except Exception as e:  # noqa: BLE001
        # v1.0.660（构建门禁）：脱敏摘要生成失败不能静默——那会让"待审批列表"出现
        # 一条没有描述的记录，用户无从判断这是什么操作。
        _log(f"[approval_queue] 生成脱敏摘要失败（退回仅工具名）：{e}")
    summary = str(tool_name or "?")
    if bits:
        summary += " " + " ".join(bits[:4])
    if reason:
        summary += "｜" + str(reason)[:80]
    return summary[:200]


def build_impact(tool_name, args):
    """从工具参数里抽"影响预览"（v1.0.660 功能 B3）。

    审批弹窗只有一句"执行 xxx"时，用户无法判断这次到底会动什么、能不可逆。
    这里从参数里提取**拿得到**的事实：目标路径、目标文件大小、是否覆盖已有文件、
    是否是删除类动作。全部是本地文件系统事实，不含原文。
    """
    import os as _os
    out = {"tool": str(tool_name or "?"), "paths": [], "sizes": [],
           "overwrite": False, "destructive": False, "notes": []}
    try:
        a = args if isinstance(args, dict) else {}
        raw_paths = []
        for k in ("path", "file", "dir", "target", "dest", "destination", "source", "src"):
            v = a.get(k)
            if isinstance(v, str) and v.strip():
                raw_paths.append(v.strip())
        if isinstance(a.get("paths"), list):
            raw_paths += [str(x) for x in a["paths"] if isinstance(x, str) and x.strip()]
        seen = set()
        for p in raw_paths[:8]:
            if p in seen:
                continue
            seen.add(p)
            out["paths"].append(p[:200])
            try:
                if _os.path.isfile(p):
                    sz = _os.path.getsize(p)
                    out["sizes"].append({"path": p[:200], "size": sz})
                    # 目标已存在 + 本次是写类动作 → 覆盖风险
                    if str(tool_name or "") in ("save_file", "save_workspace_file",
                                               "append_progress", "create_excel",
                                               "create_docx", "create_pptx"):
                        out["overwrite"] = True
            except OSError as e:
                # v1.0.660（构建门禁）：取大小失败（文件可能刚被删/无权限）必须留痕——
                # 静默吞掉等于"影响预览漏了体积"，用户据此批准了却不知规模。
                _log(f"[approval_queue] 读取目标文件大小失败（{p[:60]}）：{e}")
        # 删除类动作：显式参数标记 + 工具名含 delete/rm
        if a.get("op") in ("delete", "rm", "remove") or "delete" in str(tool_name or "").lower():
            out["destructive"] = True
        if a.get("auto_approve"):
            out["destructive"] = True
            out["notes"].append("会自动判定通过并真实提交审批单，事后不可撤销")
        if a.get("expression"):
            out["notes"].append("会在企微页面执行脚本")
        if not out["paths"]:
            out["notes"].append("本次操作不涉及明确的目标文件")
    except Exception as e:  # noqa: BLE001
        # v1.0.660（构建门禁）：影响预览构造失败必须留痕——静默返回半截数据会让
        # 用户以为"这次不影响任何文件"，据此点了批准。
        _log(f"[approval_queue] 构造影响预览失败（可能不完整）：{e}")
    return out


def add_pending(tool_name, args, reason="", source="", level="ask_every"):
    """入队一条待审批操作，返回条目 id。

    source：来源会话（pane_id / 定时任务 id），便于用户知道是哪来的。
    level  ：当时生效的权限档（仅记录，不参与判定）。
    """
    global _seq
    with _lock:
        _seq += 1
        item_id = "%s-%d" % (int(time.time() * 1000) % 100000000, _seq)
        rec = {
            "id": item_id,
            "tool": str(tool_name or "?"),
            "summary": _summarize(tool_name, args, reason),
            "reason": str(reason or "")[:200],
            # v1.0.660（B3）：影响预览（目标路径/大小/是否覆盖），随条目一起返回
            "impact": build_impact(tool_name, args),
            # 一次性放行键（security_ops 口径）：批准时用它签发令牌
            "grant_hash": _grant_hash(tool_name, args),
            "source": str(source or ""),
            "level": str(level or ""),
            "created_at": time.time(),
            "status": "pending",
        }
        _items[item_id] = rec
        # 超上限：丢最旧的（dict 保序，第一条即最旧）
        while len(_items) > MAX_PENDING:
            oldest = next(iter(_items))
            _items.pop(oldest, None)
        _persist_locked()   # v1.0.667：入队即落盘（锁内，见 _persist_locked 契约）
        return item_id


def list_pending():
    """列出全部待审批条目（按创建时间倒序，新的在前）。"""
    with _lock:
        return sorted((dict(r) for r in _items.values()),
                      key=lambda r: r.get("created_at") or 0, reverse=True)


def get(item_id):
    """取单条（副本）。"""
    with _lock:
        r = _items.get(str(item_id))
        return dict(r) if r else None


def resolve(item_id, approved):
    """处理一条待审批：approved=True 批准，False 驳回。

    批准时签发**一次性放行令牌**（security_ops 的一次性令牌机制）：用户重跑
    同一个操作时闸门直接放行，不必再弹一次窗。令牌 600 秒后过期（security_ops 侧语义）。
    条目处理完即出队（pending 是"待处理"，处理完不该继续占着上限名额）。
    """
    with _lock:
        r = _items.pop(str(item_id), None)
        if r:
            _persist_locked()   # v1.0.667：处理后同步落盘，重启不复活已处理条目
    if not r:
        return None
    r = dict(r)
    r["status"] = "approved" if approved else "rejected"
    r["resolved_at"] = time.time()
    if approved and r.get("grant_hash"):
        try:
            # begin_approval + grant 连用 = 用户已在安全中心点过"批准"，
            # 由服务端代签一次性令牌（nonce 不外泄，只在服务端内部流转）。
            import security_ops
            nonce = security_ops.begin_approval(r["grant_hash"])
            granted = bool(security_ops.grant(r["grant_hash"], nonce))
            r["granted"] = granted
        except Exception:  # noqa: BLE001
            r["granted"] = False
    return r


def clear():
    """清空队列（返回清掉的条数）。"""
    with _lock:
        n = len(_items)
        _items.clear()
        _pending_grants.clear()
        if n:
            _persist_locked()   # v1.0.667：清空要同步到盘上
        return n
