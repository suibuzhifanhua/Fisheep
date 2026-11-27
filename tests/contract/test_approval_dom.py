# -*- coding: utf-8 -*-
"""tests/contract/test_approval_dom.py — 审批 DOM 级硬闸的**行为锁**（评审 P1-5）。

验收判据：构造一个**不在提示词示例里的同义按钮文案**，未确认时点击被拦且审计留痕。
本文件同时锁住「硬闸必须在任何 CDP 调用之前」与「页面 JS 路径同样覆盖」。
"""
import io
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import approval_dom as AD  # noqa: E402


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.setattr(AD, "_allowlist", lambda: frozenset())


# ---------- ① 同义改写必须被拦（提示词示例外） ----------

@pytest.mark.parametrize("text", [
    "同意", "批准", "提交", "退回", "保存", "用印",
    "予以通过", "确认生效", "办结归档", "最终递交", "核准并发",
    "Approve & Submit", "FINALIZE", "确认 同意",
])
def test_synonym_button_text_is_caught(text):
    assert AD.dom_approval_hit(text), text


@pytest.mark.parametrize("text", [
    "下一页", "查看报销单", "展开详情", "A 正确", "3", "搜索", "导出明细",
])
def test_neutral_text_passes(text):
    assert AD.dom_approval_hit(text) is None, text


def test_normalization_handles_fullwidth_and_punctuation():
    assert AD.dom_approval_hit("【审批通过】")
    assert AD.dom_approval_hit("确 认 提 交")


# ---------- ② guard_click：拒绝 + 审计 + 白名单 ----------

def test_guard_click_blocks_and_audits(monkeypatch):
    audits = []
    monkeypatch.setattr(AD, "_audit", lambda t, h, a, r: audits.append((h, a, r)))
    r = AD.guard_click("予以通过")
    assert isinstance(r, dict) and r.get("ok") is False
    assert r.get("needs_confirmation") is True
    assert r.get("dom_guard_hit")
    assert len(audits) == 1 and audits[0][1] is False, "拦截必须写审计且标 denied"


def test_guard_click_allows_preapproved(monkeypatch):
    monkeypatch.setattr(AD, "_allowlist",
                        lambda: frozenset({AD._norm("予以通过")}))
    audits = []
    monkeypatch.setattr(AD, "_audit", lambda t, h, a, r: audits.append((h, a, r)))
    assert AD.guard_click("予以通过") is None
    assert audits and audits[0][1] is True, "白名单放行也要留痕"


def test_guard_click_wildcard_allowlist(monkeypatch):
    monkeypatch.setattr(AD, "_allowlist", lambda: frozenset({"*"}))
    assert AD.guard_click("确认提交") is None


def test_guard_click_reads_real_dom_attributes(monkeypatch):
    """数字索引 target 本身无语义，必须靠 element_meta 里的真实 DOM 属性命中。"""
    r = AD.guard_click(3, {"aria_label": "同意并继续", "id": "btn-next",
                           "text": "第 3 项"})
    assert r is not None and r.get("dom_guard_hit")


def test_guard_click_neutral_target_is_free():
    assert AD.guard_click("下一页") is None
    assert AD.guard_click(7) is None


# ---------- ③ 执行器接线：必须在任何 CDP 调用之前 ----------

def test_click_element_blocks_before_any_cdp_call(monkeypatch):
    from bridge import read as R
    called = []
    monkeypatch.setattr(R, "_require_page",
                        lambda *a: (called.append(1) or
                                    ({"ok": False, "error": "probe"}, None, None)))
    r = R.click_element(1, "确认同意并继续", port=9999)
    assert r.get("ok") is False and r.get("dom_guard_hit")
    assert not called, "硬闸必须在 _require_page / CDP 之前，不能点了再判"


def test_click_element_neutral_target_reaches_cdp(monkeypatch):
    from bridge import read as R
    called = []
    monkeypatch.setattr(R, "_require_page",
                        lambda *a: (called.append(1) or
                                    ({"ok": False, "error": "probe"}, None, None)))
    R.click_element(1, "下一页", port=9999)
    assert called, "中性文案不得被硬闸误拦"


# ---------- ③b D8 ①：DOM 硬闸接入通用审批管道 + 短时凭据（一次批准不弹两遍） ----------

def test_dom_guard_emits_approval_request():
    """D8 ①：未放行时硬闸必须带 __approval_needed__ + kind:dom + hash + reason，
    供 loop 走通用挂起→弹窗→grant 管道。改回旧写法（只 ok:False 不带审批字段）→ 必红。"""
    r = AD.guard_click(3, {"aria_label": "同意并继续"}, name="click_wecom_element")
    assert r is not None
    assert r.get("__approval_needed__") is True
    assert r.get("kind") == "dom"
    assert r.get("hash"), "必须带 hash 供 /api/security/grant 用"
    assert r.get("dom_guard_hit") and r.get("needs_confirmation") is True
    assert "流程状态词" in r.get("reason", "")


def test_dom_guard_confirmed_credential_passes(monkeypatch):
    """D8 ①：闸门放行登记的短时凭据 → 硬闸不再弹第二遍窗（同一次批准两关同过）。
    改回旧写法（硬闸无条件拒绝、不查凭据）→ 本测试必红。"""
    import security_ops as SO
    SO._DOM_APPROVED.clear()
    SO._grants.clear()
    # 模拟「闸门已放行并登记了 DOM 短时凭据」
    SO._DOM_APPROVED[SO.dom_approval_hash("click_wecom_element", "同意")] = time.time() + 120
    audits = []
    monkeypatch.setattr(AD, "_audit", lambda t, h, a, r: audits.append((h, a, r)))
    r = AD.guard_click("同意", name="click_wecom_element")
    assert r is None, "凭据在册时应放行，不得再发 __approval_needed__"
    assert audits and audits[0][1] is True, "凭据放行也要留痕（allowed）"
    # 凭据一次性：被消费后再次调用应回到拦截
    r2 = AD.guard_click("同意", name="click_wecom_element")
    assert r2 is not None and r2.get("__approval_needed__") is True


def test_gate_marks_dom_credential_on_click_approval(monkeypatch):
    """D8 ①：click_wecom_element 闸门放行（consume 通过）时必须登记 DOM 短时凭据——
    同一次批准让硬闸不再弹第二遍窗（否则同 hash 二次触发 → 无限弹窗）。
    改回旧写法（闸门不放行 DOM 凭据）→ 本测试必红。"""
    import security_ops as SO
    SO._DOM_APPROVED.clear()
    SO._grants.clear()
    # 「提交审批」既命中闸门 _APPROVAL_TEXT（→ _always_ask_hit），又命中硬闸 FLOW_STATE_WORDS
    args = {"target": "提交审批", "pick": 1}
    h = SO.args_hash("perm", "click_wecom_element",
                     json.dumps(args, ensure_ascii=False, sort_keys=True, default=str))
    SO._grants[h] = time.time() + 600          # 预置一次性令牌：gate 的 consume(h) 会命中它
    monkeypatch.setattr(SO, "audit", lambda *a, **k: None)
    SO.gate_write_tool("click_wecom_element", args, {}, level="ask_every")
    assert SO.dom_approval_hash("click_wecom_element", "提交审批") in SO._DOM_APPROVED, \
        "闸门放行必须登记 DOM 短时凭据，否则硬闸会弹第二遍窗"


# ---------- ③c D8 ②：数字索引点击前只读 DOM 属性预取 ----------

def test_click_element_prefetches_index_meta_and_blocks(monkeypatch):
    """D8 ②：int 数字索引 + 未带 element_meta → 只读 CDP 预取真实 DOM 属性，
    命中流程状态词时在真点击前拦截。改回旧写法（不预取）→ 硬闸拿不到属性 → 误放行 → 必红。"""
    from bridge import read as R
    clicked = []
    # 预取阶段：_page_ws 返回假 ws_url，_cdp_eval 返回"同意"按钮的属性
    monkeypatch.setattr(R, "_page_ws",
                        lambda *a, **k: ("ws://fake", {"url": "http://x"}))
    meta_json = json.dumps({"aria_label": "同意并继续", "text": "第 3 项"})
    monkeypatch.setattr(R, "_cdp_eval", lambda *a, **k: meta_json)
    # 真点击阶段：若走到 _require_page/点击 = 预取后硬闸没拦住 = 失败
    monkeypatch.setattr(R, "_require_page",
                        lambda *a: (clicked.append(1) or
                                    ({"ok": False, "error": "x"}, None, None)))
    r = R.click_element(1, 3, port=9999)
    assert r.get("ok") is False and r.get("dom_guard_hit"), r
    assert not clicked, "命中流程状态词必须在真点击前拦截（预取只读，不得真点）"


def test_click_element_index_neutral_prefetch_passes(monkeypatch):
    """反向锁：int 数字索引 + 中性预取属性 → 不被硬闸误拦，正常走到点击。"""
    from bridge import read as R
    clicked = []
    monkeypatch.setattr(R, "_page_ws",
                        lambda *a, **k: ("ws://fake", {"url": "http://x"}))
    monkeypatch.setattr(R, "_cdp_eval",
                        lambda *a, **k: json.dumps({"text": "下一页", "id": "btn-next"}))
    monkeypatch.setattr(R, "_require_page",
                        lambda *a: (clicked.append(1) or
                                    ({"ok": False, "error": "x"}, None, None)))
    R.click_element(1, 3, port=9999)
    assert clicked, "中性按钮不得被 DOM 硬闸误拦"


def test_wecom_run_js_dom_hit_forces_approval(monkeypatch):
    """页面 JS 路径：不含危险/敏感 API、但含流程状态词 → 仍要审批。"""
    import security_ops as SO
    from agent_tools import executors as E
    monkeypatch.setattr(SO, "js_danger_scan", lambda x: [])
    monkeypatch.setattr(SO, "js_sensitive_api_scan", lambda x: [])
    monkeypatch.setattr(SO, "js_approval_state", lambda n, a: (False, ""))
    monkeypatch.setattr(SO, "js_approval_hash", lambda n, a: "probehash")
    monkeypatch.setattr(SO, "audit", lambda *a, **k: None)
    expr = ("document.querySelectorAll('button').find("
            "b=>b.innerText.includes('予以通过')).click()")
    r = E._run_wecom_js_safe({"expression": expr}, {})
    assert r.get("__approval_needed__") is True
    assert "流程状态词" in r.get("reason", "")


def test_wecom_run_js_clean_expression_still_runs(monkeypatch):
    """反向锁：中性表达式不得被 DOM 硬闸误伤（保持跑课/媒体自动化可用）。"""
    import security_ops as SO
    from agent_tools import executors as E
    monkeypatch.setattr(SO, "js_danger_scan", lambda x: [])
    monkeypatch.setattr(SO, "js_sensitive_api_scan", lambda x: [])
    monkeypatch.setattr(SO, "js_approval_state", lambda n, a: (True, "approved"))
    seen = []
    monkeypatch.setattr(E.wb, "run_js",
                        lambda *a, **k: seen.append(1) or {"ok": True})
    r = E._run_wecom_js_safe({"expression": "document.querySelector('video').currentTime"}, {})
    assert r == {"ok": True} and seen


# ---------- ④ 源码形态锁 ----------

def test_source_shape_keeps_both_hooks():
    read_src = io.open(os.path.join(ROOT, "bridge", "read.py"),
                       encoding="utf-8").read()
    body = read_src.split("def click_element(", 1)[1][:4000]
    assert "guard_click" in body and "element_meta" in body
    exec_src = io.open(os.path.join(ROOT, "agent_tools", "executors.py"),
                       encoding="utf-8").read()
    assert "dom_approval_hit" in exec_src
    assert "click_wecom_element" in exec_src
