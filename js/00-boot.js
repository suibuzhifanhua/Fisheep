// 启动进度遮罩：纯前端、窗口一出现即可见；按 /api/status 真实状态推进，就绪后淡出（绝不无限阻塞）
(function(){
  var ov=document.getElementById("bootOverlay"), bar=document.getElementById("bootBar"),
      pct=document.getElementById("bootPct"), st=document.getElementById("bootStatus"),
      foot=document.getElementById("bootFoot");
  if(!ov) return;
  // v1.0.664 启动动画最短可见时长（用户批准方案 A）：遮罩从脚本执行到移除不得
  // 短于 1600ms，避免后端快时品牌动画"一闪而过"。t0 取脚本执行时刻（首绘前）。
  // finish 内 .done→.hide→移除 的 320+520ms 固定收尾也算在这 1600 里，所以 wait
  // 要扣掉 TAIL_MS；否则最快分支会变成 1600+840=2440ms，超出 2100ms 的验收上限。
  var t0=Date.now(), MIN_VISIBLE_MS=1600, TAIL_MS=320+520;
  var cur=6, target=20, done=false, tries=0;
  function setPct(p){ p=Math.max(0,Math.min(100,Math.round(p))); pct.textContent=p+"%"; bar.style.width=p+"%"; }
  function setMsg(m,err){ st.textContent=m; st.className="bo-status"+(err?" err":""); }
  function finish(){
    if(done) return; done=true; setPct(100);
    var wait=Math.max(0, MIN_VISIBLE_MS-(Date.now()-t0)-TAIL_MS);
    setTimeout(function(){
      ov.classList.add("done");
      setTimeout(function(){ ov.classList.add("hide"); setTimeout(function(){ if(ov.parentNode) ov.parentNode.removeChild(ov); },520); }, 320);
    }, wait);
  }
  // v1.0.660（审查修复 U3）：18s 硬超时后不再"静默淡出"——用户看到界面却不知道
  // 后端到底起没起来。此处补一个"后端似乎启动失败"的引导 + 两个动作按钮：
  //   ① 重试：重新拉一次 /api/status（后端可能只是慢，点了就恢复）；
  //   ② 打开日志目录：调后端已有的 /api/workspace/open {dir:"logs"}
  //      （js/11 的「打开日志文件夹」用的就是它，接口真实存在，不是臆造的）。
  //      后端也起不来时该请求同样失败 → 退化为提示日志路径文本。
  function showFailed(){
    if(done) return;
    var box=document.createElement("div");
    box.className="bo-actions";
    var bRetry=document.createElement("button");
    bRetry.className="primary"; bRetry.textContent="🔄 重试";
    bRetry.onclick=function(){
      bRetry.disabled=true; bRetry.textContent="正在重试…";
      api("/api/status",{cache:"no-store"}).then(function(d){
        var w=d.wecom||{};
        if(d.ready){ target=100; setMsg("重试成功 · 已就绪"); setTimeout(finish,300); return; }
        bRetry.disabled=false; bRetry.textContent="🔄 重试";
        setMsg("后端仍未就绪（可稍后再试，或先看日志）", true);
      }).catch(function(){
        bRetry.disabled=false; bRetry.textContent="🔄 重试";
        setMsg("仍连不上后端——软件可能已退出，请重新启动", true);
      });
    };
    var bLog=document.createElement("button");
    bLog.textContent="📂 打开日志目录";
    bLog.onclick=function(){
      // 后端没起来时这个请求也会失败 → 兜底显示日志路径文本（不臆造接口）
      api("/api/workspace/open",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dir:"logs"})})
        .then(function(){ setMsg("已打开日志目录（产物库/日志/companion.log）"); })
        .catch(function(){
          setMsg("后端未响应，无法自动打开目录。日志在：产物库/日志/companion.log（与软件同目录）", true);
        });
    };
    box.appendChild(bRetry); box.appendChild(bLog);
    ov.appendChild(box);
    setMsg("后端似乎启动失败（已等待 18 秒）——可重试，或打开日志目录查看原因", true);
  }
  (function tick(){ if(done) return; if(cur<target){ cur+=Math.max(.3,(target-cur)*.14); setPct(cur); } setTimeout(tick,80); })();
  function poll(){
    if(done) return; tries++;
    api("/api/status",{cache:"no-store"}).then(function(d){
      var w=d.wecom||{};
      if(foot && d.version && !foot.textContent) foot.textContent="v"+d.version;
      if(d.ready && w.bound && w.windows>0){ target=100; setMsg("就绪 · "+w.windows+" 个窗口"); setTimeout(finish,340); }
      else if(w.needs_kill){ target=93; setMsg("已进入 · 请在「设置 → CDP 绑定」确认重启并绑定"); setTimeout(finish,1700); }
      else if(w.bound){ target=96; setMsg("已进入 · 后台连接绑定目标中…"); setTimeout(finish,1200); }
      else if(d.ready){ target=88; setMsg("已进入 · AI 助手就绪"); setTimeout(finish,1200); }
      else { target=80; setMsg("服务就绪 · 后台初始化中…"); setTimeout(finish,1700); }
    }).catch(function(){ /* 服务未就绪：靠 18s 硬超时兜底 */ });
    if(!done) setTimeout(poll,350);
  }
  setMsg("正在启动…");
  setTimeout(poll,150);
  // 硬兜底：18s 仍未就绪 → 给出失败引导与「重试 / 打开日志目录」两个动作。
  // v1.0.660（审查修复 U3）：注意**不再在这里直接淡出**——淡出后用户看到的是
  // 一个"能进但后端没起"的界面，问题被藏起来了。现在改为显示引导 + 两个动作按钮；
  // 若后端只是慢，poll 链会在就绪时正常 finish()（遮罩自动消失）；
  // 若后端真的没起来，38s 的第二道硬兜底仍会把遮罩淡出——**绝不无限阻塞界面**。
  setTimeout(function(){ if(!done) showFailed(); }, 18000);
  setTimeout(function(){ if(!done) finish(); }, 38000);  // 第二道硬兜底：无论如何淡出
})();
// v1.0.772（暗病巡检③）：全局运行时错误网——此前全软件 0 处 unhandledrejection/error
// 监听，未捕获的异步失败 = 「什么都没发生」式静默失败，用户无从得知操作失败了。
// 这里在最外层兜住：console.error 全量落日志 + toast 限频提示（30s 一条，防错误风暴刷屏）。
// ① toast 定义在 01-core.js（晚于本文件执行），且跨模块不能裸引用，故用
//    typeof 守卫——错误真正发生时 01-core 早已执行完，此时 window.toast 必然可用；
// ② AbortError 过滤：启动轮询/取消流程会主动 abort 请求，属预期行为不算异常；
// ③ resource 加载失败（图标/CSS 404）：error 为 null 且 target 非 window，跳过不打扰。
(function(){
  var lastWarn=0;
  function _warn(label, info){
    try{
      if(info && (info.name==="AbortError" || String(info.message||"").toLowerCase().indexOf("abort")>=0)) return;
      console.error("[global-net]["+label+"]", info);
      if(Date.now()-lastWarn < 30000) return;   // 30s 限频
      lastWarn=Date.now();
      if(typeof toast==="function"){
        var msg="未处理的运行错误："+(info && (info.message || info.name) || "未知");
        toast(msg.length>80 ? msg.slice(0,80)+"…" : msg, "err");
      }
    }catch(_){}
  }
  window.addEventListener("unhandledrejection", function(ev){
    ev.preventDefault();
    _warn("unhandledrejection", ev.reason);
  });
  window.addEventListener("error", function(ev){
    if(!ev.error && ev.target && ev.target !== window) return;
    _warn("error", { name:"Error", message: ev.message || "未知", filename: ev.filename, lineno: ev.lineno });
  });
})();
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {};
/* ===== v1.0.756 ESM BRIDGE END ===== */
