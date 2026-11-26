# -*- coding: utf-8 -*-
"""tests/contract/test_pc_run_argv.py — 参数数组通道的行为锁（v1.0.755，评审 P0-2）。

锁的是**行为**，不是源码文案：
  · shell=False 零展开（元字符按字面传，改回 shell=True 必红）；
  · 与 pc_run 共用同一套安全闸门（危险命令/保护路径/审批/审计）；
  · schema 与执行器一一对应（漏注册会在加载期抛错，这里再钉一次）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from agent_tools import os_ops, registry, executors  # noqa: E402


def _src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_schema_and_handler_are_registered():
    names = [t["function"]["name"] for t in registry.TOOLS]
    assert "pc_run_argv" in names
    assert "pc_run_argv" in executors._TOOL_HANDLERS
    assert names.count("pc_run_argv") == 1


def test_argv_validation():
    assert os_ops.pc_run_argv(None)["ok"] is False
    assert os_ops.pc_run_argv([])["ok"] is False
    assert os_ops.pc_run_argv("git status")["ok"] is False
    assert os_ops.pc_run_argv(["git", ""])["ok"] is False


def test_shell_false_literal_args(monkeypatch):
    """参数里的 shell 元字符必须原样到达程序，且 Popen 收到的是 list + shell=False。"""
    if not os_ops.IS_WIN:
        return
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kw):
            captured["args"] = args
            captured["kw"] = kw
            self.returncode = 0

        def communicate(self, timeout=None):
            return b"ok", b""

        def kill(self):
            return None

    monkeypatch.setattr(os_ops.subprocess, "Popen", _FakePopen)
    # 直接桩掉安全模块的三处判定，只验执行形态
    import security_ops
    monkeypatch.setattr(security_ops, "check_command", lambda cmd, cfg: ("pass", ""))
    monkeypatch.setattr(security_ops, "check_paths", lambda ps, cfg: (True, ""))
    monkeypatch.setattr(security_ops, "audit", lambda *a, **k: None)
    special = ["prog", "a&b | c>d", "%VAR%", '"quoted"']
    r = os_ops.pc_run_argv(special)
    assert r["ok"] is True and r["shell"] == "argv"
    assert captured["args"] == special
    assert captured["kw"].get("shell") is False, "改回 shell=True 会让这条锁变红"


def test_dangerous_command_still_blocked(monkeypatch):
    """新通道不得绕过 check_command：block 判定必须返回 ok=False。"""
    if not os_ops.IS_WIN:
        return
    import security_ops
    monkeypatch.setattr(security_ops, "check_command",
                        lambda cmd, cfg: ("block", "危险模式"))
    monkeypatch.setattr(security_ops, "audit", lambda *a, **k: None)
    r = os_ops.pc_run_argv(["format", "C:"])
    assert r["ok"] is False and "拦截" in r["error"]


def test_protected_path_still_blocked(monkeypatch):
    """新通道不得绕过 check_paths：保护路径命中必须拒绝。"""
    if not os_ops.IS_WIN:
        return
    import security_ops
    monkeypatch.setattr(security_ops, "check_command", lambda cmd, cfg: ("pass", ""))
    monkeypatch.setattr(security_ops, "check_paths", lambda ps, cfg: (False, "C:\\Windows"))
    monkeypatch.setattr(security_ops, "audit", lambda *a, **k: None)
    r = os_ops.pc_run_argv(["copy", "x", "C:\\Windows\\System32\\evil.dll"])
    assert r["ok"] is False and "保护路径" in r["error"]


def test_legacy_pc_run_unchanged():
    """兼容通道仍在，且仍是 shell=True（deferred.md 登记到期复核）。"""
    src = _src("agent_tools/os_ops.py")
    assert "def pc_run(command" in src
    assert "p = subprocess.Popen(command, shell=True" in src
    assert "docs/deferred.md" in src
