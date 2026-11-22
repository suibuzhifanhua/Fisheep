# -*- coding: utf-8 -*-
"""v1.0.704 组件提醒回归锁：
① 配置段声明与默认值（enabled/first_run_seen 必须进 DEFAULT_CONFIG，否则被 save 抹段）；
② 需求事件记账（去重/已读/清空）；
③ 开关持久化（set_enabled / mark_first_seen 落 config.json）；
④ 路由四动作 + 未知动作 400；
⑤ 三处任务钩子（OCR/语音/dsh）缺组件时记需求事件；
⑥ 前端接线存在性（弹窗/总开关/首跑检查/轮询搭车/开关回显，改一处漏一处就变红）。
运行：pytest tests/test_component_reminder.py -q
"""
from conftest import _src

import core.component_reminder as CR   # noqa: E402


def test_default_config_declares_component_reminder():
    from core.config_store import DEFAULT_CONFIG
    cr = DEFAULT_CONFIG.get("component_reminder")
    assert isinstance(cr, dict), "component_reminder 段必须进 DEFAULT_CONFIG（否则 save 抹段）"
    assert cr["enabled"] is True and cr["first_run_seen"] is False


def test_load_config_defaults_merged(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    import core.config_store as cs
    monkeypatch.setattr(cs, "config_path", lambda: str(cfg))
    cs._CFG_CACHE["path"] = None
    cs._CFG_CACHE["stamp"] = None
    cs._CFG_CACHE["cfg"] = None
    got = cs.load_config()
    assert got["component_reminder"]["enabled"] is True
    assert got["component_reminder"]["first_run_seen"] is False


def test_record_demand_dedup_and_seen():
    CR.reset()
    assert CR.record_demand("node", "Node.js", "未安装") is True
    # 同 key+msg 冷却期内重复触发不重复记账
    assert CR.record_demand("node", "Node.js", "未安装") is False
    # 同 key 不同消息照记
    assert CR.record_demand("node", "Node.js", "另一条消息") is True
    assert len(CR.pending_demands()) == 2
    CR.mark_all_seen()
    assert CR.pending_demands() == []
    CR.reset()


def test_set_enabled_and_first_seen_persist(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    import core.config_store as cs
    monkeypatch.setattr(cs, "config_path", lambda: str(cfg))
    cs._CFG_CACHE["path"] = None
    cs._CFG_CACHE["stamp"] = None
    cs._CFG_CACHE["cfg"] = None
    st = CR.get_state()
    assert st == {"enabled": True, "first_run_seen": False}
    CR.set_enabled(False)
    assert CR.get_state()["enabled"] is False
    CR.mark_first_seen()
    st = CR.get_state()
    assert st["enabled"] is False and st["first_run_seen"] is True
    # 落盘可回读（save→load 往返不被抹段）
    assert (cs.load_config().get("component_reminder") or {})["first_run_seen"] is True


def test_reminder_get_default(app_client):
    c, _ = app_client
    CR.reset()
    r = c.get("/api/components/reminder").get_json()
    assert r["enabled"] is True and r["first_run_seen"] is False and r["demands"] == []


def test_reminder_actions(app_client):
    c, _ = app_client
    CR.reset()
    CR.record_demand("ocr", "OCR 组件", "未安装")
    r = c.get("/api/components/reminder").get_json()
    assert len(r["demands"]) == 1 and r["demands"][0]["key"] == "ocr"
    assert c.post("/api/components/reminder", json={"action": "dismiss_all"}).status_code == 200
    assert c.get("/api/components/reminder").get_json()["demands"] == []
    assert c.post("/api/components/reminder", json={"action": "disable"}).status_code == 200
    assert c.get("/api/components/reminder").get_json()["enabled"] is False
    assert c.post("/api/components/reminder", json={"action": "enable"}).status_code == 200
    assert c.get("/api/components/reminder").get_json()["enabled"] is True
    assert c.post("/api/components/reminder", json={"action": "first_seen"}).status_code == 200
    assert c.get("/api/components/reminder").get_json()["first_run_seen"] is True
    CR.reset()


def test_reminder_bad_action_400(app_client):
    c, _ = app_client
    r = c.post("/api/components/reminder", json={"action": "bogus"})
    assert r.status_code == 400


def test_hook_ocr_missing_records_demand(monkeypatch):
    import core.ocr as O
    calls = []
    monkeypatch.setattr(O, "ensure_ocr_runtime", lambda: False)
    monkeypatch.setattr(O, "install_ocr_async", lambda *a, **k: calls.append(1))
    CR.reset()
    assert O.ocr_ready_or_install() is False
    assert calls, "未就绪仍要触发后台安装（原行为不能变）"
    assert any(d["key"] == "ocr" for d in CR.pending_demands())
    CR.reset()


def test_hook_asr_missing_records_demand(monkeypatch):
    import services.asr as A
    monkeypatch.setattr(A, "_ensure_component_path", lambda: False)
    A._state["_failed"] = False
    A._state["_failed_at"] = 0.0
    CR.reset()
    assert A._get_model() is None
    assert any(d["key"] == "asr" for d in CR.pending_demands())
    CR.reset()


def test_hook_dsh_missing_records_demand(monkeypatch):
    import agent_tools.dsh_bridge as D
    monkeypatch.setattr(D, "_which_dsh", lambda: None)
    CR.reset()
    r = D.dsh_run({}, "随便一个任务", timeout=1)
    assert r.get("ok") is False and "dsh 未安装" in r.get("error", "")
    assert any(d["key"] == "dsh" for d in CR.pending_demands())
    CR.reset()


def test_frontend_reminder_wiring():
    js11 = _src("js", "11-workspace-kb-study.js")
    for fn in ("loadCompReminder", "compRemSet", "checkCompFirstRun", "pollCompDemands",
               "compRemAction", "_compRemShow"):
        assert fn + "(" in js11, f"js/11 缺 {fn}"
    html = _src("index.html")
    assert 'id="compRemOverlay"' in html, "组件提醒弹窗缺位"
    assert 'name="compRemMode"' in html and "不再提醒" in html, "总开关（每次提醒/不再提醒）缺位"
    assert "每次遇到上述情况提醒" in html
    js12 = _src("js", "12-init.js")
    assert "checkCompFirstRun()" in js12, "首跑检查未挂启动链"
    assert "pollCompDemands()" in js12, "需求事件未搭状态轮询链"
    js02 = _src("js", "02-nav-quicklinks.js")
    assert "loadCompReminder()" in js02, "运行组件页未回显总开关"
    # 三处任务钩子在位（后端记账入口）
    assert "record_demand" in _src("core", "ocr.py")
    assert "record_demand" in _src("services", "asr.py")
    assert "record_demand" in _src("agent_tools", "dsh_bridge.py")


def test_first_run_popup_splits_missing_vs_degraded():
    """v1.0.705（深检 P2-2）：首跑弹窗不许把 degraded（可选增强）列进「未安装」——
    旧过滤 `!=="ok"&&!=="na"` 把 dsh/git 也混进未装清单，吓用户装用不到的组件。
    现在 missing 列清单、degraded 只给一行轻提示。"""
    js11 = _src("js", "11-workspace-kb-study.js")
    seg = js11[js11.find("async function checkCompFirstRun"):]
    seg = seg[:seg.find("async function pollCompDemands")]
    assert 'c.status==="missing"' in seg, "首跑弹窗还在用旧过滤（degraded 被误报成未安装）"
    assert 'c.status==="degraded"' in seg, "degraded 轻提示缺位"
