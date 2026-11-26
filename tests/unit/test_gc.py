# -*- coding: utf-8 -*-
"""v1.0.743：buildlib.gc —— 构建收尾垃圾回收的单元测试（路径全部注入 tmp_path）。

覆盖五类判定口径：
  S2   _dist_backup 顶层目录孤儿（dist 无同名源=删；有源=留；文件类条目=不碰）
  S2b  不再备份段副本（产物库/组件、产物库/cdp_profiles）
  S3   _rollback 非机制残留（只保留当前品牌名 exe）
  G3   live 档案可再生缓存（删 Cache/GPUCache 等，登录态 Cookies/Local State 保留）
  G4   _chat_archive 用户数据（只列不删）
  run_gc 行为（无候选=0；未确认=dry-run 零删除；auto_yes=执行）
"""
import os

from buildlib import gc


def _mk(path, content="x"):
    """建父目录 + 落一个文件，返回该文件路径。"""
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        f.write(content)
    return str(path)


class TestS2Orphans:
    def test_orphan_dir_removed_src_kept_files_untouched(self, tmp_path):
        dist = tmp_path / "dist"
        bak = tmp_path / "build" / "_dist_backup"
        _mk(dist / "产物库" / "a.txt")                    # dist 有源 → 备份同名条目保留
        _mk(bak / "产物库" / "a.txt")
        _mk(bak / "cdp_profiles" / "edge" / "blob.bin")  # dist 无同名源 → 孤儿，删
        _mk(bak / "config.json")                          # 文件类条目 → 一律不碰
        removed, freed = gc.prune_backup_orphans(dist_dir=str(dist), backup_dir=str(bak))
        assert removed == 1
        assert freed > 0
        assert not (bak / "cdp_profiles").exists()
        assert (bak / "产物库" / "a.txt").exists()
        assert (bak / "config.json").exists()

    def test_missing_dirs_noop(self, tmp_path):
        assert gc.prune_backup_orphans(dist_dir=str(tmp_path / "no"),
                                       backup_dir=str(tmp_path / "none")) == (0, 0)


class TestS2bUnbacked:
    def test_both_segments_pruned_still_backed_kept(self, tmp_path):
        bak = tmp_path / "build" / "_dist_backup"
        _mk(bak / "产物库" / "组件" / "node" / "x")
        _mk(bak / "产物库" / "cdp_profiles" / "edge" / "y")
        _mk(bak / "产物库" / "知识库" / "z.txt")   # 仍备份的段 → 保留
        removed, freed = gc.prune_unbacked_copies(backup_dir=str(bak))
        assert removed == 2
        assert freed > 0
        assert not (bak / "产物库" / "组件").exists()
        assert not (bak / "产物库" / "cdp_profiles").exists()
        assert (bak / "产物库" / "知识库" / "z.txt").exists()

    def test_absent_noop(self, tmp_path):
        assert gc.prune_unbacked_copies(backup_dir=str(tmp_path / "none")) == (0, 0)


class TestS3Rollback:
    def test_keeps_current_brand_only(self, tmp_path):
        rb = tmp_path / "build" / "_rollback"
        _mk(rb / "Fisheep.exe", "cur")
        _mk(rb / "Lingban.exe")
        _mk(rb / "WeComCompanion.exe")
        _mk(rb / "Fisheep_1.0.100.exe")
        removed, freed = gc.prune_rollback_junk(rollback_dir=str(rb), keep_name="Fisheep.exe",
                                               keep_names=set())   # hermetic：不读真 versions.json
        assert removed == 3
        assert freed > 0
        assert sorted(os.listdir(str(rb))) == ["Fisheep.exe"]

    def test_versioned_names_parsing(self, tmp_path):
        import json
        _mk(tmp_path / "产物库" / "版本" / "versions.json",
            json.dumps([{"version": "1.0.7", "exe": "build/_rollback/Fisheep_1.0.7.exe"},
                       {"version": "1.0.6", "exe": ""},      # 已 prune → 不算引用
                       "not-a-dict"]))
        assert gc._versioned_exe_names(base_dir=str(tmp_path)) == {"Fisheep_1.0.7.exe"}

    def test_versioned_names_missing_file(self, tmp_path):
        assert gc._versioned_exe_names(base_dir=str(tmp_path / "none")) == set()

    def test_version_referenced_exe_kept(self, tmp_path):
        """v1.0.745：versions.json 引用的版本化 exe 是版本管理回滚的机制文件 → 保留。"""
        rb = tmp_path / "build" / "_rollback"
        _mk(rb / "Fisheep.exe")
        _mk(rb / "Fisheep_1.0.7.exe")      # 被引用 → 留
        _mk(rb / "Fisheep_1.0.6.exe")      # 未被引用 → 删
        _mk(rb / "Lingban.exe")            # 旧品牌名 → 删
        removed, _ = gc.prune_rollback_junk(rollback_dir=str(rb), keep_name="Fisheep.exe",
                                           keep_names={"Fisheep_1.0.7.exe"})
        assert removed == 2
        assert sorted(os.listdir(str(rb))) == ["Fisheep.exe", "Fisheep_1.0.7.exe"]

    def test_keep_name_injected(self, tmp_path):
        rb = tmp_path / "build" / "_rollback"
        _mk(rb / "X.exe")
        _mk(rb / "Y.exe")
        removed, _ = gc.prune_rollback_junk(rollback_dir=str(rb), keep_name="Y.exe")
        assert removed == 1
        assert (rb / "Y.exe").exists()
        assert not (rb / "X.exe").exists()


class TestG3LiveCaches:
    def test_caches_removed_login_state_kept(self, tmp_path):
        dist = tmp_path / "dist"
        prof = dist / "产物库" / "cdp_profiles"
        _mk(prof / "edge" / "Default" / "Cache" / "idx")
        _mk(prof / "edge" / "Default" / "Service Worker" / "sw")
        _mk(prof / "edge" / "GPUPersistentCache" / "g")
        _mk(prof / "edge" / "Cookies", "login")
        _mk(prof / "edge" / "Local State", "state")
        freed = gc.gc_live_caches(dist_dir=str(dist))
        assert freed > 0
        assert not (prof / "edge" / "Default" / "Cache").exists()
        assert not (prof / "edge" / "Default" / "Service Worker").exists()
        assert not (prof / "edge" / "GPUPersistentCache").exists()
        assert (prof / "edge" / "Cookies").exists()
        assert (prof / "edge" / "Local State").exists()

    def test_no_profiles_noop(self, tmp_path):
        assert gc.gc_live_caches(dist_dir=str(tmp_path)) == 0


class TestG4AndRunGc:
    def test_chat_archive_lists_only(self, tmp_path):
        ch = tmp_path / "build" / "_chat_archive"
        _mk(ch / "s1" / "chat.json")
        n, sz = gc.chat_archive_report(chat_dir=str(ch))
        assert n == 1
        assert sz > 0
        assert (ch / "s1" / "chat.json").exists()   # 只列不删

    def test_run_gc_no_candidates_returns_zero(self, tmp_path):
        assert gc.run_gc(base_dir=str(tmp_path), auto_yes=True) == 0

    def test_run_gc_unconfirmed_deletes_nothing(self, tmp_path, monkeypatch):
        (tmp_path / "dist").mkdir()                  # dist 需存在，S2 孤儿判定才成立
        bak = tmp_path / "build" / "_dist_backup"
        _mk(bak / "cdp_profiles" / "edge" / "blob.bin")   # 候选（S2 孤儿）
        monkeypatch.setattr("builtins.input",
                            lambda _p="": (_ for _ in ()).throw(EOFError))
        assert gc.run_gc(base_dir=str(tmp_path), auto_yes=False) == 0
        assert (bak / "cdp_profiles").exists()       # 未确认 → 保持现状

    def test_run_gc_auto_yes_executes(self, tmp_path):
        (tmp_path / "dist").mkdir()
        bak = tmp_path / "build" / "_dist_backup"
        _mk(bak / "cdp_profiles" / "edge" / "blob.bin")
        _mk(tmp_path / "build" / "_rollback" / "Lingban.exe")
        removed = gc.run_gc(base_dir=str(tmp_path), auto_yes=True)
        assert removed == 2                            # S2 孤儿 + S3 残留
        assert not (bak / "cdp_profiles").exists()
        assert not (tmp_path / "build" / "_rollback" / "Lingban.exe").exists()


class TestGcAfterBuild:
    def test_full_cycle(self, tmp_path):
        """gc_after_build 一轮清掉全部五类（G4 只报告）。"""
        base = tmp_path
        (base / "dist").mkdir()
        _mk(base / "build" / "_dist_backup" / "cdp_profiles" / "edge" / "blob")   # S2
        _mk(base / "build" / "_dist_backup" / "产物库" / "组件" / "node")        # S2b-1
        _mk(base / "build" / "_dist_backup" / "产物库" / "cdp_profiles" / "x")  # S2b-2
        _mk(base / "build" / "_rollback" / "Fisheep.exe", "cur")                  # S3 保留
        _mk(base / "build" / "_rollback" / "Lingban.exe")                        # S3 删
        _mk(base / "dist" / "产物库" / "cdp_profiles" / "chrome" / "ShaderCache" / "s")  # G3
        gc.gc_after_build(base_dir=str(base))
        assert not (base / "build" / "_dist_backup" / "cdp_profiles").exists()
        assert not (base / "build" / "_dist_backup" / "产物库" / "组件").exists()
        assert not (base / "build" / "_dist_backup" / "产物库" / "cdp_profiles").exists()
        assert (base / "build" / "_rollback" / "Fisheep.exe").exists()
        assert not (base / "build" / "_rollback" / "Lingban.exe").exists()
        assert not (base / "dist" / "产物库" / "cdp_profiles" / "chrome" / "ShaderCache").exists()
