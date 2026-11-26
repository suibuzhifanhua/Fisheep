# -*- coding: utf-8 -*-
"""tests/contract/test_contract_silent_business_fail.py — 三条「失败被当成功」的行为锁。

① 批审 auto_approve：判定绿 ≠ 审批已提交。点「通过」失败时原实现只写 approve
   字段，verdict 仍是 green → 报告显示 ✅ 通过、slim 把 green 全过滤（模型一个字
   看不到）、摘要报「22 张通过」。用户以为审批完了，企微里还挂着待办。
② 团队审查：_llm_complete 的**全部**失败路径都 return None、从不抛异常，所以
   原来那个「审查失败发可见事件」的 except 分支是死代码——真实失败形态是空串，
   一路落进「空视为通过」，用户以为复核过了。
③ 输出截断：finish_reason=length 且正文非空时，半截答复被当最终交付，
   通知弹「✅ 任务完成」。llm_client 只拦了零正文那一档。

三条都真跑被测函数（批审真跑流水线 + 真写报告文件），改回旧写法必红。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import services.batch_flow as bf  # noqa: E402


# ---------------- ① 批审：点通过失败必须一路可见 ----------------

class _FakeTransport:
    """只实现本用例用到的传输触点，其余一律不碰 CDP。"""

    def __init__(self, approve_ok, note):
        self._approve = (approve_ok, note)
        self.approve_calls = 0

    def open_detail(self, port, list_pick, link, issues):
        return "fp-1"

    def approve_in_detail(self, port, fp):
        self.approve_calls += 1
        return self._approve

    def close_new_targets(self, port, base_ids):
        return 0


@pytest.fixture
def one_item(monkeypatch, tmp_path):
    """把 _audit_one 的 CDP 触点全打桩，判定固定 green，其余走真实代码。"""
    monkeypatch.setattr(bf, "workspace_root", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(bf, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(bf.wb, "list_targets", lambda port: [])
    monkeypatch.setattr(bf.wb, "read_fields", lambda fp, keys, port=None: {"fields": []})
    monkeypatch.setattr(bf.wb, "read_page", lambda fp, port=None: {
        "body": "本月应运维天数：23\n实际运维天数：23\n", "fields": [], "links": []})
    import services.rule_check as RC
    monkeypatch.setattr(RC, "run_rule", lambda name, data: {
        "result": "green", "checks": [], "reason": ""})
    return tmp_path


def _run_one(monkeypatch, tmp_path, approve_ok, note="未找到可见的「通过/同意」按钮"):
    tr = _FakeTransport(approve_ok, note)
    orch = bf.AuditOrchestrator(transport=tr)
    rec = orch._audit_one(9222, "pick", {"text": "某营业部月报", "url": "http://x/1"},
                          str(tmp_path), 0, auto_approve=True)
    return rec, tr


def test_approve_failure_is_recorded_as_an_issue(one_item, monkeypatch):
    rec, tr = _run_one(monkeypatch, one_item, False)
    assert tr.approve_calls == 1, "auto_approve 没真去点通过"
    assert rec["verdict"] == "green", "规则判定本身不该被改写"
    assert rec["approve_ok"] is False
    assert any("未提交" in s for s in rec["issues"]), \
        "点通过失败没进 issues——报告/疑问计数都看不到它：%r" % rec["issues"]


def test_approve_success_marks_ok_without_issue(one_item, monkeypatch):
    """阳性对照：成功路径不能被一起打成失败。"""
    rec, _ = _run_one(monkeypatch, one_item, True, "已点通过")
    assert rec["approve_ok"] is True
    assert not any("未提交" in s for s in rec["issues"])


@pytest.fixture
def pipe(monkeypatch, tmp_path):
    """整条流水线：只桩 _audit_one，报告/摘要/slim 全走真实代码。"""
    monkeypatch.setattr(bf, "workspace_root", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(bf, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(bf, "_find_list_page", lambda port: ("pick", []))
    monkeypatch.setattr(bf, "_retry_degraded", lambda fn, label, **kw: (True, None, None))
    items = [{"text": "甲营业部月报", "url": "http://x/1"},
             {"text": "乙营业部月报", "url": "http://x/2"}]
    monkeypatch.setattr(bf, "_load_all_report_links", lambda pick, port: list(items))

    def fake(port, list_pick, lk, download_dir, idx, auto_approve=False):
        ok = idx == 0
        return {"index": idx, "title": lk["text"], "url": lk["url"], "verdict": "green",
                "approve_ok": ok, "approve": "已点通过" if ok else "点通过失败：按钮未找到",
                "issues": [] if ok else ["点通过失败（判定通过但**未提交**，需人工补点）：按钮未找到"]}

    monkeypatch.setattr(bf, "_audit_one", fake)
    return tmp_path


def test_green_but_unsubmitted_reaches_the_model(pipe):
    r = bf.audit_monthly_reports(port=9222, auto_approve=True)
    assert r["ok"] is True, r
    titles = [x["title"] for x in r["results"]]
    assert "乙营业部月报" in titles, "判定绿但未提交的单据被 slim 过滤掉了：%r" % titles
    assert "甲营业部月报" not in titles, "已提交的绿单不该回传（瘦身契约）"
    assert "未提交" in r["summary"], "摘要没点名未提交单据：%s" % r["summary"]


def test_report_distinguishes_verdict_from_submission(pipe):
    r = bf.audit_monthly_reports(port=9222, auto_approve=True)
    body = open(os.path.join(str(pipe), r["report_path"]), encoding="utf-8").read()
    assert "审批提交" in body, "报告没有提交列，绿=已审批的误读无从纠正"
    assert "❌ 未提交" in body and "✅ 已提交" in body, body


def test_unsubmitted_is_not_silently_re_approved_on_resume():
    """不可撤销动作不自动重试：resume 仍视 green 为已完成。
    重复点「通过」会二次提交，宁可报告点名让人工补点。"""
    prev = [{"url": "http://x/1", "verdict": "green", "approve_ok": False}]
    keep, todo = bf.resume_partition(prev, [{"text": "甲", "url": "http://x/1"}])
    assert len(keep) == 1 and todo == []


# ---------------- ② 团队审查：无输出必须发可见事件 ----------------

def _team_events(monkeypatch, complete):
    import agent.team as TM
    monkeypatch.setattr(TM, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(TM, "_llm_complete", complete)
    monkeypatch.setattr(TM, "_is_aborted", lambda pane: False)
    monkeypatch.setattr(TM, "_plan_model", lambda cfg: {"id": "m", "name": "审查模型"})

    def gen(msgs):
        yield "data: " + json.dumps({"type": "final", "text": "已按要求完成任务"},
                                    ensure_ascii=False) + "\n\n"

    return list(TM.team_pipeline([{"role": "user", "content": "做事"}], "做事",
                                 {"feature_flags": {"team": True}}, gen,
                                 pane_id="pTeam"))


def _review_notices(events):
    out = []
    for e in events:
        s = e.strip()
        if not s.startswith("data:"):
            continue
        try:
            obj = json.loads(s[5:].strip())
        except ValueError:
            continue
        if "未经复核" in str(obj.get("summary") or ""):
            out.append(obj)
    return out


def test_review_none_output_is_reported_as_not_reviewed(monkeypatch):
    """_llm_complete 返回 None（超时/取消/流中断）——真实失败形态，必须可见。"""
    evs = _team_events(monkeypatch, lambda *a, **k: None)
    assert _review_notices(evs), "审查没跑成却没有任何可见交代：%r" % evs


def test_review_empty_output_is_reported_as_not_reviewed(monkeypatch):
    evs = _team_events(monkeypatch, lambda *a, **k: "   ")
    assert _review_notices(evs)


def test_review_pass_does_not_emit_degraded_notice(monkeypatch):
    """阳性对照：审查真跑成且判 PASS 时不该报「未经复核」。"""
    evs = _team_events(monkeypatch, lambda *a, **k: "PASS")
    assert not _review_notices(evs), evs


# ---------------- ③ 输出截断：半截答复不算完成 ----------------

def _loop_events(monkeypatch, tmp_path, finish_reason, text):
    import agent.loop as AL
    from core import paths as CP
    monkeypatch.setattr(CP, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(AL, "dispatch_tool", lambda n, a, c: {"ok": True})
    monkeypatch.setattr(AL, "stream_llm_events", lambda *a, **k: iter(
        [("done", {"text": text, "usage": None, "tool_calls": [],
                   "finish_reason": finish_reason})]))
    cfg = {"models": [{"id": "m1", "name": "T", "base_url": "http://127.0.0.1:59999/v1",
                       "api_key": "", "model": "t", "enabled": True}],
           "wecom": {}, "connectors": {}, "custom_skills": [], "skill_overrides": {},
           "enabled_skills": [], "feature_flags": {"skills": False}}
    out = []
    for item in AL.agent_loop([{"role": "user", "content": "写长文"}], cfg,
                              pane_id="pTrunc", max_rounds=2):
        s = item.strip() if isinstance(item, str) else ""
        if s.startswith("data:"):
            try:
                out.append(json.loads(s[5:].strip()))
            except ValueError:
                continue
    return out


def test_truncated_answer_is_not_silently_complete(monkeypatch, tmp_path):
    evs = _loop_events(monkeypatch, tmp_path, "length", "这份报告的结论是")
    assert any(e.get("type") == "error" and "截断" in str(e.get("text") or "")
               for e in evs), "半截答复没有任何可见交代：%r" % evs


def test_normal_answer_has_no_truncation_notice(monkeypatch, tmp_path):
    evs = _loop_events(monkeypatch, tmp_path, "stop", "这份报告的结论是完整的。")
    assert not [e for e in evs if e.get("type") == "error"], evs