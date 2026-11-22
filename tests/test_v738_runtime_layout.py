# -*- coding: utf-8 -*-
"""v1.0.738：运行时目录收进产物库——路径口径 / 迁移 / 发布剥离 回归。

用户诉求：软件生成的 cdp_profiles/ 与 ocr/ 别散在应用目录顶层，统一放 产物库/ 里，
让顶层只剩 exe + config.json + 产物库/。本文件锁住这条布局口径，防后续改动又把目录
写回顶层，或 --release 把 OCR 运行时当开发机数据一起剥掉（发布产物会静默丢 OCR）。
运行：pytest tests/test_v738_runtime_layout.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.paths as P                       # noqa: E402
import core.ocr as O                         # noqa: E402
import core.workspace_io as WIO              # noqa: E402
import core.relocate as R                    # noqa: E402
import buildlib.release as REL               # noqa: E402
import buildlib.pii as PII                   # noqa: E402
from wecom_bridge import app_targets as AT   # noqa: E402


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """workspace_root 改道到临时产物库；写入闸门换成 spy，绝不碰真实项目目录。"""
    root = tmp_path / "产物库"
    calls = []
    monkeypatch.setattr(P, "workspace_root", lambda: str(root))
    monkeypatch.setattr(P, "allow_workspace_creation",
                        lambda reason: calls.append(reason) or False)
    return root, calls


def test_runtime_subs_are_internal_segments():
    """两个段是软件内部数据：隐藏于文件树、也不在 agent 可写名单里。"""
    assert P.RUNTIME_SUBS == ("cdp_profiles", "ocr")
    assert "cdp_profiles" in P._TREE_HIDE and "ocr" in P._TREE_HIDE
    assert "cdp_profiles" not in P._ALLOWED_SUBS and "ocr" not in P._ALLOWED_SUBS


def test_runtime_root_is_readonly_and_under_workspace(ws):
    """只读口径：只回路径，不建目录、不触发首启闸门（守住 v1.0.619 归位时序）。"""
    root = ws[0]
    calls = ws[1]
    d = P.runtime_root("ocr")
    assert d == os.path.join(str(root), "ocr")
    assert not os.path.isdir(d), "只读口径不许建目录"
    assert calls == [], "只读口径不许触发首启闸门"
    with pytest.raises(ValueError):
        P.runtime_root("随便什么")


def test_runtime_dir_creates_under_workspace_and_opens_gate(ws):
    root = ws[0]
    calls = ws[1]
    d = P.runtime_dir("cdp_profiles")
    assert d == os.path.join(str(root), "cdp_profiles")
    assert os.path.isdir(d)
    assert calls and calls[0].startswith("runtime:"), calls


def test_tree_subs_hides_runtime_segments(tmp_path, monkeypatch):
    root = tmp_path / "产物库"
    for name in ("产出", "台账", "组件", "系统", "知识库", "cdp_profiles", "ocr"):
        (root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(P, "workspace_root", lambda: str(root))
    subs = P.tree_subs()
    assert "产出" in subs and "台账" in subs
    for hidden in ("组件", "系统", "知识库", "cdp_profiles", "ocr"):
        assert hidden not in subs, hidden


def test_ocr_dir_follows_workspace(ws):
    root = ws[0]
    assert O.ocr_dir() == os.path.join(str(root), "ocr")
    assert not os.path.isdir(O.ocr_dir())
    d = O.ocr_dir(create=True)
    assert d == os.path.join(str(root), "ocr") and os.path.isdir(d)


def test_browser_profile_dir_under_workspace(ws):
    root = ws[0]
    d = AT.browser_profile_dir("edge")
    assert d == os.path.join(str(root), "cdp_profiles", "edge")
    assert os.path.isdir(d)
    assert "cdp_profiles" in d, "WMI 判据仍靠路径里的这一段识别调试实例"


def test_migrate_runtime_layout_moves_legacy_top_level_dirs(tmp_path):
    app = tmp_path / "app"
    (app / "cdp_profiles" / "edge").mkdir(parents=True)
    (app / "ocr" / "cv2").mkdir(parents=True)
    (app / "config.json").write_text("{}", encoding="utf-8")
    moved = WIO.migrate_runtime_layout(data_dir=str(app))
    # v1.0.739：顶层最后一个散落文件 config.json 也一并归位
    assert sorted(moved) == ["cdp_profiles", "config.json", "ocr"], moved
    assert (app / "产物库" / "cdp_profiles" / "edge").is_dir()
    assert (app / "产物库" / "ocr" / "cv2").is_dir()
    assert (app / "产物库" / "config.json").is_file()
    assert not (app / "cdp_profiles").exists() and not (app / "ocr").exists()
    assert sorted(os.listdir(app)) == ["产物库"], "归位后顶层只剩产物库/"


def test_migrate_runtime_layout_is_idempotent_and_never_overwrites(tmp_path):
    app = tmp_path / "app"
    (app / "产物库" / "ocr").mkdir(parents=True)
    (app / "ocr").mkdir()
    (app / "ocr" / "new.bin").write_bytes(b"x")
    assert WIO.migrate_runtime_layout(data_dir=str(app)) == []
    assert (app / "ocr" / "new.bin").is_file(), "新位置已存在时绝不擅自删/覆盖旧目录"
    assert WIO.migrate_runtime_layout(data_dir=str(app)) == []


def test_migrate_moves_top_level_config_when_both_exist(tmp_path):
    """v1.0.739：两边都在 = 顶层是可证孤儿（config_path ① 分支优先，顶层永不再被读）——
    移进 产物库/config.json.legacy_top 留档，绝不覆盖新位置配置（不删用户数据）。"""
    app = tmp_path / "app"
    (app / "产物库").mkdir(parents=True)
    (app / "config.json").write_text('{"legacy": 1}', encoding="utf-8")
    (app / "产物库" / "config.json").write_text('{"current": 2}', encoding="utf-8")
    moved = WIO.migrate_runtime_layout(data_dir=str(app))
    assert moved == ["config.json(→legacy_top)"], moved
    assert (app / "产物库" / "config.json").read_text(encoding="utf-8") == '{"current": 2}'
    assert (app / "产物库" / "config.json.legacy_top").read_text(encoding="utf-8") == '{"legacy": 1}'
    assert sorted(os.listdir(app)) == ["产物库"], "归位后顶层只剩 产物库/"
    # 幂等：顶层已空，第二次跑不再动
    assert WIO.migrate_runtime_layout(data_dir=str(app)) == []


def test_release_keeps_ocr_without_lean(tmp_path, monkeypatch):
    """--release 剥开发机数据，但 OCR 是分发内容，必须留在 产物库/ocr/。"""
    monkeypatch.setattr(REL, "BASE", str(tmp_path))
    dist = tmp_path / "dist"
    (dist / "产物库" / "ocr" / "cv2").mkdir(parents=True)
    (dist / "产物库" / "对话记录").mkdir(parents=True)
    # v1.0.739：新布局 config 在 产物库/ 内（随整树剥离）；顶层那份是 739 之前的老布局
    (dist / "产物库" / "config.json").write_text("{}", encoding="utf-8")
    (dist / "config.json").write_text("{}", encoding="utf-8")
    REL.release_mode_clean(lean=False)
    assert (dist / "产物库" / "ocr" / "cv2").is_dir(), "OCR 是分发内容，非 lean 不能剥"
    assert not (dist / "产物库" / "对话记录").exists()
    assert not (dist / "产物库" / "config.json").exists(), "开发机 config 必须随产物库剥走"
    assert not (dist / "config.json").exists()
    assert sorted(os.listdir(dist)) == ["产物库"], "顶层只剩产物库/"
    stamps = list((tmp_path / "build" / "_release_excluded").iterdir())
    assert stamps and (stamps[0] / "产物库" / "config.json").is_file()
    assert (stamps[0] / "config.json").is_file(), "老布局顶层 config 也要剥"


def test_release_lean_strips_ocr(tmp_path, monkeypatch):
    """--lean 连 产物库/ocr 一起剥走，发布产物只剩 exe。"""
    monkeypatch.setattr(REL, "BASE", str(tmp_path))
    dist = tmp_path / "dist"
    (dist / "产物库" / "ocr" / "cv2").mkdir(parents=True)
    (dist / "产物库" / "对话记录").mkdir(parents=True)
    REL.release_mode_clean(lean=True)
    assert not (dist / "产物库" / "ocr").exists()
    stamps = list((tmp_path / "build" / "_release_excluded").iterdir())
    assert stamps and (stamps[0] / "产物库" / "ocr" / "cv2").is_dir()


def test_pii_skips_browser_profile_cache():
    assert "cdp_profiles" in PII._SKIP_DIR_NAMES
    assert "ocr" in PII._SKIP_DIR_NAMES


def test_relocate_still_carries_legacy_top_level_dirs():
    assert "cdp_profiles" in R._COMPANIONS
    assert "产物库" in R._COMPANIONS
    assert "config.json" in R._COMPANIONS   # v1.0.739：只为兼容 739 之前的老 dist


# ============================================================ v1.0.739：config 也进产物库
import core.config as CFG           # noqa: E402
import core.config_store as CS     # noqa: E402


def _patch_appdir(monkeypatch, app):
    monkeypatch.setattr(CFG, "_app_dir", lambda: str(app))


def test_config_path_prefers_workspace_config(tmp_path, monkeypatch):
    """① 产物库/config.json 存在 → 直接用它（顶层还留着旧文件也不分心）。"""
    _patch_appdir(monkeypatch, tmp_path)
    (tmp_path / "产物库").mkdir()
    (tmp_path / "产物库" / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert CFG.config_path() == os.path.join(str(tmp_path), "产物库", "config.json")


def test_config_path_falls_back_to_legacy_top_level(tmp_path, monkeypatch):
    """② 未迁移的老 dist：顶层 config 存在 → 先指旧位置（读兼容，迁移线程随后归位）。"""
    _patch_appdir(monkeypatch, tmp_path)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert CFG.config_path() == os.path.join(str(tmp_path), "config.json")


def test_config_path_first_run_no_side_effects(tmp_path, monkeypatch):
    """③ 首启：指向 产物库/config.json，且**读路径绝不建目录**（守 v1.0.619 归位时序）。"""
    _patch_appdir(monkeypatch, tmp_path)
    p = CFG.config_path()
    assert p == os.path.join(str(tmp_path), "产物库", "config.json")
    assert not (tmp_path / "产物库").exists(), "读路径把 产物库/ 建出来了——闸门时序被抢"
    assert not (tmp_path / ".wc_write_test").exists(), "写权限探测留了垃圾文件"


def test_config_path_unwritable_appdir_uses_fallback(tmp_path, monkeypatch):
    """④ 应用目录不可写 → %LOCALAPPDATA%\\<品牌目录> 回退（口径与 v1.0.739 之前一致）。"""
    _patch_appdir(monkeypatch, tmp_path / "no_such_dir")
    fb = tmp_path / "localapp"
    fb.mkdir()
    monkeypatch.setattr(CFG, "_fallback_dir", lambda: str(fb))
    assert CFG.config_path() == os.path.join(str(fb), "config.json")


def test_config_save_creates_workspace_parent(tmp_path, monkeypatch):
    """写路径：首启 产物库/ 尚不存在时 save_config 补建父目录（只建目录、不开闸门）。

    v1.0.739 事故：save_config 走的是 config_store 模块绑定 config_path，而该绑定
    会被其它测试裸赋值改道且从不还原（test_wecom_bind 模块级 / 曾经的
    test_security_center 函数级）——全套件跑批时本测试的写入会落进泄漏的 scratch
    目录。本测试依赖**真实解析链**（core.config.config_path 的 4 分支），故先把它
    monkeypatch 回真函数（自动还原，维持本仓「各自 patch、动态平衡」的既有约定）。
    """
    _patch_appdir(monkeypatch, tmp_path)
    monkeypatch.setattr(CS, "config_path", CFG.config_path)
    CS.save_config({"wecom": {"cdp_port": 9222}})
    p = tmp_path / "产物库" / "config.json"
    assert p.is_file(), "首启写配置没把 产物库/ 补建出来"
    import json as _j
    assert _j.loads(p.read_text(encoding="utf-8"))["wecom"]["cdp_port"] == 9222


def test_experiences_path_follows_workspace_root(tmp_path, monkeypatch):
    """v1.0.739：dirname(config_path()) 口径已死——experiences 必须跟 workspace_root
    （测试 patch core.paths.workspace_root 即可改道，不写进真实目录）。"""
    root = tmp_path / "产物库"
    monkeypatch.setattr(P, "workspace_root", lambda: str(root))
    assert CS._experiences_path() == os.path.join(str(root), "experiences.json")
