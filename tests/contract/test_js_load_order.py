# -*- coding: utf-8 -*-
"""tests/contract/test_js_load_order.py — ES Module 单一入口 / import 顺序契约的**变异行为锁**。

v1.0.755 建立（锁 classic 18 个 <script src> 顺序）；
v1.0.756（评审 P0-3 字面补做）重写：前端已全量 ES Module，index.html 只留一个
<script type="module" src="js/_entry.js">，_entry.js 按 JS_LOAD_ORDER 顺序写副作用
import。每种「改回坏形状」都必须让校验报错：改回 classic 脚本、删/加 import、
打乱顺序、12-init 提前、删模块文件、加未登记 js、删桥接块、删入口。
"""
import io
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import frontend_check as F  # noqa: E402

_HTML = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
_JS = os.path.join(ROOT, "js")
_ENTRY = io.open(os.path.join(_JS, "_entry.js"), encoding="utf-8").read()


def _check(html, js_dir=None):
    errors = []
    F.check_js_load_order(html, errors, js_dir if js_dir is not None else _JS)
    return errors


def _jsdir(tmp_path, mutate=None):
    """复制一份完整 js/ 到临时目录，可选对目录做变异。"""
    d = tmp_path / "js"
    d.mkdir()
    for fn in sorted(os.listdir(_JS)):
        if fn.endswith(".js"):
            shutil.copyfile(os.path.join(_JS, fn), str(d / fn))
    if mutate:
        mutate(d)
    return str(d)


def _write_entry(d, text):
    io.open(os.path.join(str(d), "_entry.js"), "w", encoding="utf-8").write(text)


# ---------- 现状与单一事实源 ----------

def test_current_html_passes():
    assert _check(_HTML) == []
    assert F.run_all(ROOT) == []


def test_order_contract_is_single_source():
    assert len(F.JS_LOAD_ORDER) == 18
    assert F.JS_LOAD_ORDER[-1] == "js/12-init.js"
    assert len(set(F.JS_LOAD_ORDER)) == 18
    assert F.ESM_ENTRY == "js/_entry.js"


def test_entry_import_order_matches_contract():
    import re
    got = ["js/" + m for m in re.findall(
        r'^\s*import\s+["\']\./([^"\']+)["\']\s*;?\s*$', _ENTRY, flags=re.M)]
    assert got == list(F.JS_LOAD_ORDER), got


def test_entry_is_only_module_script():
    import re
    tags = re.findall(r"<script\b([^>]*)>", _HTML, flags=re.I)
    assert len(tags) == 1
    assert re.search(r'type\s*=\s*["\']module["\']', tags[0], re.I)
    assert re.search(r'src\s*=\s*["\']js/_entry\.js["\']', tags[0], re.I)


# ---------- 变异：改回 classic 形状 ----------

def test_classic_script_regression_is_rejected():
    """把唯一入口改回 classic <script src> → 红。"""
    bad = _HTML.replace('<script type="module" src="js/_entry.js"></script>',
                        '<script src="js/12-init.js"></script>', 1)
    assert bad != _HTML
    assert _check(bad), "改回 classic <script src> 却没被拦住"


def test_module_type_removed_is_rejected():
    bad = _HTML.replace(' type="module"', "", 1)
    assert bad != _HTML
    assert _check(bad), "去掉 type=module 却没被拦住"


def test_extra_classic_script_is_rejected():
    bad = _HTML.replace('<script type="module" src="js/_entry.js"></script>',
                        '<script src="js/99-unknown.js"></script>\n'
                        '<script type="module" src="js/_entry.js"></script>', 1)
    assert _check(bad), "又挂了一个 classic <script src> 却没被拦住"


# ---------- 变异：_entry.js 的 import 清单 ----------

def test_missing_import_is_rejected(tmp_path):
    line = 'import "./10-chat.js";\n'
    assert line in _ENTRY
    errs = _check(_HTML, _jsdir(tmp_path, lambda d: _write_entry(d, _ENTRY.replace(line, "", 1))))
    assert any("10-chat.js" in e for e in errs), errs


def test_extra_import_is_rejected(tmp_path):
    bad = _ENTRY.replace('import "./12-init.js";',
                         'import "./99-unknown.js";\nimport "./12-init.js";', 1)
    errs = _check(_HTML, _jsdir(tmp_path, lambda d: _write_entry(d, bad)))
    assert any("99-unknown" in e or "不一致" in e for e in errs), errs


def test_reordered_init_is_rejected(tmp_path):
    """12-init.js 挪到最前（v1.0.526 事故形态）→ 必须红。"""
    line = 'import "./12-init.js";\n'
    bad = _ENTRY.replace(line, "", 1)
    bad = bad.replace('import "./16-icons.js";', line + 'import "./16-icons.js";', 1)
    errs = _check(_HTML, _jsdir(tmp_path, lambda d: _write_entry(d, bad)))
    assert errs, "12-init 提前却没有被契约拦住"


def test_entry_deleted_is_rejected(tmp_path):
    def _mut(d):
        os.remove(os.path.join(str(d), "_entry.js"))
    errs = _check(_HTML, _jsdir(tmp_path, _mut))
    assert any("_entry.js" in e for e in errs), errs


# ---------- 变异：js/ 目录 ----------

def test_new_js_file_without_declaration_is_rejected(tmp_path):
    """js/ 目录新增文件但没登记进 JS_LOAD_ORDER → 红。"""
    def _mut(d):
        io.open(os.path.join(str(d), "99-new.js"), "w", encoding="utf-8").write("export {};\n")
    errs = _check(_HTML, _jsdir(tmp_path, _mut))
    assert any("99-new.js" in e for e in errs), errs


def test_deleted_js_file_is_rejected(tmp_path):
    def _mut(d):
        os.remove(os.path.join(str(d), "10-chat.js"))
    errs = _check(_HTML, _jsdir(tmp_path, _mut))
    assert any("10-chat.js" in e for e in errs), errs


def test_bridge_removed_is_rejected(tmp_path):
    """去掉某模块的 export/桥接块（退回 classic 全局脚本）→ 红。"""
    def _mut(d):
        p = os.path.join(str(d), "10-chat.js")
        s = io.open(p, encoding="utf-8").read()
        i = s.find(F.ESM_BRIDGE_MARK)
        assert i > 0
        io.open(p, "w", encoding="utf-8").write(s[:s.rfind("/*", 0, i)])
    errs = _check(_HTML, _jsdir(tmp_path, _mut))
    assert any("10-chat.js" in e and "桥接" in e for e in errs), errs
