# -*- coding: utf-8 -*-
"""tests/contract/test_contract_frontend_escape.py — 内联事件/属性转义的行为锁。

事故形态：``jsStr`` 的产物要过**两层**解析——HTML 属性实体解码，然后才是 JS
字符串字面量求值。原实现转了 \\ ' " 换行却漏了 ``&``：数据里原样写着 ``&quot;``
时，浏览器先把它解成真引号，于是跳出属性边界注入事件。一个字符漏转义 = 存储型
XSS（模型/模板/技能的 id 与名称都来自可导入的外部数据）。

本锁真跑 node：把 01-core.js 的转义函数装进 vm，按「拼属性 → 实体解码 → JS 求值」
的真实顺序走一遍，断言拿回来的值与输入逐字相同；并用**修复前的旧实现**做阳性
对照（旧实现必须过不了同一条断言），防止锁本身变成永远绿的摆设。

另含两条结构锁（无法在 mock DOM 下真跑，理由写在各自 docstring 里）。
"""
import glob
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from core import portable  # noqa: E402


def _node():
    p = portable.find_node()
    if p:
        return p
    # 开发树 workspace_root() 指向源码侧 产物库，便携 node 实际装在 dist 侧；
    # 不回落就等于这条锁在开发机上永远 skip。
    hits = sorted(glob.glob(os.path.join(ROOT, "dist", "产物库", "组件", "node",
                                         "**", "node.exe"), recursive=True))
    return hits[0] if hits else None


_NODE = _node()


def _prelude():
    """01-core.js 开头到 jsStr 声明为止——纯声明段，无 DOM 副作用。"""
    src = open(os.path.join(ROOT, "js", "01-core.js"), encoding="utf-8").read()
    i = src.index("\nfunction jsStr(")
    chunk = src[:src.index("\n", i + 1)]
    assert "function escapeHtml(" in chunk, "escapeHtml 不在 jsStr 之前，抽取口径需更新"
    return chunk


_HARNESS = r"""
const vm = require('node:vm');
const ctx = {};
vm.runInNewContext(__PRELUDE__, ctx);
const jsStr = ctx.jsStr, escapeHtml = ctx.escapeHtml;
if (typeof jsStr !== 'function' || typeof escapeHtml !== 'function')
  throw new Error('转义函数没装进沙箱');

// 浏览器对属性值做的实体解码（覆盖两个转义器会产出的全部实体 + 数字实体）
const NAMED = {amp:'&', lt:'<', gt:'>', quot:'"', '#39':"'", apos:"'", nbsp:'\u00a0'};
function htmlDecode(s){
  return String(s).replace(/&(amp|lt|gt|quot|#39|apos|nbsp);|&#(\d+);|&#x([0-9a-fA-F]+);/g,
    (m, n, d, h) => n !== undefined ? NAMED[n]
      : String.fromCodePoint(d !== undefined ? +d : parseInt(h, 16)));
}
// 真实顺序：拼进 onclick 属性 → 实体解码 → 当 JS 求值。
// 求值抛异常也算失败：跳出字符串边界后要么语法错、要么执行了注入代码。
function roundTrip(esc, s){
  const decoded = htmlDecode("f('" + esc(s) + "')");
  let got, threw = false;
  try { vm.runInNewContext(decoded, {f: (x) => { got = x; }}); }
  catch (e) { threw = true; }
  return !threw && got === s;
}
// 修复前的实现（阳性对照用）：漏 &
function oldJsStr(s){ return (s||"").replace(/\\/g,"\\\\").replace(/'/g,"\\'").replace(/"/g,"&quot;").replace(/\r?\n/g,"\\n"); }

const ATTACKS = [
  'a&quot; onmouseover=alert(1) x=&quot;',
  'a&#39; onfocus=alert(1) autofocus y=&#39;',
  "a'b", 'a"b', 'a\\b', 'a&b', 'line1\nline2',
  '<img src=x onerror=alert(1)>', "');alert(1);//", '&amp;',
];
const out = {roundtrip: [], rawQuote: [], htmlRoundtrip: [], oldFails: [], coerce: {}};
for (const s of ATTACKS){
  out.roundtrip.push([s, roundTrip(jsStr, s)]);
  out.rawQuote.push([s, !String(jsStr(s)).includes('"')]);
  out.htmlRoundtrip.push([s, htmlDecode(escapeHtml(s)) === s]);
  out.oldFails.push([s, !roundTrip(oldJsStr, s)]);
}
out.coerce = {zero: jsStr(0), nul: jsStr(null), undef: jsStr(undefined), num: jsStr(42)};
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(not _NODE, reason="无 node：转义行为验证跳过")
def test_inline_event_escape_survives_both_parse_layers(tmp_path):
    f = tmp_path / "escape_probe.js"
    f.write_text(_HARNESS.replace("__PRELUDE__", json.dumps(_prelude())),
                 encoding="utf-8")
    r = subprocess.run([_NODE, str(f)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stdout[-800:], r.stderr[-800:])
    out = json.loads(r.stdout)

    bad = [s for s, ok in out["roundtrip"] if not ok]
    assert not bad, "拼进内联 onclick 后取不回原值（属性/JS 注入面）：%r" % bad
    bad = [s for s, ok in out["rawQuote"] if not ok]
    assert not bad, "转义结果里仍有裸双引号，可跳出 HTML 属性边界：%r" % bad
    bad = [s for s, ok in out["htmlRoundtrip"] if not ok]
    assert not bad, "escapeHtml 后的属性值解不回原值：%r" % bad

    # 阳性对照：旧实现必须在 &quot; 这条上翻车，否则本锁根本没在测东西
    assert out["oldFails"][0][1] is True, "旧实现也能过——这条锁是假绿"

    assert out["coerce"] == {"zero": "0", "nul": "", "undef": "", "num": "42"}, \
        "非字符串入参口径应与 escapeHtml 一致（原 (s||\"\") 对数字直接 .replace 会抛）"


# ---- 以下两条为结构锁 ----
# 断流重试与 data-id 都发生在真实浏览器 DOM/网络栈里，mock 出来要桩掉十几个
# 前端全局（renderRich/consumeStream/__chipState/...），桩本身比被测逻辑还大，
# 反而更容易假绿。这两条改为形状锁：判据是「不许出现某种写法」，不是文案匹配。

def test_stream_retry_never_downgrades_continue_to_new_chat():
    """断流重试把 /api/chat/continue 改写成 /api/chat 时，body 里没有 messages：
    后端走新会话分支 → drop_chat_state() 清断点、readonly 回落 false。
    一次网络抖动就把「继续」变成空对话，并把只问答会话悄悄改成可写。"""
    hits = []
    for p in sorted(glob.glob(os.path.join(ROOT, "js", "*.js"))):
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r'url\s*=\s*url\.replace\([^)]*"/api/chat"', src):
            hits.append((os.path.basename(p), m.group(0)))
    assert not hits, "重试把 continue 降级成新会话：%r" % hits


def test_plain_html_attributes_do_not_use_js_literal_escaper():
    """jsStr 产出的是 **JS 字符串字面量**（' → \\'、\\ → \\\\）。用在普通 HTML
    属性（id / data-*）上时，dataset 读回来的是带反斜杠的错值——按 id 找元素、
    把 id 回传后端都会对不上。普通属性只能用 escapeHtml。"""
    pat = re.compile(
        r'\b(?:data-[a-z-]+|id|class|title|href|src|value|aria-label)\s*=\s*"\$\{jsStr\(')
    hits = []
    for p in sorted(glob.glob(os.path.join(ROOT, "js", "*.js"))):
        src = open(p, encoding="utf-8").read()
        for m in pat.finditer(src):
            hits.append((os.path.basename(p), m.group(0)))
    assert not hits, "普通 HTML 属性误用 JS 字面量转义器：%r" % hits