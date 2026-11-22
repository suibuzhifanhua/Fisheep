# -*- coding: utf-8 -*-
"""version_store tests (v1.0.358): record/prune/list/switch/pending-switch.

All filesystem operations run inside a tmp root patched over VS._APP_ROOT;
git is disabled by patching _git_exe -> None (source restore skipped).

v1.0.691（改名 Fisheep）：文件名断言全部改走 core.branding 常量
（EXE_NAME=当前 exe 名，APP_ID=备份文件名基名）——不再硬编码任何一代旧名，
改名时只需改 branding 一处。另新增 test_apply_pending_switch_legacy_name：
改名过渡期残留的**旧名** .next 也要能完成排队切换（见 version_store
apply_pending_switch 的 v1.0.691 注释）。
"""
import json
import os

import pytest

from core.branding import APP_ID, EXE_NAME, ALL_EXE_NAMES
from services import version_store as VS

# 一个真实存在的旧名（第 2 代 Lingban），用于锁住"旧名 .next 残留"行为
_LEGACY_EXE = ALL_EXE_NAMES[1] if ALL_EXE_NAMES[1] != EXE_NAME else ALL_EXE_NAMES[0]


@pytest.fixture()
def vs_root(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    (root / "version.py").write_text('VERSION = "v1.0.999"\n', encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist" / EXE_NAME).write_bytes(b"EXE-999")
    (root / "build" / "_rollback").mkdir(parents=True)
    monkeypatch.setattr(VS, "_APP_ROOT", str(root))
    monkeypatch.setattr(VS, "_git_exe", lambda: None)
    return root


def test_record_and_list(vs_root):
    e1 = VS.record_version(note="first")
    assert e1["version"] == "v1.0.999"
    assert os.path.isfile(os.path.join(vs_root, "build", "_rollback",
                                        "%s_v1.0.999.exe" % APP_ID))
    # bump version + new exe -> second record, newest first
    (vs_root / "version.py").write_text('VERSION = "v1.0.1000"\n', encoding="utf-8")
    (vs_root / "dist" / EXE_NAME).write_bytes(b"EXE-1000")
    VS.record_version(note="second")
    d = VS.list_versions()
    assert [r["version"] for r in d["versions"]] == ["v1.0.1000", "v1.0.999"]
    assert d["current"] == "v1.0.1000"
    # dedupe: record same version again -> no duplicate
    VS.record_version(note="again")
    assert len(VS.list_versions()["versions"]) == 2
    print("PASS record/dedupe/list")


def test_prune_keeps_last_N(vs_root):
    (vs_root / "dist" / EXE_NAME).write_bytes(b"EXE")
    for i in range(10):
        (vs_root / "version.py").write_text('VERSION = "v1.0.9%02d"\n' % i, encoding="utf-8")
        VS.record_version(note="n%02d" % i)
        # fake exe backup per record so prune has something to delete
        recs = VS._load()
        p = os.path.join(vs_root, "build", "_rollback", "%s_v1.0.9%02d.exe" % (APP_ID, i))
        with open(p, "wb") as f:
            f.write(b"X")
        recs[0]["exe"] = "build/_rollback/%s_v1.0.9%02d.exe" % (APP_ID, i)
        VS._save(recs)
    d = VS.list_versions()
    keep = VS._KEEP   # v1.0.373：8→3（回滚备份瘦身，用户确认）
    assert len(d["versions"]) == keep, len(d["versions"])
    assert d["versions"][0]["version"] == "v1.0.909"
    # exe backups of pruned records are deleted (earliest ones gone)
    assert not os.path.exists(os.path.join(vs_root, "build", "_rollback",
                                           "%s_v1.0.900.exe" % APP_ID))
    assert os.path.exists(os.path.join(vs_root, "build", "_rollback",
                                       "%s_v1.0.909.exe" % APP_ID))
    print("PASS prune to last %d + exe cleanup" % keep)


def test_switch_restores_exe(vs_root):
    (vs_root / "dist" / EXE_NAME).write_bytes(b"EXE-V1")
    (vs_root / "version.py").write_text('VERSION = "v1.0.1"\n', encoding="utf-8")
    VS.record_version(note="v1")
    (vs_root / "dist" / EXE_NAME).write_bytes(b"EXE-V2")
    (vs_root / "version.py").write_text('VERSION = "v1.0.2"\n', encoding="utf-8")
    VS.record_version(note="v2")
    r = VS.switch_version("v1.0.1")
    assert r["ok"], r
    assert (vs_root / "dist" / EXE_NAME).read_bytes() == b"EXE-V1"
    # simulate the source-side switch (git disabled here, so version.py is ours)
    (vs_root / "version.py").write_text('VERSION = "v1.0.1"\n', encoding="utf-8")
    # switching to current version is rejected
    r2 = VS.switch_version("v1.0.1")
    assert not r2["ok"]
    print("PASS switch restores exe + same-version rejected")


def test_apply_pending_switch(vs_root):
    # simulate a staged swap left by switch while the exe was locked
    (vs_root / "dist" / EXE_NAME).write_bytes(b"OLD")
    (vs_root / "dist" / (EXE_NAME + ".next")).write_bytes(b"NEW")
    (vs_root / "dist" / ".version_switch").write_text('{"version":"v1.0.1"}', encoding="utf-8")
    assert VS.apply_pending_switch() is True
    assert (vs_root / "dist" / EXE_NAME).read_bytes() == b"NEW"
    assert not os.path.exists(vs_root / "dist" / (EXE_NAME + ".next"))
    assert not os.path.exists(vs_root / "dist" / ".version_switch")
    # no marker -> no-op
    assert VS.apply_pending_switch() is False
    print("PASS pending switch applied at startup")


class TestStageUntrackedSource:
    """v1.0.744：743 漏文件修复——`commit -am` 只 stage tracked 改动，新增源文件
    （?? 未跟踪）会被永久漏出 release 提交；_stage_untracked_source 显式补 stage。"""

    def test_stages_all_untracked_source(self, vs_root, monkeypatch):
        calls = []

        def fake_git(*args):
            if args[:2] == ("ls-files", "--others"):
                return "buildlib/gc.py\0tests/test_gc.py\0"   # -z 输出是 NUL 分隔
            if args[:2] == ("add", "--"):
                calls.append(args[2])
                return "ok"
            return ""

        monkeypatch.setattr(VS, "_git", fake_git)
        assert VS._stage_untracked_source() == 2
        assert calls == ["buildlib/gc.py", "tests/test_gc.py"]

    def test_no_untracked_adds_nothing(self, vs_root, monkeypatch):
        calls = []

        def fake_git(*args):
            calls.append(args)
            return "" if args[:2] == ("ls-files", "--others") else "ok"

        monkeypatch.setattr(VS, "_git", fake_git)
        assert VS._stage_untracked_source() == 0
        assert all(a[:2] != ("add", "--") for a in calls)

    def test_git_failure_returns_zero(self, vs_root, monkeypatch):
        # git 不可用（ls-files 返回 None）→ 0，不抛（best-effort 语义）
        monkeypatch.setattr(VS, "_git", lambda *a: None)
        assert VS._stage_untracked_source() == 0


def test_apply_pending_switch_legacy_name(vs_root):
    # v1.0.691（改名）：改名过渡期，老版本排队切换时可能残留**旧名** .next
    # （如 Lingban.exe.next）——新名找不到时必须回退认旧名，否则那次排队
    # 的切换会被静默丢弃（marker 被删、exe 没换）。
    (vs_root / "dist" / EXE_NAME).write_bytes(b"OLD")
    (vs_root / "dist" / (_LEGACY_EXE + ".next")).write_bytes(b"LEGACY-NEW")
    (vs_root / "dist" / ".version_switch").write_text('{"version":"v1.0.1"}', encoding="utf-8")
    assert VS.apply_pending_switch() is True
    assert (vs_root / "dist" / EXE_NAME).read_bytes() == b"LEGACY-NEW"
    assert not os.path.exists(vs_root / "dist" / (_LEGACY_EXE + ".next"))
    assert not os.path.exists(vs_root / "dist" / ".version_switch")
    print("PASS legacy-name .next applied at startup")
