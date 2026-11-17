# -*- coding: utf-8 -*-
"""build journal 顺序 / 空号自愈 / 只读子命令 回归锁（v1.0.663 D1+D2）。

每条锁对应一次真实事故或一次「差点漏掉」：
  · C1  self_heal 回归锁：journal 最后一条是非终态、version.py 已 bump、版本未登记
        → 必须回滚到 frm，且回滚后还能重新 bump（661 空号事故：版本被消耗）。
        `bump_intent` 与旧 `bump` 两种记录都要能自愈（D2 新增的记录名）。
  · D1  只读/维护子命令锁：--current 只打印版本、--prune-release 只清旧剥离份，
        都不得触发 self_heal——自愈会**写 version.py**，用户只想看一眼版本号，
        却把版本号改掉、把排查现场破坏掉。
  · D2  写入顺序锁：bump_intent 落盘时 version.py 必须还是旧版；`bump` 落盘时已是
        新版。原顺序（先 bump 再记 journal）在这个窗口被硬杀＝空号无人认领。

隔离方式：version.py / journal 全部指到 tmp_path（monkeypatch 模块级路径），
真实 version.py 与 build/build_journal.log 一个字节都不碰。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from buildlib import journal, versioning  # noqa: E402


def _isolate(tmp_path, monkeypatch, start_version="1.0.100"):
    """把 version.py / journal 重定向到 tmp_path，返回版本文件。"""
    vp = tmp_path / "version.py"
    vp.write_text('__version__ = "%s"\n' % start_version, encoding="utf-8")
    monkeypatch.setattr(versioning, "VPATH", str(vp))
    monkeypatch.setattr(journal, "BASE", str(tmp_path))
    monkeypatch.setattr(journal, "JOURNAL",
                        str(tmp_path / "build" / "build_journal.log"))
    return vp


def _write_bumped(vp, v):
    vp.write_text('__version__ = "%s"\n' % v, encoding="utf-8")


@pytest.mark.parametrize("stage_name", ["bump", "bump_intent"])
def test_self_heal_rolls_back_unregistered_bump(tmp_path, monkeypatch, stage_name):
    """非终态 + 已 bump + 未登记 → 回滚，且版本号没被消耗、能重新 bump。"""
    vp = _isolate(tmp_path, monkeypatch)
    _write_bumped(vp, "1.0.101")
    journal.stage(stage_name, ver="1.0.101", frm="1.0.100")

    assert journal.self_heal() is True
    assert versioning.current_version() == "1.0.100"
    assert journal.last_record()["stage"] == "self_healed"
    # 回滚之后还能重新 bump（与 661 事故形态相反：空号被救回来了）
    assert versioning.bump_version() == ("1.0.100", "1.0.101")


def test_self_heal_ignores_terminal_stage(tmp_path, monkeypatch):
    """终态记录不得触发回滚——正常完成的构建不是空号。

    这里**故意**给终态记录补上 frm，使判据②③都成立：这样唯一能拦住回滚的就是
    判据①（stage 是终态）。否则「done 记录本来就没带 frm」会让这条锁恒绿，
    删掉 stage 检查也测不出来——那才是典型的假绿锁。
    """
    vp = _isolate(tmp_path, monkeypatch)
    _write_bumped(vp, "1.0.101")
    journal.stage("done", ver="1.0.101", frm="1.0.100")

    assert journal.self_heal() is False
    assert versioning.current_version() == "1.0.101"


def test_bump_intent_recorded_before_version_write(tmp_path, monkeypatch):
    """D2：intent 落盘时 version.py 还是旧版；bump 落盘时已是新版。"""
    import build as B

    _isolate(tmp_path, monkeypatch)
    seen = []
    real_stage = journal.stage

    def spy_stage(name, **kv):
        seen.append((name, versioning.current_version()))
        return real_stage(name, **kv)

    monkeypatch.setattr(journal, "stage", spy_stage)
    assert B._bump_version_with_journal() == ("1.0.100", "1.0.101")
    assert seen == [("bump_intent", "1.0.100"), ("bump", "1.0.101")]


def test_current_subcommand_skips_self_heal(monkeypatch):
    """--current 只打印版本，不得触发自愈（自愈会写 version.py）。"""
    import build as B

    calls = []
    monkeypatch.setattr(B, "_SANDBOX_VARS", ())   # 不在测试进程里清环境变量
    monkeypatch.setattr(B.journal, "self_heal", lambda: calls.append("heal"))
    monkeypatch.setattr(B.sys, "argv", ["build.py", "--current"])

    assert B.main() is None
    assert calls == []


def test_prune_release_skips_self_heal(monkeypatch):
    """--prune-release 只清旧剥离份，不得触发自愈。"""
    import build as B

    calls = []
    monkeypatch.setattr(B, "_SANDBOX_VARS", ())
    monkeypatch.setattr(B.journal, "self_heal", lambda: calls.append("heal"))
    monkeypatch.setattr(B, "prune_release_excluded", lambda auto_yes: 7)
    monkeypatch.setattr(B.sys, "argv", ["build.py", "--prune-release"])

    assert B.main() == 7
    assert calls == []
