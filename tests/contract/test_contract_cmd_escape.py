# -*- coding: utf-8 -*-
"""tests/contract/test_contract_cmd_escape.py — cmd 转义/变量拼接绕过命令闸门的行为锁。

事故形态：cmd.exe 会吃掉未成对的 ``^``（``d^el`` 执行成 ``del``），并在 ``&``/``&&``
分段间共享 ``set`` 定义的变量表。而危险命令正则、EncodedCommand 开关识别、路径
token 抽取三处都只扫**原始串**——于是一个 ``^`` 同时绕过三道闸，``%VAR%`` 拼接
同理。本锁真跑 ``pc_run``，把 ``subprocess.Popen`` 换成 recorder，断言
「闸门拦住 + 执行器零调用」；改回只扫原文本必红。

另含两条反向锁（防修过头）：
  · 良性命令仍必须抵达执行器（否则「全拦」也能骗过上面的断言）；
  · ``pc_run_argv``（shell=False）**不做** ``^`` 归一——那里 ``^`` 是字面字符，
    归一会把用户真实参数改坏。
"""
import base64
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import security_ops as SO  # noqa: E402
from agent_tools import os_ops as OO  # noqa: E402

# 测试样例路径刻意不用真实用户名（脱敏词表覆盖 C:\Users\<用户名>）
_DOC = "C:" + os.sep + os.path.join("Users", "me", "Documents")


class _FakeProc:
    returncode = 0

    def communicate(self, timeout=None):
        return b"", b""

    def kill(self):
        pass


@pytest.fixture
def spawned(monkeypatch):
    """把全进程 Popen 换成 recorder：任何真实执行都不可能发生。"""
    calls = []

    def _rec(*a, **k):
        calls.append((a, k))
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _rec)
    monkeypatch.setattr(SO, "audit", lambda *a, **k: None)
    with SO._lock:
        SO._grants.clear()
        SO._pending.clear()
    return calls


def _cfg():
    return {"security": {"permission_level": "ask", "command_mode": "block"}}


def _b64(text):
    return base64.b64encode(text.encode("utf-16-le")).decode("ascii")


@pytest.mark.parametrize("cmd", [
    f"d^el /f /s /q {_DOC}\\*",
    f"cmd /c d^el /f /s /q {_DOC}\\*",
    f"r^d /s /q {_DOC}",
    f'set "a=del" && call %a% /f /s /q {_DOC}\\*',
    f"set a=rd&& %a% /s /q {_DOC}",
])
def test_caret_and_var_splice_never_reach_executor(spawned, cmd):
    r = OO.pc_run(cmd, cfg=_cfg())
    assert r.get("ok") is not True, "转义/变量拼接后的灾难命令被放行：%s" % cmd
    assert not spawned, "闸门没拦住，命令已抵达执行器：%s" % cmd


def test_caret_hidden_encodedcommand_switch_is_decoded(spawned):
    """``-en^c`` 还原成 ``-enc`` 才认得出开关，base64 体内的 del 必须被看到。"""
    payload = f"del /f /s /q {_DOC}\\*"
    r = OO.pc_run("powershell -en^c %s" % _b64(payload), cfg=_cfg())
    assert r.get("ok") is not True
    assert not spawned


def test_caret_inside_decoded_payload_is_normalized(spawned):
    """解码出来的命令里再带 ^：还原副本要覆盖到解码层，不只覆盖最外层。"""
    r = OO.pc_run("powershell -enc %s" % _b64(f"d^el /f /s /q {_DOC}\\*"),
                  cfg=_cfg())
    assert r.get("ok") is not True
    assert not spawned


def test_caret_in_protected_path_is_rejected(spawned):
    """``C:\\Win^dows`` 落成 C:\\Windows —— 保护路径闸门必须看到还原后的路径。"""
    win = "C:" + os.sep + "Win^dows" + os.sep + "System32" + os.sep + "evil.dll"
    r = OO.pc_run("echo x > %s" % win, cfg=_cfg())
    assert r.get("ok") is not True
    assert "保护路径" in (r.get("error") or ""), r
    assert not spawned


def test_benign_command_still_reaches_executor(spawned):
    """阳性对照：归一不能把正常命令一起拦死，否则上面全部断言都是假绿。"""
    r = OO.pc_run("echo fisheep-ok", cfg=_cfg())
    assert r.get("ok") is True, r
    assert len(spawned) == 1, "良性命令没抵达执行器"


def test_argv_channel_keeps_caret_literal(spawned):
    """shell=False 通道 ^ 是字面字符：归一会改坏用户参数，必须原样传下去。"""
    argv = ["cmd", "/c", "echo", "a^b"]
    r = OO.pc_run_argv(argv, cfg=_cfg())
    assert r.get("ok") is True, r
    assert len(spawned) == 1
    assert list(spawned[0][0][0]) == argv, "argv 被改写了"
    assert spawned[0][1].get("shell") is False


def test_variants_always_include_the_original_text():
    """原文本必须始终在扫描集里：只扫还原副本会漏掉「^ 本身有意义」的写法。"""
    for s in ("echo a^b", "plain text", ""):
        assert OO._cmd_scan_variants(s)[0] == s


def test_variants_expand_only_in_command_vars():
    """只展开本条命令内 set 定义的变量；父进程变量留给 expandvars，不在这里猜。"""
    out = OO._cmd_scan_variants('set "a=del" && %a% /f')
    assert any("del /f" in v for v in out)
    assert all("%PATH%" in v for v in OO._cmd_scan_variants("echo %PATH%"))