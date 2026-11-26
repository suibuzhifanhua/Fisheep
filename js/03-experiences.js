let __exps = [];
let __skillOptions = [];
async function loadExperiences(){
  const list = $("expList");
  showLoading(list,"加载经验…");
  try{
    // 并行拉取经验列表 + 技能清单（模块下拉选项）
    const [er, sr] = await Promise.all([
      getJson("/api/experiences"),
      getJson("/api/skills").catch(()=>({skills:[]}))
    ]);
    __exps = er.items || [];
    __skillOptions = sr.skills || [];
    renderExpModuleOptions();
    renderExps();
  }catch(e){
    showEmpty(list,"加载失败："+escapeHtml(String(e)),{ic:"alert",title:"加载失败"});
  }
}
function renderExpModuleOptions(){
  const sel = $("expModule");
  const opts = ['<option value="">（通用/未分类）</option>'];
  for(const s of __skillOptions){
    if(s && s.id) opts.push('<option value="'+escapeHtml(s.id)+'">'+escapeHtml((s.icon||"")+ " "+ (s.name||s.id))+'</option>');
  }
  sel.innerHTML = opts.join("");
}
function renderExps(){
  const list = $("expList");
  if(!__exps.length){
    showEmpty(list,"把各模块踩过的坑和解决方法记下来，下次遇到直接复用。",{ic:"notebook",title:"暂无经验记录",action:"openExpForm()",actionLabel:"新增经验"});
    return;
  }
  // 按模块分组
  const groups = {};
  for(const e of __exps){
    const k = e.module || "（通用）";
    (groups[k] = groups[k] || []).push(e);
  }
  let html = "";
  for(const [mod, items] of Object.entries(groups)){
    html += '<div style="margin-bottom:16px">';
    html += '<div style="font-size:var(--fs-md);font-weight:600;color:var(--muted);margin-bottom:8px">📁 '+escapeHtml(mod)+'（'+items.length+' 条）</div>';
    for(const e of items){
      html += '<div class="exp-card" style="border:1px solid var(--border);border-radius:var(--r-md);padding:10px 12px;margin-bottom:8px;background:var(--panel2)">';
      html += '<div style="font-size:var(--fs-md);margin-bottom:6px"><b>问题：</b>'+escapeHtml(e.problem||"（未填写）")+'</div>';
      html += '<div style="font-size:var(--fs-md);margin-bottom:8px"><b style="color:var(--brand-fg)">解决：</b>'+escapeHtml(e.solution||"（未填写）")+'</div>';
      html += '<div class="row" style="justify-content:space-between;gap:8px">';
      html += '<span style="font-size:var(--fs-xs);color:var(--muted)">更新：'+escapeHtml(e.updated||"")+'</span>';
      html += '<span class="row" style="gap:6px">';
      html += '<button class="ghost" onclick="openExpForm(\''+jsStr(e.id)+'\')">编辑</button>';
      html += '<button class="ghost" style="color:var(--err-fg)" onclick="delExp(\''+jsStr(e.id)+'\')">删除</button>';
      html += '</span></div></div>';
    }
    html += '</div>';
  }
  list.innerHTML = html;
}
function openExpForm(id){
  $("expForm").style.display = "block";
  $("expId").value = id || "";
  const e = id ? (__exps.find(x=>x.id===id) || null) : null;
  $("expModule").value = e ? (e.module||"") : "";
  $("expProblem").value = e ? (e.problem||"") : "";
  $("expSolution").value = e ? (e.solution||"") : "";
}
function closeExpForm(){
  $("expForm").style.display = "none";
  $("expId").value = "";
}
async function saveExp(btn){
  const id = $("expId").value;
  const payload = {module: $("expModule").value, problem: $("expProblem").value.trim(), solution: $("expSolution").value.trim()};
  if(!payload.problem && !payload.solution){ toast("问题与解决方法至少填一项","warn"); return; }
  btn.disabled = true;
  try{
    const url = id ? "/api/experiences/"+id : "/api/experiences";
    const method = id ? "PUT" : "POST";
    const r = await fetch(url, {method, headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
    const j = await r.json();
    if(!j.ok){ toast("保存失败：" + (j.error||""),"err"); return; }
    closeExpForm();
    loadExperiences();
  }catch(e){ toast("保存失败："+String(e),"err"); }
  finally{ btn.disabled = false; }   // v1.0.616：原来写在 try 之后——服务端回 ok:false 时
                                     // 上面那条 return 会跳过复位，保存按钮从此永久禁用
}
async function delExp(id){
  // v1.0.471（UI#6）：原生 confirm → confirmAsk
  confirmAsk("删除经验", "确定删除这条经验？", async ()=>{
    try{
      const r = await fetch("/api/experiences/"+id, {method:"DELETE"});
      const j = await r.json();
      if(!j.ok){ toast("删除失败：" + (j.error||""),"err"); return; }
      loadExperiences();
    }catch(e){ toast("删除失败："+String(e),"err"); }
  }, "删除");
}
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {
  __exps, __skillOptions, closeExpForm, delExp, loadExperiences, openExpForm,
  renderExpModuleOptions, renderExps, saveExp
};
Object.defineProperties(window, {
  __exps: { configurable: true, enumerable: true, get(){ return __exps; }, set(v){ __exps = v; } },
  __skillOptions: { configurable: true, enumerable: true, get(){ return __skillOptions; }, set(v){ __skillOptions = v; } },
  closeExpForm: { configurable: true, enumerable: true, get(){ return closeExpForm; }, set(v){ closeExpForm = v; } },
  delExp: { configurable: true, enumerable: true, get(){ return delExp; }, set(v){ delExp = v; } },
  loadExperiences: { configurable: true, enumerable: true, get(){ return loadExperiences; }, set(v){ loadExperiences = v; } },
  openExpForm: { configurable: true, enumerable: true, get(){ return openExpForm; }, set(v){ openExpForm = v; } },
  renderExpModuleOptions: { configurable: true, enumerable: true, get(){ return renderExpModuleOptions; }, set(v){ renderExpModuleOptions = v; } },
  renderExps: { configurable: true, enumerable: true, get(){ return renderExps; }, set(v){ renderExps = v; } },
  saveExp: { configurable: true, enumerable: true, get(){ return saveExp; }, set(v){ saveExp = v; } },
});
/* ===== v1.0.756 ESM BRIDGE END ===== */
