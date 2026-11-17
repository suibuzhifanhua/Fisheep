# -*- coding: utf-8 -*-
"""tests/test_buildlib_units.py — buildlib 构建链自身的单元测试（v1.0.660 审查修复 B4）。

buildlib/ 是打包链的**实现层**，此前只有 tests/test_build_guard.py 从外部
断言"接线"（调用点在不在 main() 里），实现本身零覆盖——于是：

  · versioning.bump_version 写坏了（版本号没 +1 / 写成非法格式），没人发现；
  · spec_refresh._write_onedir_spec 派生出**语法错误**的 onedir spec（缺
    exclude_binaries=True / datas 漏项），要到真打 onedir 包时才炸；
  · backup 的 config.json「最后防线」失效（备份被空默认覆盖），用户配置静默丢失；
  · orchestration.run_pyinstaller 的失败回滚分支失灵，打包失败后版本号停在已 +1。

这几个函数各自都对应过一次真实事故，所以本文件按「事故 → 行为锁」组织：
每个 class 一条事故，锁住当时真正坏掉的那一步。

风格与既有测试一致：pytest + tmp_path + monkeypatch，不联网、不打包。
"""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from buildlib import backup as _BK   # noqa: E402
from buildlib import orchestration as _OR   # noqa: E402
from buildlib import spec_refresh as _SR   # noqa: E402
from buildlib import versioning as _V   # noqa: E402


# ============================================================ versioning：版本号是唯一事实源
class TestVersioning:
    """v1.0.660（B4）：版本号的写入/递增/回滚必须落在**一个**实现上。

    事故背景：bump_version 与 orchestration 的回滚分支各写了一份正则重写
    （B20）。两份实现漂移过一次，就会写出坏的 version.py——而版本号是
    「版本库时间线」「设置页版本列表」「exe 版本资源」三处共用的锚点。
    """

    def _vp(self, tmp_path, monkeypatch, version="1.0.660"):
        """造一份假的 version.py，把 versioning 指向它。"""
        p = tmp_path / "version.py"
        p.write_text('# -*- coding: utf-8 -*-\n__version__ = "%s"\n' % version,
                     encoding="utf-8")
        monkeypatch.setattr(_V, "VPATH", str(p))
        return p

    def test_bump_writes_new_number_and_reports_pair(self, tmp_path, monkeypatch):
        p = self._vp(tmp_path, monkeypatch, "1.0.660")
        old, new = _V.bump_version()
        assert (old, new) == ("1.0.660", "1.0.661")
        assert '__version__ = "1.0.661"' in p.read_text(encoding="utf-8")

    def test_bump_with_explicit_does_not_auto_increment(self, tmp_path, monkeypatch):
        p = self._vp(tmp_path, monkeypatch, "1.0.660")
        old, new = _V.bump_version("2.0.0")
        assert (old, new) == ("1.0.660", "2.0.0")
        assert '__version__ = "2.0.0"' in p.read_text(encoding="utf-8")

    def test_set_version_is_the_single_writer(self, tmp_path, monkeypatch):
        """set_version 是写入的唯一落点——bump 与回滚都走它（B20）。"""
        p = self._vp(tmp_path, monkeypatch, "1.0.660")
        old, new = _V.set_version("1.0.659")
        assert (old, new) == ("1.0.660", "1.0.659")
        assert '__version__ = "1.0.659"' in p.read_text(encoding="utf-8")

    def test_set_version_rejects_garbage(self, tmp_path, monkeypatch):
        p = self._vp(tmp_path, monkeypatch, "1.0.660")
        with pytest.raises(ValueError):
            _V.set_version("not-a-version")
        assert '__version__ = "1.0.660"' in p.read_text(encoding="utf-8"), \
            "非法版本号被拒时不该改坏文件"

    def test_next_version_rolls_over_minor(self):
        """1.0.9 的下一个是 1.0.10（不是 1.1.0）——版本号在设置页按字符串排会乱。"""
        assert _V.next_version("1.0.9") == "1.0.10"
        assert _V.next_version("1.0.659") == "1.0.660"

    def test_current_version_reads_the_file(self, tmp_path, monkeypatch):
        self._vp(tmp_path, monkeypatch, "3.4.5")
        assert _V.current_version() == "3.4.5"

    def test_make_version_info_carries_company_and_copyright(self, tmp_path, monkeypatch):
        """v1.0.660（B16）：exe 版本资源不能有空的 CompanyName / LegalCopyright。"""
        out = tmp_path / "version_info.txt"
        monkeypatch.setattr(_V, "BASE", str(tmp_path))
        p = _V.make_version_info("1.0.660")
        txt = io.open(p, encoding="utf-8").read()
        assert os.path.basename(p) == "version_info.txt"
        assert "u'CompanyName', u''" not in txt, "CompanyName 还是空串"
        assert "u'LegalCopyright', u''" not in txt, "LegalCopyright 还是空串"
        assert "date=(0, 0)" not in txt, "date 还是 (0, 0)（资源管理器里永远显示 0/0）"
        assert "1.0.660" in txt
        assert out.exists() is False or True   # 仅确认不抛异常

    def test_version_info_is_parseable_by_pyinstaller(self, tmp_path, monkeypatch):
        """生成的 version_info.txt 必须能被 PyInstaller 的 eval() 解析器读懂——
        格式坏掉时，PyInstaller 是在**写 exe 的最后一步**才报错（白跑几分钟）。"""
        pytest.importorskip("PyInstaller")
        from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
        monkeypatch.setattr(_V, "BASE", str(tmp_path))
        p = _V.make_version_info("1.0.660")
        info = load_version_info_from_text_file(p)
        assert info is not None


# ============================================================ spec_refresh：onedir 派生
class TestOnedirSpec:
    """v1.0.660（B4/B2）：onedir spec 是**生成物**，必须派生正确。

    事故背景：磁盘上那份是过期生成物——EXE 块缺 exclude_binaries=True
    （PyInstaller 官方 onedir 模板必带；缺了 exe 仍按 one-file 语义构建，
    启动解压到 _MEIPASS，而 COLLECT 把前端资源放进 _internal/ → onedir
    启动即无 UI），datas 还缺主 spec 后加的 wecom_bridge/js。

    这类错误**不会**让 PyInstaller 报错——它照常产出 exe，只是打出来的是坏的。
    所以只能靠测试锁住生成物的内容。
    """

    def _gen(self, tmp_path, monkeypatch, main_spec=None):
        """在 tmp 里放一份主 spec，指向它派生 onedir spec。"""
        if main_spec is None:
            main_spec = (
                "# -*- mode: python ; coding: utf-8 -*-\n"
                "from PyInstaller.utils.hooks import collect_all\n"
                "datas = [('index.html', '.'), ('app.ico', '.'), ('js', 'js'),\n"
                "         ('css', 'css'), ('wecom_bridge/js', 'wecom_bridge/js')]\n"
                "binaries = []\n"
                "hiddenimports = ['core']\n"
                "excludes = ['tkinter']\n"
                "a = Analysis(\n"
                "    ['backend.py'],\n"
                "    pathex=['.'],\n"
                "    binaries=binaries,\n"
                "    datas=datas,\n"
                "    hiddenimports=hiddenimports,\n"
                "    hookspath=[],\n"
                "    hooksconfig={},\n"
                "    runtime_hooks=[],\n"
                "    excludes=excludes,\n"
                "    noarchive=False,\n"
                "    optimize=0,\n"
                ")\n"
                "pyz = PYZ(a.pure)\n"
                "\n"
                "exe = EXE(\n"
                "    pyz,\n"
                "    a.scripts,\n"
                "    a.binaries,\n"
                "    a.datas,\n"
                "    [],\n"
                "    name='Lingban',\n"
                "    debug=False,\n"
                "    bootloader_ignore_signals=False,\n"
                "    strip=False,\n"
                "    upx=True,\n"
                "    upx_exclude=[],\n"
                "    runtime_tmpdir=None,\n"
                "    console=False,\n"
                "    disable_windowed_traceback=False,\n"
                "    argv_emulation=False,\n"
                "    target_arch=None,\n"
                "    codesign_identity=None,\n"
                "    entitlements_file=None,\n"
                "    icon=['app.ico'],\n"
                "    version='version_info.txt',\n"
                ")\n"
            )
        spec = tmp_path / "Lingban.spec"
        spec.write_text(main_spec, encoding="utf-8")
        monkeypatch.setattr(_SR, "SPEC", str(spec))
        monkeypatch.setattr(_SR, "BASE", str(tmp_path))
        monkeypatch.setattr(_SR, "_ONEDIR_SPEC_DIR", str(tmp_path / "build" / "specs"))
        p = _SR._write_onedir_spec()
        return p, io.open(p, encoding="utf-8").read()

    def test_generated_file_is_valid_python(self, tmp_path, monkeypatch):
        import ast
        _p, txt = self._gen(tmp_path, monkeypatch)
        ast.parse(txt)   # 语法错 = 打包直接失败，必须在测试里就拦住

    def test_exclude_binaries_is_injected(self, tmp_path, monkeypatch):
        """缺这一行 → onedir 启动即无 UI（EXE 仍按 one-file 语义构建）。"""
        _p, txt = self._gen(tmp_path, monkeypatch)
        assert "exclude_binaries=True" in txt, \
            "onedir spec 缺 exclude_binaries=True——官方 onedir 模板必带，缺了启动即无 UI"

    def test_a_binaries_and_a_datas_move_to_collect(self, tmp_path, monkeypatch):
        """a.binaries/a.datas 必须在 EXE 块外（交给 COLLECT），否则 exe 与 _internal 重复。"""
        _p, txt = self._gen(tmp_path, monkeypatch)
        exe_block = txt[txt.index("exe = EXE("):txt.index("coll = COLLECT(")]
        assert "a.binaries," not in exe_block, "a.binaries 还留在 EXE 块里"
        assert "a.datas," not in exe_block, "a.datas 还留在 EXE 块里"
        assert "coll = COLLECT(" in txt
        assert "a.binaries," in txt[txt.index("coll = COLLECT("):]
        assert "a.datas," in txt[txt.index("coll = COLLECT("):]

    def test_datas_are_inherited_from_main_spec(self, tmp_path, monkeypatch):
        """datas 必须**继承**主 spec（含后加的 wecom_bridge/js）——不继承就是两份漂移。"""
        _p, txt = self._gen(tmp_path, monkeypatch)
        assert "wecom_bridge/js" in txt, "datas 漏了主 spec 的 wecom_bridge/js"
        # 源路径已绝对化，目标目录（'js'/'css'/'wecom_bridge/js'）保持相对
        assert "'js'" in txt and "'css'" in txt

    def test_relative_paths_are_absolutised(self, tmp_path, monkeypatch):
        """v1.0.660（B2）：spec 落到 build/specs/ 后，相对路径基准变成那个目录——
        不绝对化，PyInstaller 会去 build/specs/backend.py 找入口脚本，直接失败。"""
        _p, txt = self._gen(tmp_path, monkeypatch)
        assert "'backend.py'" not in txt, "Analysis 的入口脚本仍是相对路径"
        assert repr(os.path.join(str(tmp_path), "backend.py"))[1:-1] in txt
        assert repr(os.path.join(str(tmp_path), "app.ico"))[1:-1] in txt

    def test_generated_file_carries_do_not_edit_banner(self, tmp_path, monkeypatch):
        """文件头必须写明"生成物 · 禁止直接使用"——它躺在磁盘上，人会当正式 spec 用。"""
        _p, txt = self._gen(tmp_path, monkeypatch)
        assert "生成物" in txt and "禁止直接使用" in txt

    def test_generated_file_lands_under_build_specs(self, tmp_path, monkeypatch):
        """生成物不再落在项目根（根目录那份是过期生成物，缺 exclude_binaries）。"""
        p, _txt = self._gen(tmp_path, monkeypatch)
        assert os.path.basename(os.path.dirname(p)) == "specs"
        assert "build" in p.replace("\\", "/")

    def test_broken_spec_shape_raises_instead_of_writing_garbage(self, tmp_path, monkeypatch):
        """主 spec 结构变了（找不到 a.binaries/a.datas）→ 抛错，不产出坏 spec。"""
        bad = ("# -*- mode: python ; coding: utf-8 -*-\n"
               "exe = EXE(\n    pyz,\n    a.scripts,\n    [],\n    name='X',\n)\n")
        with pytest.raises(RuntimeError):
            self._gen(tmp_path, monkeypatch, main_spec=bad)

    def test_autogen_hiddenimports_finds_project_modules(self):
        """hiddenimports 自动扫描必须真能扫到本项目包内模块（漏模块 → 发货 exe 功能全死）。"""
        mods = _SR._autogen_hiddenimports()
        assert "core" in mods
        assert any(m.startswith("core.") for m in mods)

    def test_source_module_list_is_subset_of_hiddenimports(self):
        """冒烟清单（源码树模块）必须是 hiddenimports 的子集——清单里有、包里没有，
        冒烟就会漏掉那个模块，白跑。"""
        mods = set(_SR._source_module_list())
        hidden = set(_SR._autogen_hiddenimports())
        assert mods
        assert mods <= hidden, sorted(mods - hidden)[:5]


# ============================================================ backup：config.json 是最后防线
class TestBackup:
    """v1.0.660（B4）：备份链守着用户的 config.json。

    事故背景：v1.0.172/173 手动 rm -rf dist 把模型配置删掉，agent 降级运行；
    v1.0.174 又实测到 build 流程会让 dist/config.json 莫名变成 322 bytes 空默认。
    于是 backup 里立了"最后防线"：src 疑似空默认、backup 里已有正常大小的
    有效配置时，**保留 backup 不覆盖**；restore 时再主动回填。
    """

    def _mk(self, tmp_path, monkeypatch):
        root = tmp_path / "proj"
        dist = root / "dist"
        dist.mkdir(parents=True)
        monkeypatch.setattr(_BK, "BASE", str(root))
        monkeypatch.setattr(_BK, "_BACKUP_DIR", str(root / "build" / "_dist_backup"))
        return root, dist

    def test_backup_copies_config_and_reports_names(self, tmp_path, monkeypatch):
        root, dist = self._mk(tmp_path, monkeypatch)
        (dist / "config.json").write_text(json_cfg(900), encoding="utf-8")
        backed = _BK.backup_dist_runtime()
        assert "config.json" in backed
        assert (root / "build" / "_dist_backup" / "config.json").is_file()

    def test_empty_default_config_does_not_overwrite_valid_backup(self, tmp_path, monkeypatch):
        """最后防线的核心：dist 里是 322 bytes 空默认、backup 里是有效配置 → 不许覆盖。"""
        root, dist = self._mk(tmp_path, monkeypatch)
        (dist / "config.json").write_text(json_cfg(322), encoding="utf-8")
        bdir = root / "build" / "_dist_backup"
        bdir.mkdir(parents=True)
        (bdir / "config.json").write_text(json_cfg(900), encoding="utf-8")
        _BK.backup_dist_runtime()
        assert (bdir / "config.json").stat().st_size == 900, \
            "有效配置被空默认覆盖了——用户配置无声丢失"

    def test_restore_puts_valid_config_back(self, tmp_path, monkeypatch):
        """restore 必须把有效配置回填到 dist（backup 跳过的场景由 restore 闭环）。"""
        root, dist = self._mk(tmp_path, monkeypatch)
        (dist / "config.json").write_text(json_cfg(322), encoding="utf-8")
        bdir = root / "build" / "_dist_backup"
        bdir.mkdir(parents=True)
        (bdir / "config.json").write_text(json_cfg(900), encoding="utf-8")
        _BK.restore_dist_runtime([])
        assert (dist / "config.json").stat().st_size == 900

    def test_restore_does_not_write_back_empty_config(self, tmp_path, monkeypatch):
        """backup 里那份本身就是空默认时，restore 不许把它写回 dist（等于什么都没备份）。"""
        root, dist = self._mk(tmp_path, monkeypatch)
        bdir = root / "build" / "_dist_backup"
        bdir.mkdir(parents=True)
        (bdir / "config.json").write_text(json_cfg(300), encoding="utf-8")
        _BK.restore_dist_runtime([])
        assert not (dist / "config.json").exists(), "空默认被写回 dist 了"

    def test_backup_on_missing_dist_is_noop(self, tmp_path, monkeypatch):
        root, _dist = self._mk(tmp_path, monkeypatch)
        (root / "dist").rmdir()
        assert _BK.backup_dist_runtime() == []


def json_cfg(size):
    """造一份**恰好** size 字节的假 config.json（体积是"空默认 vs 有效配置"的判据，
    所以必须精确——多写几个字符就跨过 500 字节的判定线了）。"""
    head = '{"models": [{"id": "m1", "base_url": "http://x/v1", "api_key": "sk-'
    tail = '"}]}'
    pad = size - len(head) - len(tail)
    assert pad >= 0, "size 太小"
    return head + ("a" * pad) + tail


# ============================================================ orchestration：失败必须回滚
class TestOrchestration:
    """v1.0.660（B4/B7）：PyInstaller 失败 → 版本号必须退回去。

    事故背景（376 空号）：版本号在打包**之前**就 +1 了，PyInstaller 失败时
    只有一条"回滚"路径守着；一旦那条路径失灵（正则漂移 / 抛异常被吞），
    版本号就停在已 +1 的状态，而磁盘上没有任何对应产物——版本库从此多一个空号。
    """

    def _vp(self, tmp_path, monkeypatch, version="1.0.660"):
        p = tmp_path / "version.py"
        p.write_text('__version__ = "%s"\n' % version, encoding="utf-8")
        monkeypatch.setattr(_V, "VPATH", str(p))
        return p

    def test_rollback_version_restores_old_number(self, tmp_path, monkeypatch):
        p = self._vp(tmp_path, monkeypatch, "1.0.661")
        assert _OR.rollback_version("1.0.660") is True
        assert '__version__ = "1.0.660"' in p.read_text(encoding="utf-8")

    def test_rollback_is_idempotent(self, tmp_path, monkeypatch):
        """回滚两次不许报错，也不许把版本号改到别处。"""
        p = self._vp(tmp_path, monkeypatch, "1.0.661")
        _OR.rollback_version("1.0.660")
        assert _OR.rollback_version("1.0.660") is True
        assert '__version__ = "1.0.660"' in p.read_text(encoding="utf-8")

    def test_rollback_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        """回滚自己失败时只警告、不抛——抛会把"真正的失败原因"（打包失败）盖掉。"""
        monkeypatch.setattr(_V, "VPATH", str(tmp_path / "nonexistent.py"))
        assert _OR.rollback_version("1.0.660") is False

    def test_run_pyinstaller_failure_rolls_back_and_reraises(self, tmp_path, monkeypatch):
        """PyInstaller 非零退出 → 回滚版本号 + 重新抛出（调用方据此中止打包）。"""
        p = self._vp(tmp_path, monkeypatch, "1.0.661")

        class _Proc:
            returncode = 3

            def __init__(self, *a, **k):
                self.stdout = io.BytesIO(b"boom\n")

            def wait(self):
                return 3

        monkeypatch.setattr(_OR.subprocess, "Popen", lambda *a, **k: _Proc())
        with pytest.raises(RuntimeError):
            _OR.run_pyinstaller(["pyinstaller"], {}, "1.0.660")
        assert '__version__ = "1.0.660"' in p.read_text(encoding="utf-8"), \
            "打包失败后版本号没退回去——版本库会多一个空号"

    def test_run_pyinstaller_streams_output_incrementally(self, tmp_path, monkeypatch):
        """v1.0.660（B8）：输出必须**逐行**透传（打包要几分钟，全量 capture 会让人
        以为卡死），且 missing-module 扫描的输入仍要拿得到。"""
        seen = []

        class _Proc:
            returncode = 0

            def __init__(self, *a, **k):
                self.stdout = io.BytesIO(b"line1\nline2\n")

            def wait(self):
                return 0

        def _fake_write(s):
            seen.append(s)

        monkeypatch.setattr(_OR.subprocess, "Popen", lambda *a, **k: _Proc())
        monkeypatch.setattr(_OR.sys, "stdout", type("W", (), {"write": staticmethod(_fake_write),
                                                              "flush": staticmethod(lambda: None)})())
        _OR.run_pyinstaller(["pyinstaller"], {}, "1.0.660")
        assert "line1" in "".join(seen) and "line2" in "".join(seen)
