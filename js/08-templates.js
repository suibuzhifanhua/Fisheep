async function loadTemplates(){
  const box=$("tplList"); showLoading(box,"加载模板中…");
  try{
    // v1.0.303：JSON null 兜底——旧磁盘 config "templates": null 时后端可能返回 JSON null，
    // 直接 .length 会抛 "Cannot read properties of null"（任务模板页加载失败 P0）；
    // getJson（api 契约）对 null 回的是 {}，这里显式归一成 []，后面的 filter/findIndex 才有兜底。
    const _tl = await getJson("/api/templates");
    templates = Array.isArray(_tl) ? _tl : [];
    box.innerHTML="";
    if(!templates.length){ showEmpty(box,"点右上角「+ 新建模板」创建第一个",{ic:"notebook",title:"暂无模板"}); return; }
    templates.forEach(t=>{
      const el=document.createElement("div"); el.className="win";
      const usedTag=t.used?(' · 已用 '+t.used+' 次'):'';
      // v1.0.366（P3）：内置模板只读——点击直接运行；用户模板点击进编辑器
      // v1.0.742（界面审查 P2-1）：▶/🔧/🗑 emoji → 统一 SVG 线稿；:18 的纯图标删除钮是破坏性
      // 操作却无文字无悬停提示——补 danger + title + aria-label（同 skills/connectors 口径）。
      const _icT=(typeof icon==="function")?icon:(n=>"");
      el.innerHTML=`<div class="t">${escapeHtml(t.name)}${t.builtin?' <span class="pill" style="background:var(--accent-soft);color:var(--accent-fg)">内置</span>':''}</div>`+
        `<div class="u">${escapeHtml(t.description||'')} · 输入${t.input_fields?.length||0}项${usedTag}</div>`+
        `<div style="margin-top:6px"><button class="ghost smbtn" onclick="openTplRun('${jsStr(t.id)}')">${_icT("play")} 运行</button>`+
        (t.builtin?"":` <button class="ghost smbtn" onclick="openTemplateEditor('${jsStr(t.id)}')">${_icT("wrench")} 编辑</button>`+
        ` <button class="ghost smbtn danger" onclick="delTemplateById('${jsStr(t.id)}')" title="删除该模板" aria-label="删除模板">${_icT("trash")}</button>`)+
        `</div>`;
      el.onclick=null; box.appendChild(el);
    });
  }catch(e){ showEmpty(box,"加载失败<br/>"+escapeHtml(e.message||"网络错误"),{ic:"alert",title:"加载失败"}); }
}
async function delTemplateById(id){
  // v1.0.373（体验4）：原生 confirm → 统一自定义确认弹层
  confirmAsk("删除模板", "删除该模板？", async ()=>{
    templates=templates.filter(x=>x.id!==id);
    try{
      const r=await fetch("/api/templates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(templates)});
      const d=await r.json(); if(!r.ok||d.error){ toast("删除失败："+(d.error||"HTTP "+r.status),"err"); return; }
      await loadTemplates(); toast("已删除");
    }catch(e){ toast(fetchErr(e,"删除模板"),"err"); }
  }, "删除");
}
function newTemplate(){
  window.__editTid=null;
  $("tplEditorTitle").textContent="新建模板";
  $("t_name").value="";$("t_desc").value="";$("t_prompt").value="";$("t_ctx").checked=true;
  $("t_model").value="";
  $("t_inputs").innerHTML=""; addInputField();
  $("t_schema").value=`{\n  "type": "object",\n  "properties": {\n    "summary": {"type": "string"}\n  },\n  "required": ["summary"]\n}`;
  $("tplEditor").style.display="block"; $("runPanel").style.display="none";
  loadModels();
}
function addInputField(key="",label="",type="text"){
  const el=document.createElement("div"); el.className="ifield";
  el.innerHTML=`<div class="row"><input placeholder="字段key(英文)" value="${escapeHtml(key)}" class="if_key"/>`+
    `<input placeholder="显示名" value="${escapeHtml(label)}" class="if_label"/>`+
    `<select class="if_type"><option value="text">单行</option><option value="textarea">多行</option></select>`+
    `<button class="danger" title="删除" onclick="this.parentNode.parentNode.remove()">${(typeof icon==="function"?icon("xmark"):"")}</button></div>`;
  if(type==="textarea") el.querySelector(".if_type").value="textarea";
  $("t_inputs").appendChild(el);
}
function openTemplateEditor(id){
  const t=templates.find(x=>x.id===id); if(!t) return;
  window.__editTid=id;
  $("tplEditorTitle").textContent="编辑："+t.name;
  loadModels();
  $("t_name").value=t.name;$("t_desc").value=t.description||"";$("t_prompt").value=t.system_prompt||"";
  $("t_ctx").checked=!!t.use_wecom_context;$("t_model").value=t.model_id||"";
  $("t_inputs").innerHTML=""; (t.input_fields||[]).forEach(f=>addInputField(f.key,f.label,f.type));
  $("t_schema").value=JSON.stringify(t.output_schema||{type:"object",properties:{}},null,2);
  $("tplEditor").style.display="block"; $("runPanel").style.display="none";
}
function collectTemplate(){
  const inputs=[]; document.querySelectorAll("#t_inputs .ifield").forEach(el=>{
    inputs.push({key:el.querySelector(".if_key").value.trim(), label:el.querySelector(".if_label").value.trim(),
      type:el.querySelector(".if_type").value, required:true});
  });
  let schema=null; try{ schema=JSON.parse($("t_schema").value); }catch(e){ toast("输出 Schema 不是合法 JSON："+e.message,"err"); return null; }
  return { name:$("t_name").value.trim()||"未命名模板", description:$("t_desc").value.trim(),
    model_id:$("t_model").value, use_wecom_context:$("t_ctx").checked,
    system_prompt:$("t_prompt").value, input_fields:inputs, output_schema:schema };
}
async function saveTemplate(){
  const t=collectTemplate(); if(!t) return;
  if(window.__editTid) t.id=window.__editTid;
  const i=templates.findIndex(x=>x.id===window.__editTid);
  if(i>=0) templates[i]=t; else templates.push(t);
  try {
    const r = await fetch("/api/templates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(templates)});
    const d = await r.json(); if (!r.ok || d.error){ toast("保存失败："+(d.error||"HTTP "+r.status), "err"); return; }
    await loadTemplates(); $("tplEditor").style.display="none"; toast("模板已保存");
  } catch(e){ toast(fetchErr(e,"保存模板"), "err"); }
}
async function delTemplate(){
  if(!window.__editTid) return;
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("删除模板", "删除该模板？", async ()=>{
    templates=templates.filter(x=>x.id!==window.__editTid);
    try {
      const r = await fetch("/api/templates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(templates)});
      const d = await r.json(); if (!r.ok || d.error){ toast("删除失败："+(d.error||"HTTP "+r.status), "err"); return; }
      await loadTemplates(); $("tplEditor").style.display="none"; toast("已删除");
    } catch(e){ toast(fetchErr(e,"删除模板"), "err"); }
  }, "删除");
}
function openRunPanel(){
  const t=templates.find(x=>x.id===window.__editTid); if(!t){ toast("先保存模板","warn"); return; }
  $("runTplName").textContent=t.name;
  const box=$("runInputs"); box.innerHTML="";
  (t.input_fields||[]).forEach(f=>{
    const el=document.createElement("div"); el.className="field";
    const inp = f.type==="textarea"?`<textarea class="rf" data-k="${escapeHtml(f.key)}" placeholder="${escapeHtml(f.label)}"></textarea>`
      :`<input class="rf" data-k="${escapeHtml(f.key)}" placeholder="${escapeHtml(f.label)}"/>`;
    el.innerHTML=`<label>${escapeHtml(f.label||f.key)}</label>${inp}`; box.appendChild(el);
  });
  $("runResult").innerHTML=""; $("runPanel").style.display="block";
}
let __runCtrl=null, __runTimer=null;
function _runUI(running){ const b=$("runBtn"), s=$("runStopBtn"); if(b) b.disabled=running; if(s) s.style.display=running?"":"none"; }
function stopTemplate(){ if(__runCtrl){ clearInterval(__runTimer); __runCtrl.abort(); _runUI(false); $("runResult").innerHTML='<div class="msg notice">已手动停止</div>'; } }
async function runTemplate(){
  const t=templates.find(x=>x.id===window.__editTid); if(!t) return;
  const inputs={}; document.querySelectorAll("#runInputs .rf").forEach(el=>{ inputs[el.dataset.k]=el.value; });
  __runCtrl && __runCtrl.abort(); __runCtrl=new AbortController();
  let r;
  try{
    r=await fetch("/api/template/run",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({template_id:t.id, inputs}), signal:__runCtrl.signal});
  }catch(e){
    if(e && e.name==="AbortError") return;
    $("runResult").innerHTML=`<div class="msg err">${escapeHtml(fetchErr(e,"运行模板"))}</div>`; return;
  }
  if(!r.ok || !r.body){
    $("runResult").innerHTML=`<div class="msg err">${escapeHtml(r.status===400?"模板参数不合法或运行失败（HTTP 400）":"模板运行失败（HTTP "+r.status+"）")}</div>`; return;
  }
  _runUI(true); $("runResult").innerHTML='<div class="msg agent typing">生成中</div>';
  const reader=r.body.getReader(); let result=null;
  // 看门狗：120s 无任何 SSE 数据则中止请求，防止后端卡死时前端永久转圈（P2-6，本地模型慢已放宽）
  let lastData=Date.now();
  __runTimer=setInterval(()=>{
    if(Date.now()-lastData>120000){ clearInterval(__runTimer); __runCtrl.abort(); _runUI(false);
      $("runResult").innerHTML='<div class="msg notice">模板运行超时（120s 无响应），已自动停止。可重试或检查后端日志。</div>'; }
  },3000);
  try{
    await consumeSSE(reader, (j)=>{
      if(j.type==="token"){ $("runResult").innerHTML=`<pre class="json">${escapeHtml(j.text)}</pre>`; }
      else if(j.type==="result"){ result=j; }
      else if(j.type==="error"){ $("runResult").innerHTML=`<div class="msg err">${escapeHtml(j.text)}</div>`; }
    }, ()=>{ lastData=Date.now(); });
  }catch(e){
    if(e && e.name==="AbortError"){ /* 已由看门狗/手动停止处理 */ }
    else $("runResult").innerHTML=`<div class="msg err">${escapeHtml(e.message||"读取响应失败")}</div>`;
  }finally{ clearInterval(__runTimer); _runUI(false); }
  if(result){
    const ok=result.ok;
    const html=`<div class="kv"><b>状态</b><span class="pill" style="background:${ok?'var(--brand-ink)':'var(--err)'}">${ok?'校验通过':'未通过'}</span> ${result.warn?('⚠️ '+escapeHtml(result.warn)):''}</div>`+
      `<pre class="json">${escapeHtml(JSON.stringify(result.data,null,2))}</pre>`+
      `<div class="row"><button class="ghost" onclick="copyResult()">复制 JSON</button><button class="ghost" onclick="sendResultToWecom()">发到企业微信群</button></div>`;
    $("runResult").innerHTML=html;
    window.__lastResult=result.data;
    // v1.0.366（P2）：使用计数（常用模板在选择器中排前）
    postJson("/api/templates/use",{id:window.__editTid}).catch(()=>{});
  }
}

// ---- v1.0.366 模板快速选择器（工作台「📋 模板」入口：选择 → 填空 → 运行 → 结果就地展示） ----
async function openTplPicker(){
  $("tplPickerOverlay").classList.add("show");
  $("tplPickerSearch").value="";
  $("tplPickerSearch").style.display="";
  const hint=$("tplPickerHint"); if(hint) hint.style.display="";
  await _tplPickerLoad();
}
async function _tplPickerLoad(){
  try{ const _tl = await getJson("/api/templates"); templates = Array.isArray(_tl) ? _tl : []; }catch(e){ templates=[]; }
  renderTplPicker();
}
function renderTplPicker(){
  const box=$("tplPickerList"); if(!box) return;
  const q=($("tplPickerSearch").value||"").trim().toLowerCase();
  const vs=(templates||[]).filter(t=>!q || (t.name||"").toLowerCase().includes(q) || (t.description||"").toLowerCase().includes(q));
  box.innerHTML=vs.length? vs.map(t=>
    `<div class="win" style="cursor:pointer" onclick="openTplRun('${jsStr(t.id)}')">`+
      `<div class="t">${escapeHtml(t.name)}${t.builtin?' <span class="pill" style="background:var(--accent-soft);color:var(--accent-fg)">内置</span>':''}${t.used?' <span class="pill">已用 '+t.used+'</span>':''}</div>`+
      `<div class="u">${escapeHtml(t.description||'')}</div></div>`).join("")
    : '<div class="sub">没有匹配的模板（可到「📋 模板」页创建，或用 🤖 AI 生成）</div>';
}
function openTplRun(id){
  const t=templates.find(x=>x.id===id); if(!t) return;
  // v1.0.368 修复"点运行没反应"：模板管理页「▶ 运行」按钮与弹层卡片共用本函数，
  // 但本函数只改弹层内部 DOM——从管理页进入时弹层未打开，改动全部落在隐藏节点上，
  // 界面毫无反应。现显式确保弹层可见，并隐藏列表态的搜索框/提示行（填空运行态不需要）。
  $("tplPickerOverlay").classList.add("show");
  const hint=$("tplPickerHint"); if(hint) hint.style.display="none";
  $("tplPickerSearch").style.display="none";
  const box=$("tplPickerList");
  box.innerHTML=`<div class="t" style="font-weight:600;margin-bottom:8px">${escapeHtml(t.name)}</div>`+
    (t.input_fields||[]).map(f=>
      `<div class="field"><label>${escapeHtml(f.label||f.key)}</label>`+
      (f.type==="textarea"?`<textarea class="pk" data-k="${escapeHtml(f.key)}" rows="4" placeholder="${escapeHtml(f.label)}"></textarea>`
        :`<input class="pk" data-k="${escapeHtml(f.key)}" placeholder="${escapeHtml(f.label)}"/>`)+`</div>`).join("")+
    // v1.0.742（界面审查 P2-1）：▶ → 统一 SVG 线稿（与模板卡片运行钮同口径）
    `<div class="row" style="margin-top:10px"><button class="primary" id="tplPkRunBtn" onclick="runTplPicker('${jsStr(t.id)}')">${(typeof icon==="function")?icon("play"):""} 运行</button>`+
    `<button class="ghost" onclick="openTplPicker()">← 返回列表</button></div><div id="tplPkResult" style="margin-top:10px"></div>`;
}
async function runTplPicker(id){
  const t=templates.find(x=>x.id===id); if(!t) return;
  const inputs={}; document.querySelectorAll("#tplPickerList .pk").forEach(el=>{ inputs[el.dataset.k]=el.value; });
  const resBox=$("tplPkResult"); const btn=$("tplPkRunBtn");
  if(btn) btn.disabled=true;
  resBox.innerHTML='<div class="msg agent typing">生成中</div>';
  let r; const ctrl=new AbortController(); const timer=setTimeout(()=>ctrl.abort(),180000);
  try{
    r=await fetch("/api/template/run",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({template_id:id,inputs}),signal:ctrl.signal});
  }catch(e){
    clearTimeout(timer); if(btn) btn.disabled=false;
    resBox.innerHTML=`<div class="msg err">${escapeHtml(fetchErr(e,"运行模板"))}</div>`; return;
  }
  if(!r.ok || !r.body){
    clearTimeout(timer); if(btn) btn.disabled=false;
    resBox.innerHTML=`<div class="msg err">${escapeHtml(r.status===400?"模板参数不合法或运行失败":"模板运行失败（HTTP "+r.status+"）")}</div>`; return;
  }
  let result=null;
  try{
    await consumeSSE(r.body.getReader(), (j)=>{
      if(j.type==="token"){ resBox.innerHTML=`<pre class="json">${escapeHtml(j.text)}</pre>`; }
      else if(j.type==="result"){ result=j; }
      else if(j.type==="error"){ resBox.innerHTML=`<div class="msg err">${escapeHtml(j.text)}</div>`; }
    }, ()=>{});
  }catch(e){
    if(!(e && e.name==="AbortError")) resBox.innerHTML=`<div class="msg err">${escapeHtml(e.message||"读取响应失败")}</div>`;
  }finally{ clearTimeout(timer); if(btn) btn.disabled=false; }
  if(result){
    // v1.0.368：ok=false（结构化校验未通过）也要给出明确状态——此前 if(result && result.ok)
    // 导致未通过时结果区静默停留在原始输出，与老运行面板（红/绿 pill 均展示）不一致
    const ok=result.ok;
    window.__lastResult=result.data;
    if(ok) postJson("/api/templates/use",{id}).catch(()=>{});
    resBox.innerHTML=`<div class="kv"><b>状态</b><span class="pill" style="background:${ok?'var(--brand-ink)':'var(--err)'}">${ok?(result.warn?('⚠️ '+escapeHtml(result.warn)):'校验通过'):'校验未通过'}</span>`+
      `${(result.warn&&!ok)?' <span style="color:var(--err-fg)">'+escapeHtml(result.warn)+'</span>':''}</div>`+
      `<pre class="json">${escapeHtml(JSON.stringify(result.data,null,2))}</pre>`+
      `<div class="row"><button class="ghost" onclick="copyResult()">复制 JSON</button><button class="ghost" onclick="sendResultToWecom()">发到企业微信群</button>`+
      `<button class="ghost" onclick="openTplPicker()">← 返回模板列表</button></div>`;
  }
}
// v1.0.660（审查修复 V2）：原实现 `navigator.clipboard.writeText(...)` 不 await 就
// toast「已复制」——权限被拒（WebView2 非安全上下文/未授权）时 promise reject，
// 界面照样显示"已复制"，用户复制出来却是空的（提示与事实相反）。
// 改为 await + try/catch：失败才提示失败。WebView2 里 clipboard 可能仍需用户授权，
// 此时后端没有 clipboard HTTP 接口可兜底，只能给正确的失败提示（手动选择复制）。
async function copyResult(){
  if(!window.__lastResult){ toast("没有可复制的结果","err"); return; }
  const txt = JSON.stringify(window.__lastResult,null,2);
  try{
    if(!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("clipboard unavailable");
    await navigator.clipboard.writeText(txt);
    toast("已复制","ok");
  }catch(e){
    toast("复制失败，请手动选择复制","err");
  }
}
// v1.0.660（审查修复 F6）：原来直接 fetch 无 catch —— 网络失败是**未处理的 promise
// rejection**，用户点了没反应也不知道为什么。改用项目已有的统一封装 apiToast
// （01-core.js）：失败自动 toast 人话化错误，成功返回解析后的 JSON。
async function sendResultToWecom(){
  if(!window.__lastResult){ toast("没有可发送的结果","err"); return; }
  const content="```json\n"+JSON.stringify(window.__lastResult,null,2)+"\n```";
  try{
    const d = await apiToast("/api/wecom/send",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({content,msgtype:"markdown"})}, "发送到企业微信群");
    if(d && d.errcode!==undefined && d.errcode===0) toast("已发送 ✅","ok");
    else toast(JSON.stringify(d).slice(0,120),"info");
  }catch(e){ /* apiToast 已给出失败提示，这里只吞掉重抛，避免未处理 rejection */ }
}

// ---- 技能专项优化（查看/修改/agent 优化/确认落盘） ----
let __skillDraft = null;
let __allToolNames = [];

function closeSkillEditor(){ $("skillEditorOverlay").classList.remove("show"); __skillDraft=null; }
async function openSkillEditor(sid){
  try{
    const d = await (await fetch("/api/skills/"+encodeURIComponent(sid))).json();
    // 兼容判断：后端 api_skill_detail 返回 {ok, id, base, current,...}；同时兜底 d.base 缺失
    if(!d || !d.base){ toast((d && d.error) || "加载技能失败","err"); return; }
    const cur=d.current||{};
    // v1.0.303：custom 标记决定编辑器内保存路由（自定义→/skills/custom PUT，内置→override）
    __skillDraft={ sid, base:d.base||{}, current:cur, overridden:!!d.overridden, custom:!!d.custom, isNew:false };
    __allToolNames=d.tool_names||[];
    $("skillEditorTitle").textContent = d.custom ? "🧩 自定义技能管理" : "🧩 技能专项优化";
    $("se_id").value=sid;
    $("se_name").value=cur.name||"";
    $("se_icon").value=cur.icon||"";
    $("se_desc").value=cur.desc||"";
    $("se_prompt").value=cur.prompt||"";
    $("se_req").value="";
    renderSkillTools(cur.tools||[]);
    $("seBadge").innerHTML = d.custom
      ? '<span class="pill ok">自定义技能</span>'
      : (d.overridden
          ? '<span class="pill wrn">已优化（自定义）</span>'
          : '<span class="pill">内置定义</span>');
    // 内置显示「恢复内置定义」；自定义显示「删除该技能」
    $("seResetBtn").style.display = d.custom ? "none" : "";
    $("seDeleteBtn").style.display = d.custom ? "" : "none";
    $("skillEditorOverlay").classList.add("show");
  }catch(e){ toast(fetchErr(e,"加载技能"),"err"); }
}
function renderSkillTools(selected){
  const box=$("se_tools"); if(!box) return;
  box.innerHTML="";
  const names = (__allToolNames && __allToolNames.length) ? __allToolNames : (selected||[]);
  const row=document.createElement("div"); row.className="row";
  names.forEach(t=>{
    const b=document.createElement("button"); b.type="button";
    b.className="opt-chip"+( (selected||[]).indexOf(t)>=0 ? " sel" : "");
    b.textContent=t; b.dataset.tool=t; b.title=t;
    b.onclick=()=>b.classList.toggle("sel");
    row.appendChild(b);
  });
  box.appendChild(row);
}
function collectSkillEdit(){
  const tools=[]; document.querySelectorAll("#se_tools .opt-chip.sel").forEach(b=>tools.push(b.dataset.tool));
  return { name:$("se_name").value.trim()||"未命名技能", icon:$("se_icon").value.trim(),
    desc:$("se_desc").value.trim(), prompt:$("se_prompt").value, tools };
}
function _skillDiff(){
  const base=__skillDraft.base||{};
  const ed=collectSkillEdit();
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const out=[];
  if(!same(base.name,ed.name)) out.push({label:"名称", old:base.name||"", now:ed.name});
  if(!same(base.icon,ed.icon)) out.push({label:"图标", old:base.icon||"", now:ed.icon});
  if(!same(base.desc,ed.desc)) out.push({label:"描述", old:base.desc||"", now:ed.desc});
  if(!same(base.prompt,ed.prompt)) out.push({label:"提示词", old:base.prompt||"", now:ed.prompt});
  if(!same((base.tools||[]).slice().sort(), ed.tools.slice().sort()))
    out.push({label:"使用工具", old:(base.tools||[]).join(", ")||"（无）", now:ed.tools.join(", ")||"（无）"});
  return out;
}
function saveSkillEditor(){
  if(!__skillDraft) return;
  const ed=collectSkillEdit();
  if(!ed.name.trim()){ toast("技能名称不能为空","err"); return; }
  // v1.0.303：新建自定义技能无「改前」状态，跳过 diff 确认直接落盘
  if(__skillDraft.isNew){ doCreateCustomSkill(ed); return; }
  const diff=_skillDiff();
  $("confirmTitle").textContent="确认保存技能修改";
  $("confirmIntro").innerHTML=`技能 <b>${escapeHtml(__skillDraft.sid)}</b>（${escapeHtml(ed.name)}）改动要点 ${diff.length} 项，点击各项可展开查看前后对比：`;
  const body=$("confirmBody"); body.innerHTML="";
  if(!diff.length){
    body.innerHTML='<div class="sub" style="margin:6px 0">未检测到任何改动，无需保存。</div>';
    $("confirmOkBtn").disabled=true;
  }else{
    $("confirmOkBtn").disabled=false;
    diff.forEach(f=>{
      const det=document.createElement("details"); det.className="diff-block";
      const sum=document.createElement("summary");
      sum.textContent=`${f.label}：改前「${f.old}」 → 改后「${f.now}」`;
      const db=document.createElement("div"); db.className="diff-body";
      db.innerHTML=`<div class="diff-line del"><span class="dg">-</span>${escapeHtml(f.old)||"&nbsp;"}</div>`+
        `<div class="diff-line add"><span class="dg">+</span>${escapeHtml(f.now)||"&nbsp;"}</div>`;
      det.appendChild(sum); det.appendChild(db); body.appendChild(det);
    });
  }
  window.__confirmAction = ()=>doSaveSkill(ed);
  $("confirmOverlay").classList.add("show");
}
async function doSaveSkill(ed){
  const btn=$("confirmOkBtn"); setBtnLoading(btn,true);
  try{
    // v1.0.303：自定义技能走 /skills/custom PUT（编辑存储定义），内置技能走 override（覆盖内置）
    const dr=__skillDraft;
    const url = dr.custom
      ? "/api/skills/custom/"+encodeURIComponent(dr.sid)
      : "/api/skills/"+encodeURIComponent(dr.sid)+"/override";
    await api(url,
      {method:dr.custom?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(ed)});
    toast("技能已保存，运行时会立即生效","ok");
    closeConfirm(); closeSkillEditor(); loadSkills();
  }catch(e){ toast(fetchErr(e,"保存技能"),"err"); closeConfirm(); }
  finally{ setBtnLoading(btn,false); }
}
// ---- 自定义技能（v1.0.303）：新建 / 删除（复用编辑弹层） ----
function createCustomSkill(){
  __skillDraft={ sid:"", base:{}, current:{}, overridden:false, custom:true, isNew:true };
  $("skillEditorTitle").textContent="🧩 新建自定义技能";
  $("se_id").value="（保存时自动生成）";
  $("se_name").value=""; $("se_icon").value="🧩"; $("se_desc").value="";
  $("se_prompt").value=""; $("se_req").value="";
  renderSkillTools([]);
  $("seBadge").innerHTML='<span class="pill ok">新建自定义技能</span>';
  $("seResetBtn").style.display="none";
  $("seDeleteBtn").style.display="none";
  $("skillEditorOverlay").classList.add("show");
}
async function doCreateCustomSkill(ed){
  try{
    await apiToast("/api/skills/custom",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(ed)},"创建技能");
    toast("自定义技能已创建，运行时会立即生效","ok");
    closeSkillEditor(); loadSkills();
  }catch(_){}
}
async function deleteCustomSkill(sid){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("删除自定义技能", "删除该自定义技能？其提示词与工具绑定将一并移除。", async ()=>{
    try{
      await apiToast("/api/skills/custom/"+encodeURIComponent(sid),{method:"DELETE"},"删除技能");
      toast("自定义技能已删除","ok"); loadSkills();
    }catch(_){}
  }, "删除");
}
async function optimizeSkill(){
  if(!__skillDraft) return;
  const req=$("se_req").value.trim();
  if(!req){ toast("请先填写优化诉求","warn"); return; }
  const btn=document.querySelector('#skillEditorOverlay .primary');
  setBtnLoading(btn,true);
  try{
    const d=await apiToast("/api/skills/"+encodeURIComponent(__skillDraft.sid)+"/optimize",
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({request:req})},"Agent 优化");
    const s=d.skill||{};
    $("se_name").value=s.name||""; $("se_icon").value=s.icon||"";
    $("se_desc").value=s.desc||""; $("se_prompt").value=s.prompt||"";
    renderSkillTools(s.tools||[]);
    toast("已生成优化建议，请检查后点击「保存修改」","ok");
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
async function resetSkillOverride(){
  if(!__skillDraft) return;
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("恢复内置定义", "恢复该技能的内置定义？当前自定义覆盖将被清除。", async ()=>{
    try{
      await apiToast("/api/skills/"+encodeURIComponent(__skillDraft.sid)+"/override",{method:"DELETE"},"恢复内置定义");
      toast("已恢复内置定义","ok"); closeSkillEditor(); loadSkills();
    }catch(_){}
  }, "恢复");
}
// v1.0.373 修复：确认后必须关闭弹层（原只执行动作不关，批量删除等场景弹窗残留）
function confirmOk(){ if(window.__confirmAction){ const fn=window.__confirmAction; window.__confirmAction=null; closeConfirm(); fn(); } }
function closeConfirm(){ $("confirmOverlay").classList.remove("show"); window.__confirmAction=null; }
// v1.0.373（体验4）：统一破坏性确认——替换各处原生 confirm()（阻断式弹窗与整体风格割裂）
// 用法：confirmAsk("标题", "说明文字", ()=>{...确认后执行...})
function confirmAsk(title, text, onOk, okLabel){
  const t=$("confirmTitle"); if(!t){ if(onOk) onOk(); return; }   // 弹层缺失时兜底直接执行
  t.textContent = title || "确认操作";
  $("confirmIntro").innerHTML = escapeHtml(text || "");
  $("confirmBody").innerHTML = "";
  const ok=$("confirmOkBtn");
  if(ok){ ok.disabled=false; ok.textContent = okLabel || "确认保存"; }
  window.__confirmAction = onOk;
  $("confirmOverlay").classList.add("show");
}
// v1.0.471（UI#6）：输入式确认弹层（替代原生 prompt）——复用 confirmOverlay，
// 在 confirmBody 放输入框，确认时回调带回输入值（取消/空值不回调）
function promptAsk(title, label, defaultVal, onOk, okLabel){
  const t=$("confirmTitle");
  if(!t){ const v=prompt(label||title, defaultVal||""); if(v && v.trim() && onOk) onOk(v.trim()); return; }
  t.textContent = title || "输入";
  $("confirmIntro").innerHTML = escapeHtml(label || "");
  $("confirmBody").innerHTML = '<input id="__pa_inp" style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--r-md);background:var(--panel2);color:var(--ink);font-size:var(--fs-md)" />';
  const inp=$("__pa_inp"); inp.value = defaultVal || "";
  const ok=$("confirmOkBtn");
  if(ok){ ok.disabled=false; ok.textContent = okLabel || "确定"; }
  window.__confirmAction = ()=>{ const v=(inp.value||"").trim(); if(v) onOk(v); };
  $("confirmOverlay").classList.add("show");
  setTimeout(()=>{ const i=$("__pa_inp"); if(i){ i.focus(); i.select(); } }, 60);
}

// ---- 模板 AI 生成（agent 聊天式需求确认 + 可点击选项） ----
let __tplAI = {msgs:[]};
let __tplAICtrl=null, __tplAIRunning=false, __tplAIResult=null;
// 状态桥：旧全局名保持唯一存储（不改名），公共骨架经取值器读写（与 02 的 quickAI 同构）
const __tplS = {
  get msgs(){ return __tplAI.msgs; },
  get ctrl(){ return __tplAICtrl; }, set ctrl(v){ __tplAICtrl = v; },
  get running(){ return __tplAIRunning; }, set running(v){ __tplAIRunning = v; },
  get result(){ return __tplAIResult; }, set result(v){ __tplAIResult = v; },
};
// 聊天窗骨架收敛到 makeDesignAssistant（01-core）；结果渲染 renderTplAIResult 经 onResult 注入
const __tplAsst = makeDesignAssistant({
  S: __tplS, chatId: "tplaiChat", inputId: "tplaiInput", sendBtnId: "tplaiSendBtn", modelSelId: "tplaiModel",
  endpoint: "/api/template/agent/chat", errLabel: "模板 AI 生成", emptyHint: "请先描述模板需求",
  extract: (j)=>j.template||null, onResult: (t)=>renderTplAIResult(t),
});

function closeTplAI(){ $("tplAIOverlay").classList.remove("show"); if(__tplAICtrl) __tplAICtrl.abort(); __tplAIRunning=false; }
function fillTplAIModel(){
  const sel=$("tplaiModel"); if(!sel) return;
  const cur=sel.value;
  fillModelSelect(sel, models);
  if(cur) sel.value=cur;
}
function openTplAI(){
  __tplAI={msgs:[]}; __tplAICtrl=null; __tplAIRunning=false; __tplAIResult=null;
  const chat=$("tplaiChat"); if(chat) chat.innerHTML="";
  addTplAIMsg("agent","你好，我是模板设计助手。用一句话描述你要生成的模板，例如：\n「做一个会议纪要模板：输入会议记录，输出要点 JSON」\n我会与你确认几个关键选项（输出格式、JSON 结构、模型、是否附目标窗口上下文、输入字段等），确认后直接生成完整模板，可一键保存或回填编辑器微调。");
  fillTplAIModel();
  if(!(models||[]).length){ loadModels().then(fillTplAIModel).catch(()=>{}); }
  $("tplaiInput").value="";
  $("tplAIOverlay").classList.add("show");
  setTimeout(()=>{ const i=$("tplaiInput"); if(i) i.focus(); },60);
}
// addTplAIMsg / tplAISend 是同套交互的薄封装（骨架已收敛到 makeDesignAssistant，01-core），保留旧函数名。
function addTplAIMsg(role, text){ return __tplAsst.addMsg(role, text); }
function renderTplAIResult(t){
  if(!t) return;
  const chat=$("tplaiChat"); if(!chat) return;
  const el=document.createElement("div"); el.className="opt-block";
  el.innerHTML=`<div class="ob-t" style="color:var(--brand-fg)">✅ 模板已生成：「${escapeHtml(t.name||"未命名模板")}」</div>`+
    (t.description?`<div class="ob-d">${escapeHtml(t.description)}</div>`:"")+
    `<pre style="background:#0f1419;color:#d6e7ff;padding:8px 10px;border-radius:var(--r-md);font-size:var(--fs-xs);max-height:200px;overflow:auto;white-space:pre-wrap;margin:6px 0">${escapeHtml(JSON.stringify({name:t.name,description:t.description||"",model_id:t.model_id||"",use_wecom_context:!!t.use_wecom_context,system_prompt:t.system_prompt||"",input_fields:t.input_fields||[],output_schema:t.output_schema||null},null,2))}</pre>`+
    `<div class="res-actions"><button class="primary" onclick="saveAITemplate()">保存到模板列表</button>`+
    `<button class="ghost" onclick="fillAITemplateToEditor()">回填模板编辑器微调</button></div>`;
  chat.appendChild(el); chat.scrollTop=chat.scrollHeight;
}
function tplAISend(){ return __tplAsst.send(); }
async function saveAITemplate(){
  const t=__tplAIResult; if(!t) return;
  const rec={ id:"t_"+Date.now().toString(36), name:t.name||"AI 生成模板", description:t.description||"",
    model_id:t.model_id||"", use_wecom_context:!!t.use_wecom_context, system_prompt:t.system_prompt||"",
    input_fields:Array.isArray(t.input_fields)?t.input_fields:[], output_schema:t.output_schema||{type:"object",properties:{}} };
  templates.push(rec);
  try{
    const r=await fetch("/api/templates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(templates)});
    const d=await r.json(); if(!r.ok || d.error){ toast("保存失败："+(d.error||"HTTP "+r.status),"err"); return; }
    templates=d; await loadTemplates(); toast("模板已保存到列表","ok");
  }catch(e){ toast(fetchErr(e,"保存模板"),"err"); }
}
function fillAITemplateToEditor(){
  const t=__tplAIResult; if(!t) return;
  window.__editTid=null;
  $("tplEditorTitle").textContent="新建模板（AI 生成）";
  loadModels();
  $("t_name").value=t.name||""; $("t_desc").value=t.description||""; $("t_prompt").value=t.system_prompt||"";
  $("t_ctx").checked=!!t.use_wecom_context; $("t_model").value=t.model_id||"";
  $("t_inputs").innerHTML=""; (t.input_fields||[]).forEach(f=>addInputField(f.key,f.label,f.type));
  $("t_schema").value=JSON.stringify(t.output_schema||{type:"object",properties:{}},null,2);
  $("tplEditor").style.display="block"; $("runPanel").style.display="none";
  closeTplAI(); openView("templates");
  toast("已回填模板编辑器，可微调后保存","ok");
}

// ---- 通用消息渲染 ----
// 轻量 markdown：标题 / 有序·无序·任务列表 / 表格 / 引用 / 代码块 / 分隔线 / 加粗 / 斜体 / 删除线 / 链接
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {
  __allToolNames, __runCtrl, __skillDraft, __tplAI, __tplAICtrl, __tplAsst,
  __tplS, _runUI, _skillDiff, _tplPickerLoad, addInputField, addTplAIMsg,
  closeConfirm, closeSkillEditor, closeTplAI, collectSkillEdit, collectTemplate, confirmAsk,
  confirmOk, copyResult, createCustomSkill, delTemplate, delTemplateById, deleteCustomSkill,
  doCreateCustomSkill, doSaveSkill, fillAITemplateToEditor, fillTplAIModel, loadTemplates, newTemplate,
  openRunPanel, openSkillEditor, openTemplateEditor, openTplAI, openTplPicker, openTplRun,
  optimizeSkill, promptAsk, renderSkillTools, renderTplAIResult, renderTplPicker, resetSkillOverride,
  runTemplate, runTplPicker, saveAITemplate, saveSkillEditor, saveTemplate, sendResultToWecom,
  stopTemplate, tplAISend
};
Object.defineProperties(window, {
  __allToolNames: { configurable: true, enumerable: true, get(){ return __allToolNames; }, set(v){ __allToolNames = v; } },
  __runCtrl: { configurable: true, enumerable: true, get(){ return __runCtrl; }, set(v){ __runCtrl = v; } },
  __skillDraft: { configurable: true, enumerable: true, get(){ return __skillDraft; }, set(v){ __skillDraft = v; } },
  __tplAI: { configurable: true, enumerable: true, get(){ return __tplAI; }, set(v){ __tplAI = v; } },
  __tplAICtrl: { configurable: true, enumerable: true, get(){ return __tplAICtrl; }, set(v){ __tplAICtrl = v; } },
  __tplAsst: { configurable: true, enumerable: true, get(){ return __tplAsst; }, set(v){ __tplAsst = v; } },
  __tplS: { configurable: true, enumerable: true, get(){ return __tplS; }, set(v){ __tplS = v; } },
  _runUI: { configurable: true, enumerable: true, get(){ return _runUI; }, set(v){ _runUI = v; } },
  _skillDiff: { configurable: true, enumerable: true, get(){ return _skillDiff; }, set(v){ _skillDiff = v; } },
  _tplPickerLoad: { configurable: true, enumerable: true, get(){ return _tplPickerLoad; }, set(v){ _tplPickerLoad = v; } },
  addInputField: { configurable: true, enumerable: true, get(){ return addInputField; }, set(v){ addInputField = v; } },
  addTplAIMsg: { configurable: true, enumerable: true, get(){ return addTplAIMsg; }, set(v){ addTplAIMsg = v; } },
  closeConfirm: { configurable: true, enumerable: true, get(){ return closeConfirm; }, set(v){ closeConfirm = v; } },
  closeSkillEditor: { configurable: true, enumerable: true, get(){ return closeSkillEditor; }, set(v){ closeSkillEditor = v; } },
  closeTplAI: { configurable: true, enumerable: true, get(){ return closeTplAI; }, set(v){ closeTplAI = v; } },
  collectSkillEdit: { configurable: true, enumerable: true, get(){ return collectSkillEdit; }, set(v){ collectSkillEdit = v; } },
  collectTemplate: { configurable: true, enumerable: true, get(){ return collectTemplate; }, set(v){ collectTemplate = v; } },
  confirmAsk: { configurable: true, enumerable: true, get(){ return confirmAsk; }, set(v){ confirmAsk = v; } },
  confirmOk: { configurable: true, enumerable: true, get(){ return confirmOk; }, set(v){ confirmOk = v; } },
  copyResult: { configurable: true, enumerable: true, get(){ return copyResult; }, set(v){ copyResult = v; } },
  createCustomSkill: { configurable: true, enumerable: true, get(){ return createCustomSkill; }, set(v){ createCustomSkill = v; } },
  delTemplate: { configurable: true, enumerable: true, get(){ return delTemplate; }, set(v){ delTemplate = v; } },
  delTemplateById: { configurable: true, enumerable: true, get(){ return delTemplateById; }, set(v){ delTemplateById = v; } },
  deleteCustomSkill: { configurable: true, enumerable: true, get(){ return deleteCustomSkill; }, set(v){ deleteCustomSkill = v; } },
  doCreateCustomSkill: { configurable: true, enumerable: true, get(){ return doCreateCustomSkill; }, set(v){ doCreateCustomSkill = v; } },
  doSaveSkill: { configurable: true, enumerable: true, get(){ return doSaveSkill; }, set(v){ doSaveSkill = v; } },
  fillAITemplateToEditor: { configurable: true, enumerable: true, get(){ return fillAITemplateToEditor; }, set(v){ fillAITemplateToEditor = v; } },
  fillTplAIModel: { configurable: true, enumerable: true, get(){ return fillTplAIModel; }, set(v){ fillTplAIModel = v; } },
  loadTemplates: { configurable: true, enumerable: true, get(){ return loadTemplates; }, set(v){ loadTemplates = v; } },
  newTemplate: { configurable: true, enumerable: true, get(){ return newTemplate; }, set(v){ newTemplate = v; } },
  openRunPanel: { configurable: true, enumerable: true, get(){ return openRunPanel; }, set(v){ openRunPanel = v; } },
  openSkillEditor: { configurable: true, enumerable: true, get(){ return openSkillEditor; }, set(v){ openSkillEditor = v; } },
  openTemplateEditor: { configurable: true, enumerable: true, get(){ return openTemplateEditor; }, set(v){ openTemplateEditor = v; } },
  openTplAI: { configurable: true, enumerable: true, get(){ return openTplAI; }, set(v){ openTplAI = v; } },
  openTplPicker: { configurable: true, enumerable: true, get(){ return openTplPicker; }, set(v){ openTplPicker = v; } },
  openTplRun: { configurable: true, enumerable: true, get(){ return openTplRun; }, set(v){ openTplRun = v; } },
  optimizeSkill: { configurable: true, enumerable: true, get(){ return optimizeSkill; }, set(v){ optimizeSkill = v; } },
  promptAsk: { configurable: true, enumerable: true, get(){ return promptAsk; }, set(v){ promptAsk = v; } },
  renderSkillTools: { configurable: true, enumerable: true, get(){ return renderSkillTools; }, set(v){ renderSkillTools = v; } },
  renderTplAIResult: { configurable: true, enumerable: true, get(){ return renderTplAIResult; }, set(v){ renderTplAIResult = v; } },
  renderTplPicker: { configurable: true, enumerable: true, get(){ return renderTplPicker; }, set(v){ renderTplPicker = v; } },
  resetSkillOverride: { configurable: true, enumerable: true, get(){ return resetSkillOverride; }, set(v){ resetSkillOverride = v; } },
  runTemplate: { configurable: true, enumerable: true, get(){ return runTemplate; }, set(v){ runTemplate = v; } },
  runTplPicker: { configurable: true, enumerable: true, get(){ return runTplPicker; }, set(v){ runTplPicker = v; } },
  saveAITemplate: { configurable: true, enumerable: true, get(){ return saveAITemplate; }, set(v){ saveAITemplate = v; } },
  saveSkillEditor: { configurable: true, enumerable: true, get(){ return saveSkillEditor; }, set(v){ saveSkillEditor = v; } },
  saveTemplate: { configurable: true, enumerable: true, get(){ return saveTemplate; }, set(v){ saveTemplate = v; } },
  sendResultToWecom: { configurable: true, enumerable: true, get(){ return sendResultToWecom; }, set(v){ sendResultToWecom = v; } },
  stopTemplate: { configurable: true, enumerable: true, get(){ return stopTemplate; }, set(v){ stopTemplate = v; } },
  tplAISend: { configurable: true, enumerable: true, get(){ return tplAISend; }, set(v){ tplAISend = v; } },
});
/* ===== v1.0.756 ESM BRIDGE END ===== */
