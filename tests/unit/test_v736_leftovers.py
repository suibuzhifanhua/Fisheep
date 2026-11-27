# -*- coding: utf-8 -*-
"""v1.0.736：上一轮三条遗留的回归锁。

① frozen 中文日志乱码（backend 入口编码统一 + run_smoke 自适应解码）
② PII 扫描把 numpy/hf_xet 第三方测试数据误报成银行卡号
③ pytest 会话末批量 rmtree 触发沙箱 bulk-delete 守卫噪音
"""
import os

from conftest import _src   # 共享：读仓库根下相对路径文本


# ══════════════ ① frozen 中文日志乱码 ══════════════

def test_decode_smoke_stream_utf8():
    """新版 frozen 包（入口已统一 utf-8）：中文正常，不出现替换字符。"""
    from buildlib.orchestration import decode_smoke_stream
    raw = ("[smoke] OK: 118 modules imported\n"
           "[smoke] OK: /api/ping 200（frozen 路由栈可用）\n").encode("utf-8")
    out = decode_smoke_stream(raw)
    assert "frozen 路由栈可用" in out
    assert "118 modules" in out
    assert "\ufffd" not in out


def test_decode_smoke_stream_cp936_fallback():
    """旧版/边界包（console=False，stdout 仍是 cp936）中文不能变乱码。

    实测来源：dist/Fisheep.exe --smoke 原始字节里
    `真实 HTTP 服务` / `frozen 路由栈可用` 两行 utf-8 解不开、cp936 可解。
    """
    from buildlib.orchestration import decode_smoke_stream
    raw = ("[smoke-live] OK: 真实 HTTP 服务 127.0.0.1:64140 → /api/ping 200\r\n"
           .encode("cp936"))
    out = decode_smoke_stream(raw)
    assert "真实 HTTP 服务" in out
    assert "\ufffd" not in out


def test_decode_smoke_stream_empty():
    from buildlib.orchestration import decode_smoke_stream
    assert decode_smoke_stream(b"") == ""
    assert decode_smoke_stream(None) == ""


def test_run_smoke_no_longer_forces_utf8_replace():
    """run_smoke 里不能再出现无脑 utf-8+replace 解码。"""
    src = _src("buildlib", "orchestration.py")
    body = src.split("def run_smoke(dist_exe, env):", 1)[1].split("\ndef ", 1)[0]
    assert "decode_smoke_stream(_smoke.stdout)" in body
    assert "decode_smoke_stream(_smoke.stderr)" in body
    assert '.decode("utf-8", "replace")' not in body


def test_backend_unifies_frozen_stdout_to_utf8():
    """入口只在 frozen 态改编码，开发/测试态不动（不影响 capsys）。"""
    src = _src("backend.py")
    assert 'if getattr(sys, "frozen", False):' in src
    assert 'reconfigure(encoding="utf-8", errors="replace"' in src
    assert "line_buffering=True" in src


# ══════════════ ② PII 扫描 numpy 误报 ══════════════

def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_pii_skips_third_party_component_data(tmp_path, monkeypatch, capsys):
    """组件/asr/ 下 numpy 十六进制位模式 + hf_xet SHA 哈希不再被当卡号。

    实测 2026-11-20：dist/产物库/ 3684 个文本文件里命中 12+ 全在这一支，
    导致每次打包都误报「PII 风险」、逼人以为必须跑 --release。
    """
    import buildlib.pii as pii
    dist = tmp_path / "dist"
    _write(dist / "产物库" / "组件" / "asr" / "numpy" / "_core" / "tests"
           / "data" / "umath-validation-set-arccos.csv",
           "np.float64,0x8010000000000000,0x3ff921fb54442d18,1\n")
    _write(dist / "产物库" / "组件" / "asr" / "hf_xet-1.7.0.dist-info"
           / "sboms" / "hf_xet.cyclonedx.json",
           '{"alg": "SHA-256", "content": '
           '"f1dd6dbb5841937940781866fa1281a1ff7bd3bf827091440879f9994983d5"}\n')
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "PII 分发风险提醒" not in out, out
    assert "范围内未命中" in out, out


def test_pii_hex_context_filter_in_user_file(tmp_path, monkeypatch, capsys):
    """内容级兜底：第三方数据目录改名后，0x 位模式仍不算卡号。"""
    import buildlib.pii as pii
    dist = tmp_path / "dist"
    _write(dist / "产物库" / "会话" / "notes.md",
           "调试数据 np.float64,0x8010000000000000,0x3ff921fb54442d18,1\n")
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "PII 分发风险提醒" not in out, out
    assert "已扫描 1 个文本文件" in out, out


def test_pii_still_detects_real_card_in_user_content(tmp_path, monkeypatch, capsys):
    """剪枝不能削掉真 PII 召回：用户会话里的卡号必须仍然命中。"""
    import buildlib.pii as pii
    dist = tmp_path / "dist"
    _write(dist / "产物库" / "会话" / "2026-10" / "chat.md",
           "我的卡号是 6222021234567890\n")
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "疑似银行卡号" in out, out
    assert "会话" in out, out


def test_pii_still_flags_model_config_backup(tmp_path, monkeypatch, capsys):
    """api_key 导出备份的告警与剪枝无关，必须保留。"""
    import buildlib.pii as pii
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "模型配置备份_20260101.json").write_text(
        '{"api_key":"sk-x"}', encoding="utf-8")
    monkeypatch.setattr(pii, "BASE", str(tmp_path))
    pii.scan_dist_pii()
    out = capsys.readouterr().out
    assert "api_key" in out, out


# ══════════════ ③ pytest 退出清理噪音 ══════════════

def test_conftest_temp_dirs_go_to_session_base():
    """未指定 dir 的 mkdtemp 统一收进固定会话 base（不再散落 %TEMP%）。"""
    import conftest as ct
    d = ct._tracked_mkdtemp(prefix="v736_")
    assert os.path.dirname(d) == ct._SESSION_BASE
    assert os.path.isdir(ct._SESSION_BASE)
    os.rmdir(d)


def test_conftest_explicit_dir_is_untouched(tmp_path):
    """显式 dir 的调用保持原样，不强行改道。"""
    import conftest as ct
    d = ct._tracked_mkdtemp(prefix="v736_", dir=str(tmp_path))
    assert os.path.dirname(d) == str(tmp_path)
    os.rmdir(d)


def test_conftest_no_bulk_delete_at_session_end():
    """全程零 rmtree（固定 base 只建不删）——绕开 safe-delete 钩子的关键。"""
    src = _src("tests", "conftest.py")
    body = src.split("def _reclaim_temp_dirs():", 1)[1].split("@pytest.fixture", 1)[0]
    head = body.split("yield", 1)[0]
    tail = body.split("finally:", 1)[1]
    assert "os.makedirs(_SESSION_BASE, exist_ok=True)" in head
    assert "rmtree(_SESSION_BASE" not in body      # 会话开始/结束都不删 base
    assert "rmtree" not in tail                    # 退出时零删除
    assert "_SESSION_DIRS" not in src              # 旧的批量清单已撤


def test_pytest_ini_disables_basetemp_rotation():
    """pytest 自身默认 keep=3 会轮换 rm_rf 旧 pytest-N/，也要关掉。"""
    ini = _src("pytest.ini")
    assert "tmp_path_retention_count" in ini
    assert "10000" in ini
