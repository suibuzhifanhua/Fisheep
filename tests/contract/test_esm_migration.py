# -*- coding: utf-8 -*-
"""tests/contract/test_esm_migration.py — v1.0.756（评审 P0-3 字面补做）ES Module 全量迁移
的**端到端行为等价性**行为锁。

`node --check` 只能查语法，查不出「符号被收进模块作用域 → 211 处内联 on*= 全废」
「顶层 const 跨模块读不到」「共享 var 的写不再同步回全局」这类只有实跑界面才
暴露的事故（README 6.1 / 6.2 的黑屏白屏正是这类）。所以调用
`tools/esm_verify.js`（Node vm.SourceTextModule，真实 ESM 语义）：

  Phase A  classic <script> 顺序执行（剥离桥接块） → G_classic
  Phase B  ES Module 顺序求值（桥接生效）        → G_esm
  断言 G_esm ⊇ G_classic、147 个内联事件符号全部可见、零求值异常；
  并用 vm linker 逐个「模拟删除模块」，验证模块图解析期就报出依赖方 _entry.js。

变异锁：在临时项目副本上删桥接 / 改回 classic <script src> / 删模块文件，
验证器必须 ok=False——防止「验证器自己变成永远绿的摆设」。
"""
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import portable  # noqa: E402

_NODE = portable.find_node()
_VERIFIER = os.path.join(ROOT, "tools", "esm_verify.js")
pytestmark = pytest.mark.skipif(not _NODE, reason="无 node：ESM 求值验证跳过（环境性红不进基线）")


def _run(root=None):
    env = dict(os.environ)
    if root:
        env["FISHEEP_ESM_ROOT"] = str(root)
    r = subprocess.run([_NODE, "--experimental-vm-modules", _VERIFIER],
                       capture_output=True, text=True, env=env, timeout=180)
    lines = [x for x in (r.stdout or "").strip().splitlines() if x.startswith("{")]
    assert lines, (r.returncode, r.stdout[-500:], r.stderr[-500:])
    return r, json.loads(lines[-1])


def _copy_project(tmp_path, mutate=None):
    """最小项目副本：index.html + js/（验证器只读这两处）。"""
    d = tmp_path / "proj"
    (d / "js").mkdir(parents=True)
    shutil.copyfile(os.path.join(ROOT, "index.html"), str(d / "index.html"))
    for fn in sorted(os.listdir(os.path.join(ROOT, "js"))):
        if fn.endswith(".js"):
            shutil.copyfile(os.path.join(ROOT, "js", fn), str(d / "js" / fn))
    if mutate:
        mutate(d)
    return d


# ---------- 正例 ----------

def test_verifier_reports_full_equivalence():
    r, d = _run()
    assert r.returncode == 0, (r.returncode, r.stderr[-500:])
    assert d.get("ok") is True, d.get("errors")
    assert d["module_count"] == 18
    assert d["bridge_count"] == 18
    assert len(d["syntax_ok"]) == 18
    assert d["eval_errors"] == {}, d["eval_errors"]
    assert d["missing_in_esm"] == [], d["missing_in_esm"]
    assert d["inline_missing"] == [], d["inline_missing"]
    assert d["inline_symbols"] >= 140
    assert d["classic_symbols"] > 500
    # 删任一模块 → 解析期报错且同时指明模块与依赖方
    reports = d["deleted_module_reports"]
    assert len(reports) == 18
    for rel, msg in reports.items():
        assert "_entry.js" in msg and os.path.basename(rel) in msg, (rel, msg)


def test_bridge_generator_self_check_passes():
    """tools/esm_bridge.py --check 必须绿：export 清单 == defineProperties 清单、
    无 window 原生属性被覆盖、跨模块无重复桥接、18 个模块 module 语法通过。"""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "esm_bridge.py"),
                        "--check"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout[-600:], r.stderr[-600:])
    assert "校验通过" in r.stdout and "18 个模块" in r.stdout, r.stdout


def test_extra_esm_symbols_are_the_bridged_lexical_consts():
    """ESM 比 classic 多出的符号必须是顶层 let/const（classic 下只在全局词法环境，
    不挂 window）——桥接把它们也显式化了，这正是跨模块读取能继续工作的原因。"""
    r, d = _run()
    assert d["ok"] is True
    extra = set(d["extra_in_esm"])
    assert "$" in extra and "FOLLOW_MS" in extra, sorted(extra)[:20]
    assert d["missing_in_esm"] == []


# ---------- 变异锁 ----------

def test_removing_bridge_makes_verifier_fail(tmp_path):
    def _mut(d):
        p = d / "js" / "01-core.js"
        s = io.open(str(p), encoding="utf-8").read()
        i = s.find("/* ===== v1.0.756 ESM BRIDGE BEGIN")
        assert i > 0
        io.open(str(p), "w", encoding="utf-8").write(s[:s.rfind("/*", 0, i)])
    r, d = _run(_copy_project(tmp_path, _mut))
    assert d.get("ok") is False, d
    assert d.get("errors") or d.get("fatal")


def test_classic_script_regression_makes_verifier_fail(tmp_path):
    def _mut(d):
        p = d / "index.html"
        s = io.open(str(p), encoding="utf-8").read()
        s = s.replace('<script type="module" src="js/_entry.js"></script>',
                      '<script src="js/12-init.js"></script>', 1)
        io.open(str(p), "w", encoding="utf-8").write(s)
    r, d = _run(_copy_project(tmp_path, _mut))
    assert d.get("ok") is False, d
    assert d.get("errors") or d.get("fatal")


def test_deleting_module_makes_verifier_fail(tmp_path):
    def _mut(d):
        os.remove(str(d / "js" / "10-chat.js"))
    r, d = _run(_copy_project(tmp_path, _mut))
    assert d.get("ok") is False, d
    assert d.get("errors") or d.get("fatal")


def test_inline_handler_symbol_visibility_is_checked(tmp_path):
    """把 index.html 里一个真实存在、但故意改名成不存在的处理器塞进去，
    验证器必须报 inline_missing（防止内联符号提取被改成永远空集）。"""
    def _mut(d):
        p = d / "index.html"
        s = io.open(str(p), encoding="utf-8").read()
        s = s.replace("<body>", '<body><div onclick="__esm_never_defined_fn()"></div>', 1)
        io.open(str(p), "w", encoding="utf-8").write(s)
    r, d = _run(_copy_project(tmp_path, _mut))
    assert d.get("ok") is False, d
    assert "__esm_never_defined_fn" in d["inline_missing"], d["inline_missing"]
