// tests/fixture/settings_side_nav_harness.js
//
// v1.0.688「点设置必卡死」根因的**行为锁**用测试台。
//
// 背景（事故现场）：v1.0.669 给设置页加了右侧分组导航，其中
//   new MutationObserver(place).observe(view,{attributes:true,attributeFilter:["class","style"]});
// 而 place() 自己又要写 view.classList（"set-nav-on"）。浏览器规范里
// DOMTokenList.add/remove/toggle **每次调用都重写 class 属性**——哪怕
// token 集合没变也算一次属性变更 → 观察器回调 place() → place() 再写 class
// → 观察器再回调……**微任务死循环**：渲染器主线程 100% CPU，页面渲染正常
// 但 JS 全停（心跳信标断、CDP Runtime.evaluate 超时），点「设置」必卡死。
//
// 本台复刻「会触发这一循环」的那部分 DOM 语义（classList 每次调用都通知 +
// 观察器按微任务投递），把 js/11-workspace-kb-study.js 里**真实的**
// initSettingsSideNav 抽出来喂进去，数观察器回调次数：
//   · 修好的实现 → 回调次数收敛（个位数）
//   · 自激的实现 → 回调次数爆表 → 判定「死循环」
//
// 用法：node tests/fixture/settings_side_nav_harness.js <js 文件路径>
// 输出：单行 JSON {"verdict":"ok|loop|error","observerCalls":N,"detail":"..."}
"use strict";
const fs = require("fs");
const path = require("path");

const SRC = process.argv[2];
if (!SRC) {
  console.log(JSON.stringify({ verdict: "error", observerCalls: 0, detail: "缺少 js 文件路径参数" }));
  process.exit(2);
}

// ---------------------------------------------------------------- DOM 垫片
// 只实现本用例真正用到的那部分语义，且**必须**与 Chromium 一致（实测标定，
// 见本文件末尾「语义标定」）：
//   · classList.add()    —— **无条件**重写 class 属性 → 通知观察器
//       （哪怕 token 已在集合里，照样触发一次属性变更）
//   · classList.remove() —— 同上，**无条件**通知（哪怕该 token 根本不在集合里）
//   · classList.toggle(token, force) —— **只在成员状态真的变了**时才写属性、
//       才通知（force=true 且 token 已在集合里 → 直接返回，不写不通知）
//   · MutationObserver 回调按**微任务**投递（浏览器同款）→ 事件循环永不
//       让出 → 渲染线程 100% CPU
//   · attributeFilter 只放行被观察的属性名
//
// 这三条差异正是死循环的燃料：v1.0.669 的 place() 用的是 add/remove，
// 每次回调都无条件写一次 class → 观察器再回调 → 微任务永不收敛。

const OBSERVER_CALL_CAP = 400;   // 超过即判定死循环（真死循环是无限增长）

function makeEnv() {
  const state = {
    observerCalls: 0,
    records: [],
    observers: [],          // {target, attrs:Set|null, cb}
    microtasks: [],
    capHit: false,
  };

  function notifyClass(target) {
    for (const ob of state.observers) {
      if (ob.target !== target) continue;
      if (ob.attrs && !ob.attrs.has("class")) continue;
      state.records.push({ type: "attributes", attributeName: "class" });
    }
    scheduleObserverDelivery();     // 属性一变就排队投递（浏览器同款）
  }

  function notifyStyle(target) {
    for (const ob of state.observers) {
      if (ob.target !== target) continue;
      if (ob.attrs && !ob.attrs.has("style")) continue;
      state.records.push({ type: "attributes", attributeName: "style" });
    }
    scheduleObserverDelivery();
  }

  function makeClassList(owner) {
    const tokens = new Set();
    return {
      add(...args) {
        for (const t of args) {
          if (t === undefined || t === null) continue;
          for (const one of String(t).split(/\s+/)) if (one) tokens.add(one);
        }
        notifyClass(owner);          // ← 关键：集合没变也要通知
      },
      remove(...args) {
        for (const t of args) {
          if (t === undefined || t === null) continue;
          for (const one of String(t).split(/\s+/)) tokens.delete(one);
        }
        notifyClass(owner);          // ← 关键：同上
      },
      toggle(t, force) {
        const has = tokens.has(t);
        const want = force === undefined ? !has : !!force;
        if (want === has) return;          // ← Chromium 语义：状态没变就不写、不通知
        if (want) tokens.add(t); else tokens.delete(t);
        notifyClass(owner);
      },
      contains(t) { return tokens.has(t); },
      get value() { return Array.from(tokens).join(" "); },
      __tokens: tokens,
    };
  }

  function makeEl(tag) {
    const el = {
      tagName: String(tag || "div").toUpperCase(),
      id: "",
      style: {},
      children: [],
      listeners: {},
      classList: null,
      dataset: {},
      _text: "",
    };
    el.classList = makeClassList(el);
    // style 的每次赋值也要通知（观察器同时盯着 style 属性）
    const rawStyle = el.style;
    el.style = new Proxy(rawStyle, {
      set(target, prop, value) {
        target[prop] = value;
        notifyStyle(el);
        return true;
      },
    });
    el.appendChild = (c) => { el.children.push(c); return c; };
    el.querySelectorAll = () => [];
    el.addEventListener = (t, fn) => {
      (el.listeners[t] = el.listeners[t] || []).push(fn);
    };
    el.removeEventListener = () => {};
    el.getBoundingClientRect = () => ({
      width: el.__visible === false ? 0 : 800,
      height: 600,
      top: 0,
      right: 800,
      left: 0,
      bottom: 600,
    });
    el.setAttribute = (k, v) => {
      if (k === "class") notifyClass(el);
      else notifyStyle(el);
    };
    el.getAttribute = () => null;
    el.focus = () => {};
    return el;
  }

  const body = makeEl("body");
  const byId = {};

  const document = {
    body,
    createElement: (t) => makeEl(t),
    getElementById: (id) => byId[id] || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  class MutationObserver {
    constructor(cb) { this._cb = cb; }
    observe(target, opts) {
      const attrs = opts && opts.attributeFilter ? new Set(opts.attributeFilter) : null;
      state.observers.push({ target, attrs, cb: this._cb });
    }
    disconnect() {
      state.observers = state.observers.filter((o) => o.cb !== this._cb);
    }
    takeRecords() { const r = state.records.slice(); state.records.length = 0; return r; }
  }

  class IntersectionObserver {
    constructor(cb) { this._cb = cb; }
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  const win = {
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener: () => {},
    removeEventListener: () => {},
    requestAnimationFrame: (fn) => setTimeout(() => fn(Date.now()), 0),
  };

  // 投递观察器回调：微任务（与浏览器同款——死循环时事件循环永远不回头）
  function flushMicrotasks(budget) {
    let n = 0;
    while (state.microtasks.length && n < budget) {
      const t = state.microtasks.shift();
      t();
      n++;
    }
    return n;
  }

  function scheduleObserverDelivery() {
    if (state.pendingDelivery) return;
    state.pendingDelivery = true;
    state.microtasks.push(() => {
      state.pendingDelivery = false;
      const recs = state.records.splice(0, state.records.length);
      if (!recs.length) return;
      state.observerCalls++;
      if (state.observerCalls > OBSERVER_CALL_CAP) { state.capHit = true; return; }
      for (const ob of state.observers.slice()) {
        try { ob.cb(recs, ob); } catch (e) { /* 观察器抛错不影响计数 */ }
      }
    });
  }

  // 每次 class/style 变更后都要安排一次投递（notifyClass/notifyStyle 内部已调）
  state.scheduleDelivery = scheduleObserverDelivery;

  return { state, document, win, MutationObserver, IntersectionObserver, makeEl, byId, flushMicrotasks, scheduleObserverDelivery };
}

// ------------------------------------------------------- 抽函数 & 造场景
function extractFunction(src, name) {
  const marker = "function " + name + "(";
  const start = src.indexOf(marker);
  if (start < 0) throw new Error("源码里找不到 " + name);
  // 从 '{' 起做括号配平（跳过字符串/模板串/注释，避免被内容里的花括号带偏）
  let i = src.indexOf("{", start);
  if (i < 0) throw new Error(name + " 没有函数体");
  let depth = 0, inStr = null, inTpl = false, inLine = false, inBlock = false;
  for (; i < src.length; i++) {
    const c = src[i], n = src[i + 1];
    if (inLine) { if (c === "\n") inLine = false; continue; }
    if (inBlock) { if (c === "*" && n === "/") { inBlock = false; i++; } continue; }
    if (inStr) {
      if (c === "\\") { i++; continue; }
      if (c === inStr) inStr = null;
      continue;
    }
    if (inTpl) {
      if (c === "\\") { i++; continue; }
      if (c === "`") inTpl = false;
      continue;
    }
    if (c === "/" && n === "/") { inLine = true; i++; continue; }
    if (c === "/" && n === "*") { inBlock = true; i++; continue; }
    if (c === '"' || c === "'") { inStr = c; continue; }
    if (c === "`") { inTpl = true; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error(name + " 函数体括号不配平");
}

function buildScenario(env, opts) {
  const { makeEl, document, byId } = env;
  // 设置页视图（observer 的被观察对象）
  const view = makeEl("section");
  view.id = "view-settings";
  view.__visible = opts.viewVisible !== false;
  // 5 个分组（<3 个时函数会提前 return，测不出循环）
  view.querySelectorAll = (sel) => {
    if (sel === ".set-group") {
      return [0, 1, 2, 3, 4].map((i) => {
        const g = makeEl("div");
        g.textContent = "分组" + i;
        g.getBoundingClientRect = () => ({ width: 800, height: 100, top: i * 100, right: 800, left: 0, bottom: 0 });
        return g;
      });
    }
    return [];
  };
  byId["view-settings"] = view;

  const input = makeEl("input");
  input.id = "setSearchInput";
  byId["setSearchInput"] = input;
  const panel = makeEl("div");
  panel.id = "setSearchPanel";
  byId["setSearchPanel"] = panel;

  const $ = (id) => byId[id] || null;
  const escapeHtml = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  return { view, input, panel, $, escapeHtml };
}

// ------------------------------------------------------------------ 主流程

// 语义标定：本垫片的 classList 行为必须与 Chromium 一致，否则整台测试台失真。
// EXPECTED 是 Edge headless 实测值（2026-11-16，30 次封顶）：
//   重复 add 已存在的 class      → 仍触发 30 次（**无条件**重写 class 属性）
//   重复 toggle(token, true)     → 只触发 1 次（状态没变就不写）
//   重复 remove 不存在的 class   → 仍触发 30 次（**无条件**通知）
//   v1.0.669 的 place() 语义      → 30 次（死循环）
//   无条件 toggle 的 place() 语义 → 1 次（toggle 幂等，不构成循环）
function calibrate() {
  const env = makeEnv();
  const sc = buildScenario(env, { viewVisible: true });
  const view = sc.view;
  const results = {};

  function countObserver(fn, cap) {
    const state = env.state;
    const before = state.observerCalls;
    let calls = 0;
    const ob = new env.MutationObserver(function () {
      calls++;
      if (calls < cap) fn();
    });
    ob.observe(view, { attributes: true, attributeFilter: ["class", "style"] });
    fn();
    env.flushMicrotasks(2000);
    ob.disconnect();
    return calls;
  }

  const setTokens = (s) => { view.classList.__tokens.clear(); for (const t of s.split(" ")) if (t) view.classList.__tokens.add(t); };

  setTokens("");
  results.repeatedAdd = countObserver(() => view.classList.add("set-nav-on"), 30);

  setTokens("");
  results.repeatedToggleTrue = countObserver(() => view.classList.toggle("set-nav-on", true), 30);

  setTokens("");
  results.repeatedRemoveAbsent = countObserver(() => view.classList.remove("absent"), 30);

  setTokens("");
  results.originalPlaceSemantics = countObserver(() => {
    const r = view.getBoundingClientRect();
    if (!r.width) view.classList.remove("set-nav-on");
    else view.classList.add("set-nav-on");
  }, 30);

  setTokens("");
  results.unconditionalTogglePlace = countObserver(() => {
    const r = view.getBoundingClientRect();
    if (!r.width) view.classList.toggle("set-nav-on", false);
    else view.classList.toggle("set-nav-on", true);
  }, 30);

  return results;
}

const EXPECTED_CALIBRATION = {
  repeatedAdd: 30,
  repeatedToggleTrue: 1,
  repeatedRemoveAbsent: 30,
  originalPlaceSemantics: 30,
  unconditionalTogglePlace: 1,
};

function run(buggy) {
  const src = fs.readFileSync(SRC, "utf8");
  const fnSrc = extractFunction(src, "initSettingsSideNav");

  const env = makeEnv();
  const sc = buildScenario(env, { viewVisible: true });

  // 把 classList 的通知接到「安排投递」上（浏览器里属性变更后立刻排队微任务）
  // 属性变更→通知→排微任务，这条链已由 makeEnv 的 notifyClass/notifyStyle 内建，
  // 这里不需要再包一层 classList（包了反而会让语义偏离 Chromium）。

  const sandbox = {
    window: env.win,
    document: env.document,
    MutationObserver: env.MutationObserver,
    IntersectionObserver: env.IntersectionObserver,
    $: sc.$,
    escapeHtml: sc.escapeHtml,
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
  };
  sandbox.globalThis = sandbox;

  if (buggy) {
    // 还原 v1.0.669 的自激实现（place 里无条件写 classList）
    const factory = new Function(
      "document", "window", "MutationObserver", "IntersectionObserver", "$", "escapeHtml",
      `var _setNavInited=false;
       function initSettingsSideNav(){
         if(_setNavInited) return;
         var view=$("view-settings"); if(!view) return;
         var groups=[...view.querySelectorAll(".set-group")];
         if(groups.length<3) return;
         groups.forEach(function(g,i){ if(!g.id) g.id="setNavG"+i; });
         var nav=document.createElement("nav"); nav.id="setSideNav";
         nav.innerHTML=groups.map(function(g,i){return '<button class="ssn-item" data-i="'+i+'">g</button>';}).join("");
         document.body.appendChild(nav);
         var place=function(){
           var r=view.getBoundingClientRect();
           if(!r.width){ nav.style.display="none"; view.classList.remove("set-nav-on"); return; }
           nav.style.display="flex";
           nav.style.right="10px"; nav.style.top="80px";
           view.classList.add("set-nav-on");
         };
         nav.querySelectorAll(".ssn-item").forEach(function(b){ b.addEventListener("click",function(){}); });
         try{ new IntersectionObserver(function(){}).observe(view); }catch(_){}
         view.addEventListener("scroll",place);
         window.addEventListener("resize",place);
         new MutationObserver(place).observe(view,{attributes:true, attributeFilter:["class","style"]});
         place();
         _setNavInited=true;
       }
       return initSettingsSideNav;`
    );
    sandbox.initSettingsSideNav = factory(
      env.document, env.win, env.MutationObserver, env.IntersectionObserver,
      sc.$, sc.escapeHtml
    );
  } else {
    // 真实实现：把源码里的函数塞进沙箱求值
    // （_setNavInited 是源文件里的模块级 let，这里在函数工厂作用域内补一个等价声明）
    const factory = new Function(
      "document", "window", "MutationObserver", "IntersectionObserver", "$", "escapeHtml",
      "let _setNavInited=false;\n" + fnSrc + "\nreturn initSettingsSideNav;"
    );
    sandbox.initSettingsSideNav = factory(
      env.document, env.win, env.MutationObserver, env.IntersectionObserver,
      sc.$, sc.escapeHtml
    );
  }

  let error = null;
  try {
    sandbox.initSettingsSideNav();
    // 抽干微任务（真死循环时这里永远回不来——靠 observerCalls 上限兜住）
    let guard = 0;
    while (env.state.microtasks.length && guard < 20000 && !env.state.capHit) {
      env.flushMicrotasks(500);
      guard++;
    }
  } catch (e) {
    error = String((e && e.stack) || e);
  }

  const calls = env.state.observerCalls;
  const verdict = error ? "error"
    : (env.state.capHit || calls > OBSERVER_CALL_CAP ? "loop"
      : (calls <= 20 ? "ok" : "loop"));
  return { verdict, observerCalls: calls, detail: error || ("observerCalls=" + calls) };
}

const mode = process.argv[3] === "--buggy";
if (process.argv[3] === "--calibrate") {
  // 语义标定模式：把本垫片的 classList/观察器语义跑一遍，打印计数。
  // 真实 Chromium 的实测值（Edge headless，2026-11-16）见下方 EXPECTED。
  console.log(JSON.stringify(calibrate()));
  process.exit(0);
}
const r = run(mode);
console.log(JSON.stringify(r));
process.exit(r.verdict === "ok" ? 0 : 1);
