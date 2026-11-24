const $ = (id) => document.getElementById(id);
let models = [], templates = [];
let __opPick = null;

// v1.0.742（界面审查 P2-5 配套）：入参先归一成字符串——原 `(s||"")` 对数字直接 .replace 会抛
// TypeError（模型表格把 max_input_tokens 等数值补转义后需要这个兜底）。
function escapeHtml(s){ return String(s==null?"":s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
// 用于拼接内联 onclick 的 JS 字符串字面量：转义引号/反斜杠/换行，防属性与 JS 注入（P2-5）
// v1.0.373 审查修复（P2-4）：`"` 须编码为 &quot;——原 `\"` 是 JS 层转义，但外层是 HTML
// 双引号属性，HTML 解析器不认 `\"`，含 `"` 的数据（导入的模型/模板 id）可直接跳出属性边界注入事件。
function jsStr(s){ return (s||"").replace(/\\/g,"\\\\").replace(/'/g,"\\'").replace(/"/g,"&quot;").replace(/\r?\n/g,"\\n"); }
// v1.0.616（审查 P2）：标题截断统一入口。`String.slice(0,n)` 按 UTF-16 码元切，
// 遇到 emoji/生僻字（代理对）会切出半个字符 → 界面显示成 ?/方块乱码。
// Array.from 按码点切，再拼回来；后缀由调用方自己决定（有的带 …，有的不带）。
function safeSlice(s, n){ return Array.from(String(s == null ? "" : s)).slice(0, n).join(""); }
function autoResize(el){ if(!el) return; el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,180)+'px'; }
// ---------- v1.0.650 U6：状态口径单一事实源 ----------
// 同一件"装好了"以前在三个地方三种叫法两样图标：组件视图 ✅+「可用」、
// 同页汇总行「全部就绪」、设置页绿点+「已安装」；「安装中」在组件视图是 ⏳、
// 设置页是黄点（而 css 顶部 v1.0.509 的规范里黄=降级/注意，进行中=⏳）。
// 规范本来就写在 css 注释里，没法被 JS 引用，于是每页各自抄一遍、抄一版漂一版。
// 现在图标与用词都从这里取；css 的规范注释与本表由测试互相核对。
// v1.0.741（界面审查 ①）：状态图标从 emoji 换成 16-icons.js 的统一线稿（24 网格/1.6 描边，
// stroke=currentColor），语义色由内联 span 吃令牌（深浅主题各一套，无需 emoji 自带色）。
// 本文件在 16-icons.js 之后加载（index.html 顶部先挂 16-icons），icon() 已可用。
// 消费方一律走 innerHTML（toast / followState / OCR 状态行已同步切换，见各自行内注释）。
const _si = (n, c) => `<span class="st-ic" style="color:${c}">${icon(n)}</span>`;
const STATUS_ICON = {ok:_si("check","var(--ok)"), err:_si("xmark","var(--err-fg)"), degraded:_si("alert","var(--warn-fg)"), missing:_si("xmark","var(--err-fg)"), installing:_si("refresh","var(--accent)"), unknown:_si("circle","var(--muted2)"), outdated:_si("arrowup","var(--accent)"), na:_si("minus","var(--muted2)"), off:_si("circle","var(--muted2)")};
const STATUS_WORD = {ok:"可用", degraded:"降级", missing:"未就绪", installing:"安装中", unknown:"未知", outdated:"可升级", na:"不适用", off:"未安装"};
function fetchErr(e, action){
  // v1.0.373（体验A2）：常见错误人话化——技术报错翻译成"发生了什么 + 下一步做什么"
  const m = (e && e.message) || "";
  if(m === "Failed to fetch") return action + "失败：后端未运行，请重新启动 Fisheep（Fisheep.exe）";
  if(/HTTP\s*5\d\d/i.test(m)) return action + "失败：服务内部错误，请稍后重试；反复出现请重启软件";
  if(/timeout|timed?\s*out|ETIMEDOUT/i.test(m)) return action + "失败：请求超时，请检查网络后重试";
  if(/HTTP\s*4\d\d/i.test(m)) return action + "失败：请求被拒绝（" + m + "），请检查配置后重试";
  return action + "失败：" + (m || "网络错误");
}
// 通用 SSE 消费器（v1.0.309 提取自 4 处重复实现：主聊天/模板 run/快捷入口 AI/模板 AI）：
// reader 字节流 → decode → 按 "\n\n" 分帧 → "data:" JSON 解析 → 逐事件回调 onEvent(j)。
// onData：每收到一批原始字节回调一次（看门狗喂狗用，可传 null）。
// onEvent 返回非 undefined → consumeSSE 立即停止并以 {early:值} 返回（主聊天 need_continue/
// aborted 的提前返回依赖此契约；其余调用方 onEvent 恒返回 undefined，不受影响）。
// reader.read() 抛错（含 AbortError）原样上抛，由调用方按各自 UI 语义处理。
async function consumeSSE(reader, onEvent, onData){
  const dec=new TextDecoder(); let chunk="";
  while(true){
    const {done,value}=await reader.read(); if(done) break;
    if(onData) onData();
    chunk+=dec.decode(value,{stream:true});
    const parts=chunk.split("\n\n"); chunk=parts.pop();
    for(const p of parts){
      if(!p.startsWith("data:")) continue;
      let j; try{ j=JSON.parse(p.slice(5).trim()); }catch(_){ continue; }
      const r=onEvent(j);
      if(r!==undefined){ try{ reader.cancel(); }catch(_){} return {early:r}; }   // v1.0.652（审计 D15）：提前返回显式取消 reader，不悬挂连接
    }
  }
}
function toast(msg, type='ok', ms=2500){
  const t=$("toast");
  // v1.0.471（UI#2）：info 补 ℹ️ 图标；warn/info 现有专属背景色（style.css）
  // v1.0.650 U6：err 原来也画 ⚠️，和规范（错误=❌、⚠️=警告）打架——
  // 红色底的 toast 配一个"警告"图标，用户分不清这次到底成没成。改回 ❌。
  const ic = type==='err' ? STATUS_ICON.err + " " : (type==='warn' ? '⚠️ ' : (type==='info' ? 'ℹ️ ' : STATUS_ICON.ok + " "));
  // v1.0.741（界面审查 ①）：ic 现在是 SVG 串（STATUS_ICON 线稿化），textContent 会露出标签文本 → 改 innerHTML；msg 仍转义，只信 ic。
  t.innerHTML = ic + escapeHtml(msg);
  t.className=type; t.classList.add("show");
  if(toast._tmr) clearTimeout(toast._tmr);   // 防连续 toast 时前一个定时器提前隐藏后一个
  toast._tmr = setTimeout(()=>{ t.classList.remove("show"); }, ms);
}
// v1.0.471（UI#5）：统一 fetch 封装——收敛 ~40 处 fetch→json→ok判断→toast 样板。
// 成功返回解析后的 JSON；失败抛 Error（message 已是人话），由 apiToast 统一 toast。
// 覆盖后端两种失败形态：HTTP 4xx/5xx 与 HTTP 200 + {error} / {ok:false}。
async function api(url, opts){
  const r = await fetch(url, opts);
  let d = null;
  try{ d = await r.json(); }catch(_){ /* 非 JSON 响应按状态码判 */ }
  if(!r.ok || (d && (d.error || d.ok === false))){
    throw new Error((d && (d.error || d.detail)) || ("HTTP " + r.status));
  }
  return d || {};
}
async function apiToast(url, opts, action){
  try{ return await api(url, opts); }
  catch(e){ toast(fetchErr(e, action || "操作"), "err"); throw e; }
}
// fetch 样板收敛（C 档）：GET/POST JSON 薄封装——只补最常见的两种 opts 形态，
// 全部复用 api() 的同一契约（非 JSON 响应按状态码判；d.error / d.ok===false 抛错）。
// 带 AbortController、FormData、额外 headers、非 json 响应的调用继续走 api()/fetch 原样。
async function getJson(url){ return api(url); }

// ---------- v1.0.682：后端看门狗（自愈） ----------
// 2026-11-16 08:30 事故：后端服务线程停摆（所有请求永不返回）→ 各模块 fetch
// 全部悬挂（"点不开"）、关闭确认轮询断链（15s 后只弹出 Windows 默认色的原生
// 兜底框）。前端对"后端停摆"零感知、零提示、零自救。现在：
//   每 5s 打一次 /api/ping（最轻端点，不碰配置锁，4s 超时）；
//   连续 6 次失败（≈30s）判定后端停摆 → toast + 自动重载页面重连本地服务；
//   10 分钟内累计重载 2 次仍未恢复 → 停止重载，常驻报错提示让用户重启软件。
// 计数跨重载用 localStorage 记账（重载后 JS 状态清零，内存计数会丢失）。
let __hbFails = 0;
function __hbState(){
  try{
    const s = JSON.parse(localStorage.getItem("lingban_hb") || "{}");
    return {n: (s && s.n) || 0, ts: (s && s.ts) || 0};
  }catch(_e){ return {n:0, ts:0}; }
}
function __hbSave(st){ try{ localStorage.setItem("lingban_hb", JSON.stringify(st)); }catch(_e){} }
function __hbTick(){
  const ctl = new AbortController();
  const tm = setTimeout(() => ctl.abort(), 4000);
  fetch("/api/ping", {signal: ctl.signal})
    .then(r => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
    .then(d => {
      __hbFails = 0;
      try{ localStorage.removeItem("lingban_hb"); }catch(_e){}   // 后端已恢复，事故窗口清零
    })
    .catch(() => {
      __hbFails++;
      if(__hbFails < 6) return;                                  // ≈30s 内才下结论
      const st = __hbState();
      if(Date.now() - st.ts >= 10 * 60 * 1000) st.n = 0;         // 10 分钟窗口
      if(st.n >= 2){
        toast("后端无响应，若界面没有自行恢复，请重启软件（Fisheep.exe）", "err", 12000);
        return;                                                   // 不再重载；继续 ping，恢复即自愈
      }
      st.n++; st.ts = Date.now(); __hbSave(st);
      toast("后端无响应，正在尝试自动恢复…", "warn", 4000);
      setTimeout(() => location.reload(), 1200);
    })
    .finally(() => { clearTimeout(tm); setTimeout(__hbTick, 5000); });
}
setTimeout(__hbTick, 3000);   // 留给首屏 3s 落定，再开始心跳
// ---------- v1.0.684：JS 存活信标（「页面 JS 活着」的唯一证据） ----------
// 2026-11-16 三症状实例（"设置/学习中心点不开" + "按 X 很久才弹窗" + "弹窗颜色不对"）：
// 页面渲染正常但 JS 停执行——后端心跳全 200，只有页面自己每 5s 打 /api/jsbeacon
// 才能证明 JS 活着。后端在 /api/ping 上报 js_silent_s，GUI 自愈线程见「窗口可见 +
// 静默 90s」自动重载页面。
// 裸 fetch + 自带 2s AbortController（与看门狗同一纪律）：api() 没有超时能力，
// 后端停摆时信标不得悬挂累积。信标失败（后端死）直接丢弃——后端停摆已是看门狗的
// 职责，不重复报警。
(function __jsBeacon(){
  function tick(){
    try{
      const av = document.querySelector(".view.active");
      const ctl = new AbortController();
      const tm = setTimeout(() => ctl.abort(), 2000);
      fetch("/api/jsbeacon", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({view: (av && av.id) ? av.id.replace("view-","") : ""}),
        signal: ctl.signal, keepalive: true})
        .catch(()=>{})
        .finally(() => { clearTimeout(tm); setTimeout(tick, 5000); });
    }catch(_e){ setTimeout(tick, 5000); }
  }
  setTimeout(tick, 3000);   // 与看门狗同起点：首屏落定 3s
})();
async function postJson(url, data){
  return api(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
}
function showLoading(el, text){
  if(typeof el==="string") el=$(el);
  el.innerHTML=`<div class="loading-row"><span class="spinner"></span>${escapeHtml(text||"加载中…")}</div>`;
}
function showEmpty(el, text, opt){
  // 契约：text 是**受信任的 HTML 片段**（本项目内的字面量，常带 <br/> 分行），本函数不转义。
  // 调用方拼入任何外部/动态内容（错误消息、文件名、后端返回）时请自行 escapeHtml。
  // v1.0.737（视觉审查 ⑦）：统一 EmptyState —— 线稿图标 / 标题 / 说明 / 一个主操作
  //（opt = {ic,title,action,actionLabel}），配合 css .empty 的 min-height + flex 居中，
  // 各列表页空态高度与垂直位置不再各自为政。图标统一走 icAny() → 16-icons 素色线稿，
  // 不再各页混用彩色 emoji；文案装饰 emoji 已由调用点移除。
  if(typeof el==="string") el=$(el);
  const o = opt || {};
  const svg = (typeof icAny==="function") ? icAny(o.ic || "box") : "";
  el.innerHTML = '<div class="empty empty-state">'
    + '<span class="em-ic">' + svg + '</span>'
    + (o.title ? '<span class="em-t">'+escapeHtml(o.title)+'</span>' : '')
    + '<span class="em-sub">'+text+'</span>'
    + (o.action ? '<button class="primary em-action" onclick="'+escapeHtml(o.action)+'">'+escapeHtml(o.actionLabel||"开始")+'</button>' : '')
    + '</div>';
}
function setBtnLoading(btn, loading){
  if(!btn) return;
  if(loading){ btn.classList.add("loading"); btn.dataset.oldText=btn.textContent; btn.textContent="处理中..."; }
  else { btn.classList.remove("loading"); if(btn.dataset.oldText) btn.textContent=btn.dataset.oldText; }
}

// ---- 侧栏视图切换 ----

// ---- 大输入框（v1.0.646：自 js/16-bigeditor.js 并入，函数体逐字未改） ----
// 只依赖本文件顶部的 $，且调用方是 index.html 里的内联 onclick（运行时才解析），
// 因此从 12 号位提到 01 号位不影响任何加载时序。
function openBigEditor(inputId, sendFn){
  const ov=$("bigEditorOverlay"); if(!ov) return;
  const src=$(inputId); if(!src) return;
  ov._inputId=inputId; ov._sendFn=sendFn;
  $("bigEditText").value=src.value;
  ov.classList.add("show");
  $("bigEditText").focus();
}
function closeBigEditor(){ const ov=$("bigEditorOverlay"); if(ov) ov.classList.remove("show"); }
function bigEditorSend(){
  const ov=$("bigEditorOverlay"); if(!ov) return;
  const inp=$(ov._inputId); if(inp) inp.value=$("bigEditText").value;
  closeBigEditor();
  const fn=window[ov._sendFn]; if(fn && typeof fn==="function") fn();
}

// ---- js 小重复合并（审计 A10）：以下为多处同款样板的公共实现 ----

// 数字格式化（亿/万/k）：07 的 fmtTok 与 13 的 fmtDashNum 此前是两份逐字同款
// （仅 Number(n)/+n 一词之差，语义相同），收敛为单一实现后两处以薄封装保留旧函数名。
function fmtNum(n){
  n = +n || 0;
  if(n >= 1e8) return (n/1e8).toFixed(2)+"亿";
  if(n >= 1e4) return (n/1e4).toFixed(1)+"万";
  if(n >= 1e3) return (n/1e3).toFixed(1)+"k";
  return String(n);
}

// 模型下拉填充：02（fillQuickAIModel）/07（loadModels 的 t_model）/08（fillTplAIModel）
// 三处逐字同款的 option 拼串收敛到这里（value 一律 escapeHtml——v1.0.471 UI#14 的口径）。
// 只做填充：当前选中值的保存/恢复是各调用方自己的事（07 那处本来就不恢复，不替它改行为）。
function fillModelSelect(sel, models){
  if(!sel) return;
  sel.innerHTML='<option value="">（用默认模型）</option>'+(models||[]).map(m=>`<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`).join("");
}

// localStorage 读写样板：原先 ~20 处 try{localStorage.setItem/getItem(...)}catch(e){} 手包一层。
// lsSet 吞掉写失败（隐私模式/配额超限，与原 catch 空体一致）；lsGet 读失败/缺键时回默认值。
function lsSet(k, v){ try{ localStorage.setItem(k, v); }catch(e){} }
function lsGet(k, d){ try{ const v = localStorage.getItem(k); return v == null ? d : v; }catch(e){ return d; } }

// AI 设计助手迷你聊天窗工厂（quickAI/tplAI 两套整段克隆的公共骨架）。
// js/02 的 addQuickAIMsg/renderQuickAIOptions/quickAIPick/quickAISend 与 js/08 的
// tplAI 同名四件是同一套「需求确认 → 可点选项 → SSE 流式生成 → 结果卡片」交互的复制粘贴
// （~90 行 ×2，仅 DOM id/端点/文案/结果提取不同），收敛到这里后两处各留薄封装。
// cfg：{S, chatId, inputId, sendBtnId, modelSelId, endpoint, errLabel, emptyHint, extract, onResult}
//   S＝状态载体 {msgs, ctrl, running, result}——调用方用取值器桥回各自的旧全局名（不改名）；
//   extract(j)＝从 result 事件取出结果（quick 取 j.links、tpl 取 j.template）；
//   onResult(res)＝结果渲染（renderQuickAIResult / renderTplAIResult），提取为 null 时不渲染，
//   与原实现「__xxAIResult=...; if(__xxAIResult) render...」逐条一致。
function makeDesignAssistant(cfg){
  const S = cfg.S;
  const chatEl = ()=>$(cfg.chatId);
  function addMsg(role, text){
    const chat=chatEl(); if(!chat) return null;
    const el=document.createElement("div");
    el.className="msg "+(role==="user"?"user":"agent");
    const mc=document.createElement("div"); mc.className="mc";
    if(role==="agent" && !text){ mc.innerHTML='<span class="spinner" style="display:inline-block;vertical-align:-3px"></span> 正在思考…'; }
    else mc.innerHTML=renderMarkdown(text);
    el.appendChild(mc); chat.appendChild(el);
    chat.scrollTop=chat.scrollHeight;
    return el;
  }
  function renderOptions(questions){
    const chat=chatEl(); if(!chat) return;
    (questions||[]).forEach(q=>{
      const blk=document.createElement("div"); blk.className="opt-block";
      const t=document.createElement("div"); t.className="ob-t"; t.textContent=(q.label||"请选择")+(q.desc?(" · "+q.desc):"");
      blk.appendChild(t);
      const row=document.createElement("div"); row.className="row";
      (q.options||[]).forEach(o=>{
        const b=document.createElement("button"); b.type="button";
        b.className="opt-chip"+(o.value==="custom"?" opt-custom":"");
        b.textContent=o.label||o.value; b.title=o.desc||"";
        b.onclick=()=>pick(q.key,o.value,o.label||o.value);
        row.appendChild(b);
      });
      blk.appendChild(row); chat.appendChild(blk);
    });
    chat.scrollTop=chat.scrollHeight;
  }
  function pick(key, value, label){
    if(S.running){ toast("正在生成中，请稍候…","warn"); return; }
    const content = value==="custom" ? `【${label}】请向我询问具体需要什么内容` : `【选项选择】${label}`;
    S.msgs.push({role:"user", content});
    addMsg("user","选择："+label);
    send();
  }
  async function send(){
    if(S.running) return;
    const inp=$(cfg.inputId); const text=inp?inp.value.trim():"";
    if(text){ S.msgs.push({role:"user",content:text}); addMsg("user",text); if(inp) inp.value=""; }
    if(!S.msgs.length){ toast(cfg.emptyHint,"warn"); return; }
    S.running=true; const sb=$(cfg.sendBtnId); if(sb) sb.disabled=true;
    const agentEl=addMsg("agent","");
    let out="";
    const model_id=$(cfg.modelSelId)?$(cfg.modelSelId).value:"";
    if(S.ctrl) S.ctrl.abort(); S.ctrl=new AbortController();
    let r;
    try{
      r=await fetch(cfg.endpoint,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({messages:S.msgs, model_id}), signal:S.ctrl.signal});
    }catch(e){
      if(e && e.name==="AbortError"){ S.running=false; if(sb) sb.disabled=false; return; }
      S.running=false; if(sb) sb.disabled=false;
      if(agentEl) agentEl.querySelector(".mc").innerHTML='<span style="color:var(--err-fg)">⚠️ '+escapeHtml(fetchErr(e,cfg.errLabel))+'</span>';
      return;
    }
    if(!r.ok || !r.body){
      S.running=false; if(sb) sb.disabled=false;
      if(agentEl) agentEl.querySelector(".mc").innerHTML='<span style="color:var(--err-fg)">⚠️ 生成失败（HTTP '+r.status+'）</span>';
      return;
    }
    const reader=r.body.getReader();
    try{
      await consumeSSE(reader, (j)=>{
        if(j.type==="token"){ out+=(j.text||""); if(agentEl){ agentEl.querySelector(".mc").innerHTML=renderMarkdown(out); const c=chatEl(); if(c) c.scrollTop=c.scrollHeight; } }
        else if(j.type==="options"){ renderOptions(j.questions||[]); }
        else if(j.type==="result"){ S.result=cfg.extract(j); if(S.result) cfg.onResult(S.result); }
        else if(j.type==="error"){ toast(j.text||"生成失败","err"); if(agentEl) agentEl.querySelector(".mc").innerHTML='<span style="color:var(--err-fg)">⚠️ '+escapeHtml(j.text||"生成失败")+'</span>'; }
      });
    }catch(e){
      if(e && e.name!=="AbortError" && agentEl){ agentEl.querySelector(".mc").innerHTML='<span style="color:var(--err-fg)">⚠️ '+escapeHtml(e.message||"读取响应失败")+'</span>'; }
    }finally{
      S.running=false; if(sb) sb.disabled=false;
      if(out) S.msgs.push({role:"assistant", content:out});
      const chat=chatEl(); if(chat) chat.scrollTop=chat.scrollHeight;
    }
  }
  return {addMsg, renderOptions, pick, send};
}
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {
  $, STATUS_ICON, STATUS_WORD, __hbFails, __hbSave, __hbState,
  __hbTick, __opPick, _si, api, apiToast, autoResize,
  bigEditorSend, closeBigEditor, consumeSSE, escapeHtml, fetchErr, fillModelSelect,
  fmtNum, getJson, jsStr, lsGet, lsSet, makeDesignAssistant,
  models, openBigEditor, postJson, safeSlice, setBtnLoading, showEmpty,
  showLoading, toast
};
Object.defineProperties(window, {
  $: { configurable: true, enumerable: true, get(){ return $; }, set(v){ $ = v; } },
  STATUS_ICON: { configurable: true, enumerable: true, get(){ return STATUS_ICON; }, set(v){ STATUS_ICON = v; } },
  STATUS_WORD: { configurable: true, enumerable: true, get(){ return STATUS_WORD; }, set(v){ STATUS_WORD = v; } },
  __hbFails: { configurable: true, enumerable: true, get(){ return __hbFails; }, set(v){ __hbFails = v; } },
  __hbSave: { configurable: true, enumerable: true, get(){ return __hbSave; }, set(v){ __hbSave = v; } },
  __hbState: { configurable: true, enumerable: true, get(){ return __hbState; }, set(v){ __hbState = v; } },
  __hbTick: { configurable: true, enumerable: true, get(){ return __hbTick; }, set(v){ __hbTick = v; } },
  __opPick: { configurable: true, enumerable: true, get(){ return __opPick; }, set(v){ __opPick = v; } },
  _si: { configurable: true, enumerable: true, get(){ return _si; }, set(v){ _si = v; } },
  api: { configurable: true, enumerable: true, get(){ return api; }, set(v){ api = v; } },
  apiToast: { configurable: true, enumerable: true, get(){ return apiToast; }, set(v){ apiToast = v; } },
  autoResize: { configurable: true, enumerable: true, get(){ return autoResize; }, set(v){ autoResize = v; } },
  bigEditorSend: { configurable: true, enumerable: true, get(){ return bigEditorSend; }, set(v){ bigEditorSend = v; } },
  closeBigEditor: { configurable: true, enumerable: true, get(){ return closeBigEditor; }, set(v){ closeBigEditor = v; } },
  consumeSSE: { configurable: true, enumerable: true, get(){ return consumeSSE; }, set(v){ consumeSSE = v; } },
  escapeHtml: { configurable: true, enumerable: true, get(){ return escapeHtml; }, set(v){ escapeHtml = v; } },
  fetchErr: { configurable: true, enumerable: true, get(){ return fetchErr; }, set(v){ fetchErr = v; } },
  fillModelSelect: { configurable: true, enumerable: true, get(){ return fillModelSelect; }, set(v){ fillModelSelect = v; } },
  fmtNum: { configurable: true, enumerable: true, get(){ return fmtNum; }, set(v){ fmtNum = v; } },
  getJson: { configurable: true, enumerable: true, get(){ return getJson; }, set(v){ getJson = v; } },
  jsStr: { configurable: true, enumerable: true, get(){ return jsStr; }, set(v){ jsStr = v; } },
  lsGet: { configurable: true, enumerable: true, get(){ return lsGet; }, set(v){ lsGet = v; } },
  lsSet: { configurable: true, enumerable: true, get(){ return lsSet; }, set(v){ lsSet = v; } },
  makeDesignAssistant: { configurable: true, enumerable: true, get(){ return makeDesignAssistant; }, set(v){ makeDesignAssistant = v; } },
  models: { configurable: true, enumerable: true, get(){ return models; }, set(v){ models = v; } },
  openBigEditor: { configurable: true, enumerable: true, get(){ return openBigEditor; }, set(v){ openBigEditor = v; } },
  postJson: { configurable: true, enumerable: true, get(){ return postJson; }, set(v){ postJson = v; } },
  safeSlice: { configurable: true, enumerable: true, get(){ return safeSlice; }, set(v){ safeSlice = v; } },
  setBtnLoading: { configurable: true, enumerable: true, get(){ return setBtnLoading; }, set(v){ setBtnLoading = v; } },
  showEmpty: { configurable: true, enumerable: true, get(){ return showEmpty; }, set(v){ showEmpty = v; } },
  showLoading: { configurable: true, enumerable: true, get(){ return showLoading; }, set(v){ showLoading = v; } },
  toast: { configurable: true, enumerable: true, get(){ return toast; }, set(v){ toast = v; } },
});
/* ===== v1.0.756 ESM BRIDGE END ===== */
