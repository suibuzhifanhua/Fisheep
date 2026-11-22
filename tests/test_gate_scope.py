# -*- coding: utf-8 -*-
"""tests/test_gate_scope.py — 打包门禁分层护栏（v1.0.677）。

用户："每次优化修改打包和测试做回归的效率都不高"。
build 门禁从「恒跑全量 1317 条（~4min）」改为「按 git diff 判改动面」：
纯展示资产改动（图标/文案/样式/README/版本元信息）→ fast 层 301 条（~40s）；
含逻辑改动或判定失败 → 全量（fail-closed）。全量层加 xdist -n 8 并行。

本文件锁死三件事（任一被改坏即红）：
  ① classify 判定矩阵——fast 白名单命中 / 逻辑改动 / 未知扩展名 / css 目录限制 /
     空改动，全部 fail-closed 到 full 的语义；
  ② FAST_FILES 清单必须是真实存在的测试文件（漏一个 build 会在打包时才炸，
     这里提前锁）；
  ③ build.py 调用契约——_run_pytest_gate(full_only=...) 形参存在、gate_scope
     已 import、--full-tests 参数已注册（防止分层被静默删掉退化成"只快了没锁"）。
"""
import os
import re

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ① 判定矩阵 ---------------------------------------------------------------

def test_fast_scope_on_pure_display_assets():
    """图标/文案/样式/README/版本元信息全落 → fast（675→676 真实批次的形状）。"""
    from buildlib import gate_scope as g
    scope, files, _why = g.classify(BASE, changed=[
        "app.ico", "tray.ico", "index.html", "css/style.css",
        "README.md", "version.py", "version_info.txt", "CHANGELOG.md"])
    assert scope == "fast", _why
    assert "app.ico" in files


def test_full_scope_when_python_changed():
    from buildlib import gate_scope as g
    scope, _f, _w = g.classify(BASE, changed=["app.ico", "backend.py"])
    assert scope == "full"


def test_full_scope_when_js_changed():
    """前端 JS 是运行时行为，静态锁覆盖不到 → 必须全量。"""
    from buildlib import gate_scope as g
    scope, _f, _w = g.classify(BASE, changed=["js/10-chat.js"])
    assert scope == "full"


def test_full_scope_when_css_outside_css_dir():
    from buildlib import gate_scope as g
    assert g.classify(BASE, changed=["css/style.css"])[0] == "fast"
    assert g.classify(BASE, changed=["js/style.css"])[0] == "full"


def test_full_scope_when_unknown_extension():
    from buildlib import gate_scope as g
    assert g.classify(BASE, changed=["foo.xyz"])[0] == "full"
    assert g.classify(BASE, changed=["build/specs/x.spec"])[0] == "full"


def test_empty_change_is_full_not_fast():
    """无改动可判定 = 拿不准 = 全量（不能因为"没东西可改"就少测）。"""
    from buildlib import gate_scope as g
    assert g.classify(BASE, changed=[])[0] == "full"


# ② FAST_FILES 清单真实性 ---------------------------------------------------

def test_fast_files_all_exist_and_collectible():
    from buildlib import gate_scope as g
    missing = [f for f in g.FAST_FILES if not os.path.isfile(os.path.join(BASE, f))]
    assert not missing, f"fast 层引用了不存在的测试文件：{missing}"
    # 每个 fast 文件都能被 pytest 收集到至少 1 条（空文件/收集失败 = 白名单漏洞）
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *g.FAST_FILES, "--co", "-q", "-p", "no:cacheprovider"],
        cwd=BASE, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    out = r.stdout
    assert "tests collected" in out
    n = int(re.search(r"(\d+) tests? collected", out).group(1))
    assert n >= 250, f"fast 层测试数异常缩水：{n}"


# ③ build.py 调用契约 -------------------------------------------------------

def test_build_py_wires_gate_scope():
    """build.py 必须真的接了分层：import、形参、CLI 三处任一缺失 = 分层被拆掉。"""
    src = open(os.path.join(BASE, "build.py"), encoding="utf-8").read()
    assert "from buildlib import gate_scope" in src, "build.py 未 import gate_scope——分层失效"
    assert "def _run_pytest_gate(full_only=False)" in src, "门禁形参 full_only 被删"
    assert 'dest="full_tests"' in src or '"--full-tests"' in src, "--full-tests 逃生口被删"
    assert "gate_scope.classify(BASE)" in src, "门禁调用点没接 classify 判定"
    assert "gate_scope.FAST_FILES" in src, "fast 层清单引用被删"


def test_parallel_flags_only_on_full_layer():
    """并行（-n 8）只允许加在全量层；fast 层串行。锁 cmd 拼装顺序。"""
    src = open(os.path.join(BASE, "build.py"), encoding="utf-8").read()
    m = re.search(r"if targets is None and _xdist_ok\(\):", src)
    assert m, "全量层并行分支被删/改坏"
    assert '"-n", "8", "--dist", "loadfile"' in src
