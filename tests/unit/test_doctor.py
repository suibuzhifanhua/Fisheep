# -*- coding: utf-8 -*-
"""v1.0.788（P1-5）一键自诊 doctor：单页报告聚合 + 单项失败不拖垮整份。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import services.doctor as D  # noqa: E402


def test_item_never_raises():
    """单项检查抛异常 → 降级 ok=False + 留痕，不传播。"""
    def _boom():
        raise RuntimeError("boom 爆炸")

    it = D._item("测试项", _boom)
    assert it["ok"] is False and "RuntimeError" in it["detail"]
    assert it["name"] == "测试项" and it["ms"] >= 0


def test_item_ok_shape():
    it = D._item("正常项", lambda: (True, "一切良好"))
    assert it["ok"] is True and it["detail"] == "一切良好"


def test_run_report_shape_and_bad_collection(monkeypatch):
    """run() 聚合：8 项、字段齐全、bad 名单 = 所有 ok=False 的项名（含抛异常的项）。"""
    def _model_boom():
        raise ValueError("模型炸了")

    monkeypatch.setattr(D, "_chk_runtime", lambda: (True, "py3.13"))
    monkeypatch.setattr(D, "_chk_workspace", lambda: (True, "/tmp/ws"))
    monkeypatch.setattr(D, "_chk_disk", lambda: (False, "总 0 GB / 可用 0.2 GB（剩余空间偏低！）"))
    monkeypatch.setattr(D, "_chk_config", lambda: (True, "ok"))
    monkeypatch.setattr(D, "_chk_wecom", lambda: (False, "CDP 9222 不可达"))
    monkeypatch.setattr(D, "_chk_model", _model_boom)
    monkeypatch.setattr(D, "_chk_components", lambda: (True, "ocr:ok"))
    monkeypatch.setattr(D, "_chk_version", lambda: (True, "v1.0.788"))
    r = D.run()
    assert r["bad"] == ["磁盘空间", "企微 CDP", "默认模型"]
    assert all(set(it) == {"name", "ok", "detail", "ms"} for it in r["items"])
    boom = next(it for it in r["items"] if it["name"] == "默认模型")
    assert boom["ok"] is False and "ValueError" in boom["detail"]


def test_run_end_to_end_offline(monkeypatch, tmp_path):
    """run() 全量跑一遍：打桩掉慢/网络项后，报告结构完整、异常项如实进 bad。"""
    monkeypatch.setattr(D, "_chk_wecom", lambda: (False, "CDP 9222 不可达（测试桩）"))
    monkeypatch.setattr(D, "_chk_model", lambda: (True, "桩模型在线（HTTP 200）"))
    monkeypatch.setattr(D, "_chk_components", lambda: (True, "ocr:ok / asr:ok / git:ok / node:ok / dsh:ok"))
    monkeypatch.setattr(D, "_chk_workspace",
                         lambda: (True, str(tmp_path) + " · 子目录（尚未创建）"))
    r = D.run()
    assert r["ok"] is True and len(r["items"]) == 8
    assert "企微 CDP" in r["bad"] and "默认模型" not in r["bad"]
    assert r["all_ok"] is False
    assert r["total_ms"] == sum(it["ms"] for it in r["items"])


def test_chk_runtime_and_disk_pure():
    """运行环境/磁盘两项纯进程内，真跑（不桩）也必须有确定结构。"""
    ok, detail = D._chk_runtime()
    assert ok is True and "Python" in detail
    ok, detail = D._chk_disk()
    assert isinstance(ok, bool) and "GB" in detail


def test_route_doctor_wired_offline(monkeypatch):
    """/api/doctor 接线：报告 200、结构完整（网络项打桩，测试环境零外部依赖）。"""
    import backend
    monkeypatch.setattr(D, "_chk_wecom", lambda: (False, "测试桩"))
    monkeypatch.setattr(D, "_chk_model", lambda: (True, "测试桩"))
    monkeypatch.setattr(D, "_chk_components", lambda: (True, "测试桩"))
    monkeypatch.setattr(D, "_chk_workspace",
                         lambda: (True, "/tmp/ws · 子目录（尚未创建）"))
    monkeypatch.setattr(D, "_chk_config", lambda: (True, "测试桩"))
    monkeypatch.setattr(D, "_chk_version", lambda: (True, "测试桩"))
    c = backend.app.test_client(use_cookies=False)
    r = c.get("/api/doctor")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and len(d["items"]) == 8
    assert set(d["items"][0]) == {"name", "ok", "detail", "ms"}
