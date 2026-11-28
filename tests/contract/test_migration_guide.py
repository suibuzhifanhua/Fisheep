# -*- coding: utf-8 -*-
"""v1.0.756 批次 B2 行为锁：首次启动解密失败迁移引导（评审 P2-2 字面补做）。

红线：解密失败必须登记 pending（不再只留日志）；③ 弱保护未显式勾选一律拒绝；
三选一任一分支都先备份原配置且清 pending；独立页不依赖 18 个前端 js。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import paths  # noqa: E402
from core import migration_guide as MG  # noqa: E402
from core import secrets as S  # noqa: E402


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    MG.reset()
    monkeypatch.setattr(paths, "workspace_root", lambda: str(tmp_path))
    yield
    MG.reset()


def _cfg(tmp_path, token="enc:v1:BROKEN"):
    p = os.path.join(str(tmp_path), "config.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"api_token": token, "ui": {"theme": "dark"}}, f)
    return p


def _load(tmp_path):
    with io.open(os.path.join(str(tmp_path), "config.json"), encoding="utf-8") as f:
        return json.load(f)


class TestNotice:
    def test_notice_sets_pending_and_writes_actionable_log(self, monkeypatch):
        logs = []
        monkeypatch.setattr(MG, "log_event", logs.append)
        MG.notice("enc:v1:ABCDEF")
        assert MG.status()["pending"] is True
        assert any("[migration] 配置解密失败" in x for x in logs)

    def test_failed_values_are_hints_not_ciphertext(self):
        MG.notice("enc:v1:THIS_IS_A_LONG_SECRET_VALUE")
        hints = MG.status()["failed_values"]
        assert hints and all(len(h) <= 12 for h in hints)

    def test_status_returns_a_copy(self):
        MG.notice("enc:v1:xx")
        snap = MG.status()
        snap["pending"] = False
        snap["failed_values"].append("hacked")
        assert MG.status()["pending"] is True
        assert "hacked" not in MG.status()["failed_values"]

    def test_secrets_decrypt_failure_reports_to_guide(self, monkeypatch):
        """集成：_unprotect_secret 失败 → 密文原样保留 + notice 置 pending。"""
        def boom(_v):
            raise RuntimeError("simulated DPAPI failure")

        monkeypatch.setattr(S, "_dpapi_unprotect", boom, raising=False)
        val = S._ENCRYPT_PREFIX + "AAAA"
        assert S._unprotect_secret(val) == val, "解密失败绝不能销毁唯一一份密文"
        assert MG.status()["pending"] is True


class TestResolveGuards:
    def test_unknown_choice_rejected(self, tmp_path):
        _cfg(tmp_path)
        MG.notice("enc:v1:xx")
        ok, msg = MG.resolve("nope")
        assert ok is False and "未知" in msg
        assert MG.status()["pending"] is True, "拒绝后仍须等待用户处置"

    def test_weak_without_confirm_rejected(self, tmp_path):
        """红线：弱保护不显式勾选一律拒绝，绝不静默降级。"""
        _cfg(tmp_path)
        MG.notice("enc:v1:xx")
        ok, msg = MG.resolve("weak_explicit", confirm_weak=False)
        assert ok is False and "显式勾选" in msg
        assert not os.path.isdir(os.path.join(str(tmp_path), MG.BACKUP_DIRNAME)), \
            "被拒的处置不应产生任何落盘副作用"


class TestResolveBranches:
    def test_default_start_backs_up_and_clears_enc(self, tmp_path):
        _cfg(tmp_path)
        MG.notice("enc:v1:xx")
        ok, _msg = MG.resolve("default_start")
        assert ok is True
        assert os.path.isdir(MG.status()["backup_dir"]), "必须先备份"
        cfg = _load(tmp_path)
        assert cfg["api_token"] == MG.REENTER
        assert cfg["ui"]["theme"] == "dark", "非敏感键结构必须保留"
        assert MG.status()["pending"] is False
        assert MG.status()["resolved"] == "default_start"

    def test_weak_explicit_with_confirm(self, tmp_path):
        _cfg(tmp_path)
        MG.notice("enc:v1:xx")
        ok, _msg = MG.resolve("weak_explicit", confirm_weak=True)
        assert ok is True
        assert MG.status()["resolved"] == "weak_explicit"
        assert MG.status()["pending"] is False
        assert os.path.isdir(MG.status()["backup_dir"])

    def test_import_bundle_roundtrip(self, tmp_path):
        from core import profile_bundle as PB
        _cfg(tmp_path, token="plain-value")
        bundle = os.path.join(str(tmp_path), "bundle.zip")
        PB.build_bundle(bundle)
        os.remove(os.path.join(str(tmp_path), "config.json"))
        MG.notice("enc:v1:xx")
        ok, _msg = MG.resolve("import_bundle", bundle_path=bundle)
        assert ok is True
        assert os.path.isfile(os.path.join(str(tmp_path), "config.json"))
        assert MG.status()["pending"] is False

    def test_import_bundle_missing_path_rejected(self, tmp_path):
        _cfg(tmp_path)
        MG.notice("enc:v1:xx")
        ok, msg = MG.resolve("import_bundle", bundle_path="/no/such/bundle")
        assert ok is False and "不存在" in msg


class TestPage:
    def test_page_has_three_choices_and_no_placeholder(self):
        MG.notice("enc:v1:xx")
        html = MG.render_page()
        for c in MG.CHOICES:
            assert c in html
        assert "{{STATUS_LINE}}" not in html
        assert "配置解密失败" in html

    def test_page_does_not_reference_frontend_js(self):
        html = MG.render_page()
        assert "<script src=" not in html, "独立页不得触碰 18 个前端 js 契约"

    def test_docs_mark_guided_as_landed(self):
        with io.open(os.path.join(ROOT, "docs", "migration.md"), encoding="utf-8") as f:
            doc = f.read()
        assert "v1.0.756 已闭环" in doc


class TestWiring:
    def test_backend_has_migration_routes(self):
        with io.open(os.path.join(ROOT, "backend.py"), encoding="utf-8") as f:
            src = f.read()
        # v1.0.773（暗病巡检）：status 端点全仓零消费已删——迁移页服务端渲染状态、
        # resolve 响应体自带 status，前端只 fetch resolve。此断言随之收窄。
        assert '"/api/migration/resolve"' in src
        assert "migration_guide" in src and "render_page" in src

    def test_deferred_d9_closed(self):
        with io.open(os.path.join(ROOT, "docs", "deferred.md"), encoding="utf-8") as f:
            doc = f.read()
        assert "已闭环" in doc
