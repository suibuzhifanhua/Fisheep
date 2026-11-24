// ══════ v1.0.677（UI · 单一事实源）：聊天输入区（composer）模板化 ══════
// 此前工作台（opPane）与助手对话（setChatPane）各写了一份**逐字重复**的
// composer 结构（附件条 / 输入框 / 模型·权限·思考三个下拉 / 上传·语音·放大·发送），
// 两份之间只差 id 前缀、几处 title 文案与按钮顺序。任何一处改版都必须同步改两处，
// 漏一处就是两个面版行为不一致——这类"同一语义两份实现"正是本项目反复出事的根源。
//
// 现在：一份模板 + 两份配置。改结构只改模板一处，改文案只改配置一处。
//
// 时序说明：脚本放在 body 末尾（DOM 已解析完），但图标用 icAny() 直接吐 SVG
// 而不是 <span data-ic> 占位——后者依赖 DOMContentLoaded 时的 icHydrate()，
// 运行期插入的节点不会被那次扫描覆盖。
(function(){
  const CFG_OP = {
    prefix:"op",
    pane:"opPane", inputId:"opInput", attachId:"attachStrip_op",
    micId:"micOpBtn", sendId:"opSendBtn", sendFn:"sendOperate",
    modelTitle:"本任务使用的模型（按任务记忆）",
    sendTitle:"执行（Enter 发送）",
    placeholder:"例如：帮我看下屏幕上有什么 / 整理下载文件夹 / 在搜索框输入「报销」· 可直接拖入文件/文件夹",
  };
  const CFG_SET = {
    prefix:"set",
    pane:"setChatPane", inputId:"setChatInput", attachId:"attachStrip_set",
    micId:"micSetBtn", sendId:"setSendBtn", sendFn:"sendSetChat",
    modelTitle:"本对话使用的模型（独立记忆）",
    sendTitle:"发送（Enter）",
    placeholder:"让 agent 干活，例如：看看我目标应用现在开着什么 · 可直接拖入文件/文件夹",
  };

  function esc(s){
    return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function ic(name){ return (typeof icAny==="function") ? icAny(name) : ""; }

  function composerHtml(c){
    const p = c.prefix;
    return ''+
      '<div class="attach-strip" id="'+esc(c.attachId)+'"></div>'+
      '<textarea id="'+esc(c.inputId)+'" class="composer-input" placeholder="'+esc(c.placeholder)+'"'+
        ' onkeydown="inputKey(event,\''+esc(c.sendFn)+'\')" oninput="autoResize(this)"></textarea>'+
      '<div class="composer-bar">'+
        '<div class="model-dd" id="'+p+'ModelDD">'+
          '<button class="model-dd-btn" onclick="toggleModelDD(event,\''+p+'\')" title="'+esc(c.modelTitle)+'"'+
            ' aria-haspopup="true" aria-expanded="false">'+
            '<span id="'+p+'ModelName">默认模型</span>'+
            '<span class="mdd-caret" data-ic="chevron" data-ic-label="展开"></span>'+
          '</button>'+
          '<div class="model-dd-menu" id="'+p+'ModelMenu" role="menu"></div>'+
        '</div>'+
        '<div class="model-dd" id="'+p+'PermDD">'+
          '<button class="model-dd-btn" onclick="togglePermDD(event,\''+p+'\')" title="权限级别：agent 动手前要不要先问你"'+
            ' aria-haspopup="true" aria-expanded="false">'+
            '<span id="'+p+'PermName">'+ic("shield")+' 自动审批</span>'+
            '<span class="mdd-caret" data-ic="chevron" data-ic-label="展开"></span>'+
          '</button>'+
          '<div class="model-dd-menu" id="'+p+'PermMenu" role="menu"></div>'+
        '</div>'+
        '<div class="model-dd" id="'+p+'ThinkDD">'+
          '<button class="model-dd-btn" onclick="toggleThinkDD(event,\''+p+'\')"'+
            ' title="思考：关闭或调整推理强度（仅本次任务，不改模型配置）" aria-haspopup="true" aria-expanded="false">'+
            '<span id="'+p+'ThinkName">'+ic("brain")+' 跟随模型</span>'+
            '<span class="mdd-caret" data-ic="chevron" data-ic-label="展开"></span>'+
          '</button>'+
          '<div class="model-dd-menu" id="'+p+'ThinkMenu" role="menu"></div>'+
        '</div>'+
        '<button class="ghost attachbtn" onclick="pickFile(\''+esc(c.inputId)+'\')"'+
          ' title="上传文件给 agent 分析（也可直接拖入上方对话区）">'+ic("paperclip")+'</button>'+
        '<button class="ghost" id="'+esc(c.micId)+'" onclick="toggleMic(\''+esc(c.inputId)+'\',\''+esc(c.micId)+'\')"'+
          ' title="语音输入：点击开始录音，再点结束并填入（本地转写，不外发）">'+ic("mic")+'</button>'+
        '<button class="ghost" onclick="openBigEditor(\''+esc(c.inputId)+'\',\''+esc(c.sendFn)+'\')"'+
          ' title="放大编辑" aria-label="放大编辑">'+ic("expand")+'</button>'+
        // v1.0.716（用户：没有特定指向性的按钮用品牌图标）：空闲态通用发送箭头 →
        // Fisheep 品牌标（.brandmark）；运行中 setStop 仍切 stop 方块传达状态
        '<button class="composer-send" id="'+esc(c.sendId)+'"'+
          ' onclick="composerFire(\''+esc(c.pane)+'\',\''+esc(c.inputId)+'\',\''+esc(c.sendFn)+'\')"'+
          ' title="'+esc(c.sendTitle)+'"><span class="brandmark" aria-hidden="true"></span></button>'+
      '</div>';
  }

  function renderComposers(){
    try{
      [["composer_op", CFG_OP], ["composer_set", CFG_SET]].forEach(function(pair){
        const host = document.getElementById(pair[0]);
        if(host) host.innerHTML = composerHtml(pair[1]);
      });
    }catch(e){ console.error("[composer] 渲染失败", e); }
  }

  // 立即渲染（脚本位于 body 末尾，DOM 已就绪）。对外暴露以便测试与热修复调用。
  window.renderComposers = renderComposers;
  window.composerHtml = composerHtml;
  renderComposers();
})();
/* ===== v1.0.756 ESM BRIDGE BEGIN (auto-generated; do not hand-edit) ===== */
/* classic <script> 时代顶层声明自动成为全局绑定，index.html 的 211 处内联
 * on*= 处理器与跨文件调用都依赖这一语义。迁移到 <script type="module"> 后
 * 符号收进模块作用域，故显式 export 并用 getter/setter 镜像回 window：
 * 读走 getter、写走 setter，共享可变全局的状态与 classic 完全等价。
 * 清单由 tools/esm_bridge.py 生成并由 node --check 校验，勿手改。 */
export {};
/* ===== v1.0.756 ESM BRIDGE END ===== */
