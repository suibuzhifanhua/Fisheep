async function scanModels(){
  const panel=$("scanPanel"), box=$("scanResults");
  panel.style.display="block"; box.innerHTML='<div style="color:var(--muted)">正在探测本机 LM Studio / Ollama…</div>';
  $("scanHint").textContent=""; $("scanImportBtn").style.display="none";
  let d;
  try{ d = await (await fetch("/api/models/scan",{method:"POST"})).json(); }
  catch(e){ box.innerHTML=`<div style="color:var(--err-fg)">扫描失败：${escapeHtml(fetchErr(e,"扫描"))}<br/><span style="color:var(--muted);font-size:var(--fs-sm)">确认 LM Studio（端口 1234）/ Ollama（端口 11434）已启动后重试；组件缺失看「设置 → 运行组件」</span></div>`; return; }
  if (!d.ok){ box.innerHTML=`<div style="color:var(--err-fg)">扫描失败：${escapeHtml(d.error||"未知错误")}<br/><span style="color:var(--muted);font-size:var(--fs-sm)">确认本地模型服务已启动后重试</span></div>`; return; }
  const found = (d.results||[]).filter(r=>r.reachable);
  if (!found.length){
    const hints = (d.results||[]).filter(r=>!r.reachable && r.error)
      .map(r=>`${escapeHtml(r.label)}（${escapeHtml(r.base_url)}）：${escapeHtml(r.error)}`);
    // v1.0.471（UI#12）：硬编码浅色（#888/#c77/#e2e2e2/#ccc）→ CSS 变量，暗色模式不再刺眼
    box.innerHTML = hints.length
      ? hints.map(h=>`<div style="color:var(--warn-fg); font-size:var(--fs-md); margin-bottom:4px">• ${h}</div>`).join("") + '<div style="color:var(--muted); margin-top:4px">未发现可直接导入的本地模型服务。请确认已启动 LM Studio（默认端口 1234，若已开启 API Key 需手动添加）或 Ollama（默认端口 11434）后重试。</div>'
      : '<div style="color:var(--muted)">未发现本地模型服务。请确认已启动 LM Studio（默认端口 1234）或 Ollama（默认端口 11434）后重试。</div>';
    return;
  }
  $("scanHint").textContent = `共发现 ${found.length} 个服务（耗时 ${d.elapsed||0}s），勾选后点「导入选中模型」`;
  box.innerHTML = found.map((p,pi)=>`
    <div style="border:1px solid var(--border); border-radius:var(--r-md); padding:10px; margin-bottom:8px">
      <div class="row" style="align-items:center; gap:8px">
        <b>${escapeHtml(p.label)}</b>
        <span style="color:var(--muted); font-size:var(--fs-sm)">${escapeHtml(p.base_url)}</span>
        <button class="ghost smbtn" style="margin-left:auto" onclick="checkAllScan(${pi})">全选</button>
      </div>
      <div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:6px" id="scanRow${pi}">
        ${(p.models||[]).map(m=>`
          <label style="display:flex; align-items:center; gap:4px; border:1px solid var(--border); border-radius:var(--r-lg); padding:3px 10px; cursor:pointer; font-size:var(--fs-md)">
            <input type="checkbox" value="${escapeHtml(m.id)}" data-base="${escapeHtml(p.base_url)}" data-label="${escapeHtml(p.label)}" checked/>
            ${escapeHtml(m.name)}
          </label>`).join("")}
      </div>
    </div>`).join("");
  $("scanImportBtn").style.display="inline-block";
}
function checkAllScan(pi){
  const row=$("scanRow"+pi); if(!row) return;
  const cbs=row.querySelectorAll("input[type=checkbox]");
  const allChecked=[...cbs].every(cb=>cb.checked);
  cbs.forEach(cb=>cb.checked=!allChecked);
  const btn=row.parentElement.querySelector('button[onclick^="checkAllScan"]');
  if(btn) btn.textContent = allChecked ? "全选" : "取消";
}
function checkAllScanPanel(){
  document.querySelectorAll("#scanResults input[type=checkbox]").forEach(cb=>cb.checked=true);
}
function invertScanPanel(){
  document.querySelectorAll("#scanResults input[type=checkbox]").forEach(cb=>cb.checked=!cb.checked);
}
async function importScanned(){
  const cbs=[...document.querySelectorAll("#scanResults input[type=checkbox]:checked")];
  if(!cbs.length){ toast("请先勾选要导入的模型","err"); return; }
  const exists=new Set(models.map(m=>`${m.base_url}|${m.model}`));
  const needDefault=!models.some(m=>m.enabled && m.is_default);
  // v1.0.660（审查修复 F5）：先算"要加什么"，**不动全局 models**。
  // 原实现先把新记录 push 进 models 再整体 POST——POST 失败时后端没这份数据、
  // 前端全局却已经有了，且 UI 已按新状态渲染 → 前后端状态不一致（下次刷新又没了）。
  // 现在：先 POST 成功，再提交本地变更；失败则原样不动（无需回滚，因为没改过）。
  const toAdd=[];
  let dup=0;
  cbs.forEach(cb=>{
    const rec={ name: cb.value,  // v1.0.233：列表名称默认同模型参数值，导入后可编辑改名
      base_url:cb.dataset.base, model:cb.value, api_key:"",
      enabled:true, is_default:needDefault && toAdd.length===0 };
    const key=`${rec.base_url}|${rec.model}`;
    if(exists.has(key)){ dup++; return; }
    exists.add(key); toAdd.push(rec);
  });
  const added=toAdd.length;
  if(!added){ toast("所选模型均已存在，无需重复导入","err"); return; }
  // 快照：POST 用它，成功后才把新记录并入全局 models
  const snapshot=models;
  const payload=models.concat(toAdd);
  try{
    const r=await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await r.json();
    if(!r.ok || d.error){ toast("导入失败："+(d.error||"HTTP "+r.status),"err"); return; }
    // 后端已接受 → 此刻才提交本地状态
    models = snapshot.concat(toAdd);
    $("scanPanel").style.display="none";
    await loadModels(); refreshStatus();
    toast(`已导入 ${added} 个本地模型${dup?`，跳过 ${dup} 个重复`:""}`);
  }catch(e){ toast(fetchErr(e,"导入模型"),"err"); }
}
let currentJudgeId = "";
// ---- Token 用量统计（v1.0.252，模型页）----
let __usageDays = 14;
function fmtTok(n){ return fmtNum(n); }   // 与 13-dashboard 的 fmtDashNum 同款实现收敛到 01-core 的 fmtNum，保留旧函数名
async function loadUsageStats(days){
  if(days) __usageDays = days;
  const cards=$("usageCards"), chart=$("usageChart"), chartX=$("usageChartX"), mw=$("usageModelsWrap");
  if(!cards) return;
  try{
    const d = await getJson(`/api/usage/summary?days=${__usageDays}`);
    const rl = $("usageRangeLabel"); if(rl) rl.textContent = `近 ${d.days} 天`;
    document.querySelectorAll("#usageRangeBtns [data-days]").forEach(b=>{ b.style.color = (+b.dataset.days===d.days) ? "var(--brand-fg)" : ""; b.style.fontWeight = (+b.dataset.days===d.days) ? "700" : ""; });
    // 汇总卡
    cards.innerHTML = "";
    const mk = (label, value, sub)=>{
      const el=document.createElement("div"); el.className="usage-stat";
      el.innerHTML = `<div class="us-label">${label}</div><div class="us-value">${value}</div><div class="us-sub">${sub||""}</div>`;
      cards.appendChild(el);
    };
    mk("今日 Tokens", fmtTok(d.today.total), `输入 ${fmtTok(d.today.prompt)} · 输出 ${fmtTok(d.today.completion)}`);
    mk("今日请求", d.today.requests, d.today.requests ? "次调用" : "今天还没有调用");
    mk(`${d.days} 天总计`, fmtTok(d.totals.total), `共 ${d.totals.requests} 次调用`);
    mk("日均用量", fmtTok(d.avg_daily_total), "tokens / 天");
    // 柱状图（stacked：输入绿在下、输出蓝在上）
    chart.innerHTML=""; chartX.innerHTML="";
    const max = Math.max(1, ...d.day_series.map(x=>x.total));
    const hasData = d.totals.total > 0;
    if(!hasData){
      chart.innerHTML = `<div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:var(--fs-sm)">暂无数据——发起任意对话/任务后这里会出现用量柱</div>`;
    } else {
      d.day_series.forEach(x=>{
        const col=document.createElement("div"); col.className="uc-col";
        col.dataset.tip = `${x.date.slice(5)}｜总 ${fmtTok(x.total)}（入 ${fmtTok(x.prompt)} / 出 ${fmtTok(x.completion)}）｜${x.requests} 次`;
        const pt=document.createElement("div"); pt.className="uc-pt";
        pt.style.height = x.prompt/max*130 +"px";
        const ct=document.createElement("div"); ct.className="uc-ct";
        ct.style.height = x.completion/max*130 +"px";
        if(x.total>0){ pt.style.minHeight="2px"; ct.style.minHeight = x.completion>0?"2px":"0"; }
        col.appendChild(pt); col.appendChild(ct); chart.appendChild(col);
        const xl=document.createElement("span"); xl.textContent = x.date.slice(8); chartX.appendChild(xl);
      });
    }
    // 模型分布
    if(d.models && d.models.length){
      mw.style.display="";
      const box=$("usageModels"); box.innerHTML="";
      const mmax = Math.max(1, d.models[0].total);
      d.models.forEach(m=>{
        const row=document.createElement("div"); row.className="usage-mrow";
        row.title = `输入 ${m.prompt} · 输出 ${m.completion} · ${m.requests} 次调用`;
        row.innerHTML = `<span class="um-name">${escapeHtml(m.model)}</span><div class="um-bar"><div style="width:${Math.max(2, m.total/mmax*100)}%"></div></div><span class="um-val">${fmtTok(m.total)} tok · ${m.requests}次</span>`;
        box.appendChild(row);
      });
    } else { mw.style.display="none"; }
  }catch(e){
    cards.innerHTML = `<div class="usage-stat" style="grid-column:1/-1"><div class="us-label">用量加载失败</div><div class="us-sub">${escapeHtml(String(e&&e.message||e))}</div></div>`;
  }
}
// 清空 Token 用量统计（v1.0.270）：删除全部月度存档
async function clearUsageStats(){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  // v1.0.667（审查 P2-8）：文案路径纠正——实际存 产物库/日志/（services/usage_stats.py），
  // 原写 dist/logs/ 与真实落点不符，用户会误以为删的是别处。
  confirmAsk("清空用量统计", "将永久删除 产物库/日志/usage_*.jsonl 全部存档记录，不可恢复。", async ()=>{
    try{
      const d = await apiToast("/api/usage/clear", {method:"DELETE"}, "清空用量统计");
      toast(`已清空：${d.records} 条记录 / ${d.removed} 个存档文件`);
      loadUsageStats();
    }catch(_){}
  }, "清空");
}
async function loadModels(){
  try { const c = await getJson("/api/config"); currentJudgeId = c.judge_model_id || ""; } catch(e){ currentJudgeId = ""; }
  // v1.0.616（审查 P2）：这里此前不兜错——接口挂了整页停在上一版数据、非数组返回直接抛
  // forEach 异常（openView 链上表现为"模型页空白像没配模型"）。
  const tb = $("modelTable") && $("modelTable").querySelector("tbody"); if(!tb) return;
  // v1.0.667（审查 P2-11）：首刷给个「加载中」占位，慢网/冷启动时不再空白后弹出
  // （只首刷提示——本函数由 openView 单次调用，非轮询，不会刷屏）
  if(!tb.dataset.loaded) tb.innerHTML=`<tr><td colspan="6" class="sub">模型列表加载中…</td></tr>`;
  try{
    const d = await getJson("/api/models");
    if(!Array.isArray(d)) throw new Error((d && d.error) || "接口返回结构异常");
    models = d;
  }catch(e){
    tb.innerHTML = `<tr><td colspan="6" class="sub">⚠️ 模型列表读取失败：${escapeHtml(String((e&&e.message)||e))} —— 换个动作再回来会自动重试</td></tr>`;
    return;
  }
  tb.dataset.loaded="1";   // v1.0.667 P2-11：首刷占位只出一次，之后走正常失败/空态
  tb.innerHTML="";
  models.forEach(m=>{
    const tr=document.createElement("tr");
    if (m.id===currentJudgeId) tr.classList.add("judge-row");   // 判断大模型行高亮
    const tags = (m.is_default?'<span class="tag def">默认</span>':'') + (m.enabled?'':'<span class="tag dis">禁用</span>')
      + (m.id===currentJudgeId?'<span class="tag judge">判断</span>':'');
    // 推理参数徽标（v1.0.186）：思考强度/思考开关/思考预算/自定义扩展
    // v1.0.742（界面审查 P2-5）：参数值来自后端配置（用户可自填）——补 escapeHtml，
    // 防止以 < 开头的自定义值把徽标结构打穿。
    const rtags = (m.reasoning_effort?`<span class="tag" title="推理强度">🧠${escapeHtml(m.reasoning_effort)}</span>`:'')
      + (m.enable_thinking===true||m.enable_thinking===false?`<span class="tag" title="思考开关">💭${m.enable_thinking?'开':'关'}</span>`:'')
      + (m.thinking_budget?`<span class="tag" title="思考预算 token">⏱${escapeHtml(m.thinking_budget)}</span>`:'')
      + (m.extra_params?`<span class="tag" title="自定义扩展参数">🧩扩展</span>`:'');
    // 协议与能力徽标（v1.0.187）
    const protoLabel = ({openai:'OpenAI',anthropic:'Anthropic',custom:'自定义'})[m.protocol||'openai'];
    const ptags = `<span class="tag caps" title="协议">${protoLabel}</span>`
      // v1.0.742（界面审查 P2-5）：数值字段一并补转义（escapeHtml 已归一数字，安全）
      + (m.max_input_tokens?`<span class="tag caps" title="上下文窗口">📥${escapeHtml(m.max_input_tokens>=1024?(m.max_input_tokens/1024|0)+'K':m.max_input_tokens)}</span>`:'')
      + (m.max_output_tokens?`<span class="tag caps" title="输出上限">📤${escapeHtml(m.max_output_tokens>=1024?(m.max_output_tokens/1024|0)+'K':m.max_output_tokens)}</span>`:'');
    tr.innerHTML=`<td>${escapeHtml(m.name)}${tags}</td><td>${ptags}</td><td>${escapeHtml(m.model)}${rtags}</td>`+
      // v1.0.742（界面审查 P2-5）：api_key_masked 前 2 位是用户填的原文——裸插值遇 '<' 会破
      // <td> 结构、整行错位；同行 name/model 都已转义，此为漏项，补齐。
      `<td>${m.api_key_masked?escapeHtml(m.api_key_masked):'未填'}</td>`+
      // v1.0.471（UI#7）：内联 onclick 字符串拼接 → data-* + 容器事件委托
      //（消灭 jsStr 转义拼接，id 只出现在 dataset，无注入面）
      // v1.0.718（用户：入口集中在顶上）：行内「🧪 评测」按钮取消——评测统一走
      // 模型管理顶部的「综合评测」弹窗选模型（构建器在下方 openBenchModal）
      `<td class="ops"><button class="ghost smbtn" data-act="edit" data-id="${escapeHtml(m.id)}">编辑</button>`+
      `<button class="ghost smbtn" data-act="default" data-id="${escapeHtml(m.id)}">默认</button>`+
      `<button class="ghost smbtn" data-act="judge" data-id="${escapeHtml(m.id)}">${m.id===currentJudgeId?'取消判断':'判断'}</button>`+
      `<span class="btnsep" aria-hidden="true"></span>`+
      `<button class="danger smbtn" data-act="del" data-id="${escapeHtml(m.id)}">删</button></td>`;
    tb.appendChild(tr);
  });
  const sel=$("t_model"); if(sel) fillModelSelect(sel, models);   // 拼串本体收敛到 fillModelSelect（01-core），与 02/08 同款下拉共用
  if(typeof fillModelSels==="function") fillModelSels();   // v1.0.486：模型增删后同步任务选择器
  // v1.0.588：双模型分工的实际状态一眼可见（未配置时卡点自动介入/ask_judge 全部不生效，
  // 此前只有读帮助文档才知道）。value 补 escapeHtml——同款下拉在 02/08 都已转义，此处漏了一处
  const js1=$("judgeState");
  if(js1){
    const jm = models.find(m=>m.id===currentJudgeId);
    js1.innerHTML = jm
      ? `<span style="color:var(--brand-fg)">✅ 双模型分工已启用</span>：判断大模型 <b>${escapeHtml(jm.name)}</b>`+
        `（小模型跑工具循环，卡住/交卷被拦/ask_judge 时自动拉它给建议）`
      : `<span style="color:var(--warn-fg)">⚠️ 当前是单模型</span>：未设判断大模型，「卡点自动介入 / ask_judge / 团队规划审查」<b>不会生效</b>`+
        `（任务照常跑，只是没有大模型兜底）。想启用请在上面任一模型行点「判断」。`;
  }
}
// v1.0.471（UI#7）：模型表操作按钮事件委托（一次绑定，动态行无需重复挂）
document.addEventListener("click", function(e){
  const btn = e.target.closest && e.target.closest("#modelTable td.ops [data-act]");
  if(!btn) return;
  const id = btn.dataset.id, act = btn.dataset.act;
  if(act==="edit") editModel(id);
  // v1.0.718：act==="bench" 分支随按钮一起取消（入口集中在模型管理顶部弹窗）
  else if(act==="default") setDefaultModel(id);
  else if(act==="judge") setJudgeModel(id);
  else if(act==="del") delModel(id);
});
function showModelForm(){
  $("modelForm").style.display="block";
  // 基础字段
  $("m_name").value=""; $("m_model").value=""; $("m_base").value=""; $("m_key").value="";
  // v1.0.233：名称默认跟随模型参数值（未自定义时联动）
  window.__nameCustom = false;
  $("m_enabled").checked=true; $("m_default").checked=false;
  $("m_key").placeholder="sk-...（留空=保留已有）";
  // 协议
  $("m_protocol").value="openai"; onProtocolChange();
  // 高级配置 caps（v1.0.664：只保留两个生效位）
  $("m_cap_thinking").checked=false; $("m_cap_off").checked=true;
  // 思考强度
  $("m_effort").value="";
  document.querySelectorAll(".m_lv").forEach(cb=>cb.checked=false);
  // token 档位
  $("m_in_tok").value=""; $("m_out_tok").value="";
  // 兼容旧字段
  $("m_budget").value=""; $("m_extra").value="";
  window.__editId=null;
}

function onProtocolChange(){
  const v = $("m_protocol").value;
  // 自定义协议时：API Base URL 是完整 endpoint；OpenAI / Anthropic 时是 base 地址，自动补 /chat/completions
  const isCustom = v === "custom";
  // m_endpoint_label 绑的是 API Base URL 输入框（v1.0.193 重排后），按协议切换 label + placeholder
  $("m_endpoint_label").textContent = isCustom
    ? "API Base URL（完整 endpoint，自定义协议）"
    : "API Base URL（自动追加 /chat/completions）";
  $("m_base").placeholder = isCustom
    ? "https://api.example.com/v1/chat/completions"
    : "https://api.example.com/v1";
  // v1.0.664：自定义协议由本下拉表达，不再有对应的展示型死键需要联动
}

// ===== v1.0.233 模型名称默认跟随模型参数值 =====
// 未自定义时：改模型参数值/点选检测出的模型 → 名称自动同步同值；
// 用户改过名称（与参数值不同）→ 视为自定义，不再联动；清空或改回同值 → 恢复联动。
function onModelValInput(){
  if (!window.__nameCustom) $("m_name").value = $("m_model").value;
}
function onNameInput(){
  const n = $("m_name").value.trim(), mv = $("m_model").value.trim();
  window.__nameCustom = !!(n && n !== mv);
}

// v1.0.660（审查修复 F1）：原实现读隐式全局 `event`（`const ev = event || window.event`）——
// 该名字只在部分浏览器里作为 window 的全局属性存在，严格模式脚本 / Firefox 直接
// ReferenceError，点「32K/64K…」按钮整个函数体挂掉、输入框填不进值。
// 改成显式传参：调用点（index.html 的 onclick）传 event，函数不再依赖任何隐式全局。
function pickToken(ev, inputId, value){
  const el = $(inputId); if(el) el.value = value;
  // 视觉反馈：按钮短暂高亮
  if (ev && ev.target && ev.target.style){
    ev.target.style.background = "#e8f7ee";
    ev.target.style.borderColor = "var(--brand)";
    setTimeout(()=>{ try{ ev.target.style.background=""; ev.target.style.borderColor=""; }catch(e){} }, 400);
  }
}

// ===== 远程模型探测（v1.0.188）=====
async function probeModels(){
  const base = $("m_base").value.trim();
  const key = $("m_key").value.trim();
  if (!base){ toast("请先填接口地址", "err"); return; }
  const panel = $("probePanel");
  const btn = $("probeBtn");
  btn.disabled = true; btn.textContent = "检测中…";
  panel.style.display = "block";
  panel.innerHTML = `<div class="wmeta" style="padding:6px 2px">⏳ 正在拉取 <b>${escapeHtml(base)}</b> 的模型列表…</div>`;
  try{
    const r = await fetch("/api/models/probe", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({base_url: base, api_key: key})});
    const d = await r.json();
    if (!r.ok || !d.ok){ panel.innerHTML = `<div class="err-note">⚠️ ${escapeHtml(d.error||("HTTP "+r.status))}</div>`; return; }
    // effort 试探结果提示
    let hint = "";
    if (d.effort_probe === "supported") hint = `<div class="probe-ok">✅ 已确认该服务接受 reasoning_effort 参数（思考强度真生效）</div>`;
    else if (d.effort_probe === "rejected") hint = `<div class="probe-warn">ℹ️ 该服务拒绝 reasoning_effort 参数——思考强度档位不可用，请用「思考开关/自定义参数」控制</div>`;
    else if (d.effort_probe && String(d.effort_probe).startsWith("error")) hint = `<div class="probe-warn">ℹ️ reasoning_effort 试探异常（${escapeHtml(d.effort_probe)}），以模型名推断为准</div>`;
    const items = (d.models||[]).map(m=>{
      const c = m.caps || {};
      // v1.0.698（用户需求：思考六档）：五档全展示（v1.0.664 曾把 xhigh 当死档过滤，现已启用）
      const _effLbl = {low:"低",medium:"中",high:"高",xhigh:"超高",max:"极致"};
      const lvs = (m.levels||[]).filter(x=>_effLbl[x]);
      const lv = lvs.map(x=>_effLbl[x]).join("·");
      // v1.0.664：只展示仍然生效的思考位；工具/图片/仅思考/自定义协议是死键，不再展示
      const capsTxt = (c.thinking ? "💭思考" : "");
      // v1.0.660（审查修复 V3）：data-caps / data-levels 原先只把单引号换成 &#39;，
      // 其余字符（" < > &）原样进 HTML 属性 → 一个双引号就能跳出属性边界注入事件。
      // 改用项目已有的 escapeHtml（01-core）——它转义 & < > " ' 五类，属性边界安全。
      return `<div class="probe-item" onclick="pickProbedModel(this)" data-id="${jsStr(m.id)}" data-caps="${escapeHtml(JSON.stringify(c))}" data-levels="${escapeHtml(JSON.stringify(lvs))}">
        <span class="probe-id">${escapeHtml(m.id)}</span>
        <span class="probe-caps">${capsTxt}${lv?` · 强度:${escapeHtml(lv)}`:""}</span>
        <span class="probe-pick">点选填入 →</span>
      </div>`;
    }).join("");
    panel.innerHTML = `<div style="font-size:var(--fs-sm); color:var(--muted); margin-bottom:6px">共 ${d.models.length} 个可用模型（点击填入表单，能力按模型名自动推断，可手动微调）</div>${hint}<div style="max-height:220px; overflow:auto">${items}</div>`;
  }catch(e){
    panel.innerHTML = `<div class="err-note">⚠️ ${escapeHtml(e.message||"网络错误")}</div>`;
  }finally{
    btn.disabled = false; btn.textContent = "🔍 检测模型";
  }
}
// 能力勾选回填（pickProbedModel / editModel 两处同款）：v1.0.664 只回填两个生效位。
function fillCapChecks(obj, caps){
  const c = caps || (obj && obj.caps) || {};
  $("m_cap_thinking").checked = !!c.thinking;
  $("m_cap_off").checked = c.can_disable_thinking !== false;
}
function pickProbedModel(el){
  const id = el.dataset.id;
  $("m_model").value = id;
  // v1.0.233：名称未自定义时同步为模型参数值
  if (!window.__nameCustom) $("m_name").value = id;
  // 回填 caps（7 行同款收敛到 fillCapChecks）
  try{
    fillCapChecks(el, JSON.parse(el.dataset.caps||"{}"));
  }catch(e){}
  // 回填思考强度档位
  try{
    const lv = JSON.parse(el.dataset.levels||"[]");
    document.querySelectorAll(".m_lv").forEach(cb=>cb.checked = lv.includes(cb.value));
  }catch(e){}
  // 视觉高亮选中项
  document.querySelectorAll(".probe-item").forEach(x=>x.classList.remove("sel"));
  el.classList.add("sel");
  toast("已填入模型 " + id);
}

async function saveModel(btn){
  // caps 收集（v1.0.664：只保留两个生效位）
  const caps = {
    thinking: $("m_cap_thinking").checked,
    can_disable_thinking: $("m_cap_off").checked,
  };
  const levels = [...document.querySelectorAll(".m_lv:checked")].map(cb=>cb.value);
  // 思考开关：选了 caps.thinking 且用户没显式给 enable_thinking 时，按「推理模式开 = 思考开」推一个；
  // 保持与 v1.0.186 兼容（v1.0.186 字段为单选下拉），这里把「推理模式」与「enable_thinking」联动：
  // caps.thinking = true  → enable_thinking 默认 true（除非用户没勾「允许关闭思考」）
  // caps.thinking = false → enable_thinking 不设置（跟随模型默认）
  const enable_thinking = caps.thinking ? true : null;

  const rec = {
    name: $("m_name").value.trim() || $("m_model").value.trim(),  // v1.0.233：留空默认同模型参数值
    model: $("m_model").value.trim(),
    base_url: $("m_base").value.trim(),
    api_key: $("m_key").value.trim(),
    enabled: $("m_enabled").checked,
    is_default: $("m_default").checked,
    protocol: $("m_protocol").value,
    caps, thinking_supported_levels: levels,
    default_thinking_effort: $("m_effort").value || null,
    reasoning_effort: $("m_effort").value || null,  // 兼容 v1.0.186 旧逻辑（OpenAI 用）
    enable_thinking,
    thinking_budget: null, extra_params: null,
  };
  const _b = parseInt($("m_budget").value, 10);
  if ($("m_budget").value.trim() && _b>0 && _b<200000) rec.thinking_budget = _b;
  const _in = parseInt($("m_in_tok").value, 10);
  if ($("m_in_tok").value.trim() && _in>0) rec.max_input_tokens = _in;
  const _out = parseInt($("m_out_tok").value, 10);
  if ($("m_out_tok").value.trim() && _out>0) rec.max_output_tokens = _out;
  const _ep = $("m_extra").value.trim();
  if (_ep){ try{ rec.extra_params = JSON.parse(_ep); }catch(e){ toast("自定义参数不是合法 JSON："+e.message, "err"); return; } }
  if (!rec.model || !rec.base_url){ toast("模型参数值和接口地址不能为空", "err"); return; }
  if (window.__editId){ rec.id = window.__editId; }
  // 默认唯一性（v1.0.188 修复）：勾了「设为默认」就把其它模型默认位清掉，防双默认
  if (rec.is_default){
    models.forEach(m=>{ m.is_default = false; });
  }
  if (!window.__editId) models.push(rec);
  else { const idx = models.findIndex(x=>x.id===window.__editId); if(idx>=0) models[idx]=rec; else models.push(rec); }
  setBtnLoading(btn||document.querySelector("#modelForm .primary"), true);
  try {
    const r = await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(models)});
    const d = await r.json();
    if (!r.ok || d.error){ toast("保存失败："+(d.error||"HTTP "+r.status), "err"); return; }
    const isNew = !window.__editId;
    $("modelForm").style.display="none"; window.__editId=null; await loadModels(); refreshStatus();
    // v1.0.626（用户要求取消）：v1.0.319 起"新保存的模型自动跑一轮综合评测"，实测打扰大于价值
    // ——保存动作当场打真实接口、烧额度、还会撞上"已有评测在进行"；用户只是想把模型存下来。
    // v1.0.718（用户：入口集中在顶上）：行内「🧪 评测」已取消，提示语指向顶部「测试连接 / 综合评测」
    // 弹窗（评测能力一项没少，只是改回**由用户主动点**）。
    toast(isNew ? "模型已保存｜要验证连通/能力，用顶部「测试连接 / 综合评测」选模型测（保存不再自动跑）" : "模型已保存");
  } catch(e){ toast(fetchErr(e,"保存模型"), "err"); }
  finally { setBtnLoading(btn||document.querySelector("#modelForm .primary"), false); }
}
document.addEventListener("keydown", e=>{
  if (e.key==="Enter" && $("modelForm").style.display!=="none" && ["m_name","m_model","m_base","m_key"].includes(e.target.id)){
    e.preventDefault(); saveModel();
  }
});
function editModel(id){
  const m=models.find(x=>x.id===id); if(!m) return;
  showModelForm();
  $("m_name").value=m.name;$("m_model").value=m.model;$("m_base").value=m.base_url;
  $("m_key").placeholder="已保存："+(m.api_key_masked||"")+"（留空不改）";
  $("m_enabled").checked=m.enabled;$("m_default").checked=m.is_default;
  // 协议（v1.0.187）
  $("m_protocol").value = m.protocol || "openai"; onProtocolChange();
  // caps 回填（缺字段用合理默认；7 行同款收敛到 fillCapChecks）
  fillCapChecks(m, m.caps);
  // 思考强度：默认强度 + 支持档位多选回填
  $("m_effort").value = m.default_thinking_effort || m.reasoning_effort || "";
  document.querySelectorAll(".m_lv").forEach(cb=>{
    cb.checked = (m.thinking_supported_levels||[]).includes(cb.value);
  });
  // token 档位回填
  $("m_in_tok").value = m.max_input_tokens || "";
  $("m_out_tok").value = m.max_output_tokens || "";
  // 兼容旧字段回填
  $("m_budget").value = m.thinking_budget ? String(m.thinking_budget) : "";
  $("m_extra").value = m.extra_params ? JSON.stringify(m.extra_params) : "";
  // v1.0.233：编辑时名称与参数值不同 → 已自定义（不联动）；相同 → 保持联动
  window.__nameCustom = (m.name||"").trim() !== (m.model||"").trim();
  window.__editId = id;
}
function cancelModelForm(){ $("modelForm").style.display="none"; window.__editId=null; }
async function setDefaultModel(id){
  models.forEach(m=>m.is_default=(m.id===id));
  try {
    const r = await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(models)});
    const d = await r.json(); if (!r.ok || d.error){ toast("设置默认失败："+(d.error||"HTTP "+r.status), "err"); return; }
    await loadModels(); refreshStatus(); toast("已设为默认");
  } catch(e){ toast(fetchErr(e,"设置默认"), "err"); }
}
async function delModel(id){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("删除模型", "删除该模型？", async ()=>{
    models = models.filter(m=>m.id!==id);
    try {
      const r = await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(models)});
      const d = await r.json(); if (!r.ok || d.error){ toast("删除失败："+(d.error||"HTTP "+r.status), "err"); return; }
      await loadModels(); refreshStatus(); toast("已删除");
    } catch(e){ toast(fetchErr(e,"删除模型"), "err"); }
  }, "删除");
}
// 双模型分工（v1.0.234）：设置/取消「判断模型」。再点一次已设为判断的模型 → 取消（退回单模型）。
async function setJudgeModel(id){
  const newId = (currentJudgeId === id) ? "" : id;
  try {
    const r = await fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({judge_model_id:newId})});
    const d = await r.json(); if (!r.ok || d.error){ toast("设置判断模型失败："+(d.error||"HTTP "+r.status), "err"); return; }
    currentJudgeId = newId;
    await loadModels(); refreshStatus();
    toast(newId ? "已设为判断模型（双模型分工：大模型判断 + 小模型工具）" : "已取消判断模型（单模型）");
  } catch(e){ toast(fetchErr(e,"设置判断模型"), "err"); }
}

// —— 模型配置一键导出/导入（v1.0.238；v1.0.270 导出改为后端落盘——
//    pywebview 下 blob+a.download 点击不触发浏览器下载，旧版只弹 toast 没有文件）——
async function exportModelCfg(){
  try{
    const d = await apiToast("/api/models/export/save", {method:"POST"}, "导出模型配置");
    // v1.0.664：导出文件含明文 API Key，成功提示里必须再警告一次（不做弹窗确认）
    toast("模型配置已导出（"+d.count+" 个模型）：\n" + d.path + "\n⚠️ 导出含 API Key，仅本机备份，勿外传", "ok", 8000);
  }catch(_){}
}
async function importModelCfg(inp){
  const f = inp.files && inp.files[0];
  inp.value = "";
  if(!f) return;
  try{
    const text = await f.text();
    const d = await apiToast("/api/models/import",{method:"POST",headers:{"Content-Type":"application/json"},body:text}, "导入模型配置");
    toast(`已导入 ${d.count} 个模型${d.judge_model_id ? "，判断大模型已恢复" : "（未含判断大模型）"}`);
    await loadModels(); refreshStatus();
  }catch(_){}
}

// ---- 模板 ----

// ---- 综合评测弹窗（v1.0.718 用户：入口集中在模型管理顶上，行内「🧪 评测」按钮取消）----
// 旧版两条入口：① 行内单模型弹窗（SSE 单模型打分）② 页面下方 benchCard 面板（多模型）。
// 现在合并成一条：顶部「综合评测」按钮 → 弹窗选模型（默认模型预勾、可多选）→ runBench 跑 7 项打分。
// 容器 id 沿用 benchOpts/benchMode/benchRunBtn/benchBody，
// js/06 的 runBench/renderBenchOpts/renderBenchResults/_benchOneModel 零改动。
// v1.0.342 守卫口径保留：真在跑（__benchListAbort 非空）才拦并 toast；残壳自动清掉重建。
function openBenchModal(){
  const exist=document.getElementById("benchModal");
  if(exist){
    if(__benchListAbort){ toast("已有评测在进行，可点弹窗里的「⏹ 停止评测」","warn"); return; }
    exist.remove();   // 上次关闭/异常残留的壳：清掉重建（模型列表要最新）
  }
  const ov=document.createElement("div");
  ov.id="benchModal";
  ov.className="modal-veil";
  ov.innerHTML=
    `<div class="modal-card" style="--mw:680px">`+
      `<div style="font-weight:500;font-size:var(--fs-lg);margin-bottom:4px">${(typeof icon==="function"?icon("beaker"):"🧪")} 模型综合评测</div>`+
      `<div class="sub" style="font-size:var(--fs-xs);margin-bottom:10px">勾选要评测的模型（默认模型已勾，可多选）：逐个/并发跑 7 项常用能力打分（基础响应/指令遵循/中文生成/逻辑推理/工具调用意图/长文提取/响应速度），加权总分 100。</div>`+
      `<div id="benchOpts"></div>`+
      `<div class="row" style="margin-top:10px;gap:14px;font-size:var(--fs-sm)">`+
        `<label style="display:inline-flex;align-items:center;gap:5px;cursor:pointer"><input type="radio" name="benchMode" value="sequential" checked/> 逐个跑（稳，本地模型建议）</label>`+
        `<label style="display:inline-flex;align-items:center;gap:5px;cursor:pointer"><input type="radio" name="benchMode" value="parallel"/> 并发跑（快，本地模型易超时）</label>`+
      `</div>`+
      `<div id="benchBody" style="margin-top:10px"></div>`+
      `<div class="row" style="justify-content:flex-end;margin-top:10px">`+
        `<button class="ghost" id="benchRunBtn" onclick="runBench()">开始评测</button>`+
        `<button class="ghost" onclick="closeBenchModal()">关闭</button>`+
      `</div>`+
    `</div>`;
  document.body.appendChild(ov);
  renderBenchOpts();   // 刷新勾选列表（默认模型预勾，禁用模型带灰标）
}
function closeBenchModal(){
  const m=document.getElementById("benchModal");
  if(m){
    if(__benchListAbort){ try{ __benchListAbort.abort(); }catch(e){} }   // 运行中先停再关
    m.remove();
  }
}
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {
  __usageDays, cancelModelForm, checkAllScan, checkAllScanPanel, clearUsageStats, closeBenchModal,
  currentJudgeId, delModel, editModel, exportModelCfg, fillCapChecks, fmtTok,
  importModelCfg, importScanned, invertScanPanel, loadModels, loadUsageStats, onModelValInput,
  onNameInput, onProtocolChange, openBenchModal, pickProbedModel, pickToken, probeModels,
  saveModel, scanModels, setDefaultModel, setJudgeModel, showModelForm
};
Object.defineProperties(window, {
  __usageDays: { configurable: true, enumerable: true, get(){ return __usageDays; }, set(v){ __usageDays = v; } },
  cancelModelForm: { configurable: true, enumerable: true, get(){ return cancelModelForm; }, set(v){ cancelModelForm = v; } },
  checkAllScan: { configurable: true, enumerable: true, get(){ return checkAllScan; }, set(v){ checkAllScan = v; } },
  checkAllScanPanel: { configurable: true, enumerable: true, get(){ return checkAllScanPanel; }, set(v){ checkAllScanPanel = v; } },
  clearUsageStats: { configurable: true, enumerable: true, get(){ return clearUsageStats; }, set(v){ clearUsageStats = v; } },
  closeBenchModal: { configurable: true, enumerable: true, get(){ return closeBenchModal; }, set(v){ closeBenchModal = v; } },
  currentJudgeId: { configurable: true, enumerable: true, get(){ return currentJudgeId; }, set(v){ currentJudgeId = v; } },
  delModel: { configurable: true, enumerable: true, get(){ return delModel; }, set(v){ delModel = v; } },
  editModel: { configurable: true, enumerable: true, get(){ return editModel; }, set(v){ editModel = v; } },
  exportModelCfg: { configurable: true, enumerable: true, get(){ return exportModelCfg; }, set(v){ exportModelCfg = v; } },
  fillCapChecks: { configurable: true, enumerable: true, get(){ return fillCapChecks; }, set(v){ fillCapChecks = v; } },
  fmtTok: { configurable: true, enumerable: true, get(){ return fmtTok; }, set(v){ fmtTok = v; } },
  importModelCfg: { configurable: true, enumerable: true, get(){ return importModelCfg; }, set(v){ importModelCfg = v; } },
  importScanned: { configurable: true, enumerable: true, get(){ return importScanned; }, set(v){ importScanned = v; } },
  invertScanPanel: { configurable: true, enumerable: true, get(){ return invertScanPanel; }, set(v){ invertScanPanel = v; } },
  loadModels: { configurable: true, enumerable: true, get(){ return loadModels; }, set(v){ loadModels = v; } },
  loadUsageStats: { configurable: true, enumerable: true, get(){ return loadUsageStats; }, set(v){ loadUsageStats = v; } },
  onModelValInput: { configurable: true, enumerable: true, get(){ return onModelValInput; }, set(v){ onModelValInput = v; } },
  onNameInput: { configurable: true, enumerable: true, get(){ return onNameInput; }, set(v){ onNameInput = v; } },
  onProtocolChange: { configurable: true, enumerable: true, get(){ return onProtocolChange; }, set(v){ onProtocolChange = v; } },
  openBenchModal: { configurable: true, enumerable: true, get(){ return openBenchModal; }, set(v){ openBenchModal = v; } },
  pickProbedModel: { configurable: true, enumerable: true, get(){ return pickProbedModel; }, set(v){ pickProbedModel = v; } },
  pickToken: { configurable: true, enumerable: true, get(){ return pickToken; }, set(v){ pickToken = v; } },
  probeModels: { configurable: true, enumerable: true, get(){ return probeModels; }, set(v){ probeModels = v; } },
  saveModel: { configurable: true, enumerable: true, get(){ return saveModel; }, set(v){ saveModel = v; } },
  scanModels: { configurable: true, enumerable: true, get(){ return scanModels; }, set(v){ scanModels = v; } },
  setDefaultModel: { configurable: true, enumerable: true, get(){ return setDefaultModel; }, set(v){ setDefaultModel = v; } },
  setJudgeModel: { configurable: true, enumerable: true, get(){ return setJudgeModel; }, set(v){ setJudgeModel = v; } },
  showModelForm: { configurable: true, enumerable: true, get(){ return showModelForm; }, set(v){ showModelForm = v; } },
});
/* ===== v1.0.756 ESM BRIDGE END ===== */
