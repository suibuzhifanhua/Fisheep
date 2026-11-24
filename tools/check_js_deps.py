# -*- coding: utf-8 -*-
"""tools/check_js_deps.py — ES Module 单一入口 / import 顺序契约 CLI + 桥接符号报告。

v1.0.755（评审 P0-3）建立脚本顺序契约；v1.0.756（字面补做）前端已全量 ES Module：
index.html 只留一个 <script type="module" src="js/_entry.js">，_entry.js 按
JS_LOAD_ORDER 顺序写副作用 import，每个模块末尾 export + getter/setter 桥接回 window。
契约实现放在 core/frontend_check.py（随包发布、被打包门禁调用），本文件只是 CLI 壳，
与 tools/check_frontend.py 同一范式——避免两份实现各自演化。
端到端行为等价验证见 tools/esm_verify.js（tests/contract/test_esm_migration.py 调它）。

用法：
    python tools/check_js_deps.py            # 校验契约，不符退出码 1
    python tools/check_js_deps.py --report   # 额外打印跨文件符号依赖近似报告
"""
import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.frontend_check import JS_LOAD_ORDER, check_js_load_order  # noqa: E402


def _toplevel_symbols(path):
    """近似提取 js 文件顶层定义的具名函数 / window.X 挂载（文本启发式）。"""
    text = io.open(path, encoding="utf-8", errors="replace").read()
    defs = set(re.findall(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
                          text, flags=re.M))
    defs |= set(re.findall(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
                           text, flags=re.M))
    defs |= set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", text))
    return defs


def report(root=ROOT):
    """打印「模块求值顺序 + 每个模块 export/桥接了多少顶层符号」。"""
    js_dir = os.path.join(root, "js")
    print("ES Module import 顺序（%d 项，入口 js/_entry.js）：" % len(JS_LOAD_ORDER))
    total = 0
    for i, rel in enumerate(JS_LOAD_ORDER, 1):
        fp = os.path.join(js_dir, os.path.basename(rel))
        bridged = set()
        if os.path.isfile(fp):
            text = io.open(fp, encoding="utf-8", errors="replace").read()
            bridged = set(re.findall(r"^\s*([A-Za-z_$][\w$]*): \{ configurable: true",
                                     text, flags=re.M))
        total += len(bridged)
        print("  %2d. %-32s 桥接符号 %3d 个%s"
              % (i, rel, len(bridged), "  [最后·初始化]" if i == len(JS_LOAD_ORDER) else ""))
    print("\n合计桥接 %d 个顶层绑定（含 classic 下只在全局词法环境的 let/const）。" % total)
    print("行为等价（classic vs ESM 全局符号 / 内联事件可见性 / 删模块报依赖方）："
          "node --experimental-vm-modules tools/esm_verify.js")
    print("迁移设计与验收：docs/frontend-module-plan.md、tests/contract/test_esm_migration.py。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ES Module 单一入口 / import 顺序契约校验")
    ap.add_argument("--report", action="store_true", help="打印跨文件符号依赖近似报告")
    ap.add_argument("--root", default=ROOT, help="项目根（默认本仓根）")
    a = ap.parse_args(argv)
    root = a.root
    html = io.open(os.path.join(root, "index.html"), encoding="utf-8").read()
    errors = []
    check_js_load_order(html, errors, os.path.join(root, "js"))
    if errors:
        print("[js-deps] ❌ ES Module 入口 / import 顺序契约不通过：")
        for e in errors:
            print("  " + e)
        return 1
    print("[js-deps] ✓ ES Module 契约通过（单一入口 + %d 项 import，12-init.js 最后）"
          % len(JS_LOAD_ORDER))
    if a.report:
        report(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
