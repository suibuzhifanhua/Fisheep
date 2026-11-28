# -*- coding: utf-8 -*-
"""test_audit_recheck.py — v1.0.788（P0-3）月报 red/pending 批量复验队列。

锁定 P0-3 口径合同：复验轮（mode="retry"）必须把本轮复验子集**合并回上一轮全量**
后再对外（计数 / 报告 / 历史入库 / 趋势 / 群通知 / 复验队列）。若不合并，runs[-1]
退化成子集 → 第二次 retry 的 _bad 集丢失本轮之外的非 green 项 → 复验队列静默漏项。
"""
import json
import os

import pytest

from services import batch_flow as bf
import services.event_notify as en


@pytest.fixture(autouse=True)
def reset_bg():
    """与 test_audit_background 同款：隔离 _BG_STATE（本测试同步调用会打点）。"""
    with bf._BG_LOCK:
        bf._BG_STATE.update({"running": False, "started": "", "total": 0, "done": 0,
                             "green": 0, "red": 0, "pending": 0, "current": "",
                             "finished": False, "ok": None, "summary": "", "report_path": ""})
    yield
    with bf._BG_LOCK:
        bf._BG_STATE.update({"running": False, "finished": False})


# ---------- merge_retry_results（纯函数） ----------

def test_merge_retry_results_overwrite_order_append():
    """复验子集覆盖上一轮同名键、沿用上一轮次序、新机构追加尾部。"""
    prev = [{"title": "A", "url": "u://a", "verdict": "green"},
            {"title": "B", "url": "u://b", "verdict": "red"},
            {"title": "C", "url": "u://c", "verdict": "pending"}]
    new = [{"title": "B", "url": "u://b", "verdict": "green", "expect_days": 30},
           {"title": "D", "url": "u://d", "verdict": "green"}]   # D = 新机构（追加）
    merged = bf._ORCH.merge_retry_results(prev, new)
    assert [r["url"] for r in merged] == ["u://a", "u://b", "u://c", "u://d"]
    v = {r["url"]: r["verdict"] for r in merged}
    assert v == {"u://a": "green", "u://b": "green", "u://c": "pending", "u://d": "green"}
    # B 被本轮记录覆盖（携带本轮新字段）
    b = [r for r in merged if r["url"] == "u://b"][0]
    assert b.get("expect_days") == 30
    # 空入参不炸
    assert bf._ORCH.merge_retry_results([], []) == []
    assert bf._ORCH.merge_retry_results(None, prev) == [dict(r) for r in prev]


# ---------- recheck_queue（读判定历史 runs[-1]） ----------

def test_recheck_queue_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "workspace_root", lambda: str(tmp_path))
    assert bf.recheck_queue() == {"ts": "", "total": 0, "items": []}


def test_recheck_queue_lists_non_green(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "workspace_root", lambda: str(tmp_path))
    hp = os.path.join(str(tmp_path), "台账", "月报判定.json")
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    with open(hp, "w", encoding="utf-8") as f:
        json.dump({"runs": [{"ts": "2026-01-01 00:00:00", "results": [
            {"title": "A", "verdict": "green", "url": "u://a", "reason": ""},
            {"title": "B", "verdict": "red", "url": "u://b", "reason": "天数不一致"},
            {"title": "C", "verdict": "pending", "url": "u://c", "reason": "附件缺失"}]}]}, f)
    q = bf.recheck_queue()
    assert q["total"] == 2
    got = {i["title"]: (i["verdict"], i["reason"]) for i in q["items"]}
    assert got == {"B": ("red", "天数不一致"), "C": ("pending", "附件缺失")}
    assert "A" not in got   # green 不进复验队列


# ---------- e2e 口径合同（核心：复验轮写回全量口径） ----------

def _install_flow(monkeypatch, tmp_path, verdicts, calls):
    """接通四件套假运输/审计 + 工作区改道 + 群通知禁推。"""
    monkeypatch.setattr(bf, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(en, "notify_event", lambda *a, **k: None)   # 防真推企微
    links = [{"text": f"{t} 信息技术运维月报", "url": f"http://wk/{t}"} for t in "ABC"]

    def _find_list_page(port):
        return {"id": "listpage"}, [{"title": "待办"}]

    def _load_all_report_links(list_pick, port, **kw):
        return [dict(l) for l in links]

    def _probe_cdp(port):
        return True

    def _audit_one(port, list_pick, link, download_dir, idx, auto_approve=False):
        tag = link["url"].rsplit("/", 1)[-1]
        calls[tag] = calls.get(tag, 0) + 1
        v = verdicts.get(tag, "pending")
        return {"index": idx, "title": (link.get("text") or "")[:60],
                "url": (link.get("url") or "")[:200], "verdict": v,
                "issues": [] if v == "green" else [f"{tag} 疑问项"],
                "reason": "" if v == "green" else f"{tag} 原因"}

    orch = bf._ORCH
    monkeypatch.setattr(orch, "_r_find_list_page", _find_list_page)
    monkeypatch.setattr(orch, "_r_load_all_report_links", _load_all_report_links)
    monkeypatch.setattr(orch, "_r_probe_cdp", _probe_cdp)
    monkeypatch.setattr(bf, "_audit_one", _audit_one)


def test_e2e_retry_full_caliber(monkeypatch, tmp_path):
    """三轮：全量 → 复验(B 转绿) → 再复验(只跑 C)。锁定 runs[-1] 恒为全量口径。"""
    verdicts = {"A": "green", "B": "red", "C": "pending"}
    calls = {}
    _install_flow(monkeypatch, tmp_path, verdicts, calls)

    # ---- 第 1 轮：全量（A 绿 / B 红 / C 待定） ----
    r1 = bf.audit_monthly_reports(port=9222, mode="all")
    assert r1["ok"] is True
    assert (r1["total"], r1["done"], r1["green"], r1["red"], r1["pending"]) == (3, 3, 1, 1, 1)
    hist = bf._load_history()
    assert len(hist["runs"]) == 1 and len(hist["runs"][-1]["results"]) == 3

    # ---- 第 2 轮：复验（B 转绿，C 仍待定） ----
    verdicts["B"] = "green"
    r2 = bf.audit_monthly_reports(port=9222, mode="retry")
    assert r2["ok"] is True
    # 全量口径：total=3（不是复验子集的 2）
    assert r2["total"] == 3 and r2["done"] == 3
    assert (r2["green"], r2["red"], r2["pending"]) == (2, 0, 1)   # A,B 绿 / C 待定
    # 只回非 green 项（C）
    assert len(r2["results"]) == 1 and r2["results"][0]["verdict"] == "pending"
    # 核心合同：runs[-1] 必须是全量口径（3 条），不是复验子集 2 条
    hist = bf._load_history()
    assert len(hist["runs"]) == 2
    last2 = hist["runs"][-1]["results"]
    assert len(last2) == 3, f"复验轮必须写回全量口径，实际 {len(last2)} 条（子集写回 = P0-3 根因）"
    assert {r["url"]: r["verdict"] for r in last2} == {
        "http://wk/A": "green", "http://wk/B": "green", "http://wk/C": "pending"}
    # 复验队列 = 全量口径里的非 green = 只剩 C
    q = bf.recheck_queue()
    assert q["total"] == 1 and q["items"][0]["verdict"] == "pending"

    # ---- 第 3 轮：再复验，只应重跑 C（防漏项根因锁定） ----
    snap = dict(calls)
    verdicts["C"] = "green"
    r3 = bf.audit_monthly_reports(port=9222, mode="retry")
    assert r3["ok"] is True
    assert calls["C"] == snap["C"] + 1     # C 被重跑
    assert calls["A"] == snap["A"]         # A 绿，未重跑
    assert calls["B"] == snap["B"]         # B 绿，未重跑
    assert (r3["green"], r3["red"], r3["pending"]) == (3, 0, 0)
    assert r3["total"] == 3
    assert len(bf._load_history()["runs"][-1]["results"]) == 3   # 第 3 轮后仍全量口径


# ---------- 路由 ----------

def test_routes_recheck(monkeypatch):
    import backend as bm
    monkeypatch.setattr(bf, "recheck_queue",
                        lambda: {"ts": "t", "total": 2,
                                 "items": [{"title": "B", "verdict": "red", "reason": "x"}]})
    seen = {}

    def _start(port=None, mode="all", **kw):
        seen["mode"] = mode
        return {"ok": True, "background": True}

    monkeypatch.setattr(bf, "start_background_audit", _start)
    bm.app.config["TESTING"] = True
    with bm.app.test_client() as c:
        g = c.get("/api/audit/recheck").get_json()
        assert g["total"] == 2 and g["items"][0]["title"] == "B"
        p = c.post("/api/audit/recheck/run").get_json()
        assert p["ok"] is True and p.get("background") is True
        assert seen["mode"] == "retry"   # 路由必须触发 retry 模式
