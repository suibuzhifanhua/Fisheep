# -*- coding: utf-8 -*-
"""v1.0.783 借鉴批 F1+F2：产物历史版本（一键还原）+ 关键事件推企微群。

  · F1：/api/workspace/file-history（版本列表/回看）+ /api/workspace/file-restore
    （字节级写回，走 save_workspace_file → 覆盖前自动备当前版，版本链闭环）。
    版本来源 = 备份/<YYYYMMDD>/<epoch>_<原名>（security_ops.backup_file 固定格式）。
  · F2：services/event_notify.notify_event（开关+webhook+120s 冷却+静默不抛）
    + 5 挂点（loop×4：budget/blocked/done；scheduler×1：sched）源码锁。
"""
import os
import sys
import re
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============================================================ F2：event_notify 服务


def _reset_notify(monkeypatch):
    import services.event_notify as en
    with en._last_lock:
        en._last.clear()
    calls = []
    monkeypatch.setattr(en, "_push", lambda url, msg: calls.append((url, msg)))
    return en, calls


def _wait_calls(calls, n, timeout=2.0):
    """_push 走 daemon 线程（spy 异步 append）——等它落定再断言，消除时序竞态。"""
    t0 = time.time()
    while len(calls) < n and time.time() - t0 < timeout:
        time.sleep(0.01)
    return len(calls) >= n


def _patch_cfg(monkeypatch, cfg):
    monkeypatch.setattr("core.config_store.load_config", lambda: cfg)


def test_notify_off_by_default(monkeypatch):
    """开关默认关（feature_flags.event_notify 缺失）→ 不推、不抛。"""
    en, calls = _reset_notify(monkeypatch)
    _patch_cfg(monkeypatch, {"wecom": {"webhook": "https://qy.example/hook/x"}})
    en.notify_event("blocked", "任务受阻")
    assert calls == []


def test_notify_no_webhook(monkeypatch):
    """开关开但没填 webhook → 不推。"""
    en, calls = _reset_notify(monkeypatch)
    _patch_cfg(monkeypatch, {"feature_flags": {"event_notify": True}, "wecom": {}})
    en.notify_event("blocked", "任务受阻")
    assert calls == []


def test_notify_pushes_with_prefix_and_cap(monkeypatch):
    """开关开+webhook → 正文截 500 后交 _push；_push 再给 send_webhook 加「【Fisheep】」前缀。"""
    import services.event_notify as en_mod
    real_push = en_mod._push   # 先取真身（_reset_notify 会把 _push 换成 spy）
    en, calls = _reset_notify(monkeypatch)
    _patch_cfg(monkeypatch, {
        "feature_flags": {"event_notify": True},
        "wecom": {"webhook": "https://qy.example/hook/x"}})
    long = "长" * 600
    en.notify_event("done", long)
    assert _wait_calls(calls, 1)
    url, msg = calls[0]
    assert url == "https://qy.example/hook/x"
    assert len(msg) == 500  # 正文先截 500（前缀在 _push 里加）
    sent = []
    monkeypatch.setattr("bridge.send_webhook", lambda u, c, t: sent.append((u, c, t)))
    real_push(url, msg)
    assert sent == [(url, "【Fisheep】" + msg, "text")]


def test_notify_cooldown_per_kind(monkeypatch):
    """同 kind 120s 内只推一次；不同 kind 互不挤占。"""
    en, calls = _reset_notify(monkeypatch)
    _patch_cfg(monkeypatch, {
        "feature_flags": {"event_notify": True},
        "wecom": {"webhook": "https://qy.example/hook/x"}})
    en.notify_event("budget", "第一条")
    en.notify_event("budget", "第二条（冷却内，应吞）")
    en.notify_event("sched", "另一 kind，应推")
    assert _wait_calls(calls, 2)
    assert [m for (_u, m) in calls] == ["第一条", "另一 kind，应推"]


def test_notify_silent_on_broken_config(monkeypatch):
    """load_config 本身炸 → 静默返回，绝不把异常抛给调用方（主循环里调用）。"""
    en, calls = _reset_notify(monkeypatch)
    monkeypatch.setattr("core.config_store.load_config",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    en.notify_event("blocked", "x")  # 不抛即通过
    assert calls == []


# ============================================================ F1：历史版本端点


def _ws_client(tmp_path, monkeypatch):
    """裸 Flask 应用 + workspace 蓝图；workspace_root 改道 tmp（标准测试口径）。"""
    from flask import Flask
    from core import paths as P
    monkeypatch.setattr(P, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr("core.config_store.load_config",
                        lambda: {"security": {"auto_backup": True}})
    from routes import workspace as W
    app = Flask("f1_test")
    app.register_blueprint(W.bp)
    return app.test_client(use_cookies=False)


def _mk_backup(tmp_path, day, epoch, name, content):
    d = tmp_path / "备份" / day
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{epoch}_{name}"
    fp.write_text(content, encoding="utf-8")
    return f"{day}__{epoch}_{name}"


def test_file_history_list_filters_and_orders(tmp_path, monkeypatch):
    """版本列表：只认 备份/<8位日期>/<≥9位epoch>_<name>，新→旧；噪音（别的文件/坏日期/短 epoch）剔除。"""
    c = _ws_client(tmp_path, monkeypatch)
    v1 = _mk_backup(tmp_path, "20260101", "1000000001", "x.md", "v1")
    v2 = _mk_backup(tmp_path, "20260102", "1000000002", "x.md", "v2")
    _mk_backup(tmp_path, "20260101", "1000000001", "y.md", "别人")
    _mk_backup(tmp_path, "notadate", "1000000003", "x.md", "坏日期")
    _mk_backup(tmp_path, "20260101", "12", "x.md", "短 epoch")
    d = c.get("/api/workspace/file-history?sub=产出&name=x.md").get_json()
    assert d["ok"] and len(d["versions"]) == 2, f"应只列 2 个版本，实际 {d}"
    assert d["versions"][0]["view"] == v2 and d["versions"][1]["view"] == v1
    assert d["versions"][0]["size"] == 2  # "v2" 2 字节


def test_file_history_view_returns_content(tmp_path, monkeypatch):
    """&view= 回看某版本内容（走 read_preview 同一道截断闸）。"""
    c = _ws_client(tmp_path, monkeypatch)
    v = _mk_backup(tmp_path, "20260101", "1000000001", "x.md", "旧版内容")
    d = c.get(f"/api/workspace/file-history?sub=产出&name=x.md&view={v}").get_json()
    assert d["ok"] and d["content"] == "旧版内容"


def test_file_history_view_rejects_bad_view(tmp_path, monkeypatch):
    """view 键格式校验：坏格式 / 不存在的日期目录 → 404，不解析路径。"""
    c = _ws_client(tmp_path, monkeypatch)
    _mk_backup(tmp_path, "20260101", "1000000001", "x.md", "v1")
    assert c.get("/api/workspace/file-history?sub=产出&name=x.md&view=garbage").status_code == 404
    assert c.get("/api/workspace/file-history?sub=产出&name=x.md"
                 "&view=99999999__1000000001_x.md").status_code == 404
    # 路径穿越 name → basename 剥成 "config" → 合法列表请求但零版本（不出 200 空列表）
    d = c.get("/api/workspace/file-history?sub=产出&name=..%2f..%2fconfig").get_json()
    assert d["ok"] and d["versions"] == []


def _count_xmd_backups(tmp_path):
    back = tmp_path / "备份"
    if not back.is_dir():
        return 0
    return len([f for f in back.rglob("*_x.md") if f.is_file()])


def test_file_restore_roundtrip(tmp_path, monkeypatch):
    """还原 = 版本内容字节级写回；当前版先自动备份（版本链闭环、可反复回退）。"""
    c = _ws_client(tmp_path, monkeypatch)
    (tmp_path / "产出").mkdir(exist_ok=True)
    cur = tmp_path / "产出" / "x.md"
    cur.write_text("当前内容", encoding="utf-8")
    v = _mk_backup(tmp_path, "20260101", "1000000001", "x.md", "旧版内容")
    d = c.post("/api/workspace/file-restore",
              json={"sub": "产出", "name": "x.md", "view": v}).get_json()
    assert d["ok"] and d["restored_from"] == "1000000001_x.md", f"还原失败：{d}"
    assert cur.read_text(encoding="utf-8") == "旧版内容"
    assert _count_xmd_backups(tmp_path) == 2, "还原前当前版应自动进备份（2 份）"


def test_file_restore_readonly_sub_rejected(tmp_path, monkeypatch):
    """只读段（台账）不是可写段 → 400（备份只可能来自可写段）。"""
    c = _ws_client(tmp_path, monkeypatch)
    v = _mk_backup(tmp_path, "20260101", "1000000001", "x.md", "v1")
    d = c.post("/api/workspace/file-restore",
               json={"sub": "台账", "name": "x.md", "view": v})
    assert d.status_code == 400 and not d.get_json()["ok"]


def test_file_restore_missing_view_404(tmp_path, monkeypatch):
    """view 对应文件已被 500MB 裁剪清掉 → 404「版本不存在或已被清理」。"""
    c = _ws_client(tmp_path, monkeypatch)
    d = c.post("/api/workspace/file-restore",
               json={"sub": "产出", "name": "x.md", "view": "20260101__1000000001_x.md"})
    assert d.status_code == 404


# ============================================================ F2：挂点源码锁


def _src(rel):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def test_loop_hook_points_present():
    """loop.py 4 挂点 + 顶层导入（budget×2 / blocked / done）——删了必挂。"""
    s = _src("agent/loop.py")
    assert "from services.event_notify import notify_event" in s
    assert 'notify_event("budget"' in s
    assert 'notify_event("blocked"' in s
    assert 'notify_event("done"' in s


def test_scheduler_hook_point_present():
    """scheduler 非 ok（超时/受阻/异常）才推群；正常完成不推（Toast 已通知）。"""
    s = _src("services/scheduler.py")
    assert re.search(r'notify_event\(\s*"sched"', s)
    assert 'startswith("✅")' in s  # 正常完成豁免判据


def test_ff_event_notify_three_way_sync():
    """event_notify 开关三处同步：config_store 默认关 + JS 默认关名单 + 设置页有该 id。"""
    from core.config_store import FEATURE_FLAGS
    assert ("event_notify", False, True) in FEATURE_FLAGS
    js = _src("js/11-workspace-kb-study.js")
    assert "event_notify" in js and re.search(r'_FF_OFF_BY_DEFAULT\s*=\s*\[[^\]]*event_notify', js, re.S)
    html = _src("index.html")
    assert 'id="opt_ff_event_notify"' in html
