# -*- coding: utf-8 -*-
"""tools/esm_bridge.py — v1.0.756（评审 P0-3）ES Module 桥接块的**生成器 + 校验器**。

18 个 js/NN-*.js 已全量 ES Module 化：`index.html` 只留一个
<script type="module" src="js/_entry.js">，每个功能模块末尾追加
`export { ... }` + `Object.defineProperties(window, ...)` getter/setter 桥接，
把顶层绑定镜像回 window，保住 211 处内联 on*= 与跨文件共享变量。

`export { ... }` 里的名字必须是模块真实顶层绑定，多一个就 SyntaxError。
本工具用 Node 的 `vm.SourceTextModule`（构造期同步抛
`Export 'X' is not defined in module`）反复剔除「函数内局部变量 / IIFE 内部函数 /
window.X= 创建的属性」，得到一个零猜测误差的清单。为避免上千次启动 node，
校验跑在一个长驻 Node 进程里（stdin 每行一条 base64 源码）。

用法：
    python tools/esm_bridge.py --check    # 快速校验现有桥接块（门禁/日常）
    python tools/esm_bridge.py --apply    # 幂等重新生成 18 个模块 + _entry.js
    python tools/esm_bridge.py --report   # 打印每个模块的桥接符号数

行为等价性（classic vs ESM 全局符号 / 内联事件可见性 / 删模块报依赖方）由
tools/esm_verify.js 负责，本工具只管「桥接块本身是否合法、清单是否自洽」。
"""
import argparse
import base64
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "js")
ENTRY = os.path.join(JS, "_entry.js")

BRIDGE_BEGIN = "/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */"
BRIDGE_END = "/* ===== v1.0.756 ESM BRIDGE END ===== */"

ORDER = [
    "js/16-icons.js", "js/00-boot.js",
    "js/17-composer.js", "js/01-core.js", "js/02-nav-quicklinks.js",
    "js/03-experiences.js", "js/04-skills.js", "js/05-connectors-status.js",
    "js/06-windows.js", "js/07-usage-models.js", "js/08-templates.js",
    "js/09-chat-render.js", "js/10-chat.js", "js/11-workspace-kb-study.js",
    "js/15-asr-component.js", "js/13-dashboard.js", "js/14-mic.js",
    "js/12-init.js",
]

WINDOW_PROTECTED = {
    "name", "length", "top", "self", "parent", "frames", "closed", "status",
    "defaultStatus", "origin", "location", "document", "window", "globalThis",
    "event", "external", "onerror", "onload", "onbeforeunload", "onunload",
    "history", "screen", "navigator", "crypto", "performance", "localStorage",
    "sessionStorage", "innerWidth", "innerHeight", "outerWidth", "outerHeight",
    "devicePixelRatio", "scrollX", "scrollY", "pageXOffset", "pageYOffset",
    "visualViewport", "locationbar", "menubar", "personalbar", "scrollbars",
    "statusbar", "toolbar", "frameElement", "opener", "customElements",
}
KEYWORDS = {
    "var", "let", "const", "function", "class", "if", "for", "return", "while",
    "switch", "catch", "try", "else", "new", "delete", "typeof", "void", "in",
    "of", "this", "null", "true", "false", "undefined", "export", "import",
    "default", "async", "await", "yield", "throw", "break", "continue", "do",
    "case", "extends", "super", "static", "get", "set", "with", "debugger",
    "instanceof", "enum", "implements", "interface", "package", "private",
    "protected", "public",
}

# 长驻 Node 进程：stdin 每行一条 base64 源码 → stdout 一行 OK / UNDEF <name> / ERR <msg>
_WORKER_JS = r"""
const vm = require('node:vm');
const readline = require('node:readline');
const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on('line', (line) => {
  if (!line) return;
  let code;
  try { code = Buffer.from(line, 'base64').toString('utf8'); }
  catch (e) { console.log('ERR bad-input'); return; }
  try {
    new vm.SourceTextModule(code, { identifier: 'bridge-check.mjs' });
    console.log('OK');
  } catch (e) {
    const m = /Export '([^']+)' is not defined/.exec(String(e && e.message || e));
    if (m) console.log('UNDEF ' + m[1]);
    else console.log('ERR ' + String(e && e.message || e).replace(/\s+/g, ' ').slice(0, 180));
  }
});
"""


def read(p):
    with io.open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def node_exe():
    sys.path.insert(0, ROOT)
    from core import portable
    return portable.find_node()


def candidates(src):
    n = set()
    n |= set(re.findall(r"\b(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(", src))
    n |= set(re.findall(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*(?:=|,|;|:)", src))
    n |= set(re.findall(r"\bclass\s+([A-Za-z_$][\w$]*)\b", src))
    for m in re.finditer(r"\b(?:var|let|const)\s*[\[{]([^\]}]+)[\]}]", src):
        n |= set(re.findall(r"[A-Za-z_$][\w$]*", m.group(1)))
    return sorted(x for x in n if x not in KEYWORDS and x not in WINDOW_PROTECTED)


def bridge_block(names, nl="\n"):
    lines = [BRIDGE_BEGIN,
             "/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联",
             " * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type=\"module\"> 后",
             " * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：",
             " * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。",
             " * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */"]
    if names:
        lines.append("export {")
        for i in range(0, len(names), 6):
            lines.append("  " + ", ".join(names[i:i + 6]) + ("," if i + 6 < len(names) else ""))
        lines.append("};")
    else:
        lines.append("export {};")
    if names:
        lines.append("Object.defineProperties(window, {")
        for nm in names:
            lines.append("  %s: { configurable: true, enumerable: true, get(){ return %s; }, set(v){ %s = v; } }," % (nm, nm, nm))
        lines.append("});")
    lines.append(BRIDGE_END)
    return nl + nl.join(lines) + nl


def entry_source():
    lines = [
        "// v1.0.756（评审 P0-3 字面补做）：ES Module 单一入口。",
        "// index.html 只保留这一条 <script type=\"module\">；18 个功能模块按",
        "// JS_LOAD_ORDER 顺序做「副作用 import」——它们彼此没有 import（靠 window",
        "// 全局互调），因此 ESM 规范保证求值顺序 == 原 classic <script src> 顺序，",
        "// 顶层立即执行代码无需重构成 init()，也不存在 TDZ / 循环依赖风险。",
        "// 删掉任一模块 → 模块图解析期即失败并报出本文件这个依赖方（见 tools/esm_verify.js）。",
    ]
    for rel in ORDER:
        lines.append('import "./%s";' % os.path.basename(rel))
    lines.append("export {};")
    return "\n".join(lines) + "\n"


class Worker(object):
    def __init__(self, node):
        self.p = subprocess.Popen(
            [node, "--experimental-vm-modules", "-e", _WORKER_JS],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def check(self, code):
        line = base64.b64encode(code.encode("utf-8")).decode("ascii")
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return (self.p.stdout.readline() or "").strip()

    def close(self):
        # 零 except：新增裸 except-pass 会触发 buildlib.gates 的 271 条硬棘轮
        # （「不许抬基线，改回留痕形状」）。stdin.close() 幂等；只在进程仍活着时
        # terminate，长驻 worker 也会随本进程退出自然结束。
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.terminate()
        else:
            self.p.stdin.close()


def refine(src, cands, worker):
    nl = "\r\n" if "\r\n" in src[:2000] else "\n"
    cur = list(cands)
    for _ in range(len(cur) + 10):
        r = worker.check(src + bridge_block(cur, nl))
        if r == "OK":
            return cur, None
        if r.startswith("UNDEF "):
            name = r[len("UNDEF "):]
            if name in cur:
                cur.remove(name)
                continue
        return None, r
    return None, "refine 迭代次数超限"


def strip_bridge(src):
    i = src.find(BRIDGE_BEGIN)
    return src[:i].rstrip() if i >= 0 else src.rstrip()


def bridged_names(src):
    """从桥接块的 defineProperties 行提取已生成的清单。"""
    return sorted(set(re.findall(
        r"^\s*([A-Za-z_$][\w$]*): \{ configurable: true, enumerable: true, get\(\)",
        src, flags=re.M)))


def exported_names(src):
    """从桥接块的 `export { ... };` 提取名字（支持多行、逗号结尾）。"""
    i = src.find(BRIDGE_BEGIN)
    if i < 0:
        return None
    seg = src[i:]
    m = re.search(r"export\s*\{(.*?)\}\s*;", seg, flags=re.S)
    if not m:
        return None
    return sorted(set(re.findall(r"[A-Za-z_$][\w$]*", m.group(1))))


def check(node):
    """快速校验：整文件 module 语法 + 桥接块自洽 + 危险名/重复名。"""
    errors = []
    seen = {}
    total = 0
    for rel in ORDER:
        fp = os.path.join(JS, os.path.basename(rel))
        if not os.path.isfile(fp):
            errors.append("缺文件：%s" % rel)
            continue
        src = read(fp)
        if BRIDGE_BEGIN not in src or BRIDGE_END not in src:
            errors.append("%s 缺 ESM 桥接块标记" % rel)
            continue
        exp, br = exported_names(src), bridged_names(src)
        if exp is None or exp != br:
            errors.append("%s export 清单与 defineProperties 清单不一致（export=%s / bridge=%s）"
                          % (rel, exp, br))
        names = br or []
        total += len(names)
        for nm in names:
            if nm in WINDOW_PROTECTED:
                errors.append("%s 桥接了 window 原生属性 %s" % (rel, nm))
            seen.setdefault(nm, []).append(rel)
        r = subprocess.run([node, "--check", "--input-type=module"],
                           input=src, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            errors.append("%s module 语法校验失败：%s" % (rel, (r.stderr or "").strip()[-200:]))
    for nm, owners in sorted(seen.items()):
        if len(owners) > 1:
            errors.append("符号 %s 被多个模块桥接：%s" % (nm, ", ".join(owners)))
    if not os.path.isfile(ENTRY):
        errors.append("缺单一入口 js/_entry.js")
    else:
        got = ["js/" + m for m in re.findall(
            r'^\s*import\s+["\']\./([^"\']+)["\']\s*;?\s*$', read(ENTRY), flags=re.M)]
        if got != ORDER:
            errors.append("_entry.js import 清单与 ORDER 不一致")
    return errors, total


def apply(node):
    worker = Worker(node)
    try:
        plan = {}
        for rel in ORDER:
            fp = os.path.join(JS, os.path.basename(rel))
            src = strip_bridge(read(fp))
            names, err = refine(src, candidates(src), worker)
            if names is None:
                return ["%s refine 失败：%s" % (rel, err)], 0
            nl = "\r\n" if "\r\n" in src[:2000] else "\n"
            plan[fp] = src + bridge_block(names, nl)
        for fp, body in plan.items():
            write(fp, body)
        write(ENTRY, entry_source())
        return [], sum(len(bridged_names(v)) for v in plan.values())
    finally:
        worker.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="ES Module 桥接块生成 / 校验")
    ap.add_argument("--check", action="store_true", help="校验现有桥接块（默认）")
    ap.add_argument("--apply", action="store_true", help="幂等重新生成 18 个模块 + _entry.js")
    ap.add_argument("--report", action="store_true", help="打印每模块桥接符号数")
    a = ap.parse_args(argv)
    node = node_exe()
    if not node:
        print("[esm-bridge] 未找到 node，无法校验（环境性跳过）")
        return 0
    if a.apply:
        errors, total = apply(node)
        action = "重新生成"
    else:
        errors, total = check(node)
        action = "校验"
    if a.report:
        for rel in ORDER:
            print("  %-28s %3d 个桥接符号"
                  % (rel, len(bridged_names(read(os.path.join(JS, os.path.basename(rel)))))))
    if errors:
        print("[esm-bridge] ❌ %s不通过：" % action)
        for e in errors:
            print("  " + e)
        return 1
    print("[esm-bridge] ✓ %s通过（18 个模块 / %d 个桥接符号 / 单一入口 js/_entry.js）"
          % (action, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
