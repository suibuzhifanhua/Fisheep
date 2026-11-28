# -*- coding: utf-8 -*-
"""tests/contract/test_publish_github_path.py — 公共镜像刷新的 sys.path 行为锁。

v1.0.756 事故：B3 给 `tools/publish_github.py::rebuild_mirror()` 接了
`from buildlib.release import write_release_metadata`，但 build.py 是用
`subprocess.run([sys.executable, "tools/publish_github.py", "--rebuild"])` 跑的，
此时 sys.path[0] 只有 tools/、不含仓库根 → `No module named 'buildlib'`。
构建日志里只是一句「⚠️ 镜像不更新（保留上一版）」，主构建仍 exit 0，
非常容易被当成成功漏过去。

「改回坏形状必红」：删掉脚本顶部的 sys.path 修正，下面两条都会红。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "tools", "publish_github.py")


def test_script_puts_repo_root_on_sys_path():
    src = io.open(SCRIPT, encoding="utf-8").read()
    assert "sys.path.insert(0, BASE)" in src, (
        "缺仓库根 sys.path 修正——独立脚本形态下 import buildlib 会失败")


def test_buildlib_release_importable_in_script_only_sys_path():
    """复现 build.py 的调用形态：sys.path 里没有仓库根，执行脚本模块级代码后
    仍必须能 import buildlib.release（发布元数据接线的依赖）。"""
    tools = os.path.join(ROOT, "tools")
    code = (
        "import sys, runpy\n"
        "root = %r\n"
        "sys.path[:] = [%r] + [p for p in sys.path if p and p != root]\n"
        "runpy.run_path(%r, run_name='publish_probe')\n"
        "import buildlib.release\n"
        "print('OK')\n"
    ) % (ROOT, tools, SCRIPT)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        "脚本独立运行形态下 import buildlib 失败：\n" + (r.stderr or "")[-1200:])
    assert "OK" in r.stdout
