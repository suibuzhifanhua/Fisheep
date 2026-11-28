# -*- coding: utf-8 -*-
"""test_public_exe — 脱敏公开 exe 构建 + exe 级脱敏门禁（v1.0.764）回归。

覆盖 buildlib/public_exe 的四层：
  ① _scan_text / _scan_module_blob / _code_strings —— 纯函数（脱敏门禁核心判定，
     含「下钻 PYZ 冻结代码 → marshal.loads → 遍历 co_consts 取真实 str」这条关键路径）
  ② verify_public_exe 对**合成 CArchive**（PyInstaller 官方 CArchiveWriter 造的假
     exe）的端到端：脏数据 entry → 判红；全干净但缺阳性对照 → 判红（防假阴性）
  ③/④ 对**真实 exe**（skipif 缺产物）：开发版 exe 必含敏感串（验证扫描器没坏、
     且 PYZ 下钻真捞到冻结代码）；脱敏 exe 必 0 敏感 + 阳性对照命中（发布铁证）。

敏感字面量一律运行时拼装（本文件是 tracked .py，会进公共镜像被门禁自扫——写明文会自伤）。
"""
import marshal
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from buildlib.public_exe import (  # noqa: E402
    EXE_NAME, MIRROR_DIR, _code_strings, _iter_entries, _module_strings,
    _scan_module_blob, _scan_text, build_public_exe, run_public_release,
    verify_public_exe,
)
from buildlib.env import BASE   # noqa: E402
from buildlib.mask_gate import GATE_PATTERNS   # noqa: E402

# 运行时拼装敏感字面量（防本文件被镜像门禁自扫命中）。
_W_ZN = "zhi" + "-niao.com"            # 雇主学习平台域
_W_COMPANY = "\u535a\u6613\u4e91"      # 公司名
_W_VENDOR = "D" + "aoPro"              # 服务商名
_W_INNER_IP = "192.168" + ".3.9"       # 内部现网段 IP：运行时拼装（源文本/注释不含网段字面量，
                                        # 否则镜像门禁 内部IP 自扫命中——脱敏规则不覆盖测试样例 IP）
_CLEAN_IP = "192.0.2.43"               # 脱敏后的 RFC5737 TEST-NET-1（不拦）

_LABELS = {label for label, _pat in GATE_PATTERNS}


def _hits(text):
    return [label for label, _snip in _scan_text(text, GATE_PATTERNS)]


# ── ① 纯函数：_scan_text ──────────────────────────────────────────────
def test_scan_text_catches_sensitive():
    assert "雇主平台" in _hits(_W_ZN), _hits(_W_ZN)
    assert "公司名" in _hits(_W_COMPANY)
    assert "服务商名" in _hits(_W_VENDOR)
    assert "内部IP" in _hits(_W_INNER_IP)
    print("PASS _scan_text 命中全部敏感类别")


def test_scan_text_ignores_masked_values():
    # 脱敏后的值（示例域/TEST-NET-1/通用泛称）不应被判敏感
    assert _hits("study.example " + _CLEAN_IP + " SharedLLM 推理模型") == []
    print("PASS _scan_text 脱敏后值不误报")


# ── ① 纯函数：_code_strings（嵌套 co_consts 遍历）────────────────────
def test_code_strings_walks_nested_code():
    src = "TOP = %r\n\ndef f():\n    nested = %r\n    return nested\n" % (_W_ZN, _W_COMPANY)
    code = compile(src, "<t>", "exec")
    strs = []
    _code_strings(code, strs)
    assert _W_ZN in strs and _W_COMPANY in strs, strs
    print("PASS _code_strings 递归收集顶层 + 嵌套函数体 str 常量")


# ── ① 纯函数：_scan_module_blob（marshal 字节 → 真实 str 匹配，编码无关）──
def test_scan_module_blob_dirty_and_clean():
    dirty = marshal.dumps(compile("SECRET = %r" % _W_ZN, "<t>", "exec"))
    hits = [label for label, _ in _scan_module_blob(dirty, GATE_PATTERNS)]
    assert "雇主平台" in hits, hits
    clean = marshal.dumps(compile("X = 1\nY = 'flask wecom'" , "<t>", "exec"))
    assert _scan_module_blob(clean, GATE_PATTERNS) == []
    print("PASS _scan_module_blob 脏 code 判中 / 净 code 不报")


# ── ② 合成 CArchive：端到端（PyInstaller 官方 writer 造假 exe）──────────
def _mk_archive(tmp_path, entries, name="synth.exe"):
    from PyInstaller.archive.writers import CArchiveWriter
    out = str(tmp_path / name)
    CArchiveWriter(out, [(d, s, True, tc) for (d, s, tc) in entries], "python313.dll")
    return out


def test_verify_synthetic_dirty_data(tmp_path):
    src = tmp_path / "payload.bin"
    src.write_bytes(("url=" + _W_ZN + "\n").encode("utf-8"))
    exe = _mk_archive(tmp_path, [("payload.bin", str(src), "x")])
    problems, stats = verify_public_exe(exe)
    assert stats["data_files"] >= 1, stats
    assert any("雇主平台" in p for p in problems), problems
    print("PASS 合成脏数据 entry → 门禁判红（雇主平台）")


def test_verify_synthetic_missing_controls_red(tmp_path):
    # 全干净但**没有任何阳性对照**（wecom/flask/Fisheep）→ 判红（防「没解出来=假干净」）
    src = tmp_path / "clean.bin"
    src.write_bytes(("clean data " + _CLEAN_IP + "\n").encode("utf-8"))
    exe = _mk_archive(tmp_path, [("clean.bin", str(src), "x")], name="synth2.exe")
    problems, stats = verify_public_exe(exe)
    assert stats["sensitive_hits"] == 0, stats
    assert any("阳性对照" in p for p in problems), problems
    print("PASS 缺阳性对照 → 门禁判红（防假阴性）")


# ── ③/④ 真实 exe 集成（skipif 缺产物）────────────────────────────────
_DEV_EXE = os.path.join(BASE, "dist", EXE_NAME)
_MIRROR_EXE = os.path.join(MIRROR_DIR, "dist", EXE_NAME)


@pytest.mark.skipif(not os.path.isfile(_DEV_EXE), reason="无开发版 exe（先 build.py）")
def test_verify_real_dev_exe_is_dirty():
    """开发版 exe 由未脱敏源码构建 → 必含敏感串；且 PYZ 下钻真捞到冻结代码。
    这是「扫描器没坏」的正向保证：若扫描器退化到恒空，脏的 public exe 会被误放行。"""
    problems, stats = verify_public_exe(_DEV_EXE)
    assert stats["code_modules"] > 0, stats          # 确实下钻了 PYZ 冻结代码
    assert stats["sensitive_hits"] > 0, stats        # 开发版 exe 必含敏感串
    assert any("雇主平台" in p for p in problems), problems[:5]
    print("PASS 开发版 exe 检出敏感残留（雇主平台 pyz 命中 %d 处）——扫描器有效"
          % stats["sensitive_hits"])


@pytest.mark.skipif(not os.path.isfile(_MIRROR_EXE),
                    reason="无脱敏 exe（先 --public-exe 或 tools/build_public_exe.py）")
def test_verify_real_mirror_exe_is_clean():
    """脱敏 exe（从镜像树打包）→ 0 敏感残留 + 阳性对照命中 = 可发 public 的铁证。"""
    problems, stats = verify_public_exe(_MIRROR_EXE)
    assert stats["sensitive_hits"] == 0, problems[:10]
    assert not problems, problems[:10]
    assert stats["controls_found"], stats
    print("PASS 脱敏 exe 门禁全绿（0 敏感；对照 %s；模块 %d）"
          % ("/".join(stats["controls_found"]), stats["code_modules"]))


# ── ⑤ 合成 PYZ（hermetic 下钻 _iter_entries 的 'z' + verify 的 code-kind）────
# 真实 exe 依赖 dist/ 产物（CI/干净检出可能没有）→ 用 PyInstaller 官方 writer 造一个
# 含 PYZ 冻结代码 entry 的合成 exe，稳定覆盖「只扫顶层会漏冻结代码」这条关键路径，
# 并锁死 verify_public_exe 的 code-kind 阳性对照判定（对照串在 co_consts 而非 bytes）。
def _mk_pyz_archive(tmp_path, module_name, code_src,
                    ctrl=b"wecom flask Fisheep", name="synth_pyz.exe"):
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter
    cpath = tmp_path / "ctrl.bin"
    cpath.write_bytes(ctrl)
    pyz = str(tmp_path / "PYZ-00.pyz")
    code = compile(code_src, "<%s>" % module_name, "exec")
    ZlibArchiveWriter(pyz, [(module_name, module_name + ".py", "PYMODULE")],
                      code_dict={module_name: code})
    out = str(tmp_path / name)
    # PYZ 在 CArchive 里必须**不压缩**：open_embedded_archive 按原始偏移直读 PYZ magic，
    # 压缩后那偏移处是 zlib 头 →「PYZ magic pattern mismatch」。
    CArchiveWriter(out, [("PYZ-00", pyz, False, "z"),
                         ("ctrl.bin", str(cpath), True, "x")], "python313.dll")
    return out


def test_iter_entries_drills_pyz(tmp_path):
    """PYZ entry 下钻：_iter_entries 应产出 code 模块（marshal 字节），_scan_module_blob
    从冻结代码 co_consts 里捞出敏感串——「只扫顶层会漏冻结代码」的回归锁。"""
    exe = _mk_pyz_archive(tmp_path, "mymod", "SECRET = %r" % _W_ZN)
    kinds, code_hits = {}, []
    for kind, name, target in _iter_entries(exe):
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "code":
            assert isinstance(target, (bytes, bytearray)), "code 模块应是 marshal 字节"
            code_hits.extend([lab for lab, _ in _scan_module_blob(target, GATE_PATTERNS)])
    assert kinds.get("code") == 1 and kinds.get("data") == 1, kinds
    assert "雇主平台" in code_hits, code_hits
    print("PASS _iter_entries 下钻 PYZ 冻结代码，敏感串被捞出（%s）" % code_hits)


def test_verify_pyz_sensitive_dirty(tmp_path):
    """端到端：敏感串**只**藏在 PYZ 冻结代码（数据文件干净）→ 门禁仍判红。"""
    exe = _mk_pyz_archive(tmp_path, "mymod", "SECRET = %r" % _W_ZN)
    problems, stats = verify_public_exe(exe)
    assert stats["code_modules"] == 1 and stats["sensitive_hits"] >= 1, stats
    assert any("雇主平台" in p for p in problems), problems
    print("PASS 敏感串仅藏在冻结代码 → 门禁判红（PYZ 下钻铁证）")


def test_verify_pyz_controls_in_frozen_code(tmp_path):
    """对照串落在冻结代码 co_consts（数据文件无）→ 仍判为阳性对照命中，不误报假阴性；
    同时锁死 _module_strings 修复的 `str in bytes` TypeError。"""
    exe = _mk_pyz_archive(tmp_path, "mymod", "CTRL = 'wecom flask Fisheep ok'",
                          ctrl=b"clean no markers here")
    problems, stats = verify_public_exe(exe)
    assert stats["sensitive_hits"] == 0, problems
    assert "wecom" in stats["controls_found"], stats   # 从冻结代码 co_consts 判出
    assert not any("阳性对照" in p for p in problems), problems
    print("PASS 对照串在冻结代码 co_consts → 判定命中（_module_strings 修 str-in-bytes）")


# ── ⑥ buildlib/public_exe：build_public_exe（subprocess 打桩）+ run_public_release ──
def _CP(rc, out=b"", err=b""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _pe_dirs(tmp_path, has_spec=True, has_vi=True):
    base = tmp_path / "base"
    mirror = tmp_path / "mirror"
    base.mkdir()
    mirror.mkdir()
    if has_spec:
        (mirror / "Fisheep.spec").write_text("# spec", encoding="utf-8")
    if has_vi:
        (base / "version_info.txt").write_text("vi", encoding="utf-8")
    return str(base), str(mirror)


def test_build_public_exe_missing_spec(tmp_path, monkeypatch):
    import buildlib.public_exe as PE
    base, mirror = _pe_dirs(tmp_path, has_spec=False)

    def _boom(*a, **k):
        raise AssertionError("缺 spec 不该跑 pyinstaller")

    monkeypatch.setattr(PE.subprocess, "run", _boom)
    path, ok, msg = PE.build_public_exe(mirror_dir=mirror, base=base, pyinstaller="fake-py")
    assert not ok and path is None and "Fisheep.spec" in msg
    print("PASS 镜像缺 spec → 早退不跑 pyinstaller")


def test_build_public_exe_missing_version_info(tmp_path, monkeypatch):
    import buildlib.public_exe as PE
    base, mirror = _pe_dirs(tmp_path, has_vi=False)   # dev 与镜像都无 version_info.txt

    def _boom(*a, **k):
        raise AssertionError("缺 version_info 不该跑 pyinstaller")

    monkeypatch.setattr(PE.subprocess, "run", _boom)
    path, ok, msg = PE.build_public_exe(mirror_dir=mirror, base=base, pyinstaller="fake-py")
    assert not ok and path is None and "version_info" in msg
    print("PASS 缺 version_info.txt → 早退不跑 pyinstaller")


def test_build_public_exe_pyinstaller_fail(tmp_path, monkeypatch):
    import buildlib.public_exe as PE
    base, mirror = _pe_dirs(tmp_path)
    monkeypatch.setattr(PE.subprocess, "run", lambda *a, **k: _CP(1, err=b"boom"))
    path, ok, msg = PE.build_public_exe(mirror_dir=mirror, base=base, pyinstaller="fake-py")
    assert not ok and path is None and "pyinstaller 失败" in msg
    print("PASS pyinstaller 非零返回 → 打包失败")


def test_build_public_exe_exe_missing(tmp_path, monkeypatch):
    import buildlib.public_exe as PE
    base, mirror = _pe_dirs(tmp_path)
    monkeypatch.setattr(PE.subprocess, "run", lambda *a, **k: _CP(0, out=b"ok"))
    path, ok, msg = PE.build_public_exe(mirror_dir=mirror, base=base, pyinstaller="fake-py")
    assert not ok and path is None and "不存在" in msg
    print("PASS pyinstaller 成功但 exe 缺失 → 判失败")


def test_build_public_exe_success(tmp_path, monkeypatch):
    import buildlib.public_exe as PE
    base, mirror = _pe_dirs(tmp_path)
    dist = os.path.join(mirror, "dist")
    os.makedirs(dist, exist_ok=True)
    exe = os.path.join(dist, PE.EXE_NAME)

    def fake_run(cmd, cwd=None, env=None, capture_output=None, timeout=None):
        with open(exe, "wb") as f:
            f.write(b"MZ-fake")
        assert os.path.abspath(cwd) == os.path.abspath(mirror), "cwd 必须是镜像树"
        return _CP(0, out=b"built")

    monkeypatch.setattr(PE.subprocess, "run", fake_run)
    path, ok, msg = PE.build_public_exe(mirror_dir=mirror, base=base, pyinstaller="fake-py")
    assert ok and path == exe and "OK" in msg, (ok, msg)
    print("PASS 全链路成功 → 返回 exe 路径 + OK")


def test_run_public_release_build_fail(monkeypatch):
    import buildlib.public_exe as PE
    monkeypatch.setattr(PE, "build_public_exe", lambda *a, **k: (None, False, "boom"))
    ok, lines = PE.run_public_release()
    assert not ok and any("boom" in l for l in lines)
    print("PASS 打包失败 → run_public_release 返回 False")


def test_run_public_release_dirty(monkeypatch):
    import buildlib.public_exe as PE
    monkeypatch.setattr(PE, "build_public_exe", lambda *a, **k: ("/x/" + PE.EXE_NAME, True, "OK"))
    stats = {"entries": 1, "code_modules": 1, "data_files": 0, "sensitive_hits": 1,
             "controls_found": []}
    monkeypatch.setattr(PE, "verify_public_exe", lambda p: (["[敏感残留 雇主平台] m"], stats))
    ok, lines = PE.run_public_release()
    assert not ok and any("❌" in l for l in lines)
    print("PASS 门禁红 → run_public_release 返回 False + 列出问题")


def test_run_public_release_clean(monkeypatch):
    import buildlib.public_exe as PE
    monkeypatch.setattr(PE, "build_public_exe", lambda *a, **k: ("/x/" + PE.EXE_NAME, True, "OK"))
    stats = {"entries": 2, "code_modules": 1, "data_files": 1, "sensitive_hits": 0,
             "controls_found": ["wecom", "flask", "Fisheep"]}
    monkeypatch.setattr(PE, "verify_public_exe", lambda p: ([], stats))
    ok, lines = PE.run_public_release()
    assert ok and any("✅" in l for l in lines)
    print("PASS 门禁全绿 → run_public_release 返回 True")


# ── ⑦ build.py：--public-exe CLI 解析 + 发布硬闸 _run_public_exe_gate（抽成函数可单测）──
class _FakeJournal:
    def __init__(self):
        self.stages = []

    def stage(self, name, **kv):
        self.stages.append((name, kv))


def test_parse_args_public_exe_flag():
    import build as B
    assert B._parse_args(["--public-exe"]).public_exe is True
    assert B._parse_args(["--yes"]).public_exe is False
    print("PASS --public-exe 解析为 True；默认 False")


def test_public_gate_noop_when_not_requested():
    import build as B
    j = _FakeJournal()
    assert B._run_public_exe_gate(B._parse_args(["--yes"]), j, "1.0.1") is None
    assert j.stages == []
    print("PASS 未带 --public-exe → 硬闸不触发、不落 done")


def test_public_gate_release_ok(monkeypatch):
    import build as B
    import buildlib.public_exe as PE
    monkeypatch.setattr(PE, "run_public_release", lambda: (True, ["打包：OK", "✅ 通过"]))
    j = _FakeJournal()
    assert B._run_public_exe_gate(B._parse_args(["--public-exe"]), j, "1.0.1") is None
    assert j.stages == []     # 成功不落 done（由 main 统一落）
    print("PASS 脱敏门禁通过 → 硬闸放行（返回 None）")


def test_public_gate_release_fail(monkeypatch):
    import build as B
    import buildlib.public_exe as PE
    monkeypatch.setattr(PE, "run_public_release", lambda: (False, ["❌ 门禁未过"]))
    j = _FakeJournal()
    rc = B._run_public_exe_gate(B._parse_args(["--public-exe"]), j, "1.0.1")
    assert rc == 1
    assert j.stages == [("done", {"ver": "1.0.1"})]
    print("PASS 脱敏门禁红 → 硬闸返回 1 + 落 done（阻塞发布）")


def test_public_gate_release_raises(monkeypatch):
    import build as B
    import buildlib.public_exe as PE

    def _boom():
        raise RuntimeError("pyinstaller 崩了")

    monkeypatch.setattr(PE, "run_public_release", _boom)
    j = _FakeJournal()
    rc = B._run_public_exe_gate(B._parse_args(["--public-exe"]), j, "1.0.1")
    assert rc == 1
    assert j.stages == [("done", {"ver": "1.0.1"})]
    print("PASS 脱敏处理抛异常 → 视为门禁红（fail-closed 返回 1）")
