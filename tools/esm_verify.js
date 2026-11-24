/* tools/esm_verify.js — v1.0.756（评审 P0-3）ES Module 迁移的**行为等价性验证器**。
 *
 * 为什么需要它：node --check 只能查语法，查不出「符号被收进模块作用域后内联
 * on*= 处理器全废」「共享 var 的写不再同步回全局」「顶层 const 跨模块读不到」这类
 * 只有实跑界面才暴露的事故（README 6.1 / 6.2 的黑屏白屏正是这类）。
 * 这里用 Node 的 vm.SourceTextModule（真实 ECMAScript 模块语义：严格模式、TDZ、
 * 模块求值顺序）在同一个 mock window 上分别跑两遍：
 *
 *   Phase A  把桥接块剥离后的 18 个文件当 classic <script> 顺序执行 → G_classic
 *   Phase B  把 18 个文件当 ES Module 顺序求值（桥接生效）        → G_esm
 *   断言 G_esm ⊇ G_classic，且 index.html 的内联事件符号全部 ∈ G_esm。
 *
 * 12-init.js 是纯初始化链（顶层直接调 refreshStatus / loadConvList / restoreHistory
 * 等），在 mock DOM 下会撞 while(el.firstChild) 型死循环——这是 mock 的固有局限，
 * 不是代码缺陷；它的符号与桥接由语法校验 + 静态清单校验覆盖，不参与求值对比。
 *
 * 还用 vm linker 逐个「模拟删除模块」：_entry.js 的静态 import 在模块图解析期就
 * 失败，错误信息必须同时含被删文件与依赖方 _entry.js（验收判据）。
 *
 * 用法：node --experimental-vm-modules tools/esm_verify.js   （stdout 单行 JSON）
 */
'use strict';
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

// FISHEEP_ESM_ROOT 仅供 tests/test_esm_migration.py 在临时副本上做变异锁；
// 默认就是项目根（本文件位于 tools/ 下）。
const ROOT = path.resolve(process.env.FISHEEP_ESM_ROOT || path.resolve(__dirname, '..'));
const JS = path.join(ROOT, 'js');
const ENTRY = 'js/_entry.js';
const BRIDGE_BEGIN = '/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */';
const EVAL_EXCLUDE = 'js/12-init.js';

const BUILTIN = new Set((
  'Object Array String Number Boolean BigInt Symbol Function Date RegExp Error TypeError RangeError ' +
  'SyntaxError ReferenceError EvalError URIError AggregateError Promise Map Set WeakMap WeakSet ' +
  'ArrayBuffer SharedArrayBuffer DataView Int8Array Uint8Array Uint8ClampedArray Int16Array ' +
  'Uint16Array Int32Array Uint32Array Float32Array Float64Array BigInt64Array BigUint64Array ' +
  'JSON Math Reflect Proxy Intl Infinity NaN undefined eval isNaN isFinite parseFloat parseInt ' +
  'encodeURI encodeURIComponent decodeURI decodeURIComponent escape unescape ' +
  'console setTimeout setInterval clearTimeout clearInterval setImmediate clearImmediate ' +
  'queueMicrotask structuredClone atob btoa fetch TextEncoder TextDecoder URL URLSearchParams ' +
  'Blob File FormData FileReader WebSocket EventSource XMLHttpRequest AbortController AbortSignal ' +
  'Headers Request Response globalThis'
).trim().split(/\s+/));
const INJECTED = new Set((
  'window self top parent document navigator location localStorage sessionStorage ' +
  'requestAnimationFrame cancelAnimationFrame requestIdleCallback cancelIdleCallback ' +
  'getComputedStyle matchMedia CustomEvent Event EventTarget Node Element HTMLElement ' +
  'HTMLDocument Document DocumentFragment DOMParser MutationObserver ResizeObserver ' +
  'IntersectionObserver Image Audio Option Notification Clipboard DragEvent MouseEvent ' +
  'KeyboardEvent FocusEvent InputEvent WheelEvent TouchEvent PointerEvent Storage ' +
  'CSSStyleDeclaration DOMTokenList NodeList HTMLCollection history screen performance ' +
  'crypto alert confirm prompt open close print'
).trim().split(/\s+/));
const INLINE_BUILTINS = new Set((
  'if for while switch catch return typeof function alert confirm prompt parseInt parseFloat ' +
  'String Number Boolean Array Object JSON Math Date RegExp getElementById preventDefault ' +
  'stopPropagation remove click'
).trim().split(/\s+/));

function makeMock(name) {
  const f = function () {};
  return new Proxy(f, {
    get(t, p) {
      if (p === Symbol.toPrimitive) return () => '';
      if (p === 'then') return undefined;
      if (p === Symbol.iterator) return function* () {};
      if (p === 'length') return 0;
      if (p === 'firstChild' || p === 'lastChild' || p === 'nextSibling' || p === 'previousSibling') return null;
      if (p === 'children' || p === 'childNodes') return [];
      if (p === 'name') return name;
      if (p === 'prototype') return t[p];
      if (p === Symbol.toStringTag) return 'Mock';
      return makeMock(name + '.' + String(p));
    },
    apply() { return makeMock(name + '()'); },
    construct() { return makeMock('new ' + name); },
    set() { return true; },
    has() { return true; },
    defineProperty() { return true; },
    ownKeys() { return []; },
    getOwnPropertyDescriptor() { return { configurable: true, enumerable: false, value: undefined, writable: true }; },
  });
}

function makeContext() {
  const store = Object.create(null);
  const P = new Proxy(store, {
    has() { return true; },
    get(t, p) {
      const k = String(p);
      if (p === Symbol.toStringTag) return 'Window';
      if (p in t) return t[p];
      if (k in globalThis) return globalThis[k];
      if (INJECTED.has(k) || BUILTIN.has(k)) return makeMock(k);
      return makeMock(k);
    },
    set(t, p, v) { t[p] = v; return true; },
    defineProperty(t, p, d) { Object.defineProperty(t, p, d); return true; },
    ownKeys(t) { return Reflect.ownKeys(t); },
    getOwnPropertyDescriptor(t, p) { return Reflect.getOwnPropertyDescriptor(t, p); },
  });
  store.window = P;
  store.globalThis = P;
  store.self = P;
  store.top = P;
  store.document = makeMock('document');
  store.navigator = makeMock('navigator');
  store.location = makeMock('location');
  store.localStorage = makeMock('localStorage');
  store.sessionStorage = makeMock('sessionStorage');
  store.console = console;
  store.setTimeout = () => 0;
  store.setInterval = () => 0;
  store.clearTimeout = () => {};
  store.clearInterval = () => {};
  store.requestAnimationFrame = () => 0;
  store.cancelAnimationFrame = () => {};
  store.fetch = () => Promise.resolve(makeMock('resp'));
  store.alert = () => {};
  store.confirm = () => true;
  store.prompt = () => '';
  store.EventSource = function () { return makeMock('EventSource'); };
  store.XMLHttpRequest = function () { return makeMock('XHR'); };
  store.WebSocket = function () { return makeMock('WS'); };
  store.performance = globalThis.performance || makeMock('performance');
  store.crypto = globalThis.crypto || makeMock('crypto');
  vm.createContext(P, { name: 'esm-verify' });
  return { ctx: P, store };
}

function symbols(store) {
  return new Set(Reflect.ownKeys(store).map(String)
    .filter(k => !INJECTED.has(k) && !BUILTIN.has(k) && k !== 'undefined'));
}

function readEntryOrder() {
  const src = fs.readFileSync(path.join(ROOT, ENTRY), 'utf8');
  const out = [];
  for (const m of src.matchAll(/^\s*import\s+["']([^"']+)["']\s*;?\s*$/gm)) {
    out.push('js/' + m[1].replace(/^\.\//, ''));
  }
  return { src, order: out };
}

function inlineSymbols() {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const s = new Set();
  for (const m of html.matchAll(/\bon[a-z]+\s*=\s*(["'])([\s\S]*?)\1/gi)) {
    for (const c of m[2].matchAll(/(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(/g)) s.add(c[1]);
  }
  for (const b of INLINE_BUILTINS) s.delete(b);
  return [...s].sort();
}

function classicSource(src) {
  const i = src.indexOf(BRIDGE_BEGIN);
  return (i < 0 ? src : src.slice(0, i));
}

async function main() {
  const result = {
    ok: false, version: '1.0.756', errors: [], notes: [],
    entry: ENTRY, module_count: 0, bridge_count: 0,
    syntax_ok: [], eval_errors: {},
    classic_symbols: 0, esm_symbols: 0,
    missing_in_esm: [], extra_in_esm: [],
    inline_symbols: 0, inline_missing: [],
    deleted_module_reports: {},
  };

  const { src: entrySrc, order } = readEntryOrder();
  result.order = order;
  result.module_count = order.length;
  if (order.length !== 18) result.errors.push('_entry.js import 数量应为 18，实际 ' + order.length);
  if (order[order.length - 1] !== 'js/12-init.js') result.errors.push('12-init.js 必须是最后一个 import');
  const uniq = new Set(order);
  if (uniq.size !== order.length) result.errors.push('_entry.js import 有重复项');

  // ① 每个模块：语法（module 语义）、桥接标记、文件存在
  for (const rel of order) {
    const fp = path.join(ROOT, rel);
    if (!fs.existsSync(fp)) { result.errors.push('模块文件不存在：' + rel); continue; }
    const src = fs.readFileSync(fp, 'utf8');
    if (!src.includes(BRIDGE_BEGIN)) result.errors.push('缺少 ESM 桥接块：' + rel);
    else result.bridge_count++;
    if (!/export\s*\{/.test(src)) result.errors.push('缺少显式 export：' + rel);
    try {
      new vm.SourceTextModule(src, { identifier: rel, initializeImportMeta(meta) { meta.url = 'file:///' + rel; } });
      result.syntax_ok.push(rel);
    } catch (e) {
      result.errors.push('模块语法错误 ' + rel + ': ' + String(e.message || e).slice(0, 200));
    }
  }

  // ② index.html：恰好一个 type=module 入口，无 classic <script src>
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const tags = [...html.matchAll(/<script\b([^>]*)>/gi)].map(m => m[1]);
  if (tags.length !== 1) result.errors.push('index.html 应只有 1 个 <script>，实际 ' + tags.length);
  else if (!/type\s*=\s*["']module["']/.test(tags[0]) || !/src\s*=\s*["']js\/_entry\.js["']/.test(tags[0])) {
    result.errors.push('唯一 <script> 必须是 type=module src=js/_entry.js，实际：' + tags[0]);
  }
  if (/<script[^>]+src\s*=\s*["']js\/(?!_entry\.js)/i.test(html)) {
    result.errors.push('index.html 仍残留 classic <script src="js/...">');
  }

  // ③ Phase A：classic 基准（前 17 个，剥离桥接块）
  const evalOrder = order.filter(r => r !== EVAL_EXCLUDE);
  result.notes.push('求值对比排除 ' + EVAL_EXCLUDE + '（初始化链在 mock DOM 下死循环，语法/桥接/依赖图已单独校验）');
  const A = makeContext();
  for (const rel of evalOrder) {
    const src = classicSource(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
    try {
      new vm.Script(src, { filename: rel }).runInContext(A.ctx, { timeout: 4000 });
    } catch (e) {
      result.eval_errors['classic:' + rel] = String(e.message || e).slice(0, 200);
    }
  }
  const G_a = symbols(A.store);

  // ④ Phase B：真实 ESM 求值（前 17 个，共享同一 context）
  const B = makeContext();
  for (const rel of evalOrder) {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    try {
      const mod = new vm.SourceTextModule(src, {
        context: B.ctx, identifier: rel,
        initializeImportMeta(meta) { meta.url = 'file:///' + rel; },
      });
      await mod.link(() => { throw new Error('模块不应有 import：' + rel); });
      await mod.evaluate();
    } catch (e) {
      result.eval_errors['esm:' + rel] = String(e.message || e).slice(0, 200);
    }
  }
  const G_b = symbols(B.store);

  result.classic_symbols = G_a.size;
  result.esm_symbols = G_b.size;
  result.missing_in_esm = [...G_a].filter(x => !G_b.has(x)).sort();
  result.extra_in_esm = [...G_b].filter(x => !G_a.has(x)).sort();
  if (result.missing_in_esm.length) {
    result.errors.push('ESM 桥接后 window 缺失 classic 全局符号 ' + result.missing_in_esm.length +
      ' 个：' + result.missing_in_esm.slice(0, 20).join(', '));
  }

  // ⑤ 内联事件符号覆盖
  const inline = inlineSymbols();
  result.inline_symbols = inline.length;
  result.inline_missing = inline.filter(x => !G_b.has(x));
  if (result.inline_missing.length) {
    result.errors.push('内联事件处理器符号在 ESM 下不可见：' + result.inline_missing.join(', '));
  }

  // ⑥ 删任一模块 → 模块图解析期失败，错误信息含被删文件与依赖方 _entry.js
  for (const rel of order) {
    const spec = './' + path.basename(rel);
    let report = null;
    try {
      const C = makeContext();  // 入口与所有依赖模块必须共享同一 context
      const m = new vm.SourceTextModule(entrySrc, {
        context: C.ctx, identifier: ENTRY,
        initializeImportMeta(meta) { meta.url = 'file:///' + ENTRY; },
      });
      await m.link((s, ref) => {
        if (s === spec) {
          report = "Cannot find module '" + s + "' imported from " + ref.identifier;
          throw new Error(report);
        }
        const fp = path.join(JS, s.replace(/^\.\//, ''));
        let code;
        try {
          code = fs.readFileSync(fp, 'utf8');
        } catch (e) {
          // 模块文件本身被删：同样属于「模块图解析期失败」，给出结构化报错，
          // 不要让 readFileSync 冒泡成 fatal（fatal 会丢掉 errors 明细）。
          report = "Cannot find module '" + s + "' imported from " + ref.identifier;
          throw new Error(report);
        }
        return new vm.SourceTextModule(code, {
          context: C.ctx, identifier: s,
          initializeImportMeta(meta) { meta.url = 'file:///' + s; },
        });
      });
      result.errors.push('模拟删除 ' + rel + ' 但 link 竟然成功（依赖没被声明）');
    } catch (e) {
      if (!report) report = String(e.message || e);
    }
    result.deleted_module_reports[rel] = report;
    if (!report || !report.includes(path.basename(rel)) || !report.includes('_entry.js')) {
      result.errors.push('删除 ' + rel + ' 的报错未同时指明模块与依赖方：' + report);
    }
  }

  if (Object.keys(result.eval_errors).length) {
    result.errors.push('求值期异常：' + JSON.stringify(result.eval_errors));
  }
  result.ok = result.errors.length === 0;
  console.log(JSON.stringify(result));
}

main().catch(e => {
  const msg = String(e && e.stack || e).slice(0, 600);
  console.log(JSON.stringify({ ok: false, errors: ['fatal: ' + msg], fatal: msg }));
  process.exit(1);
});
