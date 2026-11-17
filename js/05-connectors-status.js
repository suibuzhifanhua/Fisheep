// v1.0.373（用户需求1/2）：跳转设置页的企微绑定卡（纯 agent 模式提示条用）
function goBindSettings(){
  if(typeof openView === "function"){
    openView("settings");
    setTimeout(()=>{ const el=document.getElementById("bindCard"); el && el.scrollIntoView({behavior:"smooth", block:"center"}); }, 200);
  }
}

async function loadConnectors(){
  const list=$("connList"); showLoading(list,"加载连接器中…");
  try{
    const d = await getJson("/api/connectors");
    connSchemas = {};
    list.innerHTML="";
    // v1.0.667（用户："连接器只能保存一个"）：用户自定义连接器（user: true）
    // 与内置同卡渲染——多一个「名称」输入框 + 删除按钮；顶部的「添加连接器」
    // 负责创建新实例（见 addConnector）。
    const addBtn=document.createElement("div"); addBtn.className="row";
    addBtn.style.marginBottom="10px";
    addBtn.innerHTML=`<button class="ghost" onclick="addConnector()">＋ 添加连接器</button>`
      +`<span class="sub" style="margin-left:8px">接入多个外部 API：OA / SaaS / REST 各自配一张卡，互不影响</span>`;
    list.appendChild(addBtn);
    const conns=(d.connectors||[]);
    if(!conns.length){ showEmpty(list,"暂无连接器<br/>点上方「添加连接器」接入第一个外部 API"); return; }
    conns.forEach(c=>{
      connSchemas[c.id]=c.config_schema||[];
      connUserIds[c.id]=!!c.user;
      let fieldsHtml="";
      if(c.user){
        fieldsHtml+=`<div class="field"><label>名称（显示用）</label><input id="conn_${c.id}_name" placeholder="如：内部OA / 报销系统" value="${escapeHtml((c.config&&c.config.name)||"")}"/></div>`;
      }
      fieldsHtml+=(c.config_schema||[]).map(f=>{
        const isPwd=f.type==="password";
        const isMasked=isPwd && c.config && c.config[f.key]==="******";
        const val=isMasked?"":(c.config? (c.config[f.key]||""):"");
        const ph=isMasked?"留空=保留不变":(f.placeholder||"");
        const wid=`conn_${c.id}_${f.key}`;
        if(f.type==="textarea"){
          return `<div class="field"><label>${escapeHtml(f.label)}</label><textarea id="${wid}" rows="2" placeholder="${escapeHtml(ph)}">${escapeHtml(val)}</textarea></div>`;
        }
        return `<div class="field"><label>${escapeHtml(f.label)}</label><input id="${wid}" type="${isPwd?'password':'text'}" placeholder="${escapeHtml(ph)}" value="${escapeHtml(val)}"/></div>`;
      }).join("");
      // v1.0.667 素色图标（与 16-icons 体系一致）：🔌 → plug 线稿
      const ic=(typeof icAny==="function")?icAny(c.icon||"plug"):"";
      const card=document.createElement("div"); card.className="conn-card";
      card.innerHTML =
        `<div class="conn-head"><span class="conn-ic">${ic}</span><b>${escapeHtml(c.name)}</b>`+
        `<span class="pill ${c.configured?'on':'warn'}">${c.configured?'已配置':'未配置'}</span>`+
        `<label class="switch" style="margin-left:auto" title="${c.user?'关闭后该连接器停用（不删除）':'关闭后该连接器停用（不删除）'}"><input type="checkbox" id="conn_en_${c.id}" ${c.enabled?'checked':''} aria-label="启用连接器 ${escapeHtml(c.name)}"/><span class="slider"></span></label></div>`+
        `<div class="conn-desc">${escapeHtml(c.desc||"")}</div>`+
        `<div class="grid2">${fieldsHtml}</div>`+
        `<div class="row" style="margin-top:10px"><button class="primary" onclick="saveConnector('${jsStr(c.id)}',this)">保存配置并${c.enabled?'保持启用':'启用'}</button>`+
        (c.user?`<button class="ghost" style="color:var(--err-fg)" onclick="deleteConnector('${jsStr(c.id)}',this)">删除</button>`:"")+
        `</div>`;
      list.appendChild(card);
    });
  }catch(e){ showEmpty(list,"⚠️ 加载失败<br/>"+escapeHtml(e.message||"网络错误")); toast(fetchErr(e,"加载连接器"),"err"); }
}
let connUserIds={};
function _connSlug(name){
  // 显示名 → id 后缀：只留 ASCII 字母数字（中文/空白剥掉）；空则时间戳兜底。
  // id 会拼进 agent 工具名（{id}_call）并进配置顶层 key，必须与正则契约一致：
  // 后端只接受 user-[A-Za-z0-9_-]{1,64}。
  const s=String(name||"").toLowerCase().replace(/[^a-z0-9]/g,"").slice(0,24);
  return (s || "conn"+Date.now().toString(36).slice(-6));
}
function addConnector(name){
  // v1.0.667（v1.0.666 审查 P1-5）：添加时先问名称——此前默认无名，卡片标题
  // 是 user-connXXXXXX 原始 id，新功能首次体验即坑。promptAsk 是回调式（onOk），
  // 取消时不回调，直接不创建实例。
  if(name===undefined && typeof promptAsk==="function"){
    promptAsk("新连接器的名称", "起个显示名（如：内部OA / 报销系统）——会显示在卡片标题与工具描述里。", "",
      (v)=>_addConnectorNamed(v));
    return;
  }
  _addConnectorNamed(name);
}
async function _addConnectorNamed(name){
  const slug=_connSlug(name);
  const cid="user-"+slug;
  try{
    await apiToast("/api/connectors",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({connectors:{[cid]:{enabled:false,name:name||"",base_url:"",api_key:"",headers:""}}})},"添加");
    toast("已添加——填写 Base URL 后保存并启用","ok");
    loadConnectors();
  }catch(e){ toast(fetchErr(e,"添加连接器"),"err"); }
}
async function saveConnector(cid, btn){
  const schema=connSchemas[cid]||[];
  const payload={ enabled: $("conn_en_"+cid) ? $("conn_en_"+cid).checked : true };
  if(connUserIds && connUserIds[cid]){
    const nEl=$("conn_"+cid+"_name");
    if(nEl) payload.name=nEl.value.trim();
  }
  schema.forEach(f=>{ const el=$("conn_"+cid+"_"+f.key); if(el) payload[f.key]=el.value.trim(); });
  setBtnLoading(btn,true);
  try{
    await apiToast("/api/connectors",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({connectors:{[cid]:payload}})},"保存");
    toast("连接器已保存","ok"); loadConnectors();
  }catch(_){}
  finally{ setBtnLoading(btn,false); }
}
async function deleteConnector(cid, btn){
  confirmAsk("删除连接器", "删除「"+cid+"」后，其配置（含密钥）会被清除，依赖它的任务将失去该外部 API。确定删除？", async ()=>{
    setBtnLoading(btn,true);
    try{
      await apiToast("/api/connectors/"+encodeURIComponent(cid),{method:"DELETE"},"删除");
      toast("连接器已删除","ok"); loadConnectors();
    }catch(_){}
    finally{ setBtnLoading(btn,false); }
  }, "删除");
}

function sbDot(id, on, label){
  // v1.0.507：侧栏状态点统一更新器；v1.0.528-2 扩展 "warn"/"err" 态。
  // v1.0.698（用户需求：五色语义）新增 "ok" 绿（模型可达）/ "none" 黑（未配置模型）；
  // 布尔/字符串均兼容旧调用（true→on 白 / false→off 灰）。
  const el = $(id); if(!el) return;
  const cls = (on === "warn") ? "warn" : (on === "err") ? "err"
    : (on === "ok") ? "ok" : (on === "none") ? "none" : (on ? "on" : "off");
  el.className = "sb-dot-item " + cls;
  el.title = label;
}

// v1.0.660（审查修复 F2）：本文件所有 DOM 写入统一走这两个小工具——
// 视图 DOM 缺失时安静返回 false，而不是抛 TypeError 被 refreshStatus 的外层 catch
// 吞掉后误报「后端未响应」（同类 bug 历史上已复发过一次：死引用把真实故障掩盖成
// 断连提示）。返回值给调用方判断是否继续，语义上「没元素=什么都没做」。
function setText(id, v){
  const el = $(id); if(!el) return false;
  el.textContent = v; return true;
}
function setVal(id, v){
  const el = $(id); if(!el) return false;
  el.checked = !!v; return true;
}
function setHtml(id, v){
  const el = $(id); if(!el) return false;
  el.innerHTML = v; return true;
}

async function refreshStatus(){
  // v1.0.660（审查修复 F2）：入口做关键元素存在性检查——视图 DOM 尚未就绪时
  // 安静退出（返回 false），而不是让后面的裸赋值抛 TypeError、被外层 catch 吞掉
  // 后误报「后端未响应」。wState 是侧栏状态行的锚点，它不在=页面结构还没起来。
  if(!$("wState")) return false;
  try {
    const d = await getJson("/api/status");
    // v1.0.603：打包后 chat_epoch 会变 → 清掉本地 hist_* 会话气泡缓存（偏好不动）
    if(typeof syncChatEpoch === "function") syncChatEpoch(d.chat_epoch);
    // v1.0.660（审查修复 F2）：wecom 段整体兜底——后端早期版本/异常路径不回该字段时
    // d.wecom 为 undefined，后面读 .bound 会 TypeError（被外层 catch 吞成"后端未响应"）。
    const w = d.wecom || {};
    const cd = $("cdpStatus");
    if (cd){
      if (w.bound) cd.innerHTML = `<span class="dot on"></span>企微已绑定 · ${w.windows} 窗口` + (w.changed?' <span class="pill warn">有变更</span>':'');
      else cd.innerHTML = `<span class="dot off"></span>企微未绑定`;
    }
    const md = $("modelStatus");
    // v1.0.528-2（用户反馈"不一致"）：与侧栏三点同一真连通口径——probe 三态决定
    // dot 颜色（ok 绿/auth 黄/down 红/检测中灰），模型名照常展示。
    const mp0 = d.model_probe || {};
    // v1.0.698（用户需求五色）：未配置=黑(none) / 可达=绿(ok) / 凭证被拒、不可达=红(err) / 检测中=灰(off)
    let mdDot = "none", mdText = "未配置模型";
    if (d.ready) {
      mdText = "模型：" + (d.default_model || "");
      if (mp0.state === "ok") mdDot = "ok";
      else if (mp0.state === "auth") { mdDot = "err"; mdText += "（服务可达但凭证被拒）"; }
      else if (mp0.state === "down") { mdDot = "err"; mdText += "（服务不可达）"; }
      else mdDot = "off";   // 检测中/未知——灰但名字照常
    }
    if (md) md.innerHTML = `<span class="dot ${mdDot}"></span>${escapeHtml(mdText)}`
      + (d.ready && d.dual_model ? ` <span class="tag judge" title="双模型分工：判断/决策类操作由大模型处理，机械工具操作走小模型">⚡判断 ${escapeHtml(d.judge_model||'')}</span>` : '');
    setText("wState", w.bound ? ("已绑定" + (w.changed ? "（有变更）" : "")) : "未绑定");
    // v1.0.507：侧栏底部状态横向三点化——dot 颜色 + 悬停 title 显示详情
    sbDot("sbConn", true, "后端：已连接");   // 能收到 /api/status 响应即后端在线
    sbDot("sbWecom", !!w.bound, "企微：" + (w.bound ? ("已绑定" + (w.changed ? "（有变更）" : "")) : "未绑定"));
    // v1.0.528-2：模型点升级为真连通三态——ready=配置存在（原口径），probe=网络实测
    //（/models 元数据端点，零 token）。ok 绿 / auth 黄（服务在凭证被拒）/ down 红 / null 灰（检测中）。
    const mp = d.model_probe || {};
    const mpState = mp.state;
    // v1.0.698（用户需求五色）：未配置=黑(none) / 可达=绿(ok) / 凭证被拒·不可达=红(err) / 检测中=灰(off)
    let modelOn = "none", modelLabel;
    if (!d.ready) {
      modelOn = "none"; modelLabel = "模型：未配置";
    } else if (mpState === "ok") {
      modelOn = "ok"; modelLabel = "模型：已连接（" + (mp.detail || "实测可达") + "）";
    } else if (mpState === "auth") {
      modelOn = "err"; modelLabel = "模型：服务可达但凭证被拒——检查 api_key";
    } else if (mpState === "down") {
      modelOn = "err"; modelLabel = "模型：服务不可达（" + (mp.detail || "连接失败") + "）";
    } else {
      modelOn = "off"; modelLabel = "模型：检测中…";
    }
    sbDot("sbModel", modelOn, modelLabel);
    // v1.0.373（用户需求1/2）：未绑定企微 → 隐藏实时窗口模块与「窗口操作」tab、
    // 强制专注对话（纯 agent 模式，工作台单栏，不再有任何企微占位）
    const notBound = !w.bound;
    document.body.classList.toggle("notbound", notBound);
    const lc = $("liveCard"), tp = $("tab-pane");
    if (lc) lc.style.display = notBound ? "none" : "";
    if (tp) tp.style.display = notBound ? "none" : "";
    // v1.0.373：聊天卡标题与提示条随绑定状态切换（纯 agent 模式不含企微字样）
    // v1.0.511 清爽化：标题去 emoji 前缀（"Agent 对话"/"AI 助手"）
    const ct = $("chatCardTitle");
    if (ct) ct.textContent = notBound ? "AI 助手" : "Agent 对话";
    const oh = $("opTargetHint");
    if (oh && notBound) {
      oh.className = "sub op-target none";
      oh.innerHTML =
        'AI 助手模式（企业微信未绑定）· 问答、生成文档/表格/PPT、联网检索、本机文件与命令<b>照常可用</b>；'
        + '只有需要操作企微页面的任务要先绑定 → <a href="#" onclick="goBindSettings();return false" style="color:var(--accent-fg);font-weight:600">设置 → 企业微信</a>';
    } else if (oh && !notBound && oh.classList.contains("none")) {
      // v1.0.425（用户实测 bug）：绑定成功后旧提示条不清除——启动时未绑定写入
      // "纯 AI 助手模式"，点绑定成功后 notBound=false 只是不再写，旧文本残留
      // （截图实测：卡片已显示"已绑定"，提示条仍引导去绑定）。恢复默认提示文案。
      oh.className = "sub op-target";
      oh.innerHTML = '未选企微窗口 · 可直接对话（详细引导见右侧对话区）';
    }
    // v1.0.516（用户需求）：提示条加 × 关闭按钮——本次启动内不再显示（sessionStorage 记忆）
    // v1.0.660（审查修复 F3）：原来每次刷新（8s 周期）都无条件 appendChild 一个新的
    // × 按钮，8 小时就是 3600 个 × 叠在提示条里。改为"已存在就不重复插"。
    if (oh && oh.innerHTML) {
      try{ if(sessionStorage.getItem("__hintClosed")==="1") oh.style.display = "none"; }catch(e){}
      if(oh.style.display !== "none" && !oh.querySelector(".hint-x")){
        const _hx = document.createElement("span");
        _hx.className = "hint-x"; _hx.textContent = "×"; _hx.title = "关闭本提示（本次启动内不再显示）";
        _hx.onclick = (ev) => {
          ev.stopPropagation();
          oh.style.display = "none";
          try{ sessionStorage.setItem("__hintClosed","1"); }catch(e){}
        };
        oh.appendChild(_hx);
      }
    }
    if (notBound) {
      // v1.0.660（审查修复 F2）：workTabs / tab-chat 也是可能缺失的 DOM 节点，
      // 原来直接 .querySelector(...).classList → 缺失即 TypeError。
      const _wt = $("workTabs");
      const _tc = _wt ? _wt.querySelector("#tab-chat") : null;
      if (_tc && !_tc.classList.contains("active")) {
        if (typeof switchWorkTab === "function") switchWorkTab("chat");
      }
    }
    // v1.0.373（体验5）：绑定按钮措辞随状态——未绑定时是「绑定企微」而非「重新绑定」
    const bb = $("btnBind");
    if (bb) bb.textContent = w.bound ? "重新绑定" : "绑定企微";
    setText("wWin2", w.windows);
    setHtml("mName2", escapeHtml(d.default_model || "—")
      + (d.dual_model ? ` <span class="tag judge" title="判断大模型">⚡${escapeHtml(d.judge_model||'')}</span>` : ''));
    setVal("opt_auto_launch", !!w.auto_launch);
    setVal("opt_auto_cdp", !!w.auto_cdp);
    const _sm = $("opt_silent_mode"); if (_sm) _sm.checked = w.silent_mode !== false;   // v1.0.523：默认开
    // v1.0.651：CDP 端口进设置页。显示的是**配置里的生效值**——端口被占用时
    // ensure_binding 会自动往后找空闲口并写回配置，所以这里看到的就是实际在用的那个。
    // v1.0.652（审计 P1-7）：字段契约修复——后端 bind_status 旧只回 port，前端读
    // w.cdp_port 恒 undefined → 显示 9222 且每 8s 把输入框刷回 9222、任何一次保存
    // 都反向覆盖自动避让端口。现后端双写 cdp_port，前端再兜底 port。
    const _effPort = (w.cdp_port != null ? w.cdp_port : w.port);
    const _cp = $("opt_cdp_port");
    if (_cp && !_cdpPortDirty) _cp.value = (_effPort == null ? 9222 : _effPort);
    const _cpn = $("cdpPortNote");
    if (_cpn) _cpn.textContent = _effPort ? ("当前生效端口：" + _effPort + (w.bound ? "（已绑定）" : "（未绑定）")) : "当前生效端口：9222（默认）";
    if (d.version) setText("verBadge", "v" + d.version);
    const ov=$("opt_version"); if(ov && d.version) ov.value="v"+d.version;
    // v1.0.526 修复（QA）：connBadge/connText 元素已在 v1.0.507 侧栏三点化时删除——
    // 死引用全部清除（曾致 TypeError→wState 误报「后端未响应」）。

  } catch(e){
    // v1.0.522：失联分支补状态清理——wWin2（窗口数）/mName2 只在成功分支写入，
    // 后端失联后残留旧值，出现「后端未响应」与「3 个可访问」并排的自相矛盾显示
    //（用户实测截图）。窗口数是动态状态必须清；模型名静态配置残留无误导，保留。
    setText("wState", "后端未响应（软件可能已退出，请重新启动）");

    const _w2 = $("wWin2"); if (_w2) _w2.textContent = "—";
    sbDot("sbConn", false, "后端：未连接"); sbDot("sbWecom", false, "企微：未知（后端未响应）"); sbDot("sbModel", false, "模型：未知（后端未响应）");   // v1.0.507
  }
}

async function doBind(btnArg){
  const btn = btnArg || $("btnBind");   // v1.0.582：设置页入口传 this（无 #btnBind 时兜底）
  setBtnLoading(btn, true);
  try {
    const post = async (confirm) => {
      // v1.0.194：绑定可能触发企微重启（taskkill + 冷启动 + 等 CDP），给 60s 超时，
      // 防止后端任何意外卡死导致前端无限"等待中"
      const ctrl = new AbortController();
      const t = setTimeout(()=>ctrl.abort(), 60000);
      try {
        const r = await fetch("/api/wecom/bind", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({confirm_kill: !!confirm}), signal: ctrl.signal});
        const txt = await r.text();
        let d;
        try { d = JSON.parse(txt); } catch(e){ throw new Error("后端返回异常：" + txt.slice(0,160)); }
        return d;
      } finally { clearTimeout(t); }
    };
    let d = await post(false);
    // P0-2：企微在运行但未带 CDP flag 时，后端仅返回提示，需用户显式确认才强杀重启
    // v1.0.471（UI#6）：原生 confirm → confirmAsk；后续绑定结果处理包进回调
    if (d.needs_kill){
      setBtnLoading(btn, false);   // 弹窗期间解除 loading，确认后重挂
      confirmAsk("重启企业微信并绑定", "企微正在运行但未带 CDP 调试端口。" + (d.reason || "重启将关闭全部企业微信窗口，未保存的聊天草稿/表单内容可能丢失。") + "是否确认重启企业微信以完成绑定？", async ()=>{
        setBtnLoading(btn, true);
        try{
          const d2 = await post(true);
          if (d2.bound) toast(`绑定成功：${d2.windows||0} 个可访问窗口` + (d2.reason ? `（${d2.reason}）` : ""), "ok", 4000);
          else toast("绑定失败：" + (d2.reason || d2.error || "未知原因") + "——确认企微已登录且未最小化到托盘，再点「重新绑定」", "err", 6000);
          refreshStatus(); refreshWindows();
        } catch(e){ toast(fetchErr(e, "绑定"), "err", 5000); }
        finally { setBtnLoading(btn, false); }
      }, "重启并绑定");
      return;
    }
    if (d.bound) toast(`绑定成功：${d.windows||0} 个可访问窗口` + (d.reason ? `（${d.reason}）` : ""), "ok", 4000);
    else toast("绑定失败：" + (d.reason || d.error || "未知原因") + "——确认企微已登录且未最小化到托盘，再点「重新绑定」", "err", 6000);
    refreshStatus(); refreshWindows();
  } catch(e){ toast(fetchErr(e, "绑定"), "err", 5000); }
  finally { setBtnLoading(btn, false); }
}
// v1.0.667（v1.0.666 审查 P1-6）：窗口诊断——把 /api/wecom/window_diag 的
// CDP 页面 vs OS 窗口两侧标题摊开，用户不用翻 log 就能看出是哪一侧空。只读。
async function runWindowDiag(btn){
  const st=$("wdiagState"), box=$("wdiagBox");
  if(btn) setBtnLoading(btn,true);
  if(st) st.textContent="诊断中…（每侧最长等 5s）";
  try{
    const d=await getJson("/api/wecom/window_diag");
    const cdp=d.cdp_pages||[], os=d.os_windows||[], proc=d.process||{};
    const ss=d.stage_status||{};
    if(box){
      box.style.display="";
      const lines=[];
      lines.push("CDP 页面 " + cdp.length + " 个" + (ss.cdp?("（阶段 "+ss.cdp+"）"):"") + ":");
      cdp.forEach(p=>lines.push("  · " + (p.title||"(无标题)") + "  [" + (p.url||"") + "]"));
      if(!cdp.length) lines.push("  （空）");
      lines.push("");
      lines.push("OS 可见窗口 " + os.length + " 个" + (ss.os?("（阶段 "+ss.os+"）"):"") + ":");
      (os||[]).slice(0,40).forEach(t=>lines.push("  · " + (t||"(无标题)")));
      if(!os.length) lines.push("  （空）");
      lines.push("");
      const procLine=Object.keys(proc).length?("进程：" + JSON.stringify(proc).slice(0,200)):"";
      if(procLine) lines.push(procLine);
      if(!cdp.length && !os.length)
        lines.push("\n两侧都空＝企微进程/CDP 都不可达：先确认企微已登录且没最小化到托盘，再点「检测并绑定」。");
      else if(cdp.length && !os.length)
        lines.push("\n有 CDP 页面无 OS 窗口＝窗口被收纳到后台/被隐藏：正常（收纳守护在干活），若非预期检查「自动收纳」开关。");
      else if(!cdp.length && os.length)
        lines.push("\n有 OS 窗口无 CDP 页＝企微没带调试端口启动：勾「自动注入 CDP」后重新绑定。");
      box.textContent=lines.join("\n");
    }
    if(st) st.textContent="CDP "+cdp.length+" / OS "+os.length;
  }catch(e){
    if(st) st.textContent="诊断失败：" + fetchErr(e,"窗口诊断");
    if(box){ box.style.display=""; box.textContent="诊断失败（后端未响应或超时）：" + (e.message||""); }
  }finally{ if(btn) setBtnLoading(btn,false); }
}
async function testModel(){
  // v1.0.660（审查修复 F2）：mTest 可能不在 DOM（视图未渲染）——原来直接 el.style 赋值
  // 会 TypeError，调用方拿不到任何反馈。缺失时安静返回。
  const el = $("mTest"); if(!el) return;
  el.style.display="inline-block"; el.textContent="测试中…"; el.className="pill";
  try {
    const d = await (await fetch("/api/models/test", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"})).json();
    if (d.ok){
      // v1.0.691（用户："模型测试增加测试出字速度"）：后端 /api/models/test 成功响应
      // 带 speed:{elapsed_s,tokens,tok_per_s}（契约字段名）。旧后端/模型未吐字时
      // 缺 speed 或 tok_per_s=0 → 只报连接成功，不显示假速度。
      const sp = d.speed;
      if (sp && sp.tok_per_s > 0) el.textContent = "✅ 连接成功 · " + sp.tok_per_s.toFixed(1) + " tok/s";
      else el.textContent = "✅ 连接成功";
      el.className="pill"; el.style.background="var(--brand-ink)";
    }
    else { el.textContent="❌ "+(d.error||"失败").slice(0,40); el.className="pill"; el.style.background="var(--err)"; }
  } catch(e){ el.textContent="❌ 后端未运行"; el.className="pill"; el.style.background="var(--err)"; }
  refreshStatus();
}

// v1.0.652（审计 P1-7）：端口「脏标记」——只在用户真的编辑过端口输入时才提交 cdp_port。
// 旧实现任何一次设置保存（勾选框 onchange）都会把输入框当前值 POST 回配置，
// 而 refreshStatus 每 8s 把输入框刷成回退值 9222 → 自动避让选出的端口被反向覆盖。
let _cdpPortDirty = false;

async function saveWecomOpt(){
  // v1.0.660（审查修复 F2）：三个勾选框可能缺失（设置视图未渲染）——原来直接 .checked
  // 读取会 TypeError。缺失按"不提交该项"处理，不影响其余项保存。
  const _al = $("opt_auto_launch"), _ac = $("opt_auto_cdp"), _sm2 = $("opt_silent_mode");
  const payload = {
    auto_launch: _al ? _al.checked : undefined,
    auto_cdp: _ac ? _ac.checked : undefined,
    silent_mode: _sm2 ? _sm2.checked : undefined
  };
  // v1.0.616（P0）：地址框留空＝不修改（后端同口径）。旧写法恒带 `webhook:""`，
  // 于是"只勾一下静默开关"也会把已配置的机器人地址清空。
  const wh = $("opt_webhook") ? $("opt_webhook").value.trim() : "";
  if(wh) payload.webhook = wh;
  // v1.0.651：端口只在"看起来合法"时才提交（1024-65535）；留空/乱填＝不改动，
  // 免得一次误输入把绑定整体拖到一个不可用的端口上。
  // v1.0.652：且只在用户编辑过（_cdpPortDirty）才提交——见上方脏标记说明。
  const _cpv = parseInt(($("opt_cdp_port")||{}).value, 10);
  if(_cdpPortDirty && !isNaN(_cpv) && _cpv >= 1024 && _cpv <= 65535) payload.cdp_port = _cpv;
  _cdpPortDirty = false;
  try {
    const r = await fetch("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const d = await r.json(); if (!r.ok || d.error){ toast("保存失败："+(d.error||"HTTP "+r.status), "err"); return; }
    refreshStatus(); toast(wh ? "设置已保存（含群机器人地址）" : "设置已保存（机器人地址未改动）", "ok");
    loadWecomOpt();
  } catch(e){ toast(fetchErr(e,"保存设置"), "err"); }
}
async function clearWecomWebhook(btn){
  confirmAsk("清除群机器人地址", "清除后依赖「发回企微群」的汇报会失效，需要重新填写地址。确定清除？", async ()=>{
    setBtnLoading(btn, true);
    try{
      await apiToast("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({clear_webhook:true})}, "清除机器人地址");
      toast("群机器人地址已清除","ok"); loadWecomOpt();
    }catch(_){}
    finally{ setBtnLoading(btn, false); }
  }, "清除");
}
async function loadWecomOpt(){
  try { const d = await getJson("/api/config");
    const el = $("opt_webhook"); if(!el) return;
    el.value = "";                                   // 地址含密钥，后端不回传、前端也不回显
    const set = !!(d.wecom && d.wecom.webhook_set);
    el.placeholder = set ? "已配置（出于安全不回显；留空保存＝保持原地址不变）"
                         : "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...";
    const st = $("webhookState");
    if(st) st.textContent = set ? "✅ 已配置机器人地址（本机 DPAPI 加密存储）" : "未配置";
    const cb = $("webhookClearBtn"); if(cb) cb.style.display = set ? "" : "none";
  } catch(e){}
}

// ---- 窗口 ----
// 企微窗口选择 + 实时跟随（按窗口签名跨刷新保持选中，避免被自动抢选）
