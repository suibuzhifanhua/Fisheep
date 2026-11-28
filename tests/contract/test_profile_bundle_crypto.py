# -*- coding: utf-8 -*-
"""v1.0.756 批次 B1 行为锁：迁移包口令加密（评审 P2-2 字面补做）。

红线：加密包不得是明文 zip、不得含明文凭据；无口令 / 错口令 / 包被篡改 /
缺 cryptography 四种坏形状必须 fail-closed 拒绝；v1 明文包仍可正常导入。
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
           "security": {"permission_level": "auto"}}
    with io.open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False))
    return cfg


def _raw(path):
    with io.open(path, "rb") as f:
        return f.read()


class TestEncryptedExport:
    def test_is_not_plain_zip_and_has_no_plaintext_secrets(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        m = PB.build_bundle(out, password="correct horse")
        raw = _raw(out)
        assert raw.startswith(PB.MAGIC)
        assert not zipfile.is_zipfile(out), "加密包绝不能是明文 zip"
        assert b"SUPER_SECRET_123" not in raw
        assert b"sk-xxx" not in raw
        assert m["format"] == PB.FORMAT_ENCRYPTED
        assert m["encrypted"] is True
        assert m["kdf"] == "pbkdf2-sha256"
        assert m["iterations"] == PB.KDF_ITERATIONS

    def test_two_exports_use_different_salt(self, ws, tmp_path):
        _write_config(ws)
        a = str(tmp_path / "a.enc")
        b = str(tmp_path / "b.enc")
        PB.build_bundle(a, password="pw")
        PB.build_bundle(b, password="pw")
        assert _raw(a)[len(PB.MAGIC):len(PB.MAGIC) + 16] != \
               _raw(b)[len(PB.MAGIC):len(PB.MAGIC) + 16], "salt 必须随机，不能复用"

    def test_include_secrets_still_works_with_password(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "full.enc")
        PB.build_bundle(out, desensitize=False, password="pw")
        assert b"SUPER_SECRET_123" not in _raw(out), "加密后不得留明文"
        m = PB.inspect_bundle(out, password="pw")
        assert m["desensitized"] is False


class TestFailClosed:
    def test_inspect_without_password_rejected(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        PB.build_bundle(out, password="pw")
        with pytest.raises(PB.BundleError):
            PB.inspect_bundle(out)
        with pytest.raises(PB.BundleError):
            PB.inspect_bundle(out, password="")

    def test_wrong_password_rejected(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        PB.build_bundle(out, password="right")
        with pytest.raises(PB.BundleError):
            PB.inspect_bundle(out, password="wrong")

    def test_apply_without_password_rejected(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        PB.build_bundle(out, password="pw")
        with pytest.raises(PB.BundleError):
            PB.apply_bundle(out, dry_run=True)

    def test_tampered_ciphertext_rejected(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        PB.build_bundle(out, password="pw")
        raw = bytearray(_raw(out))
        raw[-20] ^= 0x01
        with io.open(out, "wb") as f:
            f.write(bytes(raw))
        with pytest.raises(PB.BundleError):
            PB.inspect_bundle(out, password="pw")

    def test_missing_crypto_never_silently_degrades(self, ws, tmp_path, monkeypatch):
        """缺 cryptography 时必须抛 BundleError，且**不能产出明文 zip 文件**。"""
        _write_config(ws)
        out = str(tmp_path / "should-not-exist.enc")

        def boom():
            raise PB.BundleError("模拟缺 cryptography")

        monkeypatch.setattr(PB, "_require_crypto", boom)
        with pytest.raises(PB.BundleError):
            PB.build_bundle(out, password="pw")
        assert not os.path.exists(out), "fail-closed 不得偷偷落下明文包"


class TestRoundTrip:
    def test_inspect_and_dry_run(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        PB.build_bundle(out, password="pw")
        m = PB.inspect_bundle(out, password="pw")
        assert m["files"] and m["format"] == PB.FORMAT_ENCRYPTED
        r = PB.apply_bundle(out, dry_run=True, password="pw")
        assert r["dry_run"] is True and r["applied"]

    def test_import_restores_config_with_backup(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "bundle.enc")
        PB.build_bundle(out, password="pw")
        cfg_path = os.path.join(ws, "config.json")
        os.remove(cfg_path)
        r = PB.apply_bundle(out, password="pw")
        assert os.path.isfile(cfg_path), "导入应恢复被删的配置"
        assert "config.json" in r["applied"]
        with io.open(cfg_path, encoding="utf-8") as f:
            restored = json.load(f)
        assert PB.REDACTED in json.dumps(restored), "默认脱敏仍生效"


class TestBackwardCompat:
    def test_plain_v1_bundle_still_works(self, ws, tmp_path):
        _write_config(ws)
        out = str(tmp_path / "plain.zip")
        m = PB.build_bundle(out)
        assert zipfile.is_zipfile(out)
        assert m["format"] == PB.FORMAT
        assert PB.inspect_bundle(out)["files"] == m["files"]
        assert PB.apply_bundle(out, dry_run=True)["applied"]

    def test_v1_bundle_with_password_arg_ignores_it(self, ws, tmp_path):
        """v1 明文包即使误传口令也应能读（口令只用于 v2 容器）。"""
        _write_config(ws)
        out = str(tmp_path / "plain.zip")
        PB.build_bundle(out)
        assert PB.inspect_bundle(out, password="irrelevant")["format"] == PB.FORMAT


class TestDependencyPin:
    def test_cryptography_is_pinned_in_requirements(self):
        with io.open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            req = f.read()
        assert "cryptography==" in req

    def test_spec_collects_cryptography(self):
        with io.open(os.path.join(ROOT, "Fisheep.spec"), encoding="utf-8") as f:
            spec = f.read()
        assert "collect_all('cryptography')" in spec
