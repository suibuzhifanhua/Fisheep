# -*- coding: utf-8 -*-
"""tests/unit/test_usage_cost.py — v1.0.788（P2-7）成本单价路由。

GET /api/usage/cost 回显；POST 保存（0=关闭，钳 0~1000，非数字 400）。
前端用法量卡单价 × day_series tokens 折算成本趋势图（纯前端，无服务端造数）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_get_default_zero(app_client):
    c, _ = app_client
    r = c.get("/api/usage/cost")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["per_1k"] == 0.0


def test_post_save_and_read_back(app_client):
    c, _ = app_client
    r = c.post("/api/usage/cost", json={"per_1k": 5.5})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["per_1k"] == 5.5
    d = c.get("/api/usage/cost").get_json()
    assert d["per_1k"] == 5.5


def test_post_clamps_to_range(app_client):
    c, _ = app_client
    # 上限 1000
    assert c.post("/api/usage/cost", json={"per_1k": 99999}).get_json()["per_1k"] == 1000.0
    # 下限 0
    assert c.post("/api/usage/cost", json={"per_1k": -5}).get_json()["per_1k"] == 0.0


def test_post_non_numeric_400(app_client):
    c, _ = app_client
    r = c.post("/api/usage/cost", json={"per_1k": "abc"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False
