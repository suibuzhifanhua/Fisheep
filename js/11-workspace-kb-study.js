async function loadWorkspace(){
  const tree=$("wsTree"); showLoading(tree,"加载产物库中…");
  try{
    const [d,sr]=await Promise.all([
      getJson("/api/workspace"),
      getJson("/api/workspace/size").catch(()=>null)
    ]);
    $("wsRoot").textContent="保存位置："+d.root;
    // 产物库体积概览：总计 + 大头构成 + 大文件提示（复用后端 workspace_size_info）。
    // v1.0.614（审查 ㉜）：后端口径改成「整个产物库」后总计会与资源管理器一致（实测
    // 23.2 → 1109.4MB），所以必须同时讲清大头是 组件/ 运行环境——否则用户会以为
    // 一堆历史下载吃掉了磁盘，去删真正要留的产出。
    let sizeHtml="";
    if(sr && typeof sr.total_mb==="number"){
      const big=(sr.large_files||[]).slice(0,5);
      const rt=Number(sr.runtime_mb)||0;
      const tops=Object.keys(sr.dirs||{}).map(k=>({k:k,v:Number(sr.dirs[k])||0}))
        .sort((a,b)=>b.v-a.v).slice(0,4).map(o=>`${escapeHtml(o.k)} ${o.v.toFixed(1)}MB`).join(" · ");
      // 警示底/边/字全走令牌：原来硬编码 #d97706 + rgba(217,119,6,.08)，深色模式下
      // 会变成"深底上贴一块浅琥珀"，且对比度不受令牌契约管。
      sizeHtml=`<div style="margin:6px 2px 10px;padding:8px 10px;border:1px solid ${big.length?"var(--warn-bd)":"var(--border)"};border-radius:var(--r-md);background:${big.length?"var(--warn-bg)":"rgba(120,140,170,.05)"};font-size:var(--fs-sm);color:var(--muted)">`+
        `📦 产物库总计 <b>${sr.total_mb.toFixed(1)} MB</b>`+
        (rt>=1?` · 其中 <b>组件 ${rt.toFixed(1)} MB</b> 是运行环境（OCR/语音/便携 Node·Git），占盘大头但<b>别手动删</b>——删了下次用要重新下载`:"")+
        (tops?`<div class="dim" style="margin-top:3px">构成：${tops}${sr.cached?`（${escapeHtml(String(sr.stat_at||""))} 统计）`:""}</div>`:"")+
        (big.length?` · ⚠️ 含 ${sr.large_files.length} 个大文件（≥10MB）：`+big.map(b=>`<span title="${escapeHtml(b.path)}">${escapeHtml(String(b.path).split(/[\\/]/).pop())}(${b.size_mb}MB)</span>`).join("、")+(sr.large_files.length>5?" 等":"")+`，可在「下载/产出」中清理`:"")+
        `</div>`;
    }
    if(!d.tree || !d.tree.length){ showEmpty(tree,"agent 产生的文件会出现在这里",{ic:"folder",title:"产物库为空"}); return; }
    const html=d.tree.map(g=>{
      const files=g.files.length? g.files.map(f=>{
        const kb=(f.size/1024).toFixed(1);
        const enc=encodeURIComponent(f.name);
        // v1.0.361 审查修复（P17）：文件名来自下载/上传/agent 产出（页面可控数据），
        // 原实现未转义直接进 innerHTML+onclick（存储型 XSS）；enc 也不转义单引号可逃逸
        const safeName=escapeHtml(f.name);
        const encQ=enc.replace(/'/g,"%27");
        // v1.0.650（F5）：进度台账行尾补「↻ 开新一轮」——它是纯追加的，上一轮记过的条目
        // 会让这一轮的"已处理就跳过"判定喂错，而"开新一轮"此前只有 agent 工具一条路，
        // 用户在界面里看着满屏旧记录却没有任何出口。
        // v1.0.660（审查修复 F8）：g.sub 是后端给的目录名（动态），进内联 onclick 的
        // JS 字符串字面量位必须走 jsStr——目录名含引号/反斜杠就会跳出字面量。
        // encQ 已是 encodeURIComponent 结果（' 已换 %27），本身安全，不动。
        const roundBtn = f.name.endsWith("_进度.md")
          ? `<button class="ghost" onclick="newWsRound('${jsStr(g.sub)}','${encQ}')" title="把本轮记录改名归档（带日期，留在同一目录可查），本轮从空文件重新开始">↻ 开新一轮</button>`
          : "";
        // v1.0.783（F1）：行尾「🕘 历史」——覆盖前自动备份的版本列表（查看/一键还原）。
        // 只读段（台账/日志）没有覆盖备份来源，ro 传进后端 400 兜底 + 列表提示空。
        const histBtn = `<button class="ghost" onclick="wsHistory('${jsStr(g.sub)}','${encQ}',${g.readonly?1:0})" title="覆盖前自动留下的历史版本：查看 / 一键还原">🕘 历史</button>`;
        return `<div class="wrow"><span class="wname" onclick="viewWsFile('${jsStr(g.sub)}','${encQ}')">📄 ${safeName}</span>`
          + `<span class="wmeta">${kb}KB · ${f.mtime}</span>`
          + `<button class="ghost" onclick="openWsFile('${jsStr(g.sub)}','${encQ}')">打开</button>${histBtn}${roundBtn}</div>`;
      }).join("") : `<div class="wmeta" style="padding:4px 0">${g.readonly?"（此目录由软件自己写：台账/日志。可查看，agent 不会往里放文件）":"（此分类暂无文件——agent 产出对应类型文件后会自动归入此处）"}</div>`;
      return `<div class="wgroup"><h4>${escapeHtml(g.sub)}（${g.files.length}）${g.readonly?' <span class="dim">只读</span>':""}</h4>${files}</div>`;
    }).join("");
    $("wsTree").innerHTML=sizeHtml+html;
  }catch(e){ showEmpty(tree,"加载失败<br/>"+escapeHtml(e.message||"网络错误"),{ic:"alert",title:"加载失败"}); }
}
async function viewWsFile(sub, name){
  try{
    const d=await getJson("/api/workspace/file?sub="+encodeURIComponent(sub)+"&name="+name);
    $("wsFileName").textContent=d.name;
    $("wsFileContent").textContent=d.content;
    $("wsTree").style.display="none"; $("wsFileView").style.display="block";
  }catch(e){ toast(e.message||"读取失败","err"); }
}
function closeWsFile(){ $("wsTree").style.display="block"; $("wsFileView").style.display="none"; }
async function newWsRound(sub, encName){
  // v1.0.650（F5）：进度台账开新一轮（与 agent 工具 new_progress_round 同一后端函数）。
  const name = decodeURIComponent(encName);
  confirmAsk("开新一轮",
    "把「"+escapeHtml(name)+"」里已记的条目改名归档（同目录，带日期，随时可查），本轮从空文件重新开始。<br/>"
    + "用途：快捷入口会先读进度台账、把记过的条目当作“已处理”跳过——开新一轮就是告诉它“这些重来不算”。",
    async ()=>{
      try{
        const d=await postJson("/api/workspace/new-round",{sub:sub, name:name});
        toast(`已归档上一轮 ${d.items||0} 条 → ${d.archived}`,"ok",4200);
        loadWorkspace();
      }catch(e){ toast(e.message||"开新一轮失败","err"); }
    });
}
async function openWsFolder(){
  try{ await postJson("/api/workspace/open",{}); }catch(e){ toast(e.message||"打开失败","err"); }
}
async function openWsFile(sub, name){
  try{ await postJson("/api/workspace/open",{sub:sub, name:decodeURIComponent(name)}); }catch(e){ toast(e.message||"打开失败","err"); }
}

// ---- v1.0.783（F1）：产物历史版本（列表 / 查看 / 一键还原）----
// 版本 = save_workspace_file 覆盖已存在非空文件前自动留的备份（产物库/备份/<日期>/*_<文件名>）。
// 复用 wsFileView 面板两态：列表态（版本行 + 按钮）/ 查看态（版本内容 + 返回/还原按钮）。
// 安全口径与文件行一致：进 onclick 的字符串一律 jsStr，展示一律 escapeHtml。
async function wsHistory(sub, encName, ro){
  let d;
  try{ d=await getJson("/api/workspace/file-history?sub="+encodeURIComponent(sub)+"&name="+encName); }
  catch(e){ toast(e.message||"读取历史版本失败","err"); return; }
  const vs=d.versions||[];
  $("wsFileName").textContent="历史版本 · "+decodeURIComponent(encName);
  const box=$("wsFileContent");
  box.style.whiteSpace="normal";
  if(!vs.length){
    box.innerHTML='<span class="dim">暂无历史版本——该文件每次被覆盖前都会自动备份一个版本，出现版本后即可在此查看、还原。</span>';
  }else{
    box.innerHTML=vs.map((v,i)=>
      `<div style="display:flex;gap:8px;align-items:center;padding:2px 0">`
      +`<span>${i+1}. ${escapeHtml(v.mtime)} · ${(v.size/1024).toFixed(1)}KB</span>`
      +`<button class="ghost smbtn" onclick="wsHistoryView('${jsStr(sub)}','${jsStr(encName)}','${jsStr(v.view)}',${ro?1:0})">查看</button>`
      +(ro?'':`<button class="ghost smbtn" onclick="wsHistoryRestore('${jsStr(sub)}','${jsStr(encName)}','${jsStr(v.view)}')">还原此版本</button>`)
      +`</div>`).join("");
  }
  $("wsTree").style.display="none"; $("wsFileView").style.display="block";
}
async function wsHistoryView(sub, encName, view, ro){
  try{
    const d=await getJson("/api/workspace/file-history?sub="+encodeURIComponent(sub)+"&name="+encName+"&view="+encodeURIComponent(view));
    $("wsFileName").textContent="版本 "+view+" · "+decodeURIComponent(encName);
    const box=$("wsFileContent");
    box.style.whiteSpace="normal";
    box.innerHTML=
      `<div style="margin-bottom:6px"><button class="ghost smbtn" onclick="wsHistory('${jsStr(sub)}','${jsStr(encName)}',${ro?1:0})">← 返回版本列表</button>`
      +(ro?'':` <button class="ghost smbtn" onclick="wsHistoryRestore('${jsStr(sub)}','${jsStr(encName)}','${jsStr(view)}')">还原此版本</button>`)
      +`</div><div style="white-space:pre-wrap">${escapeHtml(d.content||"（空文件）")}</div>`;
    $("wsTree").style.display="none"; $("wsFileView").style.display="block";
  }catch(e){ toast(e.message||"读取版本失败","err"); }
}
function wsHistoryRestore(sub, encName, view){
  const name=decodeURIComponent(encName);
  confirmAsk("还原此版本",
    "把「"+escapeHtml(name)+"」的当前内容替换为 "+escapeHtml(view)+" 这一版？<br/>"
    +"当前版本会先自动备份——还原后仍可回到还原前的状态，可反复回退。",
    async ()=>{
      try{
        const d=await postJson("/api/workspace/file-restore",{sub:sub,name:name,view:view});
        toast("已还原："+(d.restored_from||"")+" → "+sub+"/"+name,"ok",4200);
        loadWorkspace();
      }catch(e){ toast(e.message||"还原失败","err"); }
    });
}

// ---- v1.0.478 修复：本月自动化月报统计（loadMonthlyStats 此前从未定义——
// 切 workspace 视图时同步 ReferenceError 连坐 loadMacroList，两卡片一起卡「加载中…」）----
async function loadMonthlyStats(){
  const box=$("monthlyStats"); if(!box) return;
  try{
    const d=await api("/api/audit/monthly_stats");
    const retry='<span class="sub" style="cursor:pointer;color:var(--accent-fg)" onclick="loadMonthlyStats()">🔄</span>';
    if(!d.audited){
      box.innerHTML='<b style="font-size:var(--fs-md)">'+((typeof icon==="function")?icon("chart"):"")+' 本月自动化月报</b>'
        +'<span class="sub">本月暂无自动审计记录——到「工作台」跑一次，或等定时任务触发</span>'+retry;
      return;
    }
    box.innerHTML='<b style="font-size:var(--fs-md)">'+((typeof icon==="function")?icon("chart"):"")+' 本月自动化月报</b>'
      +'<span class="tag def">'+escapeHtml(d.month)+'</span>'
      +'<span class="sub">'+d.runs+' 轮 · 审阅 <b>'+d.audited+'</b> 份：'
      +'<b style="color:var(--brand-fg)">✅ '+d.green+'</b> · '
      +'<b style="color:var(--err-fg)">❌ '+d.red+'</b>'
      +(d.other?' · ⏳ 待复检 '+d.other+' 份':'')+'</span>'
      // v1.0.650（F3）：结果能落地却不能发出去——明细在月报正文里，通道（群机器人）也在设置里，
      // 中间那一步以前只能手抄。现在点一下就推，且**只在用户点了才推**：不进定时任务、不看阈值。
      // v1.0.742（界面审查 P2-1）：📤 emoji → 统一 SVG 线稿（send）
      +(d.red>0?'<button class="ghost smbtn" onclick="pushRejectList()" title="把最近一轮判为不通过的机构清单发到群里（发前给你看内容；只发消息，不碰目标页面、不点任何审批按钮）">'+((typeof icon==="function")?icon("send"):"📤")+' 推不通过清单</button>':'')
      +(d.last_ts?'<span class="sub" style="margin-left:auto">最近：'+escapeHtml(d.last_ts)+'</span>':'')
      +retry;
  }catch(e){
    box.innerHTML='<b style="font-size:var(--fs-md)">'+((typeof icon==="function")?icon("chart"):"")+' 本月自动化月报</b>'
      +'<span class="sub" style="color:var(--err-fg)">加载失败：'+escapeHtml(e.message||"网络错误")+'</span>'
      +'<span class="sub" style="cursor:pointer;color:var(--accent-fg)" onclick="loadMonthlyStats()">重试</span>';
  }
}
async function pushRejectList(){
  // v1.0.650（F3）：先读预览 → 用户确认 → 才真发。发出去的是群消息，不可撤回，
  // 所以这一步没有"顺手自动推"的分支，也没有定时任务会调它。
  let d;
  try{ d = await (await fetch("/api/audit/reject_list")).json(); }
  catch(e){ toast(fetchErr(e,"读取不通过清单"),"err"); return; }
  if(!d.ok){ toast(d.error||"没读到不通过清单","err"); return; }
  if(!d.red){ toast("最近一轮没有判定为不通过的机构，无需推送","info"); return; }
  const preview = d.lines.slice(0,5).map(l=>l.replace(/^·\s*/,"")).join("；");
  confirmAsk("推送不通过清单到群",
    "最近一轮（"+(d.ts||"")+"，共审阅 "+d.total+" 份）有 "+d.red+" 家不通过：\n"
    + preview + (d.lines.length>5?"……等 "+d.red+" 家":"")
    + "\n\n将通过群机器人发出，群里所有人都看得到、不可撤回。确定发送？",
    async ()=>{
      toast("正在推送…","info",1500);
      try{
        const r = await fetch("/api/audit/push_rejects",{method:"POST",headers:{"Content-Type":"application/json"},
                                                       body:JSON.stringify({})});
        const j = await r.json();
        if(j.ok) toast(`已推送到群：${j.items} 家 / ${j.sent} 条消息`,"ok",4200);
        else toast(j.error||"推送失败","err",6000);
      }catch(e){ toast(fetchErr(e,"推送不通过清单"),"err"); }
    }, "确认推送");
}
// ---- v1.0.405（M6 死引用补齐）：loadMacroList / openWebUI ----
// 背景：index.html 的「🔄 刷新宏列表」按钮与 02-nav-quicklinks.js 切换 workspace
// 视图都在调 loadMacroList，但该函数从未落地——按钮点击即 ReferenceError，
// 工作台宏卡片永远停在「加载中…」。openWebUI 同理（后端端点也缺）。
async function loadMacroList(){
  const box=$("macroListBox"); if(!box) return;
  try{
    const d=await getJson("/api/macro/list");
    const items=(d && d.macros)||[];
    if(!items.length){ box.innerHTML='<div class="sub">暂无宏——成功完成任务后，agent 气泡底部「存为宏」可保存操作序列</div>'; return; }
    box.innerHTML=items.map((m,i)=>{
      // v1.0.660（审查修复 F8）：宏 id 进内联 onclick 的 JS 字符串字面量位，改走 jsStr。
      // 原写法 `replace(/'/g,"%27")` 是手工转义且**改变了值**——id 里带 ' 会被改成
      // %27，传给后端就找不到这条宏了。jsStr 只转义不改变解析后的值。
      const idQ=jsStr(String(m.id));
      // v1.0.666：宏列表图标素色化——🎬/▶/🗑 换 16-icons 线稿（icon() 由 16-icons.js 全局提供）
      const _mi=(n)=>(typeof icon==="function")?icon(n):"";
      return `<div class="wrow"><span class="wname" title="${escapeHtml(m.created)}">${_mi("clapper")} ${escapeHtml(m.name)}</span>`
        + `<span class="wmeta">${m.steps} 步 · ${escapeHtml(m.created||"")}</span>`
        + `<button class="ghost" onclick="runMacro('${idQ}',this)">${_mi("play")} 重放</button>`
        + `<button class="ghost" title="删除" aria-label="删除" onclick="delMacro('${idQ}')">${_mi("trash")}</button></div>`;
    }).join("");
  }catch(e){ box.innerHTML='<div class="sub">加载失败：'+escapeHtml(e.message||"网络错误")+'</div>'; }
}
// v1.0.616：宏重放接权限闸门（后端 routes/b_macro.py 逐步过 gate_write_tool）。
// 需要确认时后端推 approval_request + macro_paused(i) 并停流；批准后带 start=i 从
// **未执行的那一步**续跑——从头重放会把已经点过的「通过」再点一次，那是不可撤销的。
// 执行中按钮变「⏹ 点击停止」，再点一次 abort 掉 SSE（后端在下一步 yield 时收到断流）。
const __macroCtl = {};
async function runMacro(id, btn, start){
  const running = __macroCtl[id];
  if(running){
    try{ running.abort(); }catch(e){}
    delete __macroCtl[id];
    if(btn){ btn.textContent="正在停止…"; }
    toast("已请求停止：当前这一步跑完就停，未执行的步骤不再执行","warn",3000);
    return;
  }
  const go = ()=>_macroRun(id, btn, start||0);
  if(start) { await go(); return; }
  confirmAsk("重放宏", "请先把目标页面恢复到任务起始状态，再执行重放。", go, "重放");
}
async function _macroRun(id, btn, start){
  const ctrl = new AbortController();
  __macroCtl[id] = ctrl;
  const setBtn=(txt,dis)=>{ if(btn){ btn.textContent=txt; btn.disabled=!!dis; } };
  setBtn(start?("⏹ 点击停止（续跑）"):"⏹ 点击停止", false);
  let pausedAt = -1;
  try{
    const r=await fetch("/api/macro/run",{method:"POST",signal:ctrl.signal,
      headers:{"Content-Type":"application/json"},body:JSON.stringify({id:id,start:start})});
    // 手写分帧/解析循环收敛到 consumeSSE（01-core）：分帧（\n\n 切、余段留待下轮）、
    // data: 前缀、JSON 失败跳过与原实现逐条一致。macro_paused 的 return 映射 consumeSSE
    // 的提前返回契约（onEvent 返回非 undefined → 取消 reader 并返回 {early:...}，等价于
    // 原来的 return，只是不再悬挂连接）。
    const early = await consumeSSE(r.body.getReader(), (j)=>{
      if(j.type==="macro_step"){ toast(`宏执行 ${j.i}/${j.total}：${j.name} ${j.ok?"✅":"❌"}`, j.ok?"ok":"warn", 1500); }
      else if(j.type==="approval_request"){
        showApprovalModal("scratch", j, { noRelax:true,
          onGrant: ()=>{ toast("已批准，从第 "+(pausedAt+1)+" 步继续重放","ok"); _macroRun(id, btn, pausedAt); },
          onDeny:  ()=>{ toast("已拒绝，重放停在第 "+(pausedAt+1)+" 步（前面已执行的步骤不会回退）","warn",4000); } });
      }
      else if(j.type==="macro_paused"){ pausedAt = j.i; setBtn("▶ 重放", false); return true; }
      else if(j.type==="macro_done"){ toast(`宏完成：成功 ${j.ok} 步 / 失败 ${j.fail} 步`, j.fail?"warn":"ok", 3000); }
      else if(j.type==="error"){ toast(j.text||"宏执行失败","err",3000); }
    });
    if(early) return;   // macro_paused 提前停流：与原 return 同径（下方 finally 照样清理）
  }catch(e){
    if(String(e && e.name) !== "AbortError") toast(fetchErr(e,"重放宏"),"err");
    else toast("重放已停止","warn");
  }
  finally{ delete __macroCtl[id]; setBtn("▶ 重放", false); }
}
async function delMacro(id){
  confirmAsk("删除宏", "删除该宏？（不可恢复）", async ()=>{
    try{
      const d=await apiToast("/api/macro/del",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:id})},"删除宏");
      if(d.ok){ toast("已删除","ok",1200); loadMacroList(); }
      else toast(d.error||"删除失败","err");
    }catch(_){}
  }, "删除");
}
async function openWebUI(btn){
  // v1.0.405：后端新增 /api/webui/open（系统默认浏览器打开本机服务地址）
  try{
    const d=await apiToast("/api/webui/open",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"},"打开网页版");
    toast("已在系统浏览器打开 "+d.url,"ok");
  }catch(_){}
}

// ---- 设置页（v1.0.306：界面偏好即时生效 + 系统信息） ----
// v1.0.373（体验C8 实装）：深色模式——body.dark 变量覆盖 + localStorage 记忆，启动即恢复
function applyDarkMode(on){
  document.body.classList.toggle("dark", !!on);
  // v1.0.615：主题在运行中被切换时，重建「↓ 回到最新」那颗 pill。
  // 实测：节点是浅色态下创建的（当时 display:none），切到深色后 Chrome 不重算它身上
  // var(--muted) 的依赖 → 仍是 #5a6572，落在深底 #1a1f28 上只有 2.79:1（AA 要 4.5）。
  // 开机就是深色的用户没这问题（创建时即取到 #9aa4b5，实测 6.57:1）。
  // 删掉让 __syncJump 的自愈分支重建即可，代价是一次 DOM 操作。
  try{
    ["opPane","setChatPane"].forEach(id=>{
      const sc=document.getElementById(id);
      if(sc && sc.__jump){ sc.__jump.remove(); sc.__jump=null; }
    });
  }catch(e){}
}
function toggleDarkMode(cb){
  applyDarkMode(cb.checked);
  lsSet("darkMode", cb.checked?"1":"0");
  toast(cb.checked?"🌙 已切换深色模式":"已切换浅色模式","ok",1200);
}
try{ applyDarkMode(localStorage.getItem("darkMode")==="1"); }catch(e){}
// ===== v1.0.667 调色盘 → v1.0.668 全面扩面 =====
// 用户："主题色调色盘全面优化一下目前覆盖范围不多"——667 版只染 --brand 族+--bg，
// 而「强调/选中/当前位/链接/信息提示」走的是**另一族 --accent**（69 处消费）完全没跟。
// 668 起选中的强调色同时驱动三族（body 元素级覆盖，:root 默认值即回退）：
//   brand 族（66 处）logo/主按钮/品牌元素；accent 族（69 处）选中/当前位/链接/强调；
//   info 族（10 处）通知条/信息提示底。背景驱动 bg 族（12 处）。
// 语义状态色 --ok/--warn/--err **不随主题**（成功/警告/错误是语义，不能被染掉）。
// 派生口径按 v1.0.598 对比度标尺：*-ink 承载白字 → 亮度钳到 ≤42%（白字 ≥4.5:1）；
// *-fg 浅底上深字、深底上自动转亮一档（下限 60%）；*-soft = 主色低透明叠面板。
function _themeHsl(hex){
  try{
    const n=parseInt(hex.slice(1),16), r=(n>>16&255)/255, g=(n>>8&255)/255, b=(n&255)/255;
    const mx=Math.max(r,g,b), mn=Math.min(r,g,b), l=(mx+mn)/2;
    let h=0, s=0;
    if(mx!==mn){
      const d=mx-mn;
      s=l>0.5? d/(2-mx-mn) : d/(mx+mn);
      h = mx===r ? (g-b)/d+(g<b?6:0) : mx===g ? (b-r)/d+2 : (r-g)/d+4;
      h*=60;
    }
    return [h, s*100, l*100];
  }catch(_){ return null; }
}
// __THEME = {accent, bg} 或 null（=默认，不注入覆盖，:root 值生效）
let __THEME=null;
let __themeStyleEl=null;
function applyTheme(t){
  __THEME=(t&&(t.accent||t.bg))?t:null;
  if(!__themeStyleEl){
    __themeStyleEl=document.createElement("style");
    __themeStyleEl.id="__wc_theme";
    document.head.appendChild(__themeStyleEl);
  }
  if(!__THEME){ __themeStyleEl.textContent=""; return; }
  const a=__THEME.accent, bg=__THEME.bg;
  const hsl=_themeHsl(a||"");
  const h=hsl?Math.round(hsl[0]):146, s=hsl?Math.round(hsl[1]):84;
  const inkL=Math.min(42, Math.max(26, hsl?Math.round(hsl[2]-16):26));  // 白字对比度钳制
  const ink=`hsl(${h}, ${Math.min(s,72)}%, ${inkL}%)`;
  const fgLight=`hsl(${h}, ${Math.min(s,78)}%, ${inkL}%)`;      // 浅底深字
  const fgDark=`hsl(${h}, ${Math.min(s,78)}%, ${Math.max(62, inkL+24)}%)`; // 深底转亮
  // v1.0.669（用户："点了变颜色以后还是只有部分生效"）：668 只染了 var() 消费点，
  // 而 style.css 里还有 ~40 处品牌/强调色**硬编码**（渐变第二段 #08663a、
  // rgba(7,193,96,*) 光晕/高亮、用户气泡硬蓝底 #3a7afe）根本不走 var()。
  // 这批 669 全部令牌化，引擎多派生以下覆盖：
  //   --brand-2   品牌渐变深色端（logo/主按钮/选中芯片的 linear-gradient 第二段）
  //   --brand-glow 品牌光晕/高亮（rgba 品牌色，alpha 由消费方决定）
  //   --user-bd/-fg/-code 用户气泡四件套（此前硬蓝底 #3a7afe，换主题后残留蓝气泡）
  // 派生不覆盖（:root 默认值即回退，不改 style.css :root 行，防并发会话撞行）：
  //   --brand-fg-dark / --brand-ink-dark —— 深色侧栏激活项文字（恒深底 #1d232e，
  //   深底必须用亮色；浅色主题里侧栏照样恒深，所以这是独立于主题深浅的一档）。
  const rgb=a?_themeRgb(a):"7, 193, 96";        // "R, G, B" 文本，消费方写 rgba(var(--brand-rgb), α)
  const b2=a?_themeDarken(a,0.72):"#08663a";
  const uBg=a?`hsl(${h}, ${Math.min(s,60)}%, 47%)`:"#3a7afe";
  const uBd=a?`hsl(${h}, ${Math.min(s,60)}%, 40%)`:"#2f66d4";
  const uFg=a?`hsl(${h}, ${Math.min(s,55)}%, ${Math.max(66,inkL+34)}%)`:"#dbe7ff";
  const uCode=a?`hsl(${h}, ${Math.min(s,55)}%, ${Math.max(60,inkL+30)}%)`:"#cfe3ff";
  let light="", dark="";
  if(a){
    light+=`--brand:${a};--brand2:${b2};--brand-2:${b2};--brand-rgb:${rgb};`
          +`--brand-ink:${ink};--brand-fg:${fgLight};`
          // v1.0.774：--accent-rgb 一并派生——accent 光晕/焦点环/表格行 rgba(var(--accent-rgb),α)
          // 消费点跟主题走（此前硬编码 #3a7afe 的 rgba，换强调色后残留蓝，与 v1.0.669 brand 侧同病）
          +`--accent:${a};--accent-ink:${ink};--accent-fg:${fgLight};--accent-rgb:${rgb};`
          +`--accent-soft:rgba(${rgb},0.10);--info-bg:rgba(${rgb},0.09);--info-bd:rgba(${rgb},0.28);--info-fg:${fgLight};`
          +`--brand-soft:rgba(${rgb},0.10);`
          +`--user-bg:${uBg};--user-bd:${uBd};--user-fg:${uFg};--user-code:${uCode};`
          +`--side-fg:${fgDark};`;  // 恒深侧栏专用亮档（body 块设，body.dark 不覆盖 → 两种主题侧栏都是深底都亮）
    dark +=`--brand-fg:${fgDark};--accent-fg:${fgDark};--info-fg:${fgDark};`
          +`--accent-soft:rgba(${rgb},0.18);--brand-soft:rgba(${rgb},0.18);--info-bg:rgba(${rgb},0.16);`
          +`--user-bg:${uBg};--user-bd:${uBd};--user-fg:${uFg};--user-code:${uCode};`;
  }
  const bgL=bg?`--bg:${bg};--chat-bg:${bg};`:"";
  __themeStyleEl.textContent=`body{${light}${bgL}}` + (dark?`body.dark{${dark}}`:"");
}
function _themeRgb(hex){ try{ const n=parseInt(hex.slice(1),16); return `${n>>16&255}, ${n>>8&255}, ${n&255}`; }catch(_){ return "0,0,0"; } }
function _themeDarken(hex, f){
  try{
    const n=parseInt(hex.slice(1),16); let r=n>>16&255,g=n>>8&255,b=n&255;
    r=Math.round(r*f); g=Math.round(g*f); b=Math.round(b*f);
    return "#"+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
  }catch(_){ return hex; }
}
function themeAccentInput(){ const e=$("theme_accent"); if(e) e.value=__THEME?__THEME.accent:"#07c160"; }
function themeBgInput(){ const e=$("theme_bg"); if(e) e.value=__THEME?__THEME.bg:"#f4f6f8"; }
function loadTheme(){
  try{
    const raw=localStorage.getItem("theme");
    applyTheme(raw?JSON.parse(raw):null);
  }catch(_){ applyTheme(null); }
  themeAccentInput(); themeBgInput();
}
function saveTheme(accent,bg){
  const t={};
  if(accent) t.accent=accent;
  if(bg) t.bg=bg;
  applyTheme((t.accent||t.bg)?t:null);
  try{ localStorage.setItem("theme",(t.accent||t.bg)?JSON.stringify(t):"null"); }catch(_){}
}
function onThemeAccent(e){ saveTheme(e.target.value,null); }
function onThemeBg(e){ saveTheme(null,e.target.value); }
function themePreset(accent,bg){
  saveTheme(accent,bg); themeAccentInput(); themeBgInput();
  toast("已应用主题","ok",1200);
}
function themeReset(){
  saveTheme(null,null); themeAccentInput(); themeBgInput();
  toast("已恢复默认主题色","ok",1400);
}
// 预设主题：accent/bg 与色板一一对应（名称参照「经典/水墨/神秘」系，颜色全部中性可辨）
const __THEME_PRESETS=[
  {name:"经典绿",accent:"#07c160",bg:"#f4f6f8"},
  {name:"经典蓝",accent:"#2f6fed",bg:"#f2f5fa"},
  {name:"墨韵黑金",accent:"#8a6d3b",bg:"#f6f3ee"},
  {name:"紫丁香",accent:"#7b5ea7",bg:"#f5f2f8"},
  {name:"樱语粉",accent:"#d1628a",bg:"#faf3f6"},
  {name:"夜航蓝",accent:"#4a90d9",bg:"#1a2029"},
];
function renderThemePresets(){
  const box=$("theme_presets"); if(!box) return;
  box.innerHTML=__THEME_PRESETS.map((p,i)=>
    `<button class="ghost smbtn" onclick="themePreset('${p.accent}','${p.bg}')" title="${p.name}（背景 ${p.bg}）" `+
    `style="--_p:${p.accent};--_q:${p.bg}">`+
    `<span style="display:inline-block;width:14px;height:14px;border-radius:4px;background:var(--_p);border:1px solid var(--_q);vertical-align:-2px"></span> ${p.name}`+
    `</button>`).join("");
}
try{ loadTheme(); renderThemePresets(); }catch(e){}
// v1.0.588：界面整体缩放（字号可读性）。用 html 的 zoom 统一放大，覆盖内联 px 字号，
// 与 Ctrl++ 等效；localStorage 记忆，启动即恢复（与深色模式同一套时序，先于 openView）。
const __UI_SCALES = {s:"0.9", m:"1", l:"1.15", xl:"1.3"};
function applyUiScale(key){
  document.documentElement.style.zoom = __UI_SCALES[key] || "1";
}
function toggleUiScale(sel){
  applyUiScale(sel.value);
  lsSet("uiScale", sel.value);
  toast("界面缩放已调整：" + (sel.selectedOptions[0] ? sel.selectedOptions[0].text : sel.value), "ok", 1400);
}
try{ applyUiScale(localStorage.getItem("uiScale") || "m"); }catch(e){}
function saveUiPref(){
  const st=$("opt_show_times");
  if(st){ lsSet("timesHidden", st.checked?"0":"1"); applyTimesPref(); }
  // v1.0.633：删除「自动任务分组默认收起」的读写——分组已收进侧栏「更多」，
  // 由「更多」自身折叠，这个开关再留着就是一个不再生效的界面承诺
  const dk=$("opt_dark_mode");
  if(dk){ applyDarkMode(dk.checked); lsSet("darkMode", dk.checked?"1":"0"); }
  toast("已保存","ok",1200);
}
async function loadUiPref(){
  const st=$("opt_show_times"); if(st) st.checked=!timesHiddenPref();
  const dk=$("opt_dark_mode");
  if(dk){ let dm=false; try{ dm=localStorage.getItem("darkMode")==="1"; }catch(e){} dk.checked=dm; applyDarkMode(dm); }
  const us=$("opt_ui_scale");
  if(us){ let k = lsGet("uiScale") || "m"; if(!__UI_SCALES[k]) k="m"; us.value=k; applyUiScale(k); }
  try{ const d=await getJson("/api/workspace"); const el=$("opt_wsroot"); if(el&&d.root) el.value=d.root; }catch(e){}
}

// ══════ v1.0.664 设置页搜索（Route B：结果列表面板 + scrollIntoView + 1.6s 高亮）══════
// 设置页是单长滚动，.row 双列 flex 一旦被行内过滤隐藏就会塌成单列——所以不做行内过滤，
// 只做「索引 → 跳转 → 高亮」。面板 fixed 定位（.view{overflow:auto} 会裁剪 absolute）。
// 匹配口径：query 按空白拆 token，AND——每个 token 需命中 label 或任一 alias。
// 加设置项时只需在 SETTING_INDEX 补一行（target 用该控件/卡片的选择器）。
const SETTING_INDEX = [
  {label:"管理员模式", alias:["admin","管理员","提权","uac","系统权限"], target:"#adminRestartBtn"},
  {label:"技能系统", alias:["skill","技能","总闸"], target:"#opt_ff_skills"},
  {label:"连接器插件", alias:["connector","连接器","插件"], target:"#opt_ff_connectors"},
  {label:"智能体团队", alias:["team","团队","多智能体"], target:"#opt_ff_team"},
  {label:"本地记忆沉淀", alias:["memory","记忆","沉淀"], target:"#opt_ff_memory"},
  {label:"技能自进化", alias:["evolve","自进化","自学"], target:"#opt_ff_evolve"},
  {label:"用户固定规则", alias:["rules","规则","长期指令"], target:"#opt_user_rules"},
  {label:"报销人姓名", alias:["reimburse","name","姓名","报销"], target:"#pii_name"},
  {label:"银行账号", alias:["bank","account","银行卡","账号"], target:"#pii_account"},
  {label:"开户行", alias:["bank name","开户行","银行"], target:"#pii_bank"},
  {label:"银行账号脱敏", alias:["mask","脱敏"], target:"#pii_mask"},
  {label:"dsh 服务端点", alias:["dsh","base_url","端点"], target:"#dsh_base_url"},
  {label:"dsh 访问凭据", alias:["dsh","api_key","凭据","密钥"], target:"#dsh_api_key"},
  {label:"dsh 工作目录", alias:["dsh","cwd","目录"], target:"#dsh_cwd"},
  {label:"dsh 附加参数", alias:["dsh","args","参数"], target:"#dsh_extra_args"},
  {label:"每日工作小结", alias:["digest","daily","日报","小结"], target:"#bg_digests_daily"},
  {label:"小结时间", alias:["time","时间"], target:"#bg_digests_time"},
  {label:"知识库空闲整理", alias:["kb","organize","知识库","整理"], target:"#bg_kb_organize"},
  {label:"知识库每日 AI 消化", alias:["kb","digest","消化"], target:"#bg_kb_digest"},
  {label:"待办变更轻提醒", alias:["todo","watch","待办","提醒"], target:"#bg_todo_watch"},
  {label:"待办轮询间隔", alias:["interval","轮询","间隔"], target:"#bg_todo_interval"},
  {label:"基础输入 token 额度", alias:["budget","tokens","预算","额度"], target:"#bg_soft_budget_tokens"},
  {label:"基础时长额度", alias:["budget","minutes","时长","预算"], target:"#bg_soft_budget_minutes"},
  {label:"每完成一项追加 token", alias:["budget","per item","追加","额度"], target:"#bg_soft_budget_tokens_per_item"},
  {label:"每完成一项追加分钟", alias:["budget","per item","追加","时长"], target:"#bg_soft_budget_minutes_per_item"},
  {label:"目标驱动执行", alias:["goal","目标","轮数","轮次","驱动"], target:"#bg_goal_max_rounds"},
  {label:"立即体检", alias:["repair","自我修复","体检"], target:"#repairRunBtn"},
  {label:"每日自动体检", alias:["self_repair_daily","自我修复","体检"], target:"#opt_ff_self_repair_daily"},
  {label:"自动代码修复", alias:["self_auto_fix","自动修复","补丁"], target:"#opt_ff_self_auto_fix"},
  {label:"已归档任务", alias:["archive","归档"], target:"#archivedListBox"},
  {label:"版本管理", alias:["version","版本","回退"], target:"#versionListBox"},
  {label:"权限级别", alias:["permission","权限","ask_every","auto","full"], target:"#sec_perm_level"},
  {label:"命令安全模式", alias:["command_mode","命令","安全","拦截"], target:"#sec_mode"},
  {label:"删除审批阈值", alias:["delete","limit","删除","阈值"], target:"#sec_del_limit"},
  {label:"命令放行名单", alias:["whitelist","白名单","放行"], target:"#sec_whitelist"},
  {label:"命令黑名单", alias:["blacklist","黑名单"], target:"#sec_blacklist"},
  {label:"保护路径", alias:["protected","paths","保护","路径"], target:"#sec_paths"},
  {label:"自动备份", alias:["backup","备份"], target:"#sec_backup"},
  {label:"备份总上限", alias:["backup","limit","备份","上限"], target:"#sec_backup_mb"},
  {label:"备份保留天数", alias:["retention","保留","归档","备份"], target:"#sec_backup_retention"},
  {label:"最近安全事件", alias:["audit","审计","安全事件"], target:"#auditList"},
  {label:"显示消息时间", alias:["times","消息时间","时间"], target:"#opt_show_times"},
  {label:"深色模式", alias:["dark","mode","深色","夜间"], target:"#opt_dark_mode"},
  {label:"界面字号 / 缩放", alias:["ui","scale","字号","缩放"], target:"#opt_ui_scale"},
  {label:"开机自动启动", alias:["autostart","开机","自启"], target:"#opt_autostart"},
  {label:"管理员模式自启动", alias:["autostart","admin","管理员","自启"], target:"#opt_autostart_admin"},
  {label:"启动后隐藏主窗口", alias:["hide","main","隐藏","托盘"], target:"#opt_autostart_hide"},
  {label:"关闭窗口时的行为", alias:["close","action","关闭","退出","最小化","托盘"], target:"#opt_close_action"},
  {label:"全局热键（唤起/隐藏窗口）", alias:["hotkey","快捷键","热键","唤起","唤窗","toggle"], target:"#opt_toggle_hotkey"},
  {label:"语音转写组件", alias:["asr","voice","语音","转写"], target:"#asrComponentCard"},
  {label:"自动启动绑定目标", alias:["auto_launch","企微","启动"], target:"#opt_auto_launch"},
  {label:"自动注入 CDP", alias:["auto_cdp","cdp","调试"], target:"#opt_auto_cdp"},
  {label:"静默后台执行", alias:["silent_mode","静默","后台"], target:"#opt_silent_mode"},
  {label:"CDP 调试端口", alias:["cdp","port","端口"], target:"#opt_cdp_port"},
  {label:"群机器人 Webhook", alias:["webhook","机器人","群"], target:"#opt_webhook"},
  {label:"检测并绑定目标", alias:["bind","绑定","企微"], target:"#bindCard"},
  {label:"软件版本", alias:["version","版本号"], target:"#opt_version"},
  {label:"产物库目录", alias:["workspace","产物库","目录"], target:"#opt_wsroot"},
];
let _setSearchInited=false, _setSearchComposing=false, _setHitTimer=null;

// ===== v1.0.669（用户："设置模块右侧导航也没有"）：设置页右侧分组导航 =====
// 设置页是单长滚动（~6 组 1200+ 行），此前只有顶部搜索，翻到下半页回不去。
// 右侧粘性导航：点分组跳转 + 滚动时高亮当前分组。fixed 定位（同 setSearchPanel 口径——
// .view{overflow:auto} 会裁剪 absolute）；视图隐藏（display:none）→ rect 归零 → 自动收起。
let _setNavInited=false;
function initSettingsSideNav(){
  if(_setNavInited) return;
  const view=$("view-settings");
  if(!view) return;
  const groups=[...view.querySelectorAll(".set-group")];
  if(groups.length<3) return;   // 分组太少没导航价值
  // 1) 分组加锚点 id（幂等）
  groups.forEach((g,i)=>{ if(!g.id) g.id="setNavG"+i; });
  // 2) 导航浮层（动态生成，不写死进 index.html——分组顺序/数量变了也不用改 HTML）
  const nav=document.createElement("nav");
  nav.id="setSideNav"; nav.setAttribute("aria-label","设置分组导航");
  nav.innerHTML=groups.map((g,i)=>
    `<button class="ssn-item" data-i="${i}" title="${escapeHtml(g.textContent.trim())}">${escapeHtml(g.textContent.trim())}</button>`
  ).join("");
  document.body.appendChild(nav);
  // 3) 定位：贴住视图内容区右上（与搜索框同一参照系）
  //
  // v1.0.688（点「设置」必卡死 · 根因）：**class 写入必须幂等**。
  // 下面第 5 步的 MutationObserver 盯着 view 自己的 class/style 属性，
  // 而 place() 又要往 view.classList 写 "set-nav-on"（让内容区让出右侧 170px）。
  // 关键在 DOMTokenList 的语义差异（Chromium 实测，见 README 6.24）：
  //   · classList.add(x) / remove(x) —— **无条件**重写 class 属性，哪怕 x
  //     已在集合里（或根本不在集合里）照样算一次属性变更 → 观察器被唤醒
  //   · classList.toggle(x, force) —— 只在成员状态**真的变了**时才写属性
  // v1.0.669 的 place() 用的是无条件 add/remove，于是：
  //   MutationObserver 回调 place() → place() 写 class → 属性变更
  //     → 观察器再回调 place() → ……**微任务死循环**
  // 表现：渲染器主线程 100% CPU、页面渲染正常但 JS 全停（心跳信标断、
  // Runtime.evaluate 超时），点「设置」必卡死，与后端健康与否无关。
  // 修法：class 写入改成幂等——先 contains() 比对现状，**只有目标态 ≠ 现状
  // 才写**（顺带用 toggle 而非 add/remove，双保险）；观察器最多多回调一次
  // 就收敛，不再自激。
  const _setNavOn=(on)=>{
    if(view.classList.contains("set-nav-on")===on) return;   // 已是目标态 → 不写（不触发观察器）
    view.classList.toggle("set-nav-on", on);
  };
  const place=()=>{
    const r=view.getBoundingClientRect();
    // v1.0.735（用户："自适应窗口大小"）：窄窗优先收起导航——右侧 170px 让位
    // 会把内容挤成窄条。与 css/style.css 的 @media (max-width:1039px) 同阈值，
    // 两端一致；必须同步 _setNavOn(false) 才能真正撤掉 padding-right。
    if(!r.width || window.innerWidth < 1040){
      nav.style.display="none"; _setNavOn(false); return;   // 视图不在 / 窄窗 → 收起
    }
    nav.style.display="flex";
    nav.style.right=Math.max(8, window.innerWidth-r.right+14)+"px";
    nav.style.top=Math.max(70, r.top+86)+"px";
    // 导航是 fixed 浮层，会压在设置内容右缘上（CDP 实测 130px 重叠）→ 内容区让出右侧
    _setNavOn(true);
  };
  // 4) 跳转
  nav.querySelectorAll(".ssn-item").forEach(b=>{
    b.addEventListener("click",()=>{
      const g=groups[+b.dataset.i];
      const top=g.getBoundingClientRect().top - view.getBoundingClientRect().top + view.scrollTop - 70;
      view.scrollTo({top:Math.max(0,top), behavior:"smooth"});
    });
  });
  // 5) 滚动高亮当前分组（IO 比 scroll 事件便宜）
  const items=[...nav.querySelectorAll(".ssn-item")];
  let io=null;
  try{
    io=new IntersectionObserver(es=>{
      es.forEach(e=>{ if(e.isIntersecting){
        const i=groups.indexOf(e.target);
        items.forEach(x=>x.classList.toggle("on", +x.dataset.i===i));
      }});
    },{root:view, rootMargin:"-70px 0px -75% 0px", threshold:0});
    groups.forEach(g=>io.observe(g));
  }catch(_){ /* IO 不支持就退化成只有跳转 */ }
  view.addEventListener("scroll",place,{passive:true});
  window.addEventListener("resize",place);
  new MutationObserver(place).observe(view,{attributes:true, attributeFilter:["class","style"]});
  place();
  _setNavInited=true;
}
function initSettingsSearch(){
  if(_setSearchInited) return;
  const inp=$("setSearchInput"), panel=$("setSearchPanel"), view=$("view-settings");
  if(!inp || !panel || !view) return;
  _setSearchInited=true;
  const pos=()=>{
    const r=inp.getBoundingClientRect();
    panel.style.left=Math.round(r.left)+"px";
    panel.style.top=Math.round(r.bottom+6)+"px";
    panel.style.width=Math.round(r.width)+"px";
  };
  const close=()=>{ panel.style.display="none"; panel.innerHTML=""; };
  const show=(q)=>{
    const tokens=String(q||"").toLowerCase().split(/\s+/).filter(Boolean);
    if(!tokens.length){ close(); return; }
    const hits=SETTING_INDEX.filter(it=>{
      const hay=[it.label, ...(it.alias||[])].map(x=>String(x).toLowerCase());
      return tokens.every(t=>hay.some(h=>h.indexOf(t)>=0));
    });
    if(!hits.length){
      panel.innerHTML='<div class="set-search-empty">没有匹配的设置项</div>';
    }else{
      panel.innerHTML=hits.map(it=>{
        const i=SETTING_INDEX.indexOf(it);
        const hint=(it.alias||[]).slice(0, 3).join(" · ");
        return '<div class="set-search-item" role="option" data-i="'+i+'"><b>'+escapeHtml(it.label)+'</b><span>'+escapeHtml(hint)+'</span></div>';
      }).join("");
    }
    pos(); panel.style.display="block";
  };
  // IME 守卫：拼音输入过程中不搜索，compositionend 后再按最终文本搜一次
  inp.addEventListener("compositionstart", ()=>{ _setSearchComposing=true; });
  inp.addEventListener("compositionend", ()=>{ _setSearchComposing=false; show(inp.value); });
  inp.addEventListener("input", ()=>{ if(!_setSearchComposing) show(inp.value); });
  inp.addEventListener("focus", ()=>{ if(inp.value.trim()) show(inp.value); });
  inp.addEventListener("keydown", (e)=>{
    if(e.key==="Escape"){ inp.value=""; close(); inp.blur(); }
  });
  // mousedown 阻止默认：点击结果时输入框不失焦，面板不会先被 blur/click 外部监听关掉
  panel.addEventListener("mousedown", (e)=>{ e.preventDefault(); });
  panel.addEventListener("click", (e)=>{
    const row=e.target.closest(".set-search-item"); if(!row) return;
    const it=SETTING_INDEX[+row.dataset.i]; if(!it) return;
    close();
    const el=document.querySelector(it.target);
    if(!el){ toast("该设置项暂未加载", "warn"); return; }
    const hit=el.closest(".field, .card, .set-group") || el;
    el.scrollIntoView({block:"center", behavior:"smooth"});
    hit.classList.remove("set-hit"); void hit.offsetWidth;
    hit.classList.add("set-hit");
    if(_setHitTimer) clearTimeout(_setHitTimer);
    _setHitTimer=setTimeout(()=>{ hit.classList.remove("set-hit"); _setHitTimer=null; }, 1600);
  });
  view.addEventListener("scroll", ()=>{ if(panel.style.display!=="none") pos(); }, {passive:true});
  window.addEventListener("resize", ()=>{ if(panel.style.display!=="none") pos(); });
  document.addEventListener("click", (e)=>{
    if(!e.target.closest(".set-search-wrap")) close();
  }, true);
}

// ---- v1.0.664 关闭窗口行为（config.ui.close_action 三态：""/minimize/exit）----
// 读写口径与 desktop/shell.py::_saved_close_action 一致：WM_CLOSE 每次现读，保存后下次点 X 生效。
async function loadCloseAction(){
  const sel=$("opt_close_action"); if(!sel) return;
  try{
    const d=await getJson("/api/config");
    const v=((d&&d.ui)||{}).close_action;
    sel.value=(v==="minimize"||v==="exit") ? v : "";
  }catch(e){ /* 读取失败保留界面现值，不弹错 */ }
}
async function saveCloseAction(){
  const sel=$("opt_close_action"); if(!sel) return;
  const v=sel.value;
  try{
    const r=await fetch("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({close_action:v})});
    const d=await r.json();
    if(!r.ok || d.error){ toast("保存失败："+(d.error||("HTTP "+r.status)), "err", 4000); loadCloseAction(); return; }
    toast(v==="" ? "已恢复：每次关闭窗口都询问" : (v==="minimize" ? "已设置：关闭窗口时最小化到托盘" : "已设置：关闭窗口时直接退出"), "ok", 2600);
  }catch(e){ toast(fetchErr(e,"保存关闭行为"), "err"); }
}
// ---- v1.0.788（P2-9）全局热键（唤起/隐藏窗口）：ui.toggle_hotkey ----
// 口径：磁盘缺键 = 默认 Ctrl+Alt+Space（存量行为不变）；空串 = 显式关闭（存值不删键）。
// 保存走 POST /api/config，后端 apply_toggle_hotkey_change 即时卸旧注新，无需重启。
async function loadToggleHotkey(){
  const el=$("opt_toggle_hotkey"); if(!el) return;
  try{
    const d=await getJson("/api/config");
    const v=((d&&d.ui)||{}).toggle_hotkey;
    el.value=(typeof v==="string") ? v : "Ctrl+Alt+Space";
  }catch(e){ /* 读取失败保留界面现值，不弹错 */ }
}
async function saveToggleHotkey(){
  const el=$("opt_toggle_hotkey"); if(!el) return;
  const v=el.value.trim();
  try{
    const r=await fetch("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({toggle_hotkey:v})});
    const d=await r.json();
    if(!r.ok || d.error){ toast("保存失败："+(d.error||("HTTP "+r.status)), "err", 4000); loadToggleHotkey(); return; }
    toast(v ? "全局热键已设为 "+v+"（立即生效）" : "全局热键已关闭（唤起/隐藏请用托盘菜单）", "ok", 2600);
  }catch(e){ toast(fetchErr(e,"保存全局热键"), "err"); }
}
// ---- v1.0.373 管理员模式：状态显示 + UAC 提权重启 ----
async function loadAdminStatus(){
  const el=$("adminState"); if(!el) return;
  const btn=$("adminRestartBtn");
  try{
    const d=await getJson("/api/admin/status");
    if(d.is_admin){
      el.innerHTML='✅ 当前正以<b style="color:var(--brand-fg)">管理员模式</b>运行——系统级命令、保护目录写入、HKLM 注册表、系统级计划任务已解锁。';
      if(btn) btn.style.display="none";
    }else if(!d.elevatable){
      el.innerHTML='当前为普通权限运行。开发模式下请以管理员身份运行 IDE/终端来调试系统级能力。';
      if(btn) btn.style.display="none";
    }else{
      el.innerHTML='当前为普通权限运行。点击下方按钮会弹出 UAC 确认，确认后软件将以管理员模式重启。';
    }
  }catch(e){ el.textContent="管理员状态获取失败"; }
}
async function adminRestart(btn){
  if(btn && btn.disabled) return;
  if(btn){ btn.disabled=true; btn.textContent="⏳ 检查中…"; }
  let armed = false;
  try{
    const st = await getJson("/api/admin/status");   // v1.0.655 B6：task_status 并入 admin/status（armed 字段同形）
    armed = !!st.armed;
  }catch(e){}
  if(btn){ btn.disabled=false; btn.textContent="🛡 以管理员模式重启"; }
  if(!armed){
    // v1.0.542：未启用 → 引导一次性注册（弹 UAC 注册计划任务，之后零 UAC）
    confirmAsk("启用管理员重启支持",
      "首次使用需注册一个计划任务（弹一次 UAC 确认）。之后「管理员模式重启」不再弹 UAC、不再受安全软件拦截。现在启用吗？",
      async ()=>{
        if(btn){ btn.disabled=true; btn.textContent="⏳ 注册中…"; }
        try{
          const r=await fetch("/api/admin/arm_task",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
          const d=await r.json();
          if(!d.ok){ toast(d.error||"启用失败","err",6000); return; }
          toast("已启用——现在可以直接点「以管理员模式重启」","ok");
        }catch(e){ toast(fetchErr(e,"启用管理员重启支持"),"err"); }
        finally{ if(btn){ btn.disabled=false; btn.textContent="🛡 以管理员模式重启"; } }
      }, "启用");
    return;
  }
  confirmAsk("以管理员模式重启", "软件将退出并以管理员权限重新启动（计划任务预授权，无需 UAC）。未发送的输入会丢失，确认继续？", async ()=>{
    if(btn){ btn.disabled=true; btn.textContent="⏳ 正在提权重启…"; }
    try{
      const r=await fetch("/api/admin/restart",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
      const d=await r.json();
      if(!d.ok){
        if(d.need_arm){ toast("管理员重启支持未启用——请再次点击完成一次性启用","err",5000); }
        else { toast((d.error||"重启失败"),"err",6000); }
        if(btn){ btn.disabled=false; btn.textContent="🛡 以管理员模式重启"; }
        return;
      }
      toast(d.hint||"软件将以管理员模式重启","ok");
      document.title += "（管理员重启中…）";
    }catch(e){
      toast(fetchErr(e,"管理员重启"),"err");
      if(btn){ btn.disabled=false; btn.textContent="🛡 以管理员模式重启"; }
    }
  }, "重启");
}
// ---- v1.0.369/373 开机自启 + 启动后隐藏主窗口（HKCU Run 注册表 + --minimized 参数） ----
// v1.0.373（需求2）：管理员模式自启动（计划任务 /rl highest）——勾选弹 UAC 创建，取消弹 UAC 删除
async function loadAutostart(){
  const el=$("opt_autostart"); if(!el) return;
  const hd=$("opt_autostart_hide");
  const ad=$("opt_autostart_admin");
  try{
    const d=await getJson("/api/autostart");
    el.checked=!!d.enabled;
    if(ad){ ad.checked=(d.mode==="admin"); ad.disabled=!d.supported; }
    if(hd){ hd.checked=!!(d.mode==="admin" ? d.admin_hide : d.hide_main); hd.disabled=!d.supported || !d.enabled; }
    if(!d.supported){ el.disabled=true; el.parentElement.title="仅打包版（exe）支持开机自启；开发模式请手动配置"; }
    else el.disabled=false;
  }catch(e){ el.disabled=true; if(ad) ad.disabled=true; }
}
async function toggleAutostart(cb){
  const want=cb.checked;
  try{
    const hide=$("opt_autostart_hide");
    const r=await fetch("/api/autostart",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({enabled:want, hide_main:!!(hide&&hide.checked)})});
    const d=await r.json();
    if(!d.ok){ cb.checked=!want; toast(d.error||("HTTP "+r.status),"err"); return; }
    cb.checked=!!d.enabled;
    const ad=$("opt_autostart_admin");
    if(ad) ad.checked=(d.mode==="admin");
    if(hide){ hide.disabled=!d.enabled; hide.checked=!!d.hide_main; }
    toast(want?(d.mode==="admin"?"✅ 管理员模式自启保持开启":"✅ 已开启开机自启（下次开机自动运行）"):"已关闭开机自启","ok");
  }catch(e){ cb.checked=!want; toast(fetchErr(e,"开机自启设置"),"err"); }
}
// v1.0.373（需求2）：管理员模式自启动——勾选创建最高权限计划任务（UAC 确认），取消删除任务
async function toggleAutostartAdmin(cb){
  const want=cb.checked;
  cb.checked=!want;   // 先回滚，用户在弹层确认后再置位（confirmAsk 无取消回调）
  confirmAsk(want?"管理员模式自启动":"取消管理员模式自启动",
    want?"将创建一个最高权限的计划任务（UAC 弹窗需点「是」）：开机后软件免 UAC 直接以管理员模式运行，并替换现有普通自启。确认继续？"
        :"将删除管理员自启动的计划任务（UAC 弹窗需点「是」），开机不再自动运行软件。确认继续？",
    async ()=>{
      cb.checked=want;
      const en=$("opt_autostart");
      const hd=$("opt_autostart_hide");
      try{
        const r=await fetch("/api/autostart",{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({enabled:want, admin:want, hide_main:!!(hd&&hd.checked)})});
        const d=await r.json();
        if(!d.ok){ cb.checked=!want; toast(d.error||("HTTP "+r.status),"err"); return; }
        if(en) en.checked=!!d.enabled;
        if(hd){ hd.disabled=!d.enabled; hd.checked=!!d.hide_main; }
        toast(want?"✅ 已设置管理员模式自启动（开机免 UAC 直接提权运行）":"已取消管理员模式自启动","ok");
      }catch(e){ cb.checked=!want; toast(fetchErr(e,"管理员自启动设置"),"err"); }
    }, want?"创建任务":"删除任务");
}
async function toggleAutostartHide(cb){
  const en=$("opt_autostart");
  if(!en || !en.checked){ cb.checked=false; toast("请先开启「开机自动启动」","warn"); return; }
  try{
    const r=await fetch("/api/autostart",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({enabled:true, hide_main:cb.checked})});
    const d=await r.json();
    if(!d.ok){ cb.checked=!cb.checked; toast(d.error||("HTTP "+r.status),"err"); return; }
    cb.checked=!!d.hide_main;
    toast(cb.checked?"已设置：开机启动后隐藏主窗口（从任务栏托盘恢复）":"已设置：开机启动后正常显示主窗口","ok");
  }catch(e){ cb.checked=!cb.checked; toast(fetchErr(e,"隐藏窗口设置"),"err"); }
}
async function openLogFolder(){
  try{ await postJson("/api/workspace/open",{dir:"logs"}); }catch(e){ toast(e.message||"打开失败","err"); }
}
// ---- 功能管理（v1.0.312）：总闸存 config.feature_flags，立即生效 ----
// v1.0.617：键名唯一事实源在 core/config_store.py::FEATURE_FLAGS。这份 JS 数组是浏览器里
// 不得不再留的一份，靠 tests/test_v617_flags.py 断言"三者集合相等"（Python 键表 / 这里 /
// index.html 的 opt_ff_* 复选框 id）——加开关不改锁就变红，不再靠人肉对齐。
const _FF_KEYS=["skills","connectors","team","memory","evolve","self_repair_daily","self_auto_fix","event_notify"];
// 缺省即关的键（其余维持"缺省即开"的历史语义）；须等于 FEATURE_FLAG_OFF_BY_DEFAULT ∩ UI 键
const _FF_OFF_BY_DEFAULT=["self_auto_fix","event_notify"];
async function loadFeatureFlags(){
  try{
    const d=await getJson("/api/config");
    const ff=d.feature_flags||{};
    // v1.0.357：self_auto_fix 是危险开关（会自动改源码），默认必须为关（未设置时不勾选）
    _FF_KEYS.forEach(k=>{ const el=$("opt_ff_"+k); if(el) el.checked = _FF_OFF_BY_DEFAULT.includes(k) ? ff[k]===true : ff[k]!==false; });
  }catch(e){}
}
async function saveFeatureFlags(){
  const ff={};
  _FF_KEYS.forEach(k=>{ const el=$("opt_ff_"+k); if(el) ff[k]=!!el.checked; });
  try{
    await apiToast("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({feature_flags:ff})},"保存功能开关");
    const off=[];
    if(!ff.skills) off.push("技能系统"); if(!ff.connectors) off.push("连接器");
    toast("功能管理已保存"+(off.length?("（已停用："+off.join("、")+"）"):""),"ok");
  }catch(_){}
}
// v1.0.354 体检进度渲染：百分比无法可靠预测（LLM 输出时长不可知），
// 用「阶段指示 + 已接收字符数 + 已耗时」如实展示——字符在涨就证明还在跑。
const _REPAIR_PHASES = ["采集数据", "模型分析", "应用修复", "完成"];
const _PHASE_IDX = {collect: 0, llm: 1, apply: 2, done: 3};
function renderRepairProgress(box, prog){
  const p=(prog&&prog.phase)||"collect";
  const idx=(_PHASE_IDX[p]!==undefined)?_PHASE_IDX[p]:0;
  let chips=_REPAIR_PHASES.map((ph,i)=>
    i<idx ? '<span style="color:var(--brand-fg)">✅ '+ph+'</span>'
    : i===idx ? '<span style="color:var(--brand-fg);font-weight:600">⏳ '+ph+(p==="llm"?"…":"")+'</span>'
    : '<span class="dim">⚪ '+ph+'</span>').join(' <span style="color:var(--muted)">›</span> ');
  let line="";
  if(p==="llm"){
    line='已接收 <b style="color:var(--brand-fg)">'+(prog.tokens||0)+'</b> 字符 · 已耗时 '
      +(prog.elapsed_s||0)+' 秒'+(prog.model?(' · 模型：'+escapeHtml(prog.model)):'');
  }else if(p==="collect"){ line="正在采集运行日志与经验库…"; }
  else if(p==="apply"){ line=escapeHtml(prog.note||"正在应用修复…"); }
  else if(p==="cancelled"){ line='<span style="color:var(--err-fg)">⏹ '+escapeHtml(prog.note||"已取消")+'</span>'; }
  else if(p==="failed"){ line='<span style="color:var(--err-fg)">'+escapeHtml(prog.note||"体检失败")+'</span>'; }
  box.innerHTML='<div style="font-size:var(--fs-sm);margin-bottom:4px">'+chips+'</div>'+
    (line?'<div class="sub" style="font-size:var(--fs-sm)">'+line+'</div>':'')+
    '<div style="height:3px;border-radius:2px;background:var(--border);overflow:hidden;margin-top:6px">'+
    '<div style="width:38%;height:100%;background:var(--brand);animation:repairSlide 1.6s ease-in-out infinite"></div></div>';
}
let _repairRunning = false;   // v1.0.356：体检运行态——按钮同位切换「⏹ 取消体检」
// v1.0.373 一键打包并重启（确认式）：自我修复/版本管理页触发——
// 程序 2s 后退出 → 延迟批处理接管（杀进程 → build --force 打包 → 自动启动新 exe）
async function rebuildNow(btn){
  if(btn && btn.disabled) return;
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("打包并重启", "用当前源码打包新版本并重启软件？程序将在 2 秒后退出（未发送的输入会丢失）；打包约 1-2 分钟期间窗口消失属正常；完成后自动启动新版本，打包失败则自动回到当前版本。", async ()=>{
    if(btn){ btn.disabled=true; btn.textContent="⏳ 正在退出并打包…"; }
    try{
      const r=await fetch("/api/repair/rebuild",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({note:"软件内一键打包"})});
      const d=await r.json();
      if(!d.ok){ toast(d.error||"触发失败","err"); if(btn){ btn.disabled=false; btn.textContent="📦 打包并重启"; } return; }
      toast(d.hint||"正在退出并打包，完成后自动重启","ok");
      document.title += "（打包中…）";
    }catch(e){
      toast(fetchErr(e,"一键打包"),"err");
      if(btn){ btn.disabled=false; btn.textContent="📦 打包并重启"; }
    }
  }, "打包并重启");
}
// v1.0.358 版本管理：版本列表与一键切换（重启软件生效）。
// 版数由后端 version_store._KEEP 决定，这里不写数字——写过一次，结果 _KEEP 降到 3 之后
// 这句还是旧的，直到 v1.0.615 才被 copy-truth 锁查出来（js/ 当时还不在扫描面内）。
async function loadVersionList(){
  const box=$("versionListBox"); if(!box) return;
  try{
    const d=await getJson("/api/versions");
    const cur=d.current||"";
    const vs=d.versions||[];
    // v1.0.373（体验B5）：软件本体更新提示——产物库里有比当前运行版本更新的构建
    //（版本管理切换走 staged copy，重启生效），置顶横幅一键切换
    const latest = vs[0];
    let banner = "";
    if(latest && latest.version && d.current && latest.version !== d.current){
      banner = '<div style="margin:0 0 8px;padding:9px 12px;border:1px solid var(--accent);background:var(--accent-soft);border-radius:var(--r-md);font-size:var(--fs-sm);display:flex;align-items:center;gap:8px">'+
        '<span>🆕 检测到新版本 <b>v'+escapeHtml(latest.version)+'</b> 已构建（当前运行 v'+escapeHtml(d.current)+'）</span>'+
        '<button class="primary smbtn" style="margin-left:auto" onclick="switchVersion(\''+jsStr(latest.version)+'\')">⏬ 切换到新版本</button></div>';
    }
    if(!vs.length){ box.innerHTML='<div class="sub">还没有版本记录——下次打包构建（build.py --yes）后自动登记。</div>'; return; }
    box.innerHTML=banner+'<div style="font-size:var(--fs-sm)">'+vs.map(v=>{
        const isCur=v.version===cur;
        const t=new Date((v.ts||0)*1000);
        const tm=isFinite(t.getTime())?((t.getMonth()+1)+"-"+t.getDate()+" "+String(t.getHours()).padStart(2,"0")+":"+String(t.getMinutes()).padStart(2,"0")):"";
        const name=isCur?'<b style="color:var(--brand-fg)">'+escapeHtml(v.version)+'（当前）</b>':escapeHtml(v.version);
        const btn=isCur?'':'<button class="ghost smbtn" onclick="switchVersion(\''+jsStr(v.version)+'\')">⏪ 切换到此版</button>';
        // v1.0.360：版本介绍——相对上一版的提交变更清单（最多 10 条）
        const chs=(v.changes&&v.changes.length)
          ? '<div style="margin:3px 0 2px;padding-left:6px;border-left:2px solid var(--border);color:var(--muted);font-size:var(--fs-xs);line-height:1.55">'+
            v.changes.map(c=>'• '+escapeHtml(c)).join('<br/>')+'</div>'
          : ((v.note&&(!v.changes||!v.changes.length))?'<div style="margin:3px 0 2px;padding-left:6px;border-left:2px solid var(--border);color:var(--muted);font-size:var(--fs-xs)">'+escapeHtml(v.note)+'</div>':'');
        return '<div style="padding:8px 4px;border-top:1px solid var(--border)">'+
          '<div style="display:flex;align-items:center;gap:8px"><span>'+name+'</span>'+
          '<span style="color:var(--muted);font-size:var(--fs-xs)">'+tm+'</span>'+
          '<span style="margin-left:auto">'+btn+'</span></div>'+chs+'</div>';
      }).join("")+'</div>';
  }catch(e){ box.innerHTML='<div class="sub">加载失败：'+escapeHtml(fetchErr(e,"版本列表"))+'</div>'; }
}
async function switchVersion(ver){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("切换版本", "确定切换到 " + ver + " ？当前版本会先自动快照（可随时切回）；源码与程序文件同步回退，重启软件后生效。", async ()=>{
    try{
      const d=await apiToast("/api/versions/switch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({version:ver})},"版本切换");
      toast("已切换到 "+ver+"（"+(d.detail||"")+"）","ok");
      loadVersionList();
    }catch(_){}
  }, "切换");
}
// v1.0.788（P0-1）发版自检：更新检查只走 GitHub Releases（用户确认口径）。
// 按钮显式触发，不自动轮询；升级前后端已自动快照当前版（版本管理页可回退）。
async function checkUpdate(){
  const box=$("updateCheckBox"), btn=$("updateCheckBtn");
  if(btn){ btn.disabled=true; btn.textContent="检查中…"; }
  box.innerHTML='<div class="sub">正在查询 GitHub Releases 最新版本…</div>';
  try{
    const d=await getJson("/api/update/check");
    if(!d.ok){ box.innerHTML='<div class="sub" style="color:var(--warn-fg)">'+escapeHtml(d.error||"检查失败")+'</div>'; return; }
    const cur=d.current||"", latest=d.latest||"";
    if(d.update_available){
      const sz=(d.asset&&d.asset.size)?("（约 "+(d.asset.size/1048576).toFixed(1)+" MB）"):"";
      box.innerHTML='<div style="margin:0 0 6px;padding:9px 12px;border:1px solid var(--accent);background:var(--accent-soft);border-radius:var(--r-md);font-size:var(--fs-sm);display:flex;align-items:center;gap:8px">'+
        '<span>🆕 GitHub 有新版本 <b>v'+escapeHtml(latest)+'</b>'+(cur?"（当前运行 v"+escapeHtml(cur)+"）":"")+escapeHtml(sz)+'</span>'+
        '<button class="primary smbtn" style="margin-left:auto" onclick="installUpdate()">⏬ 下载并安装</button></div>';
    }else if(d.comparable){
      box.innerHTML='<div class="sub">✅ 当前 v'+escapeHtml(cur)+' 已是最新版本（GitHub Releases v'+escapeHtml(latest)+'）</div>';
    }else{
      box.innerHTML='<div class="sub">最新版本 v'+escapeHtml(latest)+'（版本号无法与当前对比，请人工核对）</div>';
    }
  }catch(e){
    box.innerHTML='<div class="sub" style="color:var(--warn-fg)">检查失败：'+escapeHtml(fetchErr(e,"更新检查"))+'</div>';
  }finally{
    if(btn){ btn.disabled=false; btn.textContent="🔍 检查更新（GitHub Releases）"; }
  }
}
async function installUpdate(){
  confirmAsk("一键升级", "将从 GitHub Releases 下载最新版并替换（当前版本会自动快照，可在版本管理页随时回退），替换完成后需重启软件。继续？", async ()=>{
    const box=$("updateCheckBox"), btn=$("updateCheckBtn");
    if(box) box.innerHTML='<div class="sub">正在下载最新版，视网速需 1-3 分钟…</div>';
    if(btn){ btn.disabled=true; btn.textContent="安装中…"; }
    try{
      const r=await fetch("/api/update/install",{method:"POST"});
      const d=await r.json();
      if(!d.ok){ toast(d.error||"安装失败","err"); loadVersionList(); return; }
      toast("v"+d.version+" 已下载："+(d.detail||"")+" 请重启软件","ok");
      loadVersionList();
    }catch(e){ toast(fetchErr(e,"安装新版本"),"err"); }
    finally{ if(btn){ btn.disabled=false; btn.textContent="🔍 检查更新（GitHub Releases）"; } }
  }, "下载并安装");
}
// v1.0.788（P1-5）一键自诊：单页报告 + 一键复制（报告直接发给支持/反馈用）。
async function runDoctor(){
  const box=$("doctorBox"), btn=$("doctorBtn");
  if(!box) return;
  if(btn){ btn.disabled=true; btn.textContent="自诊中…（首次几秒）"; }
  box.innerHTML='<div class="sub">正在自诊…（首次组件探测可能需几秒）</div>';
  try{
    const d=await getJson("/api/doctor");
    doctorRender(d, box);
  }catch(e){
    box.innerHTML='<div class="sub" style="color:var(--warn-fg)">自诊失败：'+escapeHtml(fetchErr(e,"自诊"))+'</div>';
  }finally{
    if(btn){ btn.disabled=false; btn.textContent="🩺 运行自诊（生成可复制的自诊报告）"; }
  }
}
function doctorRender(d, box){
  if(!d||!d.items){ box.innerHTML='<div class="sub" style="color:var(--warn-fg)">自诊数据不完整</div>'; return; }
  window.__lastDoctor=d;
  const rows=(d.items||[]).map(it=>
    '<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0;border-top:1px solid var(--border);font-size:var(--fs-sm)">'+
    '<span>'+(it.ok?'✅':'❌')+'</span>'+
    '<span style="min-width:7em">'+escapeHtml(it.name)+'</span>'+
    '<span style="color:var(--muted)">'+escapeHtml(it.detail||"")+'</span>'+
    '<span style="margin-left:auto;color:var(--muted);font-size:var(--fs-xs)">'+it.ms+'ms</span></div>'
  ).join("");
  const head=d.all_ok
    ? '<b style="color:var(--ok-fg)">✅ 全部正常</b>'
    : '<b style="color:var(--warn-fg)">⚠️ 异常项：'+escapeHtml((d.bad||[]).join("、"))+'</b>';
  box.innerHTML='<div style="margin-bottom:6px">'+head+
    '<span style="color:var(--muted);font-size:var(--fs-xs)"> · 共 '+(d.total_ms||0)+'ms · '+escapeHtml(d.ts||"")+'</span></div>'+rows+
    '<div style="margin-top:6px"><button class="ghost smbtn" onclick="doctorCopy()">📋 复制自诊报告</button></div>';
}
async function doctorCopy(){
  const d=window.__lastDoctor;
  if(!d){ toast("先运行一次自诊再复制","warn"); return; }
  const lines=["Fisheep 自诊报告（"+(d.ts||"")+"，"+(d.all_ok?"全部正常":"异常项："+(d.bad||[]).join("、"))+"）"];
  (d.items||[]).forEach(it=>lines.push((it.ok?"[正常] ":"[异常] ")+it.name+"（"+it.ms+"ms）："+it.detail));
  const txt=lines.join("\n");
  try{
    await navigator.clipboard.writeText(txt);
    toast("自诊报告已复制到剪贴板","ok");
  }catch(_){
    window.prompt("自动复制失败，手动复制：", txt);
  }
}
// ---- v1.0.788（P1-6）迁移包：配置/经验/技能/知识库 打包换机或备份 ----
// 导入流程：file input 的 File 对象没有本机路径（WebView2 无 File.path），
// 先走通用 /api/workspace/upload 落盘拿到服务端绝对路径，再交给 /api/migration/import。
window.__migrationZip=null;
async function migrationExport(btn){
  const box=$("migrationBox");
  if(box) box.innerHTML='<div class="sub">正在打包 产物库（配置+经验+技能+知识库）…</div>';
  if(btn){ btn.disabled=true; btn.textContent="打包中…"; }
  try{
    const d=await postJson("/api/migration/export",{});
    if(!d.ok){ migrationRenderErr(box,d.error||"导出失败"); return; }
    const items=d.items||{};
    box.innerHTML='<div class="sub" style="color:var(--ok-fg)">✅ 迁移包已导出（'+(d.size_kb||0)+' KB）：'+
      escapeHtml(d.path||"")+'</div>'+
      '<div class="sub">内容：配置 '+escapeHtml(items.config||"")+' / 经验 '+(items.experiences??0)+
      ' 条 / 技能 '+(items.custom_skills??0)+' 条 / 知识库 '+(items.kb_files??0)+' 个文件</div>'+
      '<div class="sub" style="color:var(--muted)">拷走这个 zip 即可换机导入（导入时配置里的绝对路径会自动归一）</div>';
    toast("迁移包已导出","ok");
  }catch(e){
    migrationRenderErr(box,fetchErr(e,"迁移包导出"));
  }finally{
    if(btn){ btn.disabled=false; btn.textContent="📦 导出迁移包"; }
  }
}
function migrationPickFile(input){
  const f=input.files&&input.files[0];
  const nm=$("migrationPickName");
  if(!f){ return; }
  input.value="";
  if(!/\.zip$/i.test(f.name||"")){ toast("请选择 zip 格式的迁移包","warn"); if(nm) nm.textContent=""; return; }
  if(nm) nm.textContent="上传中…";
  const fd=new FormData(); fd.append("file", f);
  (async()=>{
    try{
      const r=await fetch("/api/workspace/upload",{method:"POST",body:fd});
      const d=await r.json();
      if(!d.ok){ toast("上传失败："+(d.error||"未知错误"),"err"); if(nm) nm.textContent=""; return; }
      window.__migrationZip={path:d.path,name:d.filename||f.name};
      if(nm) nm.textContent="已就绪："+(d.filename||f.name);
      toast("迁移包已就绪，选择下方导入方式","ok");
    }catch(e){
      if(nm) nm.textContent="";
      toast("上传失败："+e.message,"err");
    }
  })();
}
function migrationImport(mode){
  const box=$("migrationBox");
  const zip=window.__migrationZip;
  if(!zip){ toast("请先「选择 zip」上传迁移包","warn"); return; }
  const msg=mode==="overwrite"
    ? "以迁移包为准<b>覆盖本地</b>：配置整体替换、经验/技能整替、知识库文件逐个替换。覆盖前会自动时间戳备份（保留 3 份），但仍建议先确认本地没有未备份的新改动。继续？"
    : "以本地已有数据为准<b>只补缺</b>：本地已有的配置/条目/文件不动，只写入包里多出来的。继续？";
  confirmAsk("导入迁移包（"+(mode==="overwrite"?"覆盖":"跳过已有")+"）", msg, async ()=>{
    if(box) box.innerHTML='<div class="sub">导入中…</div>';
    try{
      const d=await postJson("/api/migration/import",{path:zip.path,conflict:mode});
      migrationRender(box,d);
    }catch(e){
      migrationRenderErr(box,fetchErr(e,"迁移包导入"));
    }
  });
}
function migrationRender(box,d){
  if(!d.ok){ migrationRenderErr(box,d.error||"导入失败"); return; }
  const secs=d.sections||{}, kb=d.kb||{};
  const secTxt=["config","experiences","custom_skills"].filter(k=>secs[k]).map(k=>
    escapeHtml(k)+"："+escapeHtml(secs[k])).join("<br/>");
  const bak=(d.backed_up||[]).length;
  box.innerHTML='<div class="sub" style="color:var(--ok-fg)">✅ 导入完成（'+escapeHtml(d.conflict)+
    ' 模式）'+(bak?"，自动备份 "+bak+" 份":"")+'</div>'+
    '<div class="sub">'+secTxt+'</div>'+
    '<div class="sub">知识库：新拷 '+(kb.copied??0)+' / 跳过 '+(kb.skipped??0)+' / 覆盖 '+(kb.replaced??0)+'</div>';
  toast("迁移包导入完成","ok");
}
function migrationRenderErr(box,msg){
  if(box) box.innerHTML='<div class="sub" style="color:var(--warn-fg)">❌ '+escapeHtml(msg||"")+'</div>';
  toast("迁移包操作失败","err");
}
function repairBtnClick(){
  if(_repairRunning){ cancelSelfRepair(); } else { runSelfRepair(); }
}
async function cancelSelfRepair(){
  try{
    const d = await postJson("/api/repair/cancel",{});
    toast(d.was_running ? "已请求取消，正在中断模型调用…" : "当前没有正在运行的体检", d.was_running ? "ok" : "warn");
  }catch(e){ toast(fetchErr(e,"取消体检"),"err"); }
}
async function runSelfRepair(){
  const btn=$("repairRunBtn"); const box=$("repairResult");
  _repairRunning = true;
  window.__repairPollAborted = false;   // v1.0.471（UI#9）：新一轮体检复位轮询取消标志
  if(btn){ btn.disabled=false; btn.textContent="⏹ 取消体检"; btn.classList.remove("primary"); btn.classList.add("ghost"); }
  if(box){
    box.style.display="block";
    renderRepairProgress(box, {phase:"collect", tokens:0, elapsed_s:0});
  }
  try{
    const startTs=Math.floor(Date.now()/1000)-2;   // 容忍 2s 时钟偏差
    const r=await fetch("/api/repair/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({background:true})});
    const d=await r.json();
    if(!d.ok || !d.started) throw new Error(d.error||"启动失败");
    // 双通道轮询：progress.json（1.5s，实时字符数/阶段）+ last_run.json（完成/失败判定）。
    // 轮询窗 15 分钟——超时给非终态提示（后台线程会继续跑完落盘报告，可手动刷新）。
    // v1.0.471（UI#9）：轮询可取消——离开设置视图（cleanupViewPollers）或 DOM 已移除
    // 时停止空转，原实现循环固定空转到 15 分钟 deadline。
    const deadline=Date.now()+15*60*1000;
    let last=null;
    while(Date.now()<deadline){
      if(window.__repairPollAborted || (box && !box.isConnected)) break;
      await new Promise(res=>setTimeout(res,1500));
      if(window.__repairPollAborted) break;
      try{
        // v1.0.655 B6：last+progress 合并为 /repair/status 一次往返（原两趟轮询）
        const rs=await getJson("/api/repair/status?t="+Date.now());
        const lr=rs.last||{};
        if((lr.ts||0)>=startTs){ last=lr; break; }
        if(box && box.isConnected) renderRepairProgress(box, rs.progress||{});
      }catch(e){}
      if(btn && btn.textContent.indexOf("…")<0) btn.textContent="🩺 体检中…";
    }
    if(box){
      if(!last){
        box.innerHTML='<div class="note">⏳ 体检仍在后台进行（模型输出较慢，已等待 15 分钟——推理型模型属正常）。'+
          '后台会继续跑完并把结果写入报告，点「📁 查看/刷新结果」随时查看；期间不必重新点击体检。</div>'+
          '<div class="row" style="margin-top:6px"><button class="ghost smbtn" onclick="viewLatestRepairReport()">'+((typeof icAny==="function")?icAny("refresh"):"")+' 查看/刷新结果</button></div>';
      }else if(last.cancelled){
        box.innerHTML='<div class="note">⏹ 体检已取消（'+escapeHtml(last.error||"已被用户取消")+'）。可随时重新点「🩺 立即体检」。</div>';
      }else if(last.ok){
        const ld=(await getJson("/api/repair/status?t="+Date.now())).latest||{};   // v1.0.655 B6
        const fx=(last.autofix&&last.autofix.length)
          ? '<div style="margin-top:4px;font-size:var(--fs-sm)">'+last.autofix.map(a=>{
              const ic={committed:"✅",applied:"✅",reverted:"↩️",rejected:"⛔",error:"❌"}[a.status]||"•";
              return '<div>'+ic+' [自动修复·'+escapeHtml(a.status||"")+'] '+escapeHtml(a.finding||"")+(a.file?('（'+escapeHtml(a.file)+'）'):'')+'</div>';
            }).join("")+'</div>' : '';
        // v1.0.373（优化2）：自动修复已提交 → 修复的是源码，需打包重启才对运行的 exe 生效
        const fxDone=(last.autofix||[]).some(a=>a.status==="committed"||a.status==="applied");
        const rebuildTip=fxDone
          ? '<div style="margin-top:6px;padding:8px 10px;border:1px solid var(--accent);background:var(--accent-soft);border-radius:var(--r-md);font-size:var(--fs-sm);display:flex;align-items:center;gap:8px">'+
            '<span>🔧 修复已写入源码——需重新打包才能对运行中的软件生效</span>'+
            '<button class="primary smbtn" style="margin-left:auto" onclick="rebuildNow(this)">'+((typeof icAny==="function")?icAny("box"):"")+' 打包并重启</button></div>' : '';
        box.innerHTML='<div class="note">✅ 体检完成：自动修复 '+(last.applied||0)+' 条，跳过 '
          +(last.skipped||0)+' 条，待人工确认 '+(last.manual||0)+' 条。'+(ld.name?("报告："+escapeHtml(ld.name)):"")+'</div>'+fx+rebuildTip+
          (ld.content?'<div style="border:1px solid var(--border);border-radius:var(--r-md);padding:8px 10px;margin-top:6px;font-size:var(--fs-sm);max-height:320px;overflow:auto">'+renderRich(ld.content)+'</div>':'');
      }else{
        box.innerHTML='<div class="note">❌ 体检未完成：'+escapeHtml(last.error||"未知原因")+
          '。<br/>常见原因：模型接口不可达/超时。请检查「模型管理」里的接口连通性后重试（会自动换备用模型）。</div>';
      }
    }
    toast(last?(last.cancelled?"体检已取消":(last.ok?"自我体检完成":"体检未完成（见详情）")):"体检后台仍在进行", last?(last.ok?"ok":"warn"):"ok");
  }catch(e){
    const msg=fetchErr(e,"自我体检");
    if(box){ box.style.display="block"; box.innerHTML='<div class="note">体检失败：'+escapeHtml(msg)+'</div>'; }
    toast(msg,"err");
  }finally{
    _repairRunning = false;
    if(btn){ btn.textContent="🩺 立即体检"; btn.classList.remove("ghost"); btn.classList.add("primary"); }
  }
}
async function viewLatestRepairReport(){
  const box=$("repairResult"); if(!box) return;
  box.style.display="block";
  try{
    const ld=(await getJson("/api/repair/status?t="+Date.now())).latest||{};   // v1.0.655 B6
    box.innerHTML=ld.content
      ? '<div class="note">最新报告：'+escapeHtml(ld.name||"")+'</div><div style="border:1px solid var(--border);border-radius:var(--r-md);padding:8px 10px;margin-top:6px;font-size:var(--fs-sm);max-height:320px;overflow:auto">'+renderRich(ld.content)+'</div>'
      : '<div class="sub">还没有体检报告。若刚点过体检，模型可能仍在输出——稍等片刻再点「🔄 查看/刷新结果」。</div>';
  }catch(e){ box.innerHTML='<div class="note">读取失败：'+escapeHtml(fetchErr(e,"报告"))+'</div>'; }
}
async function viewMemory(){
  const anchor=$("opt_ff_memory");
  let box=$("memViewBox");
  if(box){ box.remove(); return; }   // 再点收起
  try{
    const d=await getJson("/api/memory/list");
    box=document.createElement("div");
    box.id="memViewBox";
    box.style.cssText="margin-top:8px;max-height:300px;overflow:auto;background:var(--panel2);border:1px solid var(--border);border-radius:var(--r-md);padding:8px 10px;font-size:var(--fs-sm)";
    if(!(d.items||[]).length){
      box.textContent = "（记忆为空——对话完成后会自动沉淀值得记住的事实）";
    } else {
      d.items.forEach(it=>{
        const row=document.createElement("div");
        row.style.cssText="display:flex;align-items:flex-start;gap:6px;padding:4px 0;border-bottom:1px solid var(--border)";
        // ts 是后端从 MEMORY.md 用 `[^\]]+` 抓出来的自由文本（模型写什么就是什么），
        // 不转义就是残余注入面——同文件别处已统一 escapeHtml（v1.0.616 补齐）。
        const meta = (it.score!=null?`<span style="color:${it.score>=8?"var(--ok-fg)":"var(--warn-fg)"};flex:0 0 auto">${it.score}分</span>`:"")+
          (it.ts?`<span style="color:var(--muted);flex:0 0 auto">${escapeHtml(it.ts)}</span>`:"");
        const txt=document.createElement("span");
        txt.style.cssText="flex:1;overflow-wrap:anywhere";
        txt.textContent=it.text;
        const del=document.createElement("button");
        del.className="ghost"; del.textContent="删"; del.style.minHeight="20px"; del.style.padding="0 6px"; del.style.fontSize="var(--fs-xs)";
        del.onclick=()=>{
          // v1.0.471（UI#6）：原生 confirm → confirmAsk
          confirmAsk("删除记忆", "删除这条记忆？", async ()=>{
            try{
              const rr=await fetch("/api/memory/delete_line",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idx:it.idx})});
              const dd=await rr.json();
              if(dd.ok){ toast("已删除","ok"); row.remove(); if(!dd.remaining){ box.textContent="（记忆已清空）"; } }
              else toast(dd.error||"删除失败","err");
            }catch(_){ toast("删除失败","err"); }
          }, "删除");
        };
        row.appendChild(meta.tagName?meta:document.createElement("span")).innerHTML=meta;
        row.appendChild(txt); row.appendChild(del);
        box.appendChild(row);
      });
    }
    if(anchor) anchor.closest("label.field").appendChild(box);
  }catch(e){ toast(fetchErr(e,"读取记忆"),"err"); }
}
async function clearMemory(){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("清空本地记忆", "清空全部本地记忆？此操作不可恢复（建议先查看确认）。", async ()=>{
    try{
      const d=await apiToast("/api/memory/clear",{method:"POST"},"清空记忆");
      toast(`已清空 ${d.removed} 条记忆`,"ok");
      const box=$("memViewBox"); if(box) box.remove();
    }catch(_){}
  }, "清空");
}
// ---- 用户固定规则（v1.0.313 三层记忆之用户级规则） ----
async function loadUserRules(){
  try{
    const d=await getJson("/api/config");
    const el=$("opt_user_rules"); if(el) el.value=d.user_rules||"";
  }catch(e){}
}
async function saveUserRules(btn){
  setBtnLoading(btn,true);
  try{
    await apiToast("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({user_rules:$("opt_user_rules").value})},"保存规则");
    toast("用户规则已保存（下次对话生效）","ok");
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
// ---- 定时任务（v1.0.313）：CRUD + 立即跑 ----
async function loadSchedules(){
  const list=$("schedList"); if(!list) return;
  try{
    const d=await getJson("/api/schedules");
    const items=d.schedules||[];
    list.innerHTML="";
    if(!items.length){ list.innerHTML='<div class="sub">暂无定时任务——用下方表单添加。</div>'; return; }
    items.forEach(s=>{
      const row=document.createElement("div");
      row.style.cssText="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border);flex-wrap:wrap";
      const dot=s.running?'<span class="pill wrn">执行中…</span>':(s.enabled?'<span class="pill ok">启用</span>':'<span class="pill">已停用</span>');
      // v1.0.614：上次结果的状态要看得见。超时中止 / 中途要确认，过去只写成一句文本、
      // 徽标一律"启用"，用户第二天以为都跑完了（判据落在 scheduler.last_status 上）。
      const _bad={timeout:"⚠️ 超时中止", blocked:"⚠️ 待确认未完成", error:"❌ 执行异常"}[s.last_status||""];
      const lastTag=_bad?` <span class="pill err" title="${escapeHtml(s.last_result||"")}">${_bad}</span>`:"";
      // v1.0.667（v1.0.666 审查 P1-3）：坏状态不再截 60 字——blocked 的 note 里
      // 写着"卡在哪一步"，截断后用户只能删任务重建。坏状态给完整 note，
      // blocked 额外指向待审批队列（P0-1 新入口）；成功态维持摘要。
      const _isBad=!!_bad;
      const _resLine=s.last_run?`<br/>上次：${escapeHtml(s.last_run)} · ${escapeHtml(_isBad?(s.last_result||""):(s.last_result||"").slice(0,60))}`:"";
      const _blockedHint=_isBad&&s.last_status==="blocked"?`<br/><span style="color:var(--warn-fg)">被拦下的操作见「安全中心 → 后台待审批」，可逐条批准/驳回；或点「立即跑」重触发。</span>`:"";
      row.innerHTML =
        `<b>${escapeHtml(s.name)}</b><code>${escapeHtml(s.cron)}</code>${dot}${lastTag}`+
        `<span class="sub" style="flex:1;min-width:200px;font-size:var(--fs-xs)">${escapeHtml((s.prompt||"").slice(0,60))}${_resLine}${_blockedHint}</span>`;
      // v1.0.742（界面审查 P2-1）：textContent→innerHTML（要嵌统一 SVG 线稿）；▶/✎/🗑 换图标；
      // 纯图标删除钮补 danger + title + aria-label（同 skills/connectors 口径）
      const _icS=(typeof icon==="function")?icon:(n=>"");
      const mkBtn=(txt,fn,primary)=>{ const b=document.createElement("button"); b.className=primary?"primary":"ghost"; b.style.padding="3px 10px"; b.style.fontSize="var(--fs-xs)"; b.innerHTML=txt; b.onclick=fn; return b; };
      row.appendChild(mkBtn(s.enabled?"停用":"启用",()=>toggleSchedule(s.id,!s.enabled)));
      row.appendChild(mkBtn(_icS("play")+" 立即跑",()=>runScheduleNow(s.id)));
      // v1.0.667（审查 P2-7）：编辑——改名称/提示词/时间走 upsert（带 id），不丢历史记录
      row.appendChild(mkBtn(_icS("edit")+" 编辑",()=>editSchedule(s.id)));
      const _delS=mkBtn(_icS("trash"),()=>delSchedule(s.id));
      _delS.classList.add("danger"); _delS.title="删除"; _delS.setAttribute("aria-label","删除定时任务");
      row.appendChild(_delS);
      list.appendChild(row);
    });
  }catch(e){ list.innerHTML='<div class="sub">加载失败：'+escapeHtml(e.message||"")+'</div>'; }
}
let __schedEditId=null;   // v1.0.667（审查 P2-7）：非空=正在编辑既有任务（带 id 走 upsert 更新）
async function saveSchedule(btn){
  const name=$("sched_name").value.trim(), prompt=$("sched_prompt").value.trim();
  if(!name||!prompt){ toast("任务名与提示词必填","warn"); return; }
  const body={name,prompt,cron:$("sched_cron").value,enabled:true};
  if(__schedEditId) body.id=__schedEditId;   // 有 id 即更新（后端 upsert 语义）
  setBtnLoading(btn,true);
  try{
    const isEdit=!!__schedEditId;
    await apiToast("/api/schedules",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)},isEdit?"修改定时任务":"添加定时任务");
    toast(isEdit?"定时任务已修改":"定时任务已添加","ok");
    cancelScheduleEdit();
    loadSchedules();
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
async function editSchedule(id){
  // v1.0.667（审查 P2-7）：改提示词/时间不再删了重建（历史记录/last_run 会丢）。
  let d;
  try{ d=await getJson("/api/schedules"); }catch(_){ return; }
  const it=(d.schedules||[]).find(s=>s.id===id); if(!it) return;
  $("sched_name").value=it.name; $("sched_prompt").value=it.prompt;
  const sel=$("sched_cron");
  // 既有任务的 cron 可能不在下拉固定项里（自定义 daily 08:30 等）→ 动态补一个 option
  if(sel && !Array.from(sel.options).some(o=>o.value===it.cron)){
    const o=document.createElement("option"); o.value=it.cron; o.textContent=it.cron; sel.appendChild(o);
  }
  if(sel) sel.value=it.cron;
  __schedEditId=id;
  const b=$("schedSaveBtn"); if(b) b.innerHTML=((typeof icAny==="function")?icAny("save"):"")+" 保存修改";
  const cb=$("schedCancelBtn"); if(cb) cb.style.display="";
  const list=$("schedList"); if(list) list.scrollIntoView({behavior:"smooth",block:"start"});
}
function cancelScheduleEdit(){
  __schedEditId=null;
  const n=$("sched_name"),p=$("sched_prompt"); if(n) n.value=""; if(p) p.value="";
  const b=$("schedSaveBtn"); if(b) b.innerHTML=((typeof icAny==="function")?icAny("plus"):"")+" 添加";
  const cb=$("schedCancelBtn"); if(cb) cb.style.display="none";
}
// v1.0.471（UI#13）：启停/删除/立即跑补错误处理（原 await fetch 裸奔，失败静默）
async function toggleSchedule(id,enabled){
  try{
    await apiToast(`/api/schedules/${id}/enabled`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled})},"切换定时任务");
    loadSchedules();
  }catch(_){}
}
async function delSchedule(id){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk（统一弹窗风格）
  confirmAsk("删除定时任务", "删除后该任务不再执行且不可恢复。", async ()=>{
    try{
      await apiToast(`/api/schedules/${id}`,{method:"DELETE"},"删除定时任务");
      loadSchedules(); toast("定时任务已删除","ok");
    }catch(_){}
  }, "删除");
}
async function runScheduleNow(id){
  try{
    await apiToast(`/api/schedules/${id}/run`,{method:"POST"},"立即执行定时任务");
    toast("已触发后台执行，结果稍后见 产物库/产出/","ok");
    setTimeout(loadSchedules, 2500);
  }catch(_){}
}
// ---- OCR 组件按需在线安装（v1.0.307）：状态展示 + 手动触发 + 下载中轮询 ----
var __ocrPolling = false;
// v1.0.660（审查修复 P2）：轮询定时器句柄 + 世代号。OCR 状态此前有**两个**轮询入口
// （pollOcrStatus 自续链 + renderOcrStatus 内的自启链），二者互不知情 →
// 重复进视图/重复点安装会叠加多条链，请求量翻倍、状态显示还会互相覆盖。
// 改为：全项目只有一条链；每次启动先作废旧链（世代号自增）并 clear 旧 timer。
var __ocrPollTimer = null;
var __ocrPollEpoch = 0;      // 世代号：在途请求回来时若世代已变 → 丢弃结果并停止续链
var __ocrInFlight = 0;       // 在途请求计数（>0 表示正在 pollOcrStatus 内）
function _ocrPollStart(){
  __ocrPolling = true;
  pollOcrStatus();          // 不带 epoch = 重开一条链（内部会清 timer、自增世代）
}
function _ocrPollStop(){
  __ocrPollEpoch++;                                   // 作废在途链
  if(__ocrPollTimer){ clearTimeout(__ocrPollTimer); __ocrPollTimer=null; }
  __ocrPolling = false;
}
// v1.0.473 组件体检：外部组件状态汇总（/api/components/status；v1.0.611 起含语音组件，共七类）
// v1.0.473（用户反馈：体检后没有具体方案）——渲染结构化 actions：
//   api  → POST /api/components/fix（软件内一键完成，如 OCR 安装/dsh 安装/OCR 升级）
//   link → 系统浏览器打开下载页（Git/Node 等系统级组件）
//   cmd  → 复制命令到剪贴板（用户在终端执行后重新体检）
var __compPoll = null;   // v1.0.606：有组件在「安装中」时的 3s 自动刷新定时器（切视图即停）
// v1.0.737（视觉审查 ⑥）：组件列表空/体检失败时复位统一引导卡，
// 不再留下半页纯留白。desc 是受信任 HTML 片段（调用方自行 escapeHtml 动态部分）。
function compEmptyHtml(title, desc){
  const icSvg=(typeof icAny==="function")?icAny("cpu"):"";
  return '<div class="comp-empty" id="compEmpty">'+icSvg+'<b>'+escapeHtml(title)+'</b><p>'+desc+'</p></div>';
}
async function loadComponents(btn){
  const box=$("compList"); if(!box) return;
  if(__compPoll){ clearTimeout(__compPoll); __compPoll=null; }
  if(btn){ setBtnLoading(btn,true); }
  try{
    const d=await api("/api/components/status");
    window.__compData=d;   // v1.0.607：一键安装要从同一份数据里挑「软件内可自动装」的项
    const need=(d.components||[]).filter(c=>c.status!=="ok"&&c.status!=="na").length;   // v1.0.619：na=本机不适用，不算待处理
    // v1.0.650 U6：汇总行以前说「全部就绪」，行内同一条目又叫「可用」——同一页两口径。
    $("comp_summary").textContent=`${d.ok_n}/${d.total} 项${STATUS_WORD.ok}${need?` · ${need} 项待处理`:` · 全部${STATUS_WORD.ok}`}${d.na_n?` · ${d.na_n} 项${STATUS_WORD.na}`:""} · ${d.checked_at}`;
    const ic=STATUS_ICON, st=STATUS_WORD;   // v1.0.508 规范 → v1.0.650 收敛到 js/01-core.js 的单一事实源
    box.innerHTML=d.components.map(c=>{
      const acts=(c.actions||[]).map((a,ai)=>{
        if(a.type==="api") return `<button class="primary smbtn" data-ck="${escapeHtml(c.key)}" data-ai="${ai}">${escapeHtml(a.label)}</button>`;
        if(a.type==="link") return `<button class="ghost smbtn" data-cl="${escapeHtml(a.url)}">${escapeHtml(a.label)}</button>`;
        if(a.type==="cmd") return `<button class="ghost smbtn" data-cc="${escapeHtml(a.cmd)}" title="${escapeHtml(a.cmd)}">${escapeHtml(a.label)}</button>`;
        return "";
      }).join(" ");
      // v1.0.607 清爽化：就绪项就一行（图标 + 名称 + 版本），不再铺一段"它能干什么"的说明；
      // 只有待处理项才展开描述/提示/按钮——需要看的注意力才给视觉空间。
      const quiet = c.status==="ok" && !acts;
      return `
      <div class="comp-item${quiet?" comp-quiet":""}">
        <span style="flex:0 0 auto">${ic[c.status]||STATUS_ICON.unknown}</span>
        <div class="comp-main">
          <div><b class="comp-name">${escapeHtml(c.name)}</b>
            <span class="tag ${c.status==="ok"?"def":(c.status==="outdated"?"judge":"dis")}">${st[c.status]||c.status}</span>
            ${c.version?`<span class="comp-ver">${escapeHtml(String(c.version).slice(0,60))}</span>`:""}
          </div>
          ${quiet?"":`<div class="comp-desc" title="${escapeHtml([c.desc,c.hint].filter(Boolean).join(' · '))}">${escapeHtml(c.desc||"")}${c.hint?`<span class="comp-sep">·</span>${escapeHtml(c.hint)}`:""}</div>`}
          ${acts?`<div class="comp-acts">${acts}</div>`:""}
        </div>
      </div>`;}).join("");
    if(!box.innerHTML.trim()) box.innerHTML=compEmptyHtml("暂无组件数据","点上方「重新体检」重新获取本机组件状态。");
    // 事件委托：api/link/cmd 三类动作
    box.querySelectorAll("[data-ck]").forEach(b=>{
      b.onclick=async()=>{
        const comp=d.components.find(x=>x.key===b.dataset.ck);
        const a=comp && (comp.actions||[])[parseInt(b.dataset.ai,10)];
        if(!a) return;
        if(a.confirm && !confirmAsk("确认操作", a.confirm, ()=>_compFix(b, comp, a), "继续")) return;
        if(!a.confirm) _compFix(b, comp, a);
      };
    });
    box.querySelectorAll("[data-cl]").forEach(b=>{
      b.onclick=()=>{ try{ window.pywebview.api.open_external(b.dataset.cl); }catch(e){ window.open(b.dataset.cl, "_blank"); } };
    });
    box.querySelectorAll("[data-cc]").forEach(b=>{
      b.onclick=async()=>{ try{ await navigator.clipboard.writeText(b.dataset.cc); toast("命令已复制——在构建环境终端执行后重新体检","ok",3500); }catch(e){ toast("复制失败，请手动选择命令文本","err"); } };
    });
    // v1.0.606：dsh / OCR 的安装是后台任务——列表里有「安装中」就 3 秒后自己再取一次，
    // 用户不必反复点体检；全部落定就停轮询（切走视图由 cleanupViewPollers 兜底清）
    if((d.components||[]).some(c=>c.status==="installing")){
      __compPoll=setTimeout(()=>{ loadComponents(); }, 3000);
    }
  }catch(e){
    box.innerHTML=compEmptyHtml("体检失败", escapeHtml(fetchErr(e,"组件体检"))+"<br/>稍后重试；持续失败请到「设置 → 打开日志文件夹」查看 companion.log");
  }
  finally{ if(btn) setBtnLoading(btn,false); }
}
async function _compFix(btn, comp, a){
  setBtnLoading(btn,true);
  try{
    const d=await api(a.api,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(a.body||{})});
    if(comp.key==="ocr"){
      toast(d.msg||"OCR 组件安装已开始","ok",3000); ocrInstallPollProgress();
    } else if(d.ok===false){
      toast(d.error||"操作失败","err",4000);
    } else {
      toast(d.msg||"操作完成——点击「立即体检」刷新状态","ok",3500);
    }
    if(comp.key!=="ocr" && !d.already_installed){ setTimeout(()=>loadComponents(), 1500); }
  }catch(e){ toast(fetchErr(e,"组件修复"),"err"); }
  finally{ setBtnLoading(btn,false); }
}
// v1.0.607 一键安装：把「软件内能自动完成」的组件一次跑完。判据用后端给的 actions——
// 只认 `type==="api" && kind==="install"`（OCR 按需下载 / dsh npm 安装 / 打包环境 pip 安装）；
// kind=update（更新到最新版，实验性）与 kind=check（自检）**不参与一键**，Git、Node 这类
// 系统安装器（type=link/cmd）更不混进来假装能装，只在确认文案里如实列出仍需手动的项。
function _instAction(c){
  return ((c && c.actions) || []).find(a => a.type === "api" && a.kind === "install") || null;
}
async function installAllComponents(btn){
  const comps=((window.__compData||{}).components)||[];
  if(!comps.length){ toast("还没有体检结果，先点「重新体检」","warn",3000); return; }
  const _todo=(c)=>c.status!=="ok"&&c.status!=="na"&&_instAction(c);      // v1.0.619：na 不参与一键安装
  const _manual=(c)=>c.status!=="ok"&&c.status!=="na"&&!_instAction(c);
  const todo=comps.filter(_todo);
  const manual=comps.filter(_manual);
  if(!todo.length){
    toast(manual.length ? ("没有可自动安装的组件；还需手动处理：" + manual.map(c=>c.name).join("、"))
                        : "组件都已就绪，无需安装", "warn", 4500);
    return;
  }
  const _oc = comps.find(c=>c.key==="ocr") || {};
  // 体积数字来自后端 size_hint（core/ocr.py 实测口径），前端不再自抄一份——抄过就漏改过。
  const _sz = (_oc.size_hint && todo.some(c=>c.key==="ocr")) ? "。OCR " + _oc.size_hint : "";
  const _pf = todo.some(c=>c.key==="node"||c.key==="git") ? "；Node/Git 装软件内便携版（不写系统 PATH）" : "";
  confirmAsk("一键安装运行组件",
    "将依次自动安装：" + todo.map(c=>c.name).join("、") + _sz + _pf +
    "，每项约 1-10 分钟，进度在本页下方逐条显示，期间请不要关闭软件。" +
    (manual.length ? ("这几项软件装不了、仍需手动：" + manual.map(c=>c.name).join("、") + "。")
                   : ("前置条件也一并自动装好，不需要你先去装任何东西。")),
    ()=>_compInstallRun(btn, todo), "开始安装");
}
async function _compInstallRun(btn, todo){
  const box=$("compProgress");
  if(box){ box.style.display="block"; box.innerHTML='<span class="pp-run">⏳ 准备中…</span>'; }
  if(btn) btn.disabled=true;
  const lines=[]; let okN=0, errN=0;
  for(let i=0;i<todo.length;i++){
    const c=todo[i];
    const a=_instAction(c)||{};
    lines.push('<span class="pp-run">⏳ '+(i+1)+'/'+todo.length+'　' + escapeHtml(c.name) + ' 处理中…</span>');
    if(box) box.innerHTML=lines.join("<br/>");
    let res={};
    try{
      res=await api(a.api||"/api/components/fix",{method:"POST",headers:{"Content-Type":"application/json"},
                                                 body:JSON.stringify(a.body||{key:c.key})});
    }catch(e){ res={ok:false, error:fetchErr(e,c.name)}; }
    lines.pop();
    const good = res.ok!==false && !res.__error__;
    if(good){ okN++; lines.push('✅ ' + escapeHtml(c.name) + '：' + escapeHtml(String(res.msg||res.note||"已开始/已完成").slice(0,70)));
      if(c.key==="ocr" && typeof ocrInstallPollProgress==="function"){ try{ ocrInstallPollProgress(); }catch(e){} } }
    else { errN++; lines.push('<span class="pp-err">❌ ' + escapeHtml(c.name) + '：' +
      escapeHtml(String(res.error||res.__error__||res.reason||"失败").slice(0,400)) + '</span>'); }
    if(box) box.innerHTML=lines.join("<br/>");
  }
  if(box) box.innerHTML=lines.join("<br/>") + '<br/>合计：成功 ' + okN + '，失败 ' + errN + '。列表稍后自动刷新。';
  if(btn) btn.disabled=false;
  toast("一键安装跑完：成功 " + okN + " / 失败 " + errN, errN?"warn":"ok", 4500);
  setTimeout(()=>loadComponents(), 1200);
}

// ---- v1.0.704（组件提醒）：提醒总开关 + 首跑体检提醒 + 任务缺组件提醒 ----
// 总开关（config component_reminder.enabled）：「每次遇到上述情况提醒」/「不再提醒」。
// 首跑提醒只提示一次（first_run_seen）；任务缺组件提醒由后端在「任务调用点发现组件未安装」
// 分支记需求事件（OCR/语音/dsh 三处），前端搭状态轮询链取走弹窗。
let __compRemBusy = false;   // 提醒弹窗在屏——防轮询重复弹
async function loadCompReminder(){
  const rds=[].slice.call(document.querySelectorAll('input[name="compRemMode"]'));
  if(!rds.length) return;
  // onchange 只在用户真实点选时触发（程序回显 checked 不触发），每次重绑幂等
  rds.forEach(x=>{ x.onchange=()=>{ if(x.checked) compRemSet(x.value); }; });
  rds.forEach(x=>x.disabled=true);
  try{
    const r=await api("/api/components/reminder");
    rds.forEach(x=>{ x.checked=(x.value===(r&&r.enabled?"on":"off")); x.disabled=false; });
  }catch(e){ rds.forEach(x=>x.disabled=true); }
}
async function compRemSet(mode){
  const rds=[].slice.call(document.querySelectorAll('input[name="compRemMode"]'));
  rds.forEach(x=>x.disabled=true);
  try{
    await api("/api/components/reminder",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:mode==="on"?"enable":"disable"})});
    toast(mode==="on"?"组件提醒：每次遇到上述情况提醒":"组件提醒：不再提醒","ok");
  }catch(e){ toast(fetchErr(e,"切换组件提醒"),"err"); }
  finally{ loadCompReminder(); }
}
function _compRemShow(title, intro, items, goLabel){
  const ov=$("compRemOverlay"); if(!ov) return;
  __compRemBusy=true;
  $("compRemTitle").textContent=title;
  $("compRemIntro").innerHTML=intro;
  $("compRemBody").innerHTML=items;
  const go=$("compRemGoBtn"); if(go) go.textContent=goLabel||"去安装";
  ov.classList.add("show");
}
function _compRemHide(){ const ov=$("compRemOverlay"); if(ov) ov.classList.remove("show"); __compRemBusy=false; }
async function compRemAction(act){
  if(!__compRemBusy) return;
  try{
    // 三种动作都先落一个后端动作：不再提醒=关总开关；关闭/去安装=需求事件全部已读
    await api("/api/components/reminder",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:act==="disable"?"disable":"dismiss_all"})});
    if(act==="disable") toast("组件提醒已关闭（不再提醒）——之后可在「运行组件」页重新打开","ok");
  }catch(e){}
  _compRemHide();
  if(act==="go"){ try{ openView("components"); }catch(e){} }
}
// 首跑体检提醒：软件首次使用弹一次（关闭 / 不再提醒），列出未装组件
async function checkCompFirstRun(){
  try{
    const r=await api("/api/components/reminder");
    if(!r || !r.enabled || r.first_run_seen) return;
    const st=await api("/api/components/status");
    // v1.0.705（深检 P2-2）：旧过滤 `!=="ok"&&!=="na"` 把 degraded（可选增强，
    // dsh/git 等，核心可用）也列进「未安装」，首跑弹窗把用户吓去装用不到的组件。
    // 现在：只列 missing，degraded 轻提示一行；三态 intro 对应三种局面。
    const all=st.components||[];
    const miss=all.filter(c=>c.status==="missing");
    const opt=all.filter(c=>c.status==="degraded");
    const rows=[];
    miss.forEach(c=>rows.push('<div class="comp-rem-item"><b>'+escapeHtml(c.name)+'</b><span> · '+(STATUS_WORD[c.status]||c.status)+'</span></div>'));
    if(miss.length){
      opt.forEach(c=>rows.push('<div class="comp-rem-item"><b>'+escapeHtml(c.name)+'</b><span> · '+(STATUS_WORD[c.status]||c.status)+'（可选增强，不装不影响核心功能）</span></div>'));
    }else if(opt.length){
      rows.push('<div class="comp-rem-item">核心组件均已就绪；'+opt.map(c=>escapeHtml(c.name)).join("、")+' 为可选增强，未安装只影响对应功能，可按需装。</div>');
    }else{
      rows.push('<div class="comp-rem-item">组件均已就绪，无需安装。</div>');
    }
    const intro=miss.length
      ? (miss.length+" 个外部组件尚未安装——缺哪项只影响对应增强功能，核心对话与页面操作照常。可到「运行组件」页体检并一键安装：")
      : (opt.length ? "核心外部组件均已就绪，另有 "+opt.length+" 个可选增强未安装（按需即可，不装不影响核心功能）："
                    : "外部组件体检已完成，无需安装。可到「运行组件」页查看详情或按需安装：");
    _compRemShow("首次使用 · 组件体检提醒", intro, rows.join(""), "去体检");
    // first_seen 在**弹出时**置位：用户看到弹窗就算提示过（直接关进程不再追弹；
    // 若之后想彻底关掉提醒，总开关/不再提醒按钮都在，口径一致）
    await api("/api/components/reminder",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:"first_seen"})});
  }catch(e){}
}
// 需求事件轮询：搭 12-init 的状态轮询链（可见 8s / 隐藏 60s），有未读事件才弹
async function pollCompDemands(){
  if(__compRemBusy || document.hidden) return;
  try{
    const r=await api("/api/components/reminder");
    if(!r || !r.enabled || !(r.demands||[]).length) return;
    const items=r.demands.slice(0,5).map(d=>'<div class="comp-rem-item"><b>'+escapeHtml(d.name||d.key)+'</b><span> · '+escapeHtml(d.msg||"")+'</span></div>').join("");
    _compRemShow("组件安装提醒",
      "以下任务需要组件但未安装——可到「运行组件」页一键安装；关闭后继续按现有流程降级使用：", items, "去安装");
  }catch(e){}
}
function ocrInstallPollProgress(){
  // 复用 OCR 区域的 2s 状态轮询（pollOcrStatus），这里确保启动
  // v1.0.660（审查修复 P2）：原实现调的是 ocrPollTick()——**该函数全项目不存在**
  // （只有这一处引用），调用即 ReferenceError，被上层 try/catch 吞掉 →
  // 「一键安装 OCR」后自检/进度条永远不刷新。改为调用真实存在的 pollOcrStatus()。
  if(typeof pollOcrStatus === "function"){ try{ pollOcrStatus(); }catch(e){} }
}
function renderOcrStatus(d){
  const el=$("opt_ocr_status"); if(!el) return;
  const btn=$("optOcrBtn");
  let txt="";
  if(d.state==="running"){ txt=`${STATUS_ICON.installing} ${d.msg}（${d.progress}%）`; }
  else if(d.state==="ready" || d.installed){ txt=`${STATUS_ICON.ok} ${STATUS_WORD.ok}（能不能真识别以下方自检为准）`; }
  else if(d.state==="failed"){ txt=`${STATUS_ICON.err} 安装失败：`+escapeHtml(d.error||"未知错误"); }   // v1.0.741（①）：下行改 innerHTML，错误文本必须转义
  else { txt=`${STATUS_ICON.off} ${STATUS_WORD.off}（碰到 OCR 事务时将自动联网下载，约 81MB / 占用约 226MB）`; }
  el.classList.toggle("st-run", d.state==="running");
  el.classList.toggle("st-err", d.state==="failed");
  // v1.0.607：状态行从「灰底 input」换成纯文本（视图清爽），同时兼容仍是 INPUT 的老节点；
  // 颜色改走 CSS 类（.st-run/.st-err），不再往元素上写 inline style——inline 色会绕过
  // 深色模式与对比度台子的判据。
  // v1.0.741（界面审查 ①）：txt 内是 SVG 串（STATUS_ICON 线稿化）→ 非 INPUT 走 innerHTML；
  // 老 INPUT 节点剥掉标签留纯文本（value 不认 HTML）。
  if(el.tagName === "INPUT") el.value = txt.replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim(); else el.innerHTML = txt;
  if(btn) btn.disabled = (d.state==="running");
  // 下载中 2s 轮询，结束自动停
  // v1.0.660（审查修复 P2）：renderOcrStatus 不再自己另起一条轮询链——它与
  // pollOcrStatus 是两个入口，各自续链会叠加成多条（重复进视图即复现）。
  // 现在只有 pollOcrStatus 一条链；这里仅在"没有链在跑且正在下载"时启动它。
  if(d.state==="running"){
    if(!__ocrPolling && __ocrInFlight===0) _ocrPollStart();
  }else{
    _ocrPollStop();
  }
  if(d.selfcheck) renderOcrSelfCheck(d.selfcheck);   // v1.0.608：状态接口顺带带来自检结论
}
function renderOcrSelfCheck(sc){
  const el=$("opt_ocr_sc"); if(!el) return;
  const secs=(ms)=> ms? (ms/1000).toFixed(1)+" 秒" : "—";
  if(!sc || sc.state==="idle" || sc.state==="running"){
    el.className="comp-ocr-status";
    el.textContent="真实识别自检：未运行（开机 20 秒后自动跑一次，或点「🔁 立即自检」）";
    return;
  }
  if(sc.ok){
    el.className="comp-ocr-status";
    el.textContent="真实识别自检 ✅ 通过（"+secs(sc.ms)+"，读到「"+String(sc.saw||"").replace(/\s+/g," ").slice(0,24)+"」）";
  } else {
    el.className="comp-ocr-status st-err";
    el.textContent="真实识别自检 ❌ 未通过（"+secs(sc.ms)+"）："+String(sc.error||"未知原因");
  }
}
async function ocrSelfCheckNow(btn){
  if(btn){ btn.disabled=true; btn.textContent="自检中…"; }
  try{
    const d=await (await fetch("/api/ocr/selfcheck",{method:"POST"})).json();
    renderOcrSelfCheck(d);
    toast(d.ok ? ("OCR 自检通过（"+((d.ms||0)/1000).toFixed(1)+" 秒）") : ("OCR 自检未通过："+(d.error||"未知原因")), d.ok?"ok":"warn", 4500);
    try{ loadComponents(); }catch(e){}
  }catch(e){ toast("自检请求失败："+(e.message||e),"err"); }
  finally{ if(btn){ btn.disabled=false; btn.textContent="🔁 立即自检"; } }
}
// v1.0.660（审查修复 P2）：轮询链只有这一条。世代号用于作废在途旧链
// （重复点安装/重复进视图时，旧请求回来不再续链、也不再渲染过期状态）。
// 不带 epoch 调用 = "重新开一条链"（先清 timer、再自增世代）。
async function pollOcrStatus(epoch){
  let ep = epoch;
  if(ep===undefined){
    if(__ocrPollTimer){ clearTimeout(__ocrPollTimer); __ocrPollTimer=null; }
    ep = ++__ocrPollEpoch;
  }
  __ocrInFlight++;
  try{
    const d=await getJson("/api/ocr/status");
    if(ep !== __ocrPollEpoch) return;    // 已被更新的链取代 → 丢弃本次结果
    renderOcrStatus(d);
    if(ep !== __ocrPollEpoch) return;    // renderOcrStatus 可能已停链/起新链
    if(d.state==="running") __ocrPollTimer = setTimeout(()=>pollOcrStatus(ep), 2000);
    else _ocrPollStop();
  }catch(e){
    if(ep === __ocrPollEpoch) _ocrPollStop();
  }finally{
    __ocrInFlight--;   // 计数递减（不用布尔，避免旧链的 finally 把新链的状态清掉）
  }
}
async function loadOcrStatus(){
  try{ renderOcrStatus(await getJson("/api/ocr/status")); }catch(e){}
}
async function ocrInstall(){
  // v1.0.660（审查修复 F4）：原来 btn.disabled=true 之后，成功路径只改 textContent，
  // 按钮**永久禁用**——装完再点「重装」没反应。改用 finally 统一恢复。
  const btn=$("optOcrBtn");
  if(btn){ btn.disabled=true; btn.textContent="下载中…"; }
  try{
    renderOcrStatus(await api("/api/ocr/install",{method:"POST"}));
  }catch(e){
    toast("触发安装失败","err");
  }finally{
    // 失败路径也要恢复：否则一次网络抖动就永久锁死这个按钮。
    if(btn){ btn.disabled=false; btn.textContent="立即安装 / 重装"; }
  }
  pollOcrStatus();
}
// ---- OCR 组件持续更新（v1.0.308）：检查更新 / 更新到最新（实验性）/ 恢复推荐版本 ----
async function ocrCheckUpdates(btn){
  if(btn){ btn.disabled=true; btn.textContent="检查中…"; }
  const box=$("opt_ocr_updates");
  try{
    const d=await getJson("/api/ocr/updates");
    if(box){
      box.style.display="block";
      if(!d.packages || !d.packages.length){ box.textContent="⚠️ 检查失败：无法访问镜像"; }
      else{
        box.innerHTML = d.packages.map(function(p){
          const cur = p.installed || (p.state==="missing" ? "未安装" : "未知");
          const latest = p.update_available ? `<b style="color:var(--accent-fg)">${p.latest||"?"}</b>` : (p.latest || "—");
          const mark = p.update_available ? "⬆" : "✅";
          return `${mark} ${p.pkg}：${cur} → ${latest}`;
        }).join("<br/>") + `<br/><b>${d.updates_available ? `共 ${d.updates_available} 个组件可更新` : "全部组件已是最新"}</b>`;
      }
    }
  }catch(e){ toast("检查更新失败——确认网络可访问更新源后重试","err"); }
  if(btn){ btn.disabled=false; btn.textContent="检查更新"; }
}
async function ocrUpdate(mode){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  if(mode==="latest"){
    confirmAsk("更新到最新版", "「更新到最新版」为实验性功能：新版本组合未经本软件回归验证，可能出现不兼容。出问题可用「恢复推荐版本」一键回退到验证过的组合。", ()=>{
      _ocrUpdateGo(mode);
    }, "继续更新");
    return;
  }
  _ocrUpdateGo(mode);
}
async function _ocrUpdateGo(mode){
  try{
    const r=await fetch("/api/ocr/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:mode})});
    const d=await r.json();
    if(d.error){ toast("更新失败："+d.error,"err"); return; }
    toast(d.msg||"已开始更新","ok");
    pollOcrStatus();
  }catch(e){ toast("更新请求失败——确认网络后重试；也可用「恢复推荐版本」回退","err"); }
}

// ---- 知识库（v1.0.294：课件/题库/制度 三分类，独立于产物库扁平树） ----
// v1.0.477：企微聊天文件静默分析（知识库/企微文件/）
// v1.0.498：openWorkspace/openKnowledge 孤儿入口已删——关闭文件预览与企微文件刷新由 openView 直接管
let __warcPoll = null;
async function loadWecomArchive(){
  const box=$("warcStatus"); if(!box) return;
  try{
    const d=await api("/api/wecom_archive/status");
    const bits=[
      `📂 账号 ${d.accounts.length} 个`,
      `📄 源文件 ${d.source_files}`,
      `✅ 已入库 ${d.archived}`,
      d.needs_ocr?`⏳ 待 OCR ${d.needs_ocr}`:"",
      d.last_scan?`🕐 上次扫描 ${d.last_scan}`:"从未扫描",
    ].filter(Boolean);
    box.innerHTML=bits.map(b=>`<span>${escapeHtml(b)}</span>`).join("");
    $("warcNote").textContent=d.note||"";
    // v1.0.588 合规闸：未授权时该功能一律不运行（daemon 与手动扫描都拦），先要一句明确同意
    const btn = $("warcScanBtn");
    const cbox = $("warcConsent");
    const consented = !!d.consent;
    if(cbox){
      cbox.innerHTML = consented
        ? `<span style="color:var(--brand-fg)">✅ 已授权解析本机企业微信文件</span>` +
          `<button class="ghost smbtn" style="margin-left:8px" onclick="wecomArchiveConsent(false)">撤回授权</button>`
        : `<span style="color:var(--warn-fg)">⚠️ 需要授权：本功能会把聊天附件<b>正文内容</b>复制进本机知识库；` +
          `只读明文文件，不解析企业微信加密的消息文本库。</span>` +
          `<button class="primary smbtn" style="margin-left:8px" onclick="wecomArchiveConsent(true)">我已了解，授权并扫描</button>`;
    }
    if(btn){
      // v1.0.742（界面审查 P2-1）：🔒/🔍 emoji → 统一 SVG 线稿（lock/search）；⏳ 属状态文本保留
      btn.disabled = !consented || !!d.running;
      btn.innerHTML = !consented ? ((typeof icon==="function")?icon("lock"):"") + " 未授权"
                     : d.running ? "⏳ 分析中…"
                     : ((typeof icon==="function")?icon("search"):"") + " 立即扫描入库";
    }
    if(d.running){
      if(!__warcPoll) __warcPoll=setTimeout(()=>{ __warcPoll=null; loadWecomArchive(); }, 3000);
    } else if(__warcPoll){ clearTimeout(__warcPoll); __warcPoll=null; }
  }catch(e){
    box.innerHTML='<span style="color:var(--err-fg)">状态获取失败：'+escapeHtml(fetchErr(e,"企业微信文件分析"))+"</span>";
  }
}
async function wecomArchiveScan(){
  const btn=$("warcScanBtn"); if(btn){ btn.disabled=true; btn.innerHTML="⏳ 分析中…"; }
  try{
    await api("/api/wecom_archive/scan",{method:"POST"});
    toast("静默分析已启动（后台执行，完成后自动刷新）","ok");
    setTimeout(loadWecomArchive, 2500);
  }catch(e){ toast(fetchErr(e,"启动分析"),"err"); if(btn){ btn.disabled=false; btn.innerHTML=((typeof icon==="function")?icon("search"):"")+" 立即扫描入库"; } }
}
// v1.0.588 合规闸：授权/撤回「解析本机企微聊天文件」
async function wecomArchiveConsent(value){
  try{
    await api("/api/wecom_archive/consent",{method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({consent: !!value})});
    toast(value ? "已授权，正在后台扫描" : "已撤回授权——今后不再读取企业微信文件（已入库内容保留）", "ok", 2600);
    if(value){ api("/api/wecom_archive/scan",{method:"POST"}).catch(()=>{}); }
    setTimeout(loadWecomArchive, value ? 1500 : 300);
  }catch(e){ toast(fetchErr(e,"授权操作"),"err"); }
}
async function loadKnowledge(){
  loadKbOps();
  const tree=$("kbTree"); showLoading(tree,"加载知识库中…");
  try{
    const d=await getJson("/api/knowledge");
    if(d.kb_root) $("kbRoot").textContent="保存位置："+d.kb_root;
    if(!d.categories || !d.categories.length){ showEmpty(tree,"课程课件 OCR 与答题记录会归档到这里",{ic:"book",title:"知识库为空"}); return; }
    /* v1.0.775（UI 统一二批）：分类 emoji 映射 → SVG 线稿键——与 v1.0.665 图标体系
       并存漏网的 emoji 子系统收口。icAny 未命中返回空串（宁可留白不画 emoji）；
       制度无线稿卷轴图标，借 shield 表「规制」语义；兜底 📁→folder。 */
    const icons={课件:"book",题库:"edit",制度:"shield",消化:"sparkles"};
    const html=d.categories.map(g=>{
      const files=g.files.length? g.files.map(f=>{
        const kb=(f.size/1024).toFixed(1);
        const enc=encodeURIComponent(f.name);
        // v1.0.361 审查修复（P17）：同产物库——文件名转义 + onclick 引号加固
        const safeName=escapeHtml(f.name);
        const encQ=enc.replace(/'/g,"%27");
        return `<div class="wrow"><span class="wname" onclick="viewKbFile('${jsStr(g.cat)}','${encQ}')">${safeName}</span>`
          + `<span class="wmeta">${kb}KB · ${f.mtime}</span></div>`;
      }).join("") : `<div class="wmeta" style="padding:4px 0">（空）</div>`;
      return `<div class="wgroup"><h4>${icAny(icons[g.cat]||"folder")} ${g.cat}（${g.files.length}）</h4>${files}</div>`;
    }).join("");
    $("kbTree").innerHTML=html;
  }catch(e){ showEmpty(tree,"加载失败<br/>"+escapeHtml(e.message||"网络错误"),{ic:"alert",title:"加载失败"}); }
}
async function viewKbFile(cat, name){
  try{
    const d=await getJson("/api/knowledge/file?cat="+encodeURIComponent(cat)+"&name="+name);
    $("kbFileName").textContent=d.name;
    $("kbFileContent").textContent=d.content;
    $("kbTree").style.display="none"; $("kbFileView").style.display="block";
  }catch(e){ toast(e.message||"读取失败","err"); }
}
function closeKbFile(){ $("kbTree").style.display="block"; $("kbFileView").style.display="none"; }
async function openKbFolder(){
  try{ await postJson("/api/knowledge/open",{}); }catch(e){ toast(e.message||"打开失败","err"); }
}

// ---- 知识库后台维护（v1.0.301：空闲自动整理 + 每日 AI 消化，daemon 60s 轮询） ----
let kbOpsPoll=null;
function _kbOpsFmtTime(ts){
  if(!ts) return "尚未";
  return new Date(ts*1000).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
}
var __kbWasBusy=false;
async function loadKbOps(){
  const el=$("kbOpsStatus"); if(!el) return;
  let d;
  try{ d=await (await fetch("/api/knowledge/maintenance/status")).json(); }
  catch(e){ el.textContent="⚠️ 维护状态不可用（后端未启动新版？）"; return; }
  if(!d.ok){ el.textContent="⚠️ "+(d.error||"获取状态失败"); return; }
  // v1.0.479：执行进度（当前动作+秒数）与上次结果——手动整理/消化不再"看不到进度"
  if(d.busy && d.action){
    const act=d.action==="organize"?"整理":"消化";
    el.innerHTML="⏳ 正在"+act+"…（已 <b>"+(d.elapsed||0)+"</b>s"+(d.action==="digest"?"，LLM 生成中":"")+"）";
  } else {
    // v1.0.666（用户："带颜色的图标都换成统一素色的"）：自动维护状态不再用彩色 emoji 当状态点——
    // 🟢/⚪ 属状态语义，统一改文字标记（状态点视觉语言归 .dot/徽章所有）。
    el.textContent=(d.running?"[开] 自动维护":"[未启动] 自动维护")+" · "+(d.idle?"空闲中":"忙碌中");
  }
  const det=$("kbOpsDetail");
  if(det){
    const lr=d.last_result;
    det.textContent="上次整理 "+_kbOpsFmtTime(d.last_organize)
      +" · 今日消化 "+(d.today_digest?(d.today_digest_empty?"无需（24h 无新增）":"已完成"):"未完成")
      +(d.latest_digest&&!d.today_digest_empty?("（最近 "+d.latest_digest+"）"):"")
      +(d.digest_fails_today?(" · 今日消化失败 "+d.digest_fails_today):"")
      +(lr?(" · 上次手动"+(lr.action==="organize"?"整理":"消化")+"："+(lr.ok?("✅ "+(lr.detail||"完成")+" "+lr.ts):("❌ "+(lr.detail||"失败")))):"")
      +(d.busy?" · 维护任务进行中":"");
  }
  // v1.0.479：自动触发条件透出——"自动维护开了为什么没执行"的答案（等空闲 10 分钟）
  const hint=$("kbOpsHint");
  if(hint && !d.busy){
    hint.textContent=d.idle?"":"自动任务等你停止使用软件 10 分钟后执行（避免抢占 agent）";
  }
  // busy→idle 跳变：弹结果通知（手动任务完成/失败即时可见）
  if(__kbWasBusy && !d.busy && d.last_result){
    const lr=d.last_result;
    if(lr.manual && lr.ok) toast((lr.action==="organize"?"整理":"消化")+"完成 ✅ "+(lr.detail||""),"ok",3500);
    else if(lr.manual && !lr.ok) toast((lr.action==="organize"?"整理":"消化")+"失败："+(lr.detail||"未知原因"),"err",5000);
  }
  __kbWasBusy=!!d.busy;
  if(d.busy){ if(!kbOpsPoll) kbOpsPoll=setInterval(()=>{ if(!document.hidden) loadKbOps(); },2000); }   // v1.0.702（A2）：隐藏不打下端
  else if(kbOpsPoll){ clearInterval(kbOpsPoll); kbOpsPoll=null; }
}
async function kbRun(action){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  if(action==="organize"){
    confirmAsk("立即整理", "生成 INDEX 索引 + 重复文件告警。不会移动/删除/重命名任何文件。", ()=>{ _kbRunGo(action); }, "开始整理");
  } else {
    confirmAsk("立即消化", "AI 将读取最近 24h 更新的文件生成今日消化。若今日已有消化文件将被覆盖（1-2 分钟）。", ()=>{ _kbRunGo(action); }, "开始消化");
  }
}
async function _kbRunGo(action){
  try{
    const r=await fetch("/api/knowledge/maintenance/run",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:action,force:action==="digest"})});
    const d=await r.json();
    if(!d.ok) toast(d.error||"触发失败","err");
    else{
      // v1.0.479：状态条立即切执行中 + 高频轮询（完成后自动弹结果通知）
      const el=$("kbOpsStatus");
      if(el) el.innerHTML="⏳ 正在"+(action==="organize"?"整理":"消化")+"…（已 <b>0</b>s）";
      __kbWasBusy=true;
      if(!kbOpsPoll) kbOpsPoll=setInterval(()=>{ if(!document.hidden) loadKbOps(); },2000);   // v1.0.702（A2）：隐藏不打下端
      loadKbOps();
      return;
    }
  }catch(e){ toast("触发请求失败："+(e.message||"网络错误"),"err"); }
  loadKbOps();
}

// v1.0.361 审查修复（P17）：离开视图时清理轮询定时器（原只建不拆，切页后每 3s 空打后端）
// v1.0.471（UI#9）：置位 __repairPollAborted——体检 progress 轮询循环随之终止
// v1.0.768（学习模块移除）：原 studyPollTimer 一行随跑课引擎一并删除
function cleanupViewPollers(){
  // v1.0.667：待审批/免审批 25s 视图级轮询——切走设置页必须停
  try{ _stopPendPoll(); }catch(e){}
  if(kbOpsPoll){ clearInterval(kbOpsPoll); kbOpsPoll=null; }
  // v1.0.606：运行组件的「安装中」自动刷新——切走本页要停，否则留个 3s 轮询在后台打接口
  if(__compPoll){ clearTimeout(__compPoll); __compPoll=null; }
  // v1.0.660（审查修复 P2）：OCR 状态轮询同样是视图级轮询——切走本页必须停，
  // 否则每 2s 一次 /api/ocr/status 在后台空转（此前根本没人清它）。
  try{ _ocrPollStop(); }catch(e){}
  // v1.0.660（审查修复 P2）：语音转写组件卡（js/15-asr-component.js）的自轮询
  // 同样是视图级轮询——切走设置页必须停，否则每 1.2s 一次 /api/asr/status 空转。
  // 用 typeof 守卫：js/15 在本文件之后加载，运行时早已就绪。
  try{ if(typeof _asrStopPoll === "function") _asrStopPoll(); }catch(e){}
  // v1.0.742（界面审查 P2-6）：企微文件「静默分析中」3s 自续轮询——切走视图必须停，
  // 否则每 3s 一次 /api/wecom_archive/status 在后台空转并重写隐藏 DOM。
  if(__warcPoll){ clearTimeout(__warcPoll); __warcPoll=null; }
  // v1.0.742（界面审查 P2-6）：批审 5s 进度轮询（js/13-dashboard）同理——切走即停。
  // 用 typeof 守卫：13 与本文件的加载顺序不保证谁先，运行时早已就绪。
  try{ if(typeof _stopAuditPoll === "function") _stopAuditPoll(); }catch(e){}
  window.__repairPollAborted = true;
}
// v1.0.768（学习模块移除）：学习中心视图与 loadStudy/refreshStudy/studyStart/
// studyStop/toggleStudyDetail/studyPollTimer 一并删除；/api/study/* 端点已下线。

function toggleLivePane(){
  const card=$("liveCard"); if(!card) return;
  const collapsed=card.classList.toggle("collapsed");
  // v1.0.561：同步 .folded 状态类——原折叠样式依赖 .work-grid:has(.livecard.collapsed)
  // + CSS 嵌套，旧版 WebView2 不支持时整块失效 → 折叠条被 stretch 拉满全高、
  // 下方大片空白（用户截图实锤）。JS 切类零兼容问题。
  const grid=card.closest(".work-grid"); if(grid){ grid.classList.toggle("folded", collapsed); }
  const btn=$("liveFoldBtn");
  if(btn){ setFold(btn, !collapsed); btn.title=collapsed?"展开窗口栏":"折叠窗口栏"; }   // v1.0.635：统一折叠原语
  lsSet("liveFold", collapsed?"1":"0");
  if(!collapsed){ setTimeout(()=>{ const s=card.querySelector(".scroll"); if(s){ s.scrollTop=s.scrollHeight; } },60); }
}
function setOpTarget(txt, ok){
  const el=$("opTargetHint"); if(!el) return;
  el.textContent=txt; el.className="sub op-target "+(ok?"ok":"none")+(el.dataset.min==="1"?" min":"");
}
// v1.0.562：运行状态条可收起——折叠后压成细线（agent 状态更新保持收起不打扰），再点还原
function toggleOpTargetMin(){
  const el=$("opTargetHint"); if(!el) return;
  const min=el.dataset.min==="1"?"0":"1";
  el.dataset.min=min;
  el.classList.toggle("min", min==="1");
  lsSet("opTargetMin", min);
}


// ---- 安全中心（v1.0.320）：配置/审计 ----
async function loadSecurity(){
  try{
    const d=await getJson("/api/security/config");
    const s=d.security||{};
    $("sec_mode").value=s.command_mode||"block";
    const _pl=$("sec_perm_level"); if(_pl) _pl.value=s.permission_level||"auto";   // v1.0.590
    $("sec_del_limit").value=s.delete_confirm_limit??10;
    $("sec_whitelist").value=(s.cmd_whitelist||[]).join("\n");
    $("sec_blacklist").value=(s.cmd_blacklist||[]).join("\n");
    $("sec_paths").value=(s.protected_paths||[]).join("\n");
    $("sec_backup").checked=s.auto_backup!==false;
    $("sec_backup_mb").value=s.backup_limit_mb??500;
    const _ret=$("sec_backup_retention"); if(_ret) _ret.value=s.backup_retention_days??0;   // v1.0.788（P2-8）
    const list=$("auditList"); if(list){
      const items=d.audit||[];
      list.innerHTML=items.length?items.map(a=>
        `<div>${a.allowed?"<b style='color:var(--brand-fg)'>放行</b>":"<b style='color:var(--err-fg)'>拦截</b>"} ${escapeHtml(a.tool)}：${escapeHtml((a.summary||"").slice(0,70))} <span class="dim">${escapeHtml(a.ts)}</span></div>`
      ).join(""):"暂无审计记录——agent 执行命令/删除/写文件等敏感操作后，这里会列出每一条放行与拦截明细。";
    }
  }catch(e){}
}
async function saveSecurity(btn){
  setBtnLoading(btn,true);
  try{
    const sec={
      command_mode:$("sec_mode").value,
      permission_level:($("sec_perm_level")||{}).value||"auto",   // v1.0.590 全局默认权限档
      delete_confirm_limit:parseInt($("sec_del_limit").value)||10,
      cmd_whitelist:$("sec_whitelist").value.split("\n").map(x=>x.trim()).filter(Boolean),
      cmd_blacklist:$("sec_blacklist").value.split("\n").map(x=>x.trim()).filter(Boolean),
      protected_paths:$("sec_paths").value.split("\n").map(x=>x.trim()).filter(Boolean),
      auto_backup:$("sec_backup").checked,
      backup_limit_mb:parseInt($("sec_backup_mb").value)||500,
      backup_retention_days:parseInt($("sec_backup_retention").value)||0,   // v1.0.788（P2-8）0=关闭
    };
    await apiToast("/api/security/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({security:sec})},"保存安全设置");
    toast("安全设置已保存（立即生效）","ok");
    loadSecurity();
    // v1.0.590：全局权限档改了要让 composer 上的权限下拉立刻反映（未单独选过档的任务）
    if(typeof loadPermDefault==="function") loadPermDefault();
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
// v1.0.788（P2-8）：立即执行一次保留归档（超期备份目录移入 产物库/备份归档/，移不删）
async function runRetentionNow(btn){
  const box=$("retentionBox");
  if(btn) setBtnLoading(btn,true);
  if(box) box.innerHTML='<div class="sub">归档中…</div>';
  try{
    const d=await apiToast("/api/security/retention/run",{method:"POST"},"归档超期备份");
    if(box){
      if(d.days<=0){ box.innerHTML='<div class="sub">保留天数未设置（0=关闭）——先在上方「备份保留天数」填 N 天并保存，再执行归档。</div>'; }
      else if((d.moved||[]).length){
        box.innerHTML='<div class="sub" style="color:var(--ok-fg)">✅ 已归档 '+escapeHtml((d.moved||[]).join("、"))+
          '（共 '+Math.round((d.total_bytes||0)/1048576*100)/100+' MB）→ 产物库/备份归档/</div>';
        if((d.skipped||[]).length) box.innerHTML+='<div class="sub" style="color:var(--warn-fg)">同名冲突跳过 '+d.skipped.length+' 个（源文件保留）</div>';
        toast("超期备份已归档","ok");
      }else{ box.innerHTML='<div class="sub">没有超过 '+d.days+' 天的备份日期目录（或当天已在自动归档中跑过）。</div>'; }
    }
  }catch(_){
    if(box) box.innerHTML='';
  }finally{ if(btn) setBtnLoading(btn,false); }
}
async function exportAudit(){
  try{
    const d=await apiToast("/api/security/audit/export",{method:"POST"},"导出审计");
    toast("已导出："+d.path,"ok",3000);
  }catch(_){}
}
async function clearAudit(){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk + 错误处理
  confirmAsk("清空审计", "清空全部安全审计记录？", async ()=>{
    try{
      await apiToast("/api/security/audit/clear",{method:"POST"},"清空审计");
      toast("审计已清空","ok"); loadSecurity();
    }catch(_){}
  }, "清空");
}

// ===== v1.0.667（v1.0.666 审查 P0-1/P0-2）：后台待审批 + 会话免审批管理 =====
// 此前后端端点齐全（routes/security.py）但前端零入口：agent 在流里引导
// "去安全中心逐条批准"，而安全中心没有这张卡——批准通道事实性断头。
// 这里补两张卡 + 25s 视图级轮询（切走由 cleanupViewPollers 停）。
let __pendPoll=null;
function _pendImpactLine(imp){
  if(!imp) return "";
  const bits=[];
  if(imp.destructive) bits.push("<b style='color:var(--err-fg)'>高风险</b>");
  if(imp.overwrite) bits.push("会覆盖已有文件");
  (imp.notes||[]).slice(0,2).forEach(n=>bits.push(escapeHtml(n)));
  return bits.length? " · "+bits.join(" · ") : "";
}
async function loadPendingApprovals(){
  const box=$("pendList"), badge=$("pendBadge");
  if(!box) return;
  let d;
  try{ d=await getJson("/api/security/pending-approvals"); }
  catch(e){ box.innerHTML="读取失败（后端未响应）"; return; }
  const items=d.items||[];
  if(badge){ badge.style.display=items.length?"":"none"; badge.textContent=items.length+" 条待处理"; badge.className=items.length?"pill wrn":"pill"; }
  if(!items.length){
    box.innerHTML="暂无待审批——后台任务（定时任务等）被拦下的不可撤销操作会出现在这里。";
    return;
  }
  box.innerHTML=items.map(it=>{
    const ts=new Date((it.created_at||0)*1000).toLocaleString();
    const paths=(it.impact&&it.impact.paths||[]).slice(0,2).map(escapeHtml).join("、");
    return `<div style="margin-bottom:8px;padding:8px 10px;border:1px solid var(--border);border-radius:var(--r-sm)">`
      +`<div><b>${escapeHtml(it.tool||"?")}</b> <span class="dim">${escapeHtml((it.summary||"").slice(0,90))}</span></div>`
      +(paths?`<div class="dim">目标：${paths}</div>`:"")
      +_pendImpactLine(it.impact)
      +`<div class="dim" style="margin-top:2px">来源 ${escapeHtml(it.source||"—")} · ${escapeHtml(ts)}</div>`
      +`<div style="margin-top:6px"><button class="ghost smbtn" onclick="resolvePending('${jsStr(it.id)}',true,this)">批准</button> `
      +`<button class="ghost smbtn" style="color:var(--err-fg)" onclick="resolvePending('${jsStr(it.id)}',false,this)">驳回</button></div>`
      +`</div>`;
  }).join("");
}
async function resolvePending(id, approve, btn){
  setBtnLoading(btn,true);
  try{
    const d=await apiToast("/api/security/pending-approvals/"+encodeURIComponent(id)+"/"+(approve?"approve":"reject"),{method:"POST"},approve?"批准":"驳回");
    if(approve) toast(d.granted?"已批准——同操作 10 分钟内免再问":"已批准","ok",3000);
    else toast("已驳回（不重跑）","ok",3000);
    loadPendingApprovals();
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
async function loadSessionAllows(){
  const box=$("saList"), btn=$("saClearAllBtn");
  if(!box) return;
  let d;
  try{ d=await getJson("/api/security/session-allow/all"); }
  catch(e){ box.innerHTML="读取失败（后端未响应）"; return; }
  const items=d.items||[];
  if(btn) btn.style.display=items.length?"":"none";
  if(!items.length){
    box.innerHTML="暂无——聊天审批弹窗点「本会话记住该操作」后会记在这里（默认 30 分钟有效）。";
    return;
  }
  box.innerHTML=items.map(r=>{
    const left=Math.max(0,Math.round(((r.expire||0)-Date.now()/1000)/60));
    return `<div style="margin-bottom:6px"><code>${escapeHtml(r.prefix||"")}</code>`
      +(r.tool?` <span class="dim">（限 ${escapeHtml(r.tool)}）</span>`:"")
      +` <span class="dim">会话 ${escapeHtml(r.pane_id||"—")} · 剩 ${left} 分</span> `
      +`<button class="ghost smbtn" style="margin-left:6px" onclick="removeSessionAllowRule('${jsStr(r.pane_id)}','${jsStr(r.id)}',this)">撤销</button></div>`;
  }).join("");
}
async function removeSessionAllowRule(paneId, ruleId, btn){
  setBtnLoading(btn,true);
  try{
    await apiToast("/api/security/session-allow",{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({pane_id:paneId,rule_id:ruleId})},"撤销");
    toast("已撤销该免审批规则","ok",3000); loadSessionAllows();
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
async function clearAllSessionAllow(btn){
  confirmAsk("清除全部免审批规则", "把全部会话的「记住该操作」免审批规则一次清光？清掉后同类操作会恢复逐次确认。", async ()=>{
    setBtnLoading(btn,true);
    try{
      const d=await apiToast("/api/security/session-allow/all",{method:"DELETE"},"清除");
      toast(`已清除 ${d.cleared} 条规则`,"ok",3000); loadSessionAllows();
    }catch(_){}
    finally{ setBtnLoading(btn,false); }
  }, "全清");
}
function _startPendPoll(){
  if(__pendPoll) return;
  const tick=()=>{
    // 视图级轮询：只在看设置页时跑（cleanupViewPollers 负责停）
    const sv=document.getElementById("view-settings");
    if(sv && sv.classList.contains("active") && !document.hidden){   // v1.0.702（A2）：隐藏不打下端
      loadPendingApprovals(); loadSessionAllows();
    }
  };
  tick();
  __pendPoll=setInterval(tick,25000);
}
function _stopPendPoll(){
  if(__pendPoll){ clearInterval(__pendPoll); __pendPoll=null; }
}

// ===== v1.0.614（审查 ㉙㉚㉛）报销个人信息 · dsh 凭据 · 后台自动任务开关 =====
// 这三组值此前只存在于 config.json，界面无处可填，而快捷入口的提示词写着「个人信息在
// 设置里」；凭据只写不回显（后端 GET 只给「有没有 + 尾号」），留空保存＝保留原值。
// _persLoaded：没成功读到当前值就不许保存——输入框空着写回 config 等于把真实姓名/银行
// 账号清掉（GET 失败但 POST 成功是可能发生的，例如上一次读的时候后端刚重启）。
let _persLoaded=false;
async function postPersonal(body,failLabel){
  const r=await fetch("/api/personal/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok||!d.ok) throw new Error(d.error||(failLabel+"失败（HTTP "+r.status+"）"));
  return d;
}
// v1.0.650（F4）：任务软预算四项。键名与后端 core.config_store.AGENT_SOFT_BUDGET_DEFAULTS
// 一一对应，输入框 id 恒为 "bg_"+键名——名单只在这里写一次，读/存/恢复默认三处共用。
// v1.0.780（目标驱动执行）：goal_max_rounds 并入同一名单——与后端
// AGENT_SOFT_BUDGET_DEFAULTS 键名一一对应（0=不限），读/存/恢复默认三处共用。
// 下拉框 id 恒为 bg_goal_max_rounds，set() 的 el.value 赋值对 select 同样成立。
const SOFT_BUDGET_KEYS = ["soft_budget_tokens", "soft_budget_minutes",
                          "soft_budget_tokens_per_item", "soft_budget_minutes_per_item",
                          "goal_max_rounds"];
const SOFT_BUDGET_DEFAULTS = { soft_budget_tokens: 400000, soft_budget_minutes: 120,
                               soft_budget_tokens_per_item: 200000, soft_budget_minutes_per_item: 15,
                               goal_max_rounds: 0 };
function _budgetNum(k){
  const v = parseInt(($("bg_" + k) || {}).value, 10);
  return isNaN(v) ? 0 : Math.max(0, v);       // 0 是合法档（=该项关闭），不能回落成默认
}
function resetBudgetForm(btn){
  SOFT_BUDGET_KEYS.forEach(k=>{ const el=$("bg_"+k); if(el) el.value = SOFT_BUDGET_DEFAULTS[k]; });
  toast("输入框已恢复出厂默认，尚未保存——点「保存任务预算」才生效", "info", 4200);
}
async function saveBudget(btn){
  if(!_persLoaded){ toast("任务预算还没加载成功，先别保存——现在保存会把实际生效的额度写成界面默认值", "err"); return; }
  setBtnLoading(btn,true);
  try{
    const budget={};
    SOFT_BUDGET_KEYS.forEach(k=>{ budget[k]=_budgetNum(k); });
    await postPersonal({ budget },"保存任务预算");
    toast("任务预算已保存（下一个任务起生效，正在跑的这一轮沿用开始时的额度）","ok",3600);
    loadPersonal();
  }catch(e){ toast(fetchErr(e,"保存任务预算"),"err"); }
  finally{ setBtnLoading(btn,false); }
}
async function loadPersonal(){
  const set=(id,v)=>{ const el=$(id); if(el) el.value=(v==null?"":v); };
  const chk=(id,v)=>{ const el=$(id); if(el) el.checked=!!v; };
  _persLoaded=false;
  try{
    const d=await getJson("/api/personal/config");
    if(!d||!d.ok) throw new Error((d&&d.error)||"接口没返回配置");
    const r=d.reimburse||{}, k=d.dsh||{}, b=d.background||{};
    set("pii_name",r.name); set("pii_account",r.bank_account); set("pii_bank",r.bank_name);
    chk("pii_mask",r.mask_for_llm);
    set("dsh_base_url",k.base_url); set("dsh_cwd",k.cwd); set("dsh_extra_args",k.extra_args);
    const keyEl=$("dsh_api_key"); if(keyEl) keyEl.value="";     // 永远不回灌密文/明文
    const has=!!k.api_key_set;
    const ck=$("dshClearKey"); if(ck) ck.style.display=has?"":"none";
    const st=$("personalState");
    let note=has?("dsh 凭据已设置 · 尾号 "+(k.api_key_tail||"")):"dsh 凭据未设置（可选）";
    // DPAPI 绑定 Windows 用户+机器：配置从别的机器/账户搬过来时那几项必然解不开。
    // 后端此时下发空值（密文不进 DOM），这里必须说清"留空保存不会清掉它"，
    // 否则用户以为已经清空、其实旧的不可读密文还躺在 config 里。
    const enc=r.encrypted_fields||[];
    if(enc.length) note="⚠ "+enc.map(f=>({name:"姓名",bank_account:"银行账号",bank_name:"开户行"}[f]||f)).join("、")
      +" 是加密存储但本机解不开（配置来自别的电脑或 Windows 账户）——留空保存不会清掉它，填新值即覆盖。"+note;
    else if(k.api_key_unreadable) note="⚠ dsh 凭据是解不开的密文（同上一句），填新值即覆盖。"+note;
    if(st) st.textContent=note;
    chk("bg_digests_daily",b.digests_daily); set("bg_digests_time",b.digests_time||"18:30");
    chk("bg_kb_organize",b.kb_organize); chk("bg_kb_digest",b.kb_digest); chk("bg_todo_watch",b.todo_watch);
    set("bg_todo_interval", b.todo_interval || 10);
    const bu=d.budget||{};
    SOFT_BUDGET_KEYS.forEach(k=>set("bg_"+k, bu[k]));
    _persLoaded=true;
  }catch(e){
    const st=$("personalState");
    if(st) st.textContent="⚠ 没读到当前配置，先别点保存（否则会把已有的姓名/银行账号存成空）";
  }
}
async function savePersonal(btn){
  if(!_persLoaded){ toast("个人信息还没加载成功，先别保存——现在保存会把已有的姓名/银行账号写成空值","err"); return; }
  setBtnLoading(btn,true);
  try{
    const body={ reimburse:{ name:$("pii_name").value.trim(), bank_account:$("pii_account").value.trim(),
                             bank_name:$("pii_bank").value.trim(), mask_for_llm:$("pii_mask").checked },
                 dsh:{ base_url:$("dsh_base_url").value.trim(),
                       cwd:($("dsh_cwd")||{}).value||"", extra_args:($("dsh_extra_args")||{}).value||"" } };
    const nk=$("dsh_api_key").value.trim();
    if(nk) body.dsh.api_key=nk;                                   // 留空＝不动原凭据
    await postPersonal(body,"保存个人信息");
    toast("个人信息已保存（姓名/银行账号/凭据按 DPAPI 加密落盘）","ok");
    loadPersonal();
  }catch(e){ toast(fetchErr(e,"保存个人信息"),"err"); }
  finally{ setBtnLoading(btn,false); }
}
async function saveBackground(btn){
  if(!_persLoaded){ toast("后台开关还没加载成功，先别保存——现在保存会把三项默认开关全部关掉","err"); return; }
  setBtnLoading(btn,true);
  try{
    await postPersonal({ background:{ digests_daily:$("bg_digests_daily").checked,
                                      digests_time:$("bg_digests_time").value||"18:30",
                                      kb_organize:$("bg_kb_organize").checked,
                                      kb_digest:$("bg_kb_digest").checked,
                                      todo_watch:$("bg_todo_watch").checked,
                                      todo_interval:parseInt(($("bg_todo_interval")||{}).value,10)||10 } },"保存后台开关");
    toast("后台开关已保存（立即生效，无需重启）","ok");
    loadPersonal();
  }catch(e){ toast(fetchErr(e,"保存后台开关"),"err"); }
  finally{ setBtnLoading(btn,false); }
}
function clearDshKey(btn){
  confirmAsk("清除 dsh 凭据","清除后「让 AI 操作这台电脑」要重新填凭据才能用。确定清除？",async ()=>{
    setBtnLoading(btn,true);
    try{
      await postPersonal({ dsh:{ clear_key:true } },"清除 dsh 凭据");
      toast("dsh 凭据已清除","ok"); loadPersonal();
    }catch(e){ toast(fetchErr(e,"清除 dsh 凭据"),"err"); }
    finally{ setBtnLoading(btn,false); }
  },"清除");
}
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {
  SETTING_INDEX, SOFT_BUDGET_DEFAULTS, SOFT_BUDGET_KEYS, _FF_KEYS, _FF_OFF_BY_DEFAULT, _PHASE_IDX,
  _REPAIR_PHASES, __THEME, __THEME_PRESETS, __UI_SCALES, __compPoll, __compRemBusy,
  __kbWasBusy, __macroCtl, __ocrInFlight, __ocrPollEpoch, __ocrPollTimer, __ocrPolling,
  __pendPoll, __schedEditId, __themeStyleEl, __warcPoll, _budgetNum, _compFix,
  _compInstallRun, _compRemHide, _compRemShow, _instAction, _kbOpsFmtTime, _kbRunGo,
  _macroRun, _ocrPollStart, _ocrPollStop, _ocrUpdateGo, _pendImpactLine, _persLoaded,
  _repairRunning, _setNavInited, _setSearchInited, _startPendPoll, _stopPendPoll, _themeDarken,
  _themeHsl, _themeRgb, adminRestart, applyDarkMode, applyTheme, applyUiScale,
  cancelScheduleEdit, cancelSelfRepair, checkCompFirstRun, checkUpdate, cleanupViewPollers, clearAllSessionAllow,
  clearAudit, clearDshKey, clearMemory, closeKbFile, closeWsFile, compEmptyHtml,
  compRemAction, compRemSet, delMacro, delSchedule, doctorCopy, doctorRender,
  editSchedule, exportAudit, initSettingsSearch, initSettingsSideNav, installAllComponents, installUpdate,
  kbOpsPoll, kbRun, loadAdminStatus, loadAutostart, loadCloseAction, loadCompReminder,
  loadComponents, loadFeatureFlags, loadKbOps, loadKnowledge, loadMacroList, loadMonthlyStats,
  loadOcrStatus, loadPendingApprovals, loadPersonal, loadSchedules, loadSecurity, loadSessionAllows,
  loadTheme, loadToggleHotkey, loadUiPref, loadUserRules, loadVersionList, loadWecomArchive,
  loadWorkspace, migrationExport, migrationImport, migrationPickFile, migrationRender, migrationRenderErr,
  newWsRound, ocrCheckUpdates, ocrInstall, ocrInstallPollProgress, ocrSelfCheckNow, ocrUpdate,
  onThemeAccent, onThemeBg, openKbFolder, openLogFolder, openWebUI, openWsFile,
  openWsFolder, pollCompDemands, pollOcrStatus, postPersonal, pushRejectList, rebuildNow,
  removeSessionAllowRule, renderOcrSelfCheck, renderOcrStatus, renderRepairProgress, renderThemePresets, repairBtnClick,
  resetBudgetForm, resolvePending, runDoctor, runMacro, runRetentionNow, runScheduleNow,
  runSelfRepair, saveBackground, saveBudget, saveCloseAction, saveFeatureFlags, savePersonal,
  saveSchedule, saveSecurity, saveTheme, saveToggleHotkey, saveUiPref, saveUserRules,
  setOpTarget, switchVersion, themeAccentInput, themeBgInput, themePreset, themeReset,
  toggleAutostart, toggleAutostartAdmin, toggleAutostartHide, toggleDarkMode, toggleLivePane, toggleOpTargetMin,
  toggleSchedule, toggleUiScale, viewKbFile, viewLatestRepairReport, viewMemory, viewWsFile,
  wecomArchiveConsent, wecomArchiveScan, wsHistory, wsHistoryRestore, wsHistoryView
};
Object.defineProperties(window, {
  SETTING_INDEX: { configurable: true, enumerable: true, get(){ return SETTING_INDEX; }, set(v){ SETTING_INDEX = v; } },
  SOFT_BUDGET_DEFAULTS: { configurable: true, enumerable: true, get(){ return SOFT_BUDGET_DEFAULTS; }, set(v){ SOFT_BUDGET_DEFAULTS = v; } },
  SOFT_BUDGET_KEYS: { configurable: true, enumerable: true, get(){ return SOFT_BUDGET_KEYS; }, set(v){ SOFT_BUDGET_KEYS = v; } },
  _FF_KEYS: { configurable: true, enumerable: true, get(){ return _FF_KEYS; }, set(v){ _FF_KEYS = v; } },
  _FF_OFF_BY_DEFAULT: { configurable: true, enumerable: true, get(){ return _FF_OFF_BY_DEFAULT; }, set(v){ _FF_OFF_BY_DEFAULT = v; } },
  _PHASE_IDX: { configurable: true, enumerable: true, get(){ return _PHASE_IDX; }, set(v){ _PHASE_IDX = v; } },
  _REPAIR_PHASES: { configurable: true, enumerable: true, get(){ return _REPAIR_PHASES; }, set(v){ _REPAIR_PHASES = v; } },
  __THEME: { configurable: true, enumerable: true, get(){ return __THEME; }, set(v){ __THEME = v; } },
  __THEME_PRESETS: { configurable: true, enumerable: true, get(){ return __THEME_PRESETS; }, set(v){ __THEME_PRESETS = v; } },
  __UI_SCALES: { configurable: true, enumerable: true, get(){ return __UI_SCALES; }, set(v){ __UI_SCALES = v; } },
  __compPoll: { configurable: true, enumerable: true, get(){ return __compPoll; }, set(v){ __compPoll = v; } },
  __compRemBusy: { configurable: true, enumerable: true, get(){ return __compRemBusy; }, set(v){ __compRemBusy = v; } },
  __kbWasBusy: { configurable: true, enumerable: true, get(){ return __kbWasBusy; }, set(v){ __kbWasBusy = v; } },
  __macroCtl: { configurable: true, enumerable: true, get(){ return __macroCtl; }, set(v){ __macroCtl = v; } },
  __ocrInFlight: { configurable: true, enumerable: true, get(){ return __ocrInFlight; }, set(v){ __ocrInFlight = v; } },
  __ocrPollEpoch: { configurable: true, enumerable: true, get(){ return __ocrPollEpoch; }, set(v){ __ocrPollEpoch = v; } },
  __ocrPollTimer: { configurable: true, enumerable: true, get(){ return __ocrPollTimer; }, set(v){ __ocrPollTimer = v; } },
  __ocrPolling: { configurable: true, enumerable: true, get(){ return __ocrPolling; }, set(v){ __ocrPolling = v; } },
  __pendPoll: { configurable: true, enumerable: true, get(){ return __pendPoll; }, set(v){ __pendPoll = v; } },
  __schedEditId: { configurable: true, enumerable: true, get(){ return __schedEditId; }, set(v){ __schedEditId = v; } },
  __themeStyleEl: { configurable: true, enumerable: true, get(){ return __themeStyleEl; }, set(v){ __themeStyleEl = v; } },
  __warcPoll: { configurable: true, enumerable: true, get(){ return __warcPoll; }, set(v){ __warcPoll = v; } },
  _budgetNum: { configurable: true, enumerable: true, get(){ return _budgetNum; }, set(v){ _budgetNum = v; } },
  _compFix: { configurable: true, enumerable: true, get(){ return _compFix; }, set(v){ _compFix = v; } },
  _compInstallRun: { configurable: true, enumerable: true, get(){ return _compInstallRun; }, set(v){ _compInstallRun = v; } },
  _compRemHide: { configurable: true, enumerable: true, get(){ return _compRemHide; }, set(v){ _compRemHide = v; } },
  _compRemShow: { configurable: true, enumerable: true, get(){ return _compRemShow; }, set(v){ _compRemShow = v; } },
  _instAction: { configurable: true, enumerable: true, get(){ return _instAction; }, set(v){ _instAction = v; } },
  _kbOpsFmtTime: { configurable: true, enumerable: true, get(){ return _kbOpsFmtTime; }, set(v){ _kbOpsFmtTime = v; } },
  _kbRunGo: { configurable: true, enumerable: true, get(){ return _kbRunGo; }, set(v){ _kbRunGo = v; } },
  _macroRun: { configurable: true, enumerable: true, get(){ return _macroRun; }, set(v){ _macroRun = v; } },
  _ocrPollStart: { configurable: true, enumerable: true, get(){ return _ocrPollStart; }, set(v){ _ocrPollStart = v; } },
  _ocrPollStop: { configurable: true, enumerable: true, get(){ return _ocrPollStop; }, set(v){ _ocrPollStop = v; } },
  _ocrUpdateGo: { configurable: true, enumerable: true, get(){ return _ocrUpdateGo; }, set(v){ _ocrUpdateGo = v; } },
  _pendImpactLine: { configurable: true, enumerable: true, get(){ return _pendImpactLine; }, set(v){ _pendImpactLine = v; } },
  _persLoaded: { configurable: true, enumerable: true, get(){ return _persLoaded; }, set(v){ _persLoaded = v; } },
  _repairRunning: { configurable: true, enumerable: true, get(){ return _repairRunning; }, set(v){ _repairRunning = v; } },
  _setNavInited: { configurable: true, enumerable: true, get(){ return _setNavInited; }, set(v){ _setNavInited = v; } },
  _setSearchInited: { configurable: true, enumerable: true, get(){ return _setSearchInited; }, set(v){ _setSearchInited = v; } },
  _startPendPoll: { configurable: true, enumerable: true, get(){ return _startPendPoll; }, set(v){ _startPendPoll = v; } },
  _stopPendPoll: { configurable: true, enumerable: true, get(){ return _stopPendPoll; }, set(v){ _stopPendPoll = v; } },
  _themeDarken: { configurable: true, enumerable: true, get(){ return _themeDarken; }, set(v){ _themeDarken = v; } },
  _themeHsl: { configurable: true, enumerable: true, get(){ return _themeHsl; }, set(v){ _themeHsl = v; } },
  _themeRgb: { configurable: true, enumerable: true, get(){ return _themeRgb; }, set(v){ _themeRgb = v; } },
  adminRestart: { configurable: true, enumerable: true, get(){ return adminRestart; }, set(v){ adminRestart = v; } },
  applyDarkMode: { configurable: true, enumerable: true, get(){ return applyDarkMode; }, set(v){ applyDarkMode = v; } },
  applyTheme: { configurable: true, enumerable: true, get(){ return applyTheme; }, set(v){ applyTheme = v; } },
  applyUiScale: { configurable: true, enumerable: true, get(){ return applyUiScale; }, set(v){ applyUiScale = v; } },
  cancelScheduleEdit: { configurable: true, enumerable: true, get(){ return cancelScheduleEdit; }, set(v){ cancelScheduleEdit = v; } },
  cancelSelfRepair: { configurable: true, enumerable: true, get(){ return cancelSelfRepair; }, set(v){ cancelSelfRepair = v; } },
  checkCompFirstRun: { configurable: true, enumerable: true, get(){ return checkCompFirstRun; }, set(v){ checkCompFirstRun = v; } },
  checkUpdate: { configurable: true, enumerable: true, get(){ return checkUpdate; }, set(v){ checkUpdate = v; } },
  cleanupViewPollers: { configurable: true, enumerable: true, get(){ return cleanupViewPollers; }, set(v){ cleanupViewPollers = v; } },
  clearAllSessionAllow: { configurable: true, enumerable: true, get(){ return clearAllSessionAllow; }, set(v){ clearAllSessionAllow = v; } },
  clearAudit: { configurable: true, enumerable: true, get(){ return clearAudit; }, set(v){ clearAudit = v; } },
  clearDshKey: { configurable: true, enumerable: true, get(){ return clearDshKey; }, set(v){ clearDshKey = v; } },
  clearMemory: { configurable: true, enumerable: true, get(){ return clearMemory; }, set(v){ clearMemory = v; } },
  closeKbFile: { configurable: true, enumerable: true, get(){ return closeKbFile; }, set(v){ closeKbFile = v; } },
  closeWsFile: { configurable: true, enumerable: true, get(){ return closeWsFile; }, set(v){ closeWsFile = v; } },
  compEmptyHtml: { configurable: true, enumerable: true, get(){ return compEmptyHtml; }, set(v){ compEmptyHtml = v; } },
  compRemAction: { configurable: true, enumerable: true, get(){ return compRemAction; }, set(v){ compRemAction = v; } },
  compRemSet: { configurable: true, enumerable: true, get(){ return compRemSet; }, set(v){ compRemSet = v; } },
  delMacro: { configurable: true, enumerable: true, get(){ return delMacro; }, set(v){ delMacro = v; } },
  delSchedule: { configurable: true, enumerable: true, get(){ return delSchedule; }, set(v){ delSchedule = v; } },
  doctorCopy: { configurable: true, enumerable: true, get(){ return doctorCopy; }, set(v){ doctorCopy = v; } },
  doctorRender: { configurable: true, enumerable: true, get(){ return doctorRender; }, set(v){ doctorRender = v; } },
  editSchedule: { configurable: true, enumerable: true, get(){ return editSchedule; }, set(v){ editSchedule = v; } },
  exportAudit: { configurable: true, enumerable: true, get(){ return exportAudit; }, set(v){ exportAudit = v; } },
  initSettingsSearch: { configurable: true, enumerable: true, get(){ return initSettingsSearch; }, set(v){ initSettingsSearch = v; } },
  initSettingsSideNav: { configurable: true, enumerable: true, get(){ return initSettingsSideNav; }, set(v){ initSettingsSideNav = v; } },
  installAllComponents: { configurable: true, enumerable: true, get(){ return installAllComponents; }, set(v){ installAllComponents = v; } },
  installUpdate: { configurable: true, enumerable: true, get(){ return installUpdate; }, set(v){ installUpdate = v; } },
  kbOpsPoll: { configurable: true, enumerable: true, get(){ return kbOpsPoll; }, set(v){ kbOpsPoll = v; } },
  kbRun: { configurable: true, enumerable: true, get(){ return kbRun; }, set(v){ kbRun = v; } },
  loadAdminStatus: { configurable: true, enumerable: true, get(){ return loadAdminStatus; }, set(v){ loadAdminStatus = v; } },
  loadAutostart: { configurable: true, enumerable: true, get(){ return loadAutostart; }, set(v){ loadAutostart = v; } },
  loadCloseAction: { configurable: true, enumerable: true, get(){ return loadCloseAction; }, set(v){ loadCloseAction = v; } },
  loadCompReminder: { configurable: true, enumerable: true, get(){ return loadCompReminder; }, set(v){ loadCompReminder = v; } },
  loadComponents: { configurable: true, enumerable: true, get(){ return loadComponents; }, set(v){ loadComponents = v; } },
  loadFeatureFlags: { configurable: true, enumerable: true, get(){ return loadFeatureFlags; }, set(v){ loadFeatureFlags = v; } },
  loadKbOps: { configurable: true, enumerable: true, get(){ return loadKbOps; }, set(v){ loadKbOps = v; } },
  loadKnowledge: { configurable: true, enumerable: true, get(){ return loadKnowledge; }, set(v){ loadKnowledge = v; } },
  loadMacroList: { configurable: true, enumerable: true, get(){ return loadMacroList; }, set(v){ loadMacroList = v; } },
  loadMonthlyStats: { configurable: true, enumerable: true, get(){ return loadMonthlyStats; }, set(v){ loadMonthlyStats = v; } },
  loadOcrStatus: { configurable: true, enumerable: true, get(){ return loadOcrStatus; }, set(v){ loadOcrStatus = v; } },
  loadPendingApprovals: { configurable: true, enumerable: true, get(){ return loadPendingApprovals; }, set(v){ loadPendingApprovals = v; } },
  loadPersonal: { configurable: true, enumerable: true, get(){ return loadPersonal; }, set(v){ loadPersonal = v; } },
  loadSchedules: { configurable: true, enumerable: true, get(){ return loadSchedules; }, set(v){ loadSchedules = v; } },
  loadSecurity: { configurable: true, enumerable: true, get(){ return loadSecurity; }, set(v){ loadSecurity = v; } },
  loadSessionAllows: { configurable: true, enumerable: true, get(){ return loadSessionAllows; }, set(v){ loadSessionAllows = v; } },
  loadTheme: { configurable: true, enumerable: true, get(){ return loadTheme; }, set(v){ loadTheme = v; } },
  loadToggleHotkey: { configurable: true, enumerable: true, get(){ return loadToggleHotkey; }, set(v){ loadToggleHotkey = v; } },
  loadUiPref: { configurable: true, enumerable: true, get(){ return loadUiPref; }, set(v){ loadUiPref = v; } },
  loadUserRules: { configurable: true, enumerable: true, get(){ return loadUserRules; }, set(v){ loadUserRules = v; } },
  loadVersionList: { configurable: true, enumerable: true, get(){ return loadVersionList; }, set(v){ loadVersionList = v; } },
  loadWecomArchive: { configurable: true, enumerable: true, get(){ return loadWecomArchive; }, set(v){ loadWecomArchive = v; } },
  loadWorkspace: { configurable: true, enumerable: true, get(){ return loadWorkspace; }, set(v){ loadWorkspace = v; } },
  migrationExport: { configurable: true, enumerable: true, get(){ return migrationExport; }, set(v){ migrationExport = v; } },
  migrationImport: { configurable: true, enumerable: true, get(){ return migrationImport; }, set(v){ migrationImport = v; } },
  migrationPickFile: { configurable: true, enumerable: true, get(){ return migrationPickFile; }, set(v){ migrationPickFile = v; } },
  migrationRender: { configurable: true, enumerable: true, get(){ return migrationRender; }, set(v){ migrationRender = v; } },
  migrationRenderErr: { configurable: true, enumerable: true, get(){ return migrationRenderErr; }, set(v){ migrationRenderErr = v; } },
  newWsRound: { configurable: true, enumerable: true, get(){ return newWsRound; }, set(v){ newWsRound = v; } },
  ocrCheckUpdates: { configurable: true, enumerable: true, get(){ return ocrCheckUpdates; }, set(v){ ocrCheckUpdates = v; } },
  ocrInstall: { configurable: true, enumerable: true, get(){ return ocrInstall; }, set(v){ ocrInstall = v; } },
  ocrInstallPollProgress: { configurable: true, enumerable: true, get(){ return ocrInstallPollProgress; }, set(v){ ocrInstallPollProgress = v; } },
  ocrSelfCheckNow: { configurable: true, enumerable: true, get(){ return ocrSelfCheckNow; }, set(v){ ocrSelfCheckNow = v; } },
  ocrUpdate: { configurable: true, enumerable: true, get(){ return ocrUpdate; }, set(v){ ocrUpdate = v; } },
  onThemeAccent: { configurable: true, enumerable: true, get(){ return onThemeAccent; }, set(v){ onThemeAccent = v; } },
  onThemeBg: { configurable: true, enumerable: true, get(){ return onThemeBg; }, set(v){ onThemeBg = v; } },
  openKbFolder: { configurable: true, enumerable: true, get(){ return openKbFolder; }, set(v){ openKbFolder = v; } },
  openLogFolder: { configurable: true, enumerable: true, get(){ return openLogFolder; }, set(v){ openLogFolder = v; } },
  openWebUI: { configurable: true, enumerable: true, get(){ return openWebUI; }, set(v){ openWebUI = v; } },
  openWsFile: { configurable: true, enumerable: true, get(){ return openWsFile; }, set(v){ openWsFile = v; } },
  openWsFolder: { configurable: true, enumerable: true, get(){ return openWsFolder; }, set(v){ openWsFolder = v; } },
  pollCompDemands: { configurable: true, enumerable: true, get(){ return pollCompDemands; }, set(v){ pollCompDemands = v; } },
  pollOcrStatus: { configurable: true, enumerable: true, get(){ return pollOcrStatus; }, set(v){ pollOcrStatus = v; } },
  postPersonal: { configurable: true, enumerable: true, get(){ return postPersonal; }, set(v){ postPersonal = v; } },
  pushRejectList: { configurable: true, enumerable: true, get(){ return pushRejectList; }, set(v){ pushRejectList = v; } },
  rebuildNow: { configurable: true, enumerable: true, get(){ return rebuildNow; }, set(v){ rebuildNow = v; } },
  removeSessionAllowRule: { configurable: true, enumerable: true, get(){ return removeSessionAllowRule; }, set(v){ removeSessionAllowRule = v; } },
  renderOcrSelfCheck: { configurable: true, enumerable: true, get(){ return renderOcrSelfCheck; }, set(v){ renderOcrSelfCheck = v; } },
  renderOcrStatus: { configurable: true, enumerable: true, get(){ return renderOcrStatus; }, set(v){ renderOcrStatus = v; } },
  renderRepairProgress: { configurable: true, enumerable: true, get(){ return renderRepairProgress; }, set(v){ renderRepairProgress = v; } },
  renderThemePresets: { configurable: true, enumerable: true, get(){ return renderThemePresets; }, set(v){ renderThemePresets = v; } },
  repairBtnClick: { configurable: true, enumerable: true, get(){ return repairBtnClick; }, set(v){ repairBtnClick = v; } },
  resetBudgetForm: { configurable: true, enumerable: true, get(){ return resetBudgetForm; }, set(v){ resetBudgetForm = v; } },
  resolvePending: { configurable: true, enumerable: true, get(){ return resolvePending; }, set(v){ resolvePending = v; } },
  runDoctor: { configurable: true, enumerable: true, get(){ return runDoctor; }, set(v){ runDoctor = v; } },
  runMacro: { configurable: true, enumerable: true, get(){ return runMacro; }, set(v){ runMacro = v; } },
  runRetentionNow: { configurable: true, enumerable: true, get(){ return runRetentionNow; }, set(v){ runRetentionNow = v; } },
  runScheduleNow: { configurable: true, enumerable: true, get(){ return runScheduleNow; }, set(v){ runScheduleNow = v; } },
  runSelfRepair: { configurable: true, enumerable: true, get(){ return runSelfRepair; }, set(v){ runSelfRepair = v; } },
  saveBackground: { configurable: true, enumerable: true, get(){ return saveBackground; }, set(v){ saveBackground = v; } },
  saveBudget: { configurable: true, enumerable: true, get(){ return saveBudget; }, set(v){ saveBudget = v; } },
  saveCloseAction: { configurable: true, enumerable: true, get(){ return saveCloseAction; }, set(v){ saveCloseAction = v; } },
  saveFeatureFlags: { configurable: true, enumerable: true, get(){ return saveFeatureFlags; }, set(v){ saveFeatureFlags = v; } },
  savePersonal: { configurable: true, enumerable: true, get(){ return savePersonal; }, set(v){ savePersonal = v; } },
  saveSchedule: { configurable: true, enumerable: true, get(){ return saveSchedule; }, set(v){ saveSchedule = v; } },
  saveSecurity: { configurable: true, enumerable: true, get(){ return saveSecurity; }, set(v){ saveSecurity = v; } },
  saveTheme: { configurable: true, enumerable: true, get(){ return saveTheme; }, set(v){ saveTheme = v; } },
  saveToggleHotkey: { configurable: true, enumerable: true, get(){ return saveToggleHotkey; }, set(v){ saveToggleHotkey = v; } },
  saveUiPref: { configurable: true, enumerable: true, get(){ return saveUiPref; }, set(v){ saveUiPref = v; } },
  saveUserRules: { configurable: true, enumerable: true, get(){ return saveUserRules; }, set(v){ saveUserRules = v; } },
  setOpTarget: { configurable: true, enumerable: true, get(){ return setOpTarget; }, set(v){ setOpTarget = v; } },
  switchVersion: { configurable: true, enumerable: true, get(){ return switchVersion; }, set(v){ switchVersion = v; } },
  themeAccentInput: { configurable: true, enumerable: true, get(){ return themeAccentInput; }, set(v){ themeAccentInput = v; } },
  themeBgInput: { configurable: true, enumerable: true, get(){ return themeBgInput; }, set(v){ themeBgInput = v; } },
  themePreset: { configurable: true, enumerable: true, get(){ return themePreset; }, set(v){ themePreset = v; } },
  themeReset: { configurable: true, enumerable: true, get(){ return themeReset; }, set(v){ themeReset = v; } },
  toggleAutostart: { configurable: true, enumerable: true, get(){ return toggleAutostart; }, set(v){ toggleAutostart = v; } },
  toggleAutostartAdmin: { configurable: true, enumerable: true, get(){ return toggleAutostartAdmin; }, set(v){ toggleAutostartAdmin = v; } },
  toggleAutostartHide: { configurable: true, enumerable: true, get(){ return toggleAutostartHide; }, set(v){ toggleAutostartHide = v; } },
  toggleDarkMode: { configurable: true, enumerable: true, get(){ return toggleDarkMode; }, set(v){ toggleDarkMode = v; } },
  toggleLivePane: { configurable: true, enumerable: true, get(){ return toggleLivePane; }, set(v){ toggleLivePane = v; } },
  toggleOpTargetMin: { configurable: true, enumerable: true, get(){ return toggleOpTargetMin; }, set(v){ toggleOpTargetMin = v; } },
  toggleSchedule: { configurable: true, enumerable: true, get(){ return toggleSchedule; }, set(v){ toggleSchedule = v; } },
  toggleUiScale: { configurable: true, enumerable: true, get(){ return toggleUiScale; }, set(v){ toggleUiScale = v; } },
  viewKbFile: { configurable: true, enumerable: true, get(){ return viewKbFile; }, set(v){ viewKbFile = v; } },
  viewLatestRepairReport: { configurable: true, enumerable: true, get(){ return viewLatestRepairReport; }, set(v){ viewLatestRepairReport = v; } },
  viewMemory: { configurable: true, enumerable: true, get(){ return viewMemory; }, set(v){ viewMemory = v; } },
  viewWsFile: { configurable: true, enumerable: true, get(){ return viewWsFile; }, set(v){ viewWsFile = v; } },
  wecomArchiveConsent: { configurable: true, enumerable: true, get(){ return wecomArchiveConsent; }, set(v){ wecomArchiveConsent = v; } },
  wecomArchiveScan: { configurable: true, enumerable: true, get(){ return wecomArchiveScan; }, set(v){ wecomArchiveScan = v; } },
  wsHistory: { configurable: true, enumerable: true, get(){ return wsHistory; }, set(v){ wsHistory = v; } },
  wsHistoryRestore: { configurable: true, enumerable: true, get(){ return wsHistoryRestore; }, set(v){ wsHistoryRestore = v; } },
  wsHistoryView: { configurable: true, enumerable: true, get(){ return wsHistoryView; }, set(v){ wsHistoryView = v; } },
});
/* ===== v1.0.756 ESM BRIDGE END ===== */
