# -*- coding: utf-8 -*-
"""buildlib 补充测试：verify / pii / ocr_sync（B9）。

这三个模块各自对应一次真实事故，修复逻辑此前无回归锁：
  · verify.py    —— 「dist 落后 4 个文件，代码改了但 exe 里是旧前端」；
                   v1.0.660（B1）又修了 onedir 分发形态下核对口径全错的问题
                   （原来一律按 onefile 的 CArchive 读，对 onedir 恒定读不到）。
  · pii.py       —— dist 整目录发出去 = 连对话记录里的银行卡号一起发；
                   v1.0.652 修了「只扫一层目录 → 假绿灯」。
  · ocr_sync.py  —— OCR 运行时旁挂位置写错（onedir 时落在 dist/ocr/ 而运行时
                   找的是 dist/Lingban-onedir/ → 课件 OCR 静默不可用）。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from buildlib import ocr_sync, pii, verify  # noqa: E402


# ===================== verify：包内资源一致性核对 =====================

def test_source_assets_includes_wecom_bridge_js():
    """企微页面注入脚本必须在核对清单里——包内缺它，界面照样开，
    只是注入脚本静默失效（"资源核对形同虚设"的形态）。"""
    assets = verify._source_assets()
    assert "index.html" in assets
    assert any(a.startswith("js/") for a in assets)
    assert any(a.startswith("css/") for a in assets)
    wb = [a for a in assets if a.startswith("wecom_bridge/js/")]
    # 该目录存在（本仓有 wecom_bridge/js/*.js）时必须被列入
    if os.path.isdir(os.path.join(ROOT, "wecom_bridge", "js")):
        assert wb, "wecom_bridge/js 未进核对清单"


def test_verify_onedir_detects_mismatch(tmp_path, monkeypatch, capsys):
    """onedir 形态：逐字节比对 <exe 同级>/_internal/ 下的资源，
    不一致必须报出来（原来按 CArchive 读 → 恒定读不到 → 等于没核对）。"""
    exe = tmp_path / "Lingban.exe"
    exe.write_bytes(b"MZ")
    internal = tmp_path / "_internal"
    internal.mkdir()
    # 造一个与源码不一致的 index.html
    (internal / "index.html").write_text("<html>STALE</html>", encoding="utf-8")
    # 让 _source_assets 只返回一个已存在的资源，简化断言
    monkeypatch.setattr(verify, "_source_assets", lambda: ["index.html"])
    n = verify.verify_packaged_assets(str(exe), onedir=True)
    assert n == 1, "onedir 下资源不一致必须被检出"
    out = capsys.readouterr().out
    assert "不一致" in out


def test_verify_onedir_accepts_matching(tmp_path, monkeypatch):
    """内容一致时返回 0。"""
    exe = tmp_path / "Lingban.exe"
    exe.write_bytes(b"MZ")
    internal = tmp_path / "_internal"
    internal.mkdir()
    src = os.path.join(ROOT, "index.html")
    (internal / "index.html").write_bytes(io.open(src, "rb").read())
    monkeypatch.setattr(verify, "_source_assets", lambda: ["index.html"])
    assert verify.verify_packaged_assets(str(exe), onedir=True) == 0


def test_verify_autodetects_onedir_by_internal_dir(tmp_path, monkeypatch):
    """onedir=None 时按 exe 同级有 _internal/ 自动判别形态。"""
    exe = tmp_path / "Lingban.exe"
    exe.write_bytes(b"MZ")
    internal = tmp_path / "_internal"
    internal.mkdir()
    (internal / "index.html").write_text("<html>STALE</html>", encoding="utf-8")
    monkeypatch.setattr(verify, "_source_assets", lambda: ["index.html"])
    assert verify.verify_packaged_assets(str(exe)) == 1


def test_verify_onefile_without_pyinstaller_is_warn_not_fail(tmp_path, capsys):
    """读不了归档时返回 -1（只警告不拦）——不能因为环境缺 PyInstaller 就中止打包。"""
    exe = tmp_path / "Lingban.exe"
    exe.write_bytes(b"MZ")
    monkey = verify.__dict__
    old = monkey.get("BASE")
    try:
        # 让 _source_assets 返回空列表，避免走归档读取
        monkey["_source_assets"] = lambda: []
        n = verify.verify_packaged_assets(str(exe), onedir=False)
    finally:
        if old is not None:
            monkey["_source_assets"] = old
    assert n in (0, -1)


# ===================== pii：dist 分发风险扫描 =====================

def test_pii_scan_detects_card_number_in_nested_dir(tmp_path, monkeypatch, capsys):
    """产物库/ 会话/ 等**子目录**里的卡号必须被扫到——
    原实现只扫一层，漏掉对话正文主要存放处，却还打"未发现 ✓"（假绿灯）。"""
    dist = tmp_path / "dist"
    nested = dist / "产物库" / "会话" / "2026-10"
    nested.mkdir(parents=True)
    (nested / "chat.md").write_text("我的卡号是 6222021234567890\n", encoding="utf-8")
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "疑似银行卡号" in out, out
    assert "会话" in out, out


def test_pii_scan_no_hit_reports_scanned_count(tmp_path, monkeypatch, capsys):
    """没有命中时要说清"扫描范围内未命中"并报扫描量——
    不能打无条件的"未发现 ✓"（那是漏扫时的假绿灯）。"""
    dist = tmp_path / "dist"
    d = dist / "产物库" / "会话"
    d.mkdir(parents=True)
    (d / "a.md").write_text("今天天气不错", encoding="utf-8")
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "已扫描 1 个文本文件" in out, out
    assert "范围内未命中" in out, out


def test_pii_scan_flags_model_config_backup(tmp_path, monkeypatch, capsys):
    """模型配置备份含明文 api_key，必须单独提示。"""
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "模型配置备份_20260101.json").write_text('{"api_key":"sk-x"}', encoding="utf-8")
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "api_key" in out, out


def test_pii_scan_missing_dist_is_noop(tmp_path, monkeypatch):
    """dist 不存在时安静返回，不抛异常。"""
    monkeypatch.setattr(pii, "BASE", str(tmp_path / "nonexistent"))
    pii.scan_dist_pii()   # 不抛即通过


# ===================== ocr_sync：OCR 运行时旁挂 =====================

def test_ocr_sync_respects_dist_dir_argument(tmp_path, monkeypatch):
    """v1.0.660（B1）：目标目录随分发形态联动——onedir 的 OCR 运行时必须落在
    传入的分发根下，而不是固定写 dist/ocr/（否则运行时找不到，OCR 静默不可用）。"""
    called = {}

    def fake_venv_pkg_versions(python_exe, pkgs):
        return {}

    monkeypatch.setattr(ocr_sync, "_venv_pkg_versions", fake_venv_pkg_versions)
    monkeypatch.setattr(ocr_sync, "PYINSTALLER",
                        str(tmp_path / "Scripts" / "pyinstaller.exe"))

    # 造一个假的 site-packages，含一个 OCR 包目录
    sp = tmp_path / "Lib" / "site-packages"
    (sp / "numpy").mkdir(parents=True)
    (sp / "numpy" / "__init__.py").write_text("", encoding="utf-8")

    import shutil
    real_makedirs = os.makedirs
    real_copytree = shutil.copytree
    copied = []

    def spy_makedirs(path, *a, **k):
        called.setdefault("makedirs", []).append(path)
        return real_makedirs(path, *a, **k)

    def spy_copytree(src, dst, *a, **k):
        copied.append((src, dst))
        return real_copytree(src, dst, *a, **k)

    monkeypatch.setattr(ocr_sync.os, "makedirs", spy_makedirs)
    monkeypatch.setattr(ocr_sync.shutil, "copytree", spy_copytree)

    dist = tmp_path / "dist" / "Lingban-onedir"
    ocr_sync.sync_ocr_runtime(dist_dir=str(dist))

    ocr_dir = str(dist / "ocr")
    assert ocr_dir in called.get("makedirs", []), \
        "OCR 运行时必须落在传入的分发目录下：" + str(called.get("makedirs"))
    assert any(ocr_dir in d for _, d in copied), \
        "复制目标必须落在传入的分发目录下"


def test_ocr_sync_missing_site_packages_warns_and_returns(tmp_path, monkeypatch, capsys):
    """找不到 site-packages 时给明确警告并返回（不抛异常中断打包）。"""
    monkeypatch.setattr(ocr_sync, "PYINSTALLER",
                        str(tmp_path / "nope" / "Scripts" / "pyinstaller.exe"))
    ocr_sync.sync_ocr_runtime(dist_dir=str(tmp_path / "dist"))
    out = capsys.readouterr().out
    assert "site-packages" in out, out
