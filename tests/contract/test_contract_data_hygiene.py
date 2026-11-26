# -*- coding: utf-8 -*-
"""tests/contract/test_contract_data_hygiene.py — 开发机数据卫生行为锁。

四处「只写不清 / 路径写错 / 词表够不着」的缺陷，改回坏形状必红：
  · gc 看不见 build/github_mirror*（镜像 v1.0.754 迁出 build/ 后旧址无人收）
    与 build/_release_excluded（PII，能无声涨到几个 GB）；
  · 备份把 产物库/ocr（232MB 可再生运行时）来回搬，而它每轮从构建 venv 重同步；
  · memory_ops 的 MEM_DIR 多带一层「产物库」，记忆落进 产物库/产物库/记忆/；
  · 脱敏词表只认完整个人路径前缀，够不着 pytest basetemp 里的裸用户名。

同时反向锁住**不能顺手删的用户数据**：_release_excluded / _chat_archive 只列不删，
备份仍须覆盖 会话 / 对话记录 / 知识库 / 产出 / config.json。
"""
import io
import os
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from buildlib import backup as BK  # noqa: E402
from buildlib import gc  # noqa: E402
from buildlib import mask_gate as MG  # noqa: E402
from services import memory_ops as MO  # noqa: E402


def _mk(path, content="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with io.open(str(path), "w", encoding="utf-8") as f:
        f.write(content)
    return str(path)


# ---------------------------------------------------------------- gc S4 旧镜像
class TestLegacyMirror:
    def test_old_mirror_and_timestamped_variants_removed(self, tmp_path):
        _mk(tmp_path / "build" / "github_mirror_stale" / "core" / "a.py")
        _mk(tmp_path / "build" / "github_mirror_stale_20260101_000000" / "b.py")
        _mk(tmp_path / "build" / "github_mirror" / "c.py")
        removed, freed = gc.prune_legacy_mirror(base_dir=str(tmp_path))
        assert removed == 3 and freed > 0
        assert not (tmp_path / "build" / "github_mirror_stale").exists()
        assert not (tmp_path / "build" / "github_mirror").exists()

    def test_unrelated_build_dirs_untouched(self, tmp_path):
        for d in ("_dist_backup", "_rollback", "_chat_archive", "Fisheep", "op_trace_manual1"):
            _mk(tmp_path / "build" / d / "keep.txt")
        gc.prune_legacy_mirror(base_dir=str(tmp_path))
        for d in ("_dist_backup", "_rollback", "_chat_archive", "Fisheep", "op_trace_manual1"):
            assert (tmp_path / "build" / d / "keep.txt").exists(), d

    def test_readonly_git_objects_do_not_defeat_the_delete(self, tmp_path):
        p = _mk(tmp_path / "build" / "github_mirror_stale" / ".git" / "objects" / "ab")
        os.chmod(p, stat.S_IREAD)
        removed, _ = gc.prune_legacy_mirror(base_dir=str(tmp_path))
        assert removed == 1
        assert not (tmp_path / "build" / "github_mirror_stale").exists()

    def test_missing_build_dir_noop(self, tmp_path):
        assert gc.prune_legacy_mirror(base_dir=str(tmp_path)) == (0, 0)

    def test_listed_as_candidate(self, tmp_path):
        _mk(tmp_path / "build" / "github_mirror_stale" / "a.py")
        names = [n for _t, n, _s in gc._gc_candidates(str(tmp_path))]
        assert "build/github_mirror_stale" in names


# ---------------------------------------------------------------- gc G5 只列不删
class TestReleaseExcludedIsReportOnly:
    def test_report_counts_and_sizes(self, tmp_path):
        _mk(tmp_path / "build" / "_release_excluded" / "20260101_000000" / "config.json", "abc")
        _mk(tmp_path / "build" / "_release_excluded" / "20260202_000000" / "config.json", "abcd")
        n, sz = gc.release_excluded_report(str(tmp_path))
        assert n == 2 and sz == 7

    def test_gc_never_deletes_release_excluded_or_chat_archive(self, tmp_path):
        rel = tmp_path / "build" / "_release_excluded" / "20260101_000000" / "config.json"
        chat = tmp_path / "build" / "_chat_archive" / "20260101_000000" / "会话" / "a.json"
        _mk(rel, "secret"); _mk(chat, "secret")
        gc.gc_after_build(base_dir=str(tmp_path))
        gc.run_gc(base_dir=str(tmp_path), auto_yes=True)
        assert rel.exists() and chat.exists(), "开发机数据/用户归档只列不删"

    def test_not_a_delete_candidate(self, tmp_path):
        _mk(tmp_path / "build" / "_release_excluded" / "s1" / "config.json")
        names = [n for _t, n, _s in gc._gc_candidates(str(tmp_path))]
        assert not any("_release_excluded" in n for n in names)

    def test_absent_dir_reports_zero(self, tmp_path):
        assert gc.release_excluded_report(str(tmp_path)) == (0, 0)


# ---------------------------------------------------------------- 备份采集面
class TestBackupIgnore:
    def test_regenerable_runtimes_skipped(self):
        names = ["组件", "cdp_profiles", "ocr", "知识库", "产出"]
        assert BK._backup_ignore(os.path.join("dist", "产物库"), names) == {
            "组件", "cdp_profiles", "ocr"}

    def test_user_data_still_backed_up(self):
        names = ["会话", "对话记录", "知识库", "产出", "技能", "config.json", "断点", "日报"]
        assert BK._backup_ignore(os.path.join("dist", "产物库"), names) == set()

    def test_ocr_only_skipped_inside_workspace(self):
        # 用户的产出目录里真有个叫 ocr 的文件夹时不能误伤
        assert BK._backup_ignore(os.path.join("dist", "产物库", "产出"), ["ocr"]) == set()

    def test_gc_cleans_the_stale_ocr_backup_copy(self, tmp_path):
        bak = tmp_path / "build" / "_dist_backup"
        _mk(bak / "产物库" / "ocr" / "cv2" / "big.pyd")
        _mk(bak / "产物库" / "知识库" / "keep.txt")
        removed, freed = gc.prune_unbacked_copies(backup_dir=str(bak))
        assert removed == 1 and freed > 0
        assert not (bak / "产物库" / "ocr").exists()
        assert (bak / "产物库" / "知识库" / "keep.txt").exists()


# ---------------------------------------------------------------- 记忆路径
class TestMemoryPath:
    def test_path_has_single_workspace_segment(self, tmp_path, monkeypatch):
        monkeypatch.setattr(MO, "workspace_root", lambda: str(tmp_path / "产物库"))
        p = MO.memory_path()
        assert p == os.path.join(str(tmp_path / "产物库"), "记忆", "MEMORY.md")
        assert "产物库" not in os.path.relpath(p, str(tmp_path / "产物库")).split(os.sep)[:-1]

    def test_legacy_double_nested_file_is_migrated(self, tmp_path, monkeypatch):
        root = tmp_path / "产物库"
        monkeypatch.setattr(MO, "workspace_root", lambda: str(root))
        old = _mk(root / "产物库" / "记忆" / "MEMORY.md", "- 旧记忆\n")
        assert MO.memory_text() == "- 旧记忆"
        assert not os.path.exists(old)
        assert (root / "记忆" / "MEMORY.md").exists()
        assert not (root / "产物库").exists(), "迁完的空壳目录应顺手收掉"

    def test_migration_never_overwrites_existing_memory(self, tmp_path, monkeypatch):
        root = tmp_path / "产物库"
        monkeypatch.setattr(MO, "workspace_root", lambda: str(root))
        old = _mk(root / "产物库" / "记忆" / "MEMORY.md", "- 旧\n")
        _mk(root / "记忆" / "MEMORY.md", "- 新\n")
        assert MO.memory_text() == "- 新"
        assert os.path.exists(old), "新址已有内容时旧文件原地保留，绝不覆盖"

    def test_nonempty_legacy_parent_is_kept(self, tmp_path, monkeypatch):
        root = tmp_path / "产物库"
        monkeypatch.setattr(MO, "workspace_root", lambda: str(root))
        _mk(root / "产物库" / "记忆" / "MEMORY.md", "- 旧\n")
        _mk(root / "产物库" / "别的" / "data.txt", "keep")
        MO.memory_path()
        assert (root / "产物库" / "别的" / "data.txt").exists()

    def test_append_lands_in_the_right_place(self, tmp_path, monkeypatch):
        root = tmp_path / "产物库"
        monkeypatch.setattr(MO, "workspace_root", lambda: str(root))
        assert MO._append_memory("用户偏好表格输出") is True
        assert (root / "记忆" / "MEMORY.md").exists()
        assert not (root / "产物库").exists()


# ---------------------------------------------------------------- 裸用户名脱敏
class TestBareUsernameMask:
    def test_mask_and_gate_are_added_as_a_pair(self):
        sample = "%TEMP%/" + MG.W_PYTEST_USER + "/pytest-12/"
        assert any(rx.search(sample) for rx, _rep in MG.MASK_RULES), "词表缺 MASK 规则"
        assert any(rx.search(sample) for _lab, rx in MG.GATE_PATTERNS), "词表缺 GATE 模式"

    def test_masking_rewrites_it_and_gate_then_passes(self):
        sample = "%TEMP%/" + MG.W_PYTEST_USER + "/pytest-12/"
        out = sample
        for rx, rep in MG.MASK_RULES:
            out = rx.sub(rep, out)
        assert MG.W_PYTEST_USER not in out
        assert "pytest-of-user" in out
        assert not any(rx.search(out) for _lab, rx in MG.GATE_PATTERNS)

    def test_gate_does_not_fire_on_unrelated_a1_text(self):
        pat = dict(MG.GATE_PATTERNS)["个人用户名"]
        for benign in ("A1", "0xA1B2", "SHA1 A1A1", "pytest-of-user", "单元格 A1"):
            assert not pat.search(benign), benign

    def test_pytest_ini_carries_no_real_username(self):
        with io.open(os.path.join(ROOT, "pytest.ini"), encoding="utf-8") as f:
            assert MG.W_PYTEST_USER not in f.read()