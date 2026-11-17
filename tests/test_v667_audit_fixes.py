# -*- coding: utf-8 -*-
"""test_v667_audit_fixes — v1.0.667（v1.0.666 整体审查）回归锁。

覆盖本轮 P0 后端改动（P0-1 审批队列落盘 + P0-2 会话免审批全局管理）。
每条锁都「会红」：对着修复前旧形态会失败，不是恒绿样板。
  P0-1  approval_queue：add_pending 落盘到 产物库/系统/待审批队列.json；
        load_persisted 重启恢复；resolve 处理后从盘上出队。
  P0-2  session-allow：新增 /all GET 聚合全部会话、/all DELETE 一键全清。

队列落盘路径走 core.paths.workspace_root，这里 monkeypatch 它改道到 tmp_path
（与 ws_sub 那批测试同一手法），不碰真实 产物库。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _isolate_workspace(monkeypatch, tmp_path):
    """把 core.paths.workspace_root 改道到 tmp_path。"""
    import core.paths as cp
    monkeypatch.setattr(cp, "workspace_root", lambda: str(tmp_path))
    return tmp_path


# ============================================================ P0-1 审批队列落盘
def test_pending_persisted_on_add(monkeypatch, tmp_path):
    import services.approval_queue as aq
    _isolate_workspace(monkeypatch, tmp_path)
    with monkeypatch.context() as mp:
        mp.setattr(aq, "_seq", 0)
        aq._items.clear()
        aq._pending_grants.clear()
        qid = aq.add_pending("run_audit_flow", {"flow": "月报", "auto_approve": True},
                             reason="bg", source="sched-1")
    f = tmp_path / "系统" / "待审批队列.json"
    assert f.is_file(), "add_pending 必须落盘（重启不丢的前提）"
    data = json.loads(f.read_text(encoding="utf-8"))
    ids = [r["id"] for r in data["items"]]
    assert qid in ids, "落盘条目应含刚入队的 id"


def test_pending_reloaded_on_startup(monkeypatch, tmp_path):
    import services.approval_queue as aq
    ws = _isolate_workspace(monkeypatch, tmp_path)
    # 模拟上次进程留下的盘文件
    sysdir = ws / "系统"
    sysdir.mkdir(parents=True, exist_ok=True)
    (sysdir / "待审批队列.json").write_text(
        json.dumps({"items": [
            {"id": "kept", "tool": "x", "summary": "s", "status": "pending",
             "created_at": 1},
            {"id": "stale", "tool": "y", "summary": "t", "status": "approved",
             "created_at": 2},
            {"id": "broken", "status": "pending"},   # 缺 created_at → 应被拒
        ]}, ensure_ascii=False), encoding="utf-8")
    with monkeypatch.context() as mp:
        aq._items.clear()
        aq.load_persisted()
        got = [r["id"] for r in aq.list_pending()]
    assert got == ["kept"], f"只应恢复 pending 且结构完整的条目，实际 {got}"


def test_pending_removed_from_disk_after_resolve(monkeypatch, tmp_path):
    import services.approval_queue as aq
    _isolate_workspace(monkeypatch, tmp_path)
    with monkeypatch.context() as mp:
        aq._items.clear()
        aq._pending_grants.clear()
        aq._seq = 0
        qid = aq.add_pending("run_audit_flow", {"auto_approve": True}, source="sched")
        # 批准后 resolve（不真发令牌：grant_hash 可能为空 → granted False 分支）
        aq.resolve(qid, True)
    f = tmp_path / "系统" / "待审批队列.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert qid not in [r["id"] for r in data["items"]], "处理后不得留在盘上（防重启复活）"


# ============================================================ P0-2 会话免审批全局管理
def test_session_allow_all_aggregates_panels(monkeypatch, app_client):
    client, _cfg = app_client
    import agent.session as s
    # 造两个会话各一条规则
    with monkeypatch.context() as mp:
        mp.setattr(s, "_ALLOW_TTL", 30 * 60)
        s._SESSION_ALLOW.clear()
        s.add_session_allow("conv_a", "git commit")
        s.add_session_allow("conv_b", "rm -rf /tmp/x")
        r1 = client.get("/api/security/session-allow/all")
        assert r1.status_code == 200
        d1 = r1.get_json()
        panels = {it["pane_id"] for it in d1["items"]}
        assert panels == {"conv_a", "conv_b"}, f"/all 应聚合全部会话，实际 {panels}"
        # 全清
        r2 = client.delete("/api/security/session-allow/all")
        assert r2.get_json()["cleared"] == 2
        # 清后空
        r3 = client.get("/api/security/session-allow/all")
        assert r3.get_json()["count"] == 0
    s._SESSION_ALLOW.clear()


def test_session_allow_all_excludes_expired(monkeypatch, app_client):
    client, _cfg = app_client
    import agent.session as s
    import time as _t
    with monkeypatch.context() as mp:
        s._SESSION_ALLOW.clear()
        s.add_session_allow("conv_x", "some cmd", ttl=1)
        for _r in s._SESSION_ALLOW["conv_x"].values():   # 造过期（不猜 rule id）
            _r["expire"] = _t.time() - 1
        d = client.get("/api/security/session-allow/all").get_json()
        assert d["count"] == 0, "过期规则不应出现在 /all"
    s._SESSION_ALLOW.clear()
