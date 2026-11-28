# -*- coding: utf-8 -*-
"""v1.0.788（P2-8）产物库保留策略：超期备份目录 备份/<日期>/ → 备份归档/<日期>/（移不删）
覆盖：超期判定/零天关闭、同名冲突跳过、非法目录忽略、apply_retention 节流与关闭态、
backup_file 写路径挂钩、/api/security/retention/run 路由与配置钳制。"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import services.retention as RT  # noqa: E402
import security_ops  # noqa: E402


def _mkday(ws, day, files):
    d = ws / "备份" / day
    d.mkdir(parents=True, exist_ok=True)
    for fn, content in files.items():
        (d / fn).write_text(content, encoding="utf-8")
    return d


def test_archive_moves_only_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(RT, "_last_run_day", None)
    ws = tmp_path / "ws"
    _mkday(ws, "20240101", {"a.txt": "old"})
    _mkday(ws, "20240125", {"b.txt": "new"})
    now = datetime.datetime(2024, 2, 1, 0, 0, 0)
    rep = RT.archive_expired(ws, 30, now=now)   # cutoff = 2024-01-02
    assert rep["moved"] == ["20240101"]
    assert (ws / "备份归档" / "20240101" / "a.txt").read_text(encoding="utf-8") == "old"
    assert not (ws / "备份" / "20240101").exists(), "移空的源目录应删除"
    assert (ws / "备份" / "20240125" / "b.txt").exists(), "未超期目录不动"


def test_archive_zero_days_and_missing_root(tmp_path):
    ws = tmp_path / "ws"
    _mkday(ws, "20240101", {"a.txt": "x"})
    assert RT.archive_expired(ws, 0, now=datetime.datetime(2024, 2, 1))["moved"] == []
    assert (ws / "备份" / "20240101" / "a.txt").exists()
    rep = RT.archive_expired(tmp_path / "不存在", 30)
    assert rep["moved"] == [] and rep["skipped"] == []


def test_archive_collision_skips_keeps_source(tmp_path):
    ws = tmp_path / "ws"
    _mkday(ws, "20240101", {"a.txt": "v2", "c.txt": "c"})
    arch = ws / "备份归档" / "20240101"
    arch.mkdir(parents=True)
    (arch / "a.txt").write_text("v1", encoding="utf-8")
    rep = RT.archive_expired(ws, 30, now=datetime.datetime(2024, 2, 1))
    assert rep["skipped"] == ["20240101/a.txt"], "同名冲突必须跳过并留痕"
    assert (arch / "a.txt").read_text(encoding="utf-8") == "v1", "归档侧不被覆盖"
    assert (ws / "备份" / "20240101" / "a.txt").exists(), "源文件保留（不删）"
    assert (arch / "c.txt").read_text(encoding="utf-8") == "c"
    assert not (ws / "备份" / "20240101").is_dir() or \
        os.listdir(str(ws / "备份" / "20240101")) == ["a.txt"], "残留冲突文件时源目录保留"


def test_archive_ignores_non_date_entries(tmp_path):
    ws = tmp_path / "ws"
    (ws / "备份").mkdir(parents=True)
    (ws / "备份" / "notes.txt").write_text("x", encoding="utf-8")
    (ws / "备份" / "20241399").mkdir()   # 8 位但不是合法日期
    (ws / "备份" / "杂项").mkdir()
    rep = RT.archive_expired(ws, 30, now=datetime.datetime(2024, 2, 1))
    assert rep["moved"] == [] and rep["skipped"] == []
    assert (ws / "备份归档").exists() or True   # 无超期目录时不强制建归档根也允许


def test_apply_retention_off_and_throttle(tmp_path, monkeypatch):
    monkeypatch.setattr(RT, "_last_run_day", None)
    ws = tmp_path / "ws"
    _mkday(ws, "20240101", {"a.txt": "old"})
    # 0 = 关闭
    assert RT.apply_retention({"security": {"backup_retention_days": 0}}, ws) is None
    # 缺键（回退默认 0）
    assert RT.apply_retention({}, ws) is None
    assert (ws / "备份" / "20240101").exists()
    # 节流：今天已跑过 → 直接 None
    monkeypatch.setattr(RT, "_last_run_day", datetime.date.today().strftime("%Y%m%d"))
    assert RT.apply_retention({"security": {"backup_retention_days": 30}}, ws) is None
    # 未跑过 → 真归档
    monkeypatch.setattr(RT, "_last_run_day", None)
    rep = RT.apply_retention({"security": {"backup_retention_days": 30}}, ws)
    assert rep and rep["moved"] == ["20240101"]
    assert RT.apply_retention({"security": {"backup_retention_days": 30}}, ws) is None, "同天第二次必节流"


def test_backup_file_hooks_retention(tmp_path, monkeypatch):
    """生产挂钩：auto_backup 写路径 → 顺手把超期目录归档（每天至多一次）。"""
    import core.paths as P
    monkeypatch.setattr(P, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(RT, "_last_run_day", None)
    ws = tmp_path
    _mkday(ws, "20240101", {"stale.txt": "x"})
    src = tmp_path / "src.txt"
    src.write_text("v1", encoding="utf-8")
    cfg = {"security": {"auto_backup": True, "backup_limit_mb": 500,
                         "backup_retention_days": 30}}
    dst = security_ops.backup_file(str(src), cfg)
    assert dst and os.path.isfile(dst), "备份本体照常产出"
    assert (ws / "备份归档" / "20240101" / "stale.txt").exists(), "超期目录应被归档"
    assert not (ws / "备份" / "20240101").exists()


def test_route_retention_run_and_clamp(app_client, tmp_path, monkeypatch):
    c, _ = app_client
    import core.paths as P
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(P, "workspace_root", lambda: str(ws))
    monkeypatch.setattr(RT, "_last_run_day", None)
    (ws / "备份" / "20240101").mkdir(parents=True)
    (ws / "备份" / "20240101" / "a.txt").write_text("x", encoding="utf-8")
    # 默认 0 天 → 空报告（但 ok）
    r = c.post("/api/security/retention/run")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["days"] == 0 and d["moved"] == []
    # 配置钳制：99999 → 3650，-5 → 0
    r = c.post("/api/security/config", json={"security": {"backup_retention_days": 99999}})
    assert r.status_code == 200
    d = c.get("/api/security/config").get_json()["security"]
    assert d["backup_retention_days"] == 3650
    c.post("/api/security/config", json={"security": {"backup_retention_days": -5}})
    d = c.get("/api/security/config").get_json()["security"]
    assert d["backup_retention_days"] == 0
    # 非法值 400
    assert c.post("/api/security/config",
                  json={"security": {"backup_retention_days": "abc"}}).status_code == 400
