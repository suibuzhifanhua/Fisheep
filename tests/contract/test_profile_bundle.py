# -*- coding: utf-8 -*-
"""tests/contract/test_profile_bundle.py — 换机迁移包的**行为锁**（评审 P2-2）。

锁住四条红线：敏感值不进包、包被篡改必须拒、导入前必须备份、路径穿越必须拒。
"""
import io
import json
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import paths  # noqa: E402
from core import profile_bundle as PB  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(paths, "workspace_root", lambda: root)
    return root


def _write_config(ws):
    cfg = {"api_token": "SUPER_SECRET_123",
           "models": {"default": "qwen", "keys": {"openai_api_key": "sk-xxx"}},
           "security": {"permission_level": "auto"},
           "nested": [{"password": "hunter2"}]}
    with io.open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False))
    return cfg


def _payload(zip_path, entry):
    with zipfile.ZipFile(zip_path) as z:
        return z.read(entry).decode("utf-8")


def test_export_redacts_secrets(ws, tmp_path):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    PB.build_bundle(out)
    data = _payload(out, "payload/config.json")
    assert "SUPER_SECRET_123" not in data
    assert "sk-xxx" not in data and "hunter2" not in data
    assert PB.REDACTED in data
    assert "permission_level" in data, "脱敏不能破坏键结构"


def test_include_secrets_keeps_values(ws, tmp_path):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    PB.build_bundle(out, desensitize=False)
    assert "SUPER_SECRET_123" in _payload(out, "payload/config.json")


def test_manifest_has_integrity_fields(ws, tmp_path):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    m = PB.build_bundle(out)
    assert m["format"] == PB.FORMAT and m["desensitized"] is True
    f = m["files"][0]
    assert len(f["sha256"]) == 64 and f["size"] > 0
    assert PB.inspect_bundle(out)["format"] == PB.FORMAT


def test_tampered_payload_is_rejected(ws, tmp_path):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    PB.build_bundle(out)
    with zipfile.ZipFile(out) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(out, "w") as z:
        for info, data in items:
            if info.filename == "payload/config.json":
                data = data.replace(b"permission_level", b"permission_levEL")
            z.writestr(info.filename, data)
    with pytest.raises(PB.BundleError):
        PB.inspect_bundle(out)
    with pytest.raises(PB.BundleError):
        PB.apply_bundle(out)


def test_directory_items_are_collected(ws, tmp_path):
    os.makedirs(os.path.join(ws, "知识库", "课件"))
    os.makedirs(os.path.join(ws, "技能"))
    io.open(os.path.join(ws, "知识库", "课件", "a.md"), "w",
            encoding="utf-8").write("hello")
    io.open(os.path.join(ws, "技能", "s.json"), "w",
            encoding="utf-8").write("{}")
    out = str(tmp_path / "bundle.zip")
    m = PB.build_bundle(out)
    entries = {f["entry"] for f in m["files"]}
    assert "payload/知识库/课件/a.md" in entries
    assert "payload/技能/s.json" in entries


def test_import_restores_and_backs_up(ws, tmp_path):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    PB.build_bundle(out)
    with io.open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"api_token": "CHANGED"}')
    r = PB.apply_bundle(out)
    assert r["applied"] == ["config.json"]
    assert "CHANGED" not in io.open(os.path.join(ws, "config.json"),
                                    encoding="utf-8").read()
    assert r["backup_dir"] and os.path.isdir(r["backup_dir"])
    backup = os.path.join(r["backup_dir"], "config.json")
    assert os.path.isfile(backup) and "CHANGED" in io.open(backup, encoding="utf-8").read()


def test_dry_run_does_not_write(ws, tmp_path):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    PB.build_bundle(out)
    before = io.open(os.path.join(ws, "config.json"), encoding="utf-8").read()
    with io.open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"api_token": "CHANGED"}')
    r = PB.apply_bundle(out, dry_run=True)
    assert r["dry_run"] is True and r["backup_dir"] is None
    assert io.open(os.path.join(ws, "config.json"), encoding="utf-8").read() != before
    assert not os.path.exists(os.path.join(ws, PB.BACKUP_DIRNAME))


def test_zip_slip_is_rejected(ws, tmp_path):
    out = str(tmp_path / "bad.zip")
    m = {"format": PB.FORMAT, "created_at": "x", "app_version": "x",
         "desensitized": True,
         "files": [{"name": "config", "entry": "payload/../../evil.txt",
                    "sha256": PB._sha256(b"x"), "size": 1}]}
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("payload/../../evil.txt", b"x")
        z.writestr(PB.MANIFEST_NAME, json.dumps(m))
    with pytest.raises(PB.BundleError):
        PB.apply_bundle(out)
    assert not os.path.exists(os.path.join(os.path.dirname(ws), "evil.txt"))


def test_missing_items_are_skipped(ws, tmp_path):
    out = str(tmp_path / "bundle.zip")
    m = PB.build_bundle(out)
    assert m["files"] == []
    assert PB.inspect_bundle(out)["files"] == []


def test_cli_export_inspect_import(ws, tmp_path, capsys):
    _write_config(ws)
    out = str(tmp_path / "bundle.zip")
    assert PB.main(["export", out]) == 0
    assert PB.main(["inspect", out]) == 0
    assert PB.main(["import", out, "--dry-run"]) == 0
    assert "脱敏" in capsys.readouterr().out
