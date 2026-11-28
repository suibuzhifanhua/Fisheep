# -*- coding: utf-8 -*-
"""v1.0.788（P1-4）会话全文搜索：服务端扫标题+全部消息正文，TTL 缓存，接线 /api。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import services.conversations as C  # noqa: E402


def _ws(tmp_path, monkeypatch):
    """会话目录改道 tmp（monkeypatch 模块内导入的 workspace_root 名字）。"""
    monkeypatch.setattr(C, "workspace_root", lambda: str(tmp_path))
    C._search_cache.clear()


def _mk(tmp_path, monkeypatch, title="月报审批流程", msgs=None):
    _ws(tmp_path, monkeypatch)
    cid = C.create(title)
    for role, content in (msgs or [("user", "如何跑月报审计"), ("agent", "运行 run_audit_flow 即可，"
            "判定结果在台账/月报判定.json")]):
        C.append_message(cid, role, content)
    return cid


def test_search_finds_content_and_title(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch)
    d = C.search("月报审批")          # 只命中标题
    assert d["ok"] and d["total"] == 1
    r = d["results"][0]
    assert any(h["where"] == "title" for h in r["hits"])
    d = C.search("run_audit_flow")    # 只命中消息正文（agent 轮）
    assert d["total"] == 1
    h = d["results"][0]["hits"][0]
    assert h["where"] == "agent" and "run_audit_flow" in h["snippet"]


def test_search_case_insensitive(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch)
    assert C.search("RUN_AUDIT_FLOW")["total"] == 1


def test_search_no_match(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch)
    d = C.search("不存在的词")
    assert d["ok"] and d["total"] == 0 and d["results"] == []


def test_search_requires_query(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch)
    assert C.search("   ")["ok"] is False
    assert C.search("")["ok"] is False


def test_search_hits_capped_per_conv(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    cid = C.create("重复词会话")
    for _ in range(5):
        C.append_message(cid, "user", "苹果 香蕉 苹果")
    d = C.search("苹果")
    assert d["total"] == 1
    assert len(d["results"][0]["hits"]) == 3, "单会话命中片段上限 3 条"


def test_search_cache_invalidation_on_append(tmp_path, monkeypatch):
    """追加消息后文件 mtime/size 变化 → 缓存自动失效，新一轮能搜到新内容。"""
    cid = _mk(tmp_path, monkeypatch)
    assert C.search("新消息里的词")["total"] == 0
    C.append_message(cid, "user", "新消息里的词")
    assert C.search("新消息里的词")["total"] == 1


def test_search_skips_broken_file(tmp_path, monkeypatch):
    """单个会话 JSON 损坏 → 跳过该文件，不中断整轮、不抛。"""
    cid = _mk(tmp_path, monkeypatch)
    d = C.conv_dir()
    bad = os.path.join(d, "conv_broken.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    out = C.search("月报审批")
    assert out["ok"] and out["total"] == 1 and out["results"][0]["id"] == cid
    os.remove(bad)


def test_search_result_shape_and_order(tmp_path, monkeypatch):
    """两个会话都命中 → 按 updated 倒序；字段齐全。"""
    cid1 = _mk(tmp_path, monkeypatch, title="甲")   # 消息含「月报」
    import time as _t
    _t.sleep(1.1)   # 让第二会话的 updated 字符串（秒级）严格更新
    cid2 = C.create("乙")
    C.append_message(cid2, "user", "月报 乙")
    d = C.search("月报")
    assert d["total"] == 2
    ids = [r["id"] for r in d["results"]]
    assert ids[0] == cid2 and ids[1] == cid1, "应按 updated 倒序"
    r = d["results"][0]
    assert set(r) >= {"id", "title", "updated", "archived", "hits"}
    assert all(set(h) == {"where", "ts", "snippet"} for h in r["hits"])


def test_route_search_wired(app_client):
    """/api/conversations/search 接线 + 参数校验（400 缺 q / 400 过长）。"""
    c, _ = app_client
    r = c.get("/api/conversations/search")
    assert r.status_code == 400 and r.get_json()["ok"] is False
    r = c.get("/api/conversations/search?q=" + "x" * 101)
    assert r.status_code == 400
    r = c.get("/api/conversations/search?q=" + "x" * 100)
    assert r.status_code == 200 and r.get_json()["ok"] is True
