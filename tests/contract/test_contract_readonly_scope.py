# -*- coding: utf-8 -*-
"""tests/contract/test_contract_readonly_scope.py — 读写判定与会话免审批的边界行为锁。

两个事故形态：
  ① ``pc_clipboard`` 整个工具名被归进只读清单，但 ``op="set"`` 会 EmptyClipboard +
     SetClipboardData 覆盖用户剪贴板（不可撤销）——「只问答」模式下静默改写。
     名字不足以判读写，必须看 args。
  ② 会话免审批 mode=prefix 按**子串**匹配：放行 `git commit` 会连带放行
     `del /f /s /q ... && git commit`，一条良性规则授权了任意灾难命令。

两条都真跑判定/真跑 agent_loop 断言执行器调用次数，改回旧写法必红。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import agent.loop as AL  # noqa: E402
import agent.session as SE  # noqa: E402
from agent.loop_guard import is_read_tool  # noqa: E402
from core import paths as CP  # noqa: E402


@pytest.mark.parametrize("args,read", [
    ({"op": "get"}, True),
    ({"op": "GET"}, True),
    ({"op": "set", "text": "x"}, False),
    ({"op": "SET", "text": "x"}, False),
    ({}, False),
    (None, False),
])
def test_clipboard_read_classification_follows_op(args, read):
    assert is_read_tool("pc_clipboard", args) is read


def test_name_only_callers_still_work():
    assert is_read_tool("read_wecom_page") and is_read_tool("finish")
    assert not is_read_tool("click_wecom_element")


def _run_loop(monkeypatch, tmp_path, name, args, pane):
    """真跑 agent_loop（readonly=True），返回被执行器实际收到的工具名列表。"""
    called = []
    monkeypatch.setattr(CP, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(AL, "dispatch_tool",
                        lambda n, a, c: (called.append(n), {"ok": True})[1])
    monkeypatch.setattr(AL, "stream_llm_events", lambda *a, **k: iter(
        [("done", {"text": "", "usage": None,
                   "tool_calls": [{"id": "1", "name": name, "args": args}]})]))
    cfg = {"models": [{"id": "m1", "name": "T", "base_url": "http://127.0.0.1:59999/v1",
                       "api_key": "", "model": "t", "enabled": True}],
           "wecom": {}, "connectors": {}, "custom_skills": [], "skill_overrides": {},
           "enabled_skills": [], "feature_flags": {"skills": False}}
    for _ in AL.agent_loop([{"role": "user", "content": "t"}], cfg,
                           pane_id=pane, readonly=True, max_rounds=3):
        pass
    return called


def test_readonly_mode_blocks_clipboard_write(monkeypatch, tmp_path):
    called = _run_loop(monkeypatch, tmp_path, "pc_clipboard",
                       {"op": "set", "text": "覆盖用户剪贴板"}, "roClipSet")
    assert called == [], "只问答模式下剪贴板写入被执行了：%s" % called


def test_readonly_mode_still_allows_clipboard_read(monkeypatch, tmp_path):
    """阳性对照：读侧不能被一起拦死，否则上一条断言是假绿。"""
    called = _run_loop(monkeypatch, tmp_path, "pc_clipboard",
                       {"op": "get"}, "roClipGet")
    assert called and set(called) == {"pc_clipboard"}, called


@pytest.fixture
def clean_allow():
    with SE._allow_lock:
        SE._SESSION_ALLOW.clear()
    yield
    with SE._allow_lock:
        SE._SESSION_ALLOW.clear()


def test_session_allow_prefix_does_not_authorize_spliced_command(clean_allow):
    SE.add_session_allow("p1", "git commit", tool="pc_run")
    assert SE.session_allows("p1", "pc_run", "git commit -m ok") is True
    evil = "del /f /s /q C:" + os.sep + os.path.join("Users", "me") + " && git commit"
    assert SE.session_allows("p1", "pc_run", evil) is False, \
        "良性前缀规则授权了拼接在前面的灾难命令"


def test_session_allow_prefix_is_case_insensitive(clean_allow):
    SE.add_session_allow("p2", "Git Commit")
    assert SE.session_allows("p2", "pc_run", "git commit -m x") is True


def test_session_allow_regex_mode_unchanged(clean_allow):
    SE.add_session_allow("p3", r"^git (status|log)\b", mode="regex")
    assert SE.session_allows("p3", "pc_run", "git status") is True
    assert SE.session_allows("p3", "pc_run", "say git status") is False


def test_session_allow_still_scoped_to_tool_and_pane(clean_allow):
    SE.add_session_allow("p4", "git status", tool="pc_run")
    assert SE.session_allows("p4", "pc_run_argv", "git status") is False
    assert SE.session_allows("p5", "pc_run", "git status") is False