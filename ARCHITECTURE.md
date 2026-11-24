# ARCHITECTURE — 技术架构 / 关键链路 / 开发环境

> v1.0.756（评审 P0-1 文档分层）：本文件由 `README.md` 的架构与构建章节拆出。
> README.md 只保留用户向的功能总览与关键事实速查（<=300 行）；
> 踩坑经验在 [docs/踩坑经验库.md](docs/踩坑经验库.md)，逐版本记录见
> [docs/版本历史.md](docs/版本历史.md) 与 [CHANGELOG.md](CHANGELOG.md)。
> **改代码前先扫一遍踩坑经验库的相关小节**（AGENTS.md 第三条）。

---

## 技术架构（v1.0.405 起；版本号以 `version.py` 为准，不在文档里写死，由 `tests/test_doc_constants.py` 校验）

```
Lingban.exe (PyInstaller onefile, --noconsole)
├── backend.py          Flask app 骨架 + 启动器（鉴权/启动绑定/看门狗/webview；行数随改动变化，不在文档里写死）
├── core/               核心基础层（v1.0.402 自 core_utils 按域拆分，转发壳保兼容）
│   ├── paths.py        路径与产物库/知识库常量（workspace_root/ws_sub/kb_dir）
│   ├── logging.py      日志轮转/事件记录/agent 活动时间戳
│   ├── config.py       配置路径/脱敏/参数拾取/JSON Schema 校验/原子写
│   ├── config_store.py 配置读写/加解密/事务 update_config/DEFAULT_CONFIG
│   ├── workspace_io.py 产物库写入/旧布局迁移/台账/拖拽白名单（DND）
│   ├── ocr.py          OCR 引擎/在线安装（wheel 直解压）/版本更新检查
│   ├── instance_lock.py 单实例 mutex（含管理员重启竞态修复，v1.0.646 自 core/security 改名）
│   ├── secrets.py      DPAPI 密钥保护（v1.0.647 自顶层 security.py 归位）
│   ├── notify.py       任务通知 + Windows 原生 Toast
│   ├── files.py        文件内容解析（pdf/docx/xls/ppt/OCR/HTML，v1.0.647 起含老格式 Office 转换）
│   ├── daemon.py       守护线程/看门狗两原语（v1.0.654：6 套守护骨架收敛于此）
│   ├── result.py       错误契约单源（v1.0.655 B5：err/is_err/err_msg，收读方双读）
│   ├── frontend_check.py 前端结构/JS 语法校验实现（冻结包内可复用）
│   ├── idle.py / portable.py / relocate.py / safe_open.py / smoke.py
│   │                   用户占用检测 / 便携运行时 / 目录归位 / 安全打开 / 打包冒烟
│   ├── strings.py / study_js.py / wecom_state.py
│   └── ─（历史合并：json_utils→config、office→files、security→instance_lock）
├── services/           功能服务层
│   ├── batch_flow.py   批审流水线（v1.0.656 三层化：FlowTransport/ReportExtractor/AuditOrchestrator）
│   ├── rule_check.py   规则判定引擎（确定性规则优先，<1ms 零成本）
│   ├── self_repair.py  自我修复：LLM 体检/白名单自动修复/报告落盘/每日守护
│   ├── scheduler.py    定时任务调度（30s tick + 串行 worker + 看门狗）
│   ├── study_engine.py 学习中心引擎 / learn_capture.py 课件采集
│   ├── knowledge_ops.py 知识库空闲整理/每日 AI 消化 / wecom_archive.py 企微文件归档
│   ├── asr.py          语音转写组件（按需安装）/ model_bench.py 模型评测
│   ├── daily_digest.py 每日工作小结 / todo_watch.py 待办监视（v1.0.654 骨架收敛）
│   ├── conversations.py 会话存档/恢复 / memory_ops.py 三层记忆 / skill_evolve.py 技能自演化
│   ├── version_store.py 版本登记/切换 / usage_stats.py 用量统计 / task_fingerprint.py 指纹
│   └── skill_versions.py 等（详见目录）
├── desktop/            桌面集成
│   ├── close_guard.py  X 关闭拦截（自绘 Win32 对话框 + web 确认层）/全局热键
│   ├── save_dialog_auto.py 另存为弹框自动应答 / tray.py 托盘 / shell.py 壳层装配
├── routes/             HTTP 路由蓝图（统一 url_prefix=/api，18 模块）
│   ├── management.py   组装壳 + 嵌套注册 6 个子蓝图
│   ├── b_models.py     模型管理 / b_repair.py 自我修复 / b_ocr_admin.py OCR 组件
│   ├── b_memory.py     记忆管理 / b_macro.py 任务宏 / b_admin.py 管理/配置/看板/快捷链
│   ├── chat.py         /chat /chat/abort /chat/continue（SSE + 断点续跑）
│   ├── wecom.py        企微 bind/windows/capture/screenshot / conversations.py 会话
│   ├── knowledge.py / workspace.py / templates.py（含 _agent_gen 公共 AI 生成器）
│   ├── skills.py / study.py / personal.py / security.py（安全中心/审批双因子）
├── agent/              对话运行时层
│   ├── loop.py         agent_loop 主循环（ReAct + 无限续跑 + LoopGuard 熔断）
│   ├── loop_ops.py     主循环辅助（配对修复/save_state_safe/judge 升级）
│   ├── loop_guard.py   死循环/交替循环检测 / messages.py 上下文压缩
│   ├── sse_parse.py    【完成】滚动缓冲抽取（v1.0.654 单源）/ sse_stream.py SSE 心跳包装
│   ├── session.py      abort/断点存储 / context.py 上下文拼装 / sys_prompts.py 系统提示词
│   └── auto_loop.py（考试续跑）/ team.py（规划-执行-审查）
├── bridge/             CDP 桥接层（v1.0.655 按域拆分，windows.py 兼容再导出全量名；v1.0.761 由 wecom_bridge 改名，wecom_bridge/ 为兼容 shim）
│   ├── cdp_client.py   CDP 内核（连接池/求值/总超时；_json_result 解析单源）
│   ├── wecom_proc.py   进程探测/绑定/端口避让 / frames.py iframe 上下文
│   ├── windows.py      窗口交互域（_page_ws/_require_page）+ 再导出保 monkeypatch 语义
│   ├── targets.py / screenshot.py / os_windows.py / tucker.py
│   │                   枚举选择 / 截图 / OS 窗口与收纳授权 / 新窗口后台化
│   ├── read.py / act.py / download.py / win_send.py（页面读取/操作/下载/键击发消息）
│   ├── js_loader.py + js/  read 内嵌 JS 外置（match/mui_candidates/read_page/read_fields）
├── agent_tools/        工具注册表 + 执行器（registry→dispatch→executors 表驱动）
├── llm_client.py       LLM 客户端：流式事件/重试/多模型兜底/is_stream_interrupt 单源
├── security_ops.py     安全中心：命令黑白名单/路径保护/审批双因子/审计 JSONL
├── css/style.css       前端样式（设计令牌化，深浅双主题一套变量）
├── index.html          前端骨架（单页全视图；v1.0.756 起只挂一个 <script type="module">）
├── js/                 前端 18 个功能域文件（v1.0.756 起全量 ES Module）+ _entry.js 单一入口
├── build.py            打包编排：版本 +1 + 七道门禁 _run_gate + --release（职责拆入 buildlib/）
├── buildlib/           打包域实现（v1.0.655）：gates/check/verify/backup/aux/release/ship/trace 等 13 模块
├── tools/              开发期脚本（check_frontend 打包门禁 / esm_verify 等价验证 / scan_silent_except 等）
├── requirements.txt    运行时依赖固化（v1.0.405 起；v1.0.661 移除 24 条传递锁行后 28 项，v1.0.756 新增 cryptography 后 29 项）
├── requirements-dev.txt 开发依赖（pyinstaller + pytest + pyflakes + pytest-cov + diff-cover）
├── CHANGELOG.md        逐版本变更记录（归档至 v1.0.544，之后见 README.md 顶部与 docs/版本历史.md）
└── Lingban.spec 打包配置（hiddenimports 构建时自动刷新 + app.ico + version_info）
```

> **运行时数据目录 `产物库/`**（dist 下，exe 实际使用；源码根同名目录为 venv 直跑用）：
> 对话记录/下载/产出/技能/上传 五个基础目录 + 日志（companion.log + 用量/安全审计）+
> 版本（versions.json 近 3 版回退库）+ 断点（会话断点落盘，重启可「继续」，v1.0.405）+
> 系统/api_token.txt（本机 API 鉴权令牌，HttpOnly cookie 校验，v1.0.405）+
> 备份（agent 覆盖/删除前的自动备份）+ 宏（任务宏）/知识库/自修复/自进化/记忆。

> **v1.0.401/402 分模块化改造**（按 `docs/archive/重构计划.md` 执行前两阶段，计划已归档）：
> 阶段一目录归位（services//desktop//core + CSS 外置）；
> 阶段二 core_utils 拆入 core/ 十域 + management 拆 6 蓝图（转发壳保兼容，
> 路径函数属性查找——patch 定义处即可全局生效）。URL 与功能零变化，
> 197 项回归全绿。阶段三（模块热更新）按计划暂缓。
> **v1.0.162 结构重构说明**（历史）：wecom_bridge/agent_tools 升级为包，
> 门面 re-export 保持 import 兼容，路由表 44 条前后一致。

### 关键链路

1. **CDP 接管**：目标客户端需以 `--remote-debugging-port=9222` 启动，`--remote-allow-origins` 取 `wecom_bridge.CDP_CLIENT_ORIGIN` 白名单值（v1.0.646 起收紧，不再用 `*`；客户端握手必须固定带同一 Origin 头，否则 403）。
   软件通过 `http://127.0.0.1:9222/json` 枚举窗口，经 websocket（`websocket-client`）向页面注入 JS 执行读取/操作。
2. **Agent 循环**：ReAct 模式——LLM 流式输出（`stream_llm_events`）→ 解析工具调用 → `dispatch_tool` 执行 →
   结果回填上下文 → 继续。常规对话**无限续跑**（v1.0.129）：不设轮次上限，上下文超
   `CONTEXT_COMPRESS_THRESHOLD=40000` 字符自动压缩中间历史后继续推进，不再触顶中断（v1.0.287 由 80000 收紧；实际阈值 = 基础载荷 + 对话预算，见 `agent/loop_ops.py`，基础载荷不参与判定）；
   任务级 `max_rounds`（如流程分析快捷任务 = 5 轮）保留「主预算 + 收尾 10 轮」约束（`TAIL_FINISH_ROUNDS`）；
   `finish` 工具结束并输出最终结论；死循环熔断（写/读/持续报错三通道）与断点续跑（`/api/chat/continue`）兜底。
3. **SSE 流式**：`text/event-stream; charset=utf-8`，事件类型：`token` / `tool` / `tool_result` / `error` /
   `need_continue` / `done` / `aborted`。
4. **技能体系**（对标 WorkBuddy）：`SKILLS` 声明式注册表（id/name/icon/desc/prompt/tools/default，v1.0.95 起增 `judge` 字段：
   `script`/`model`/`none`），`build_agent_tools(cfg)` 按 `enabled_skills` 动态装配工具与提示词；
   `_TOOL_HANDLERS` 注册表 + `dispatch_tool` 路由。
5. **脚本优先判定**：`rule_check.py` 的 `RULES` 注册表按模块实现确定性规则（财务审核/财务单据核查/数据整理/
   数据报表分析/归档整理/报销单填写）；script 模块 prompt 优先调 `run_rule_check`（`data` 结构按模块约定，
   返回 `__error__` 才退回模型），<1ms、不消耗 LLM；理解型模块（答题/审阅/会议纪要等，judge=model）保持模型判定。
6. **产物库**：`_app_dir()/产物库/`，5 个可写段：对话记录 / 下载 / 产出 / 技能 / 上传；
   其余顶层目录（台账/日志/日报/备份/自修复/记忆/宏/会话/版本…）由 `paths.tree_subs()`
   **动态**作只读段展示，新增目录不用回来补文档（v1.0.616 口径）；
   v1.0.738 起另有 2 个**内部运行时段** `cdp_profiles/`（浏览器专用 profile）与
   `ocr/`（OCR 旁挂运行时），由 `paths.runtime_root/runtime_dir` 解析、加进 `_TREE_HIDE`
   不在文件树展示，也不开放给 agent 写入；旧版散在应用目录顶层的同名目录由
   `migrate_runtime_layout()` 一次性并入（只移不删）；
   v1.0.619 起首启**延后**建立产物库（闸门：开机延时 / 任何一次写操作 / 目录本已存在）；
   `ws_rel_path()` 防路径穿越；`save_file/download_file/read_workspace_file/list_workspace_files/create_skill` 全套工具。

### 运行时

- Python venv：`%USERPROFILE%\.workbuddy\binaries\python\envs\wecom\Scripts\python.exe`
  （2026-09-17 迁机重建；路径按当前用户自适应。build.py 的 PYINSTALLER 探测顺序：当前用户 wecom → 当前用户 default → 历史机器固定路径 → PATH）
- Node：`%USERPROFILE%\.workbuddy\binaries\node\versions\22.22.2-3\node.exe`
- 前端界面由 pywebview 6.2.1 包裹（WinForms / WebView2 渲染）
- 日志：`dist/产物库/日志/companion.log`（排错第一入口；v1.0.275 起，dist 顶层那份已废弃）

---


## 开发环境与构建

### 环境初始化（新机器/重建环境）

```bash
PY="$HOME/.workbuddy/binaries/python/envs/wecom/Scripts/python.exe"
"$PY" -m pip install -r requirements.txt -r requirements-dev.txt
# requirements.txt = 运行时依赖（28 项：直接依赖 + OCR 旁挂组；v1.0.661 移除了 24 条传递锁行，
# 传递依赖由 pip 按直接依赖自动解析，已装环境由 build 的 pip check 门禁守护）
# requirements-dev.txt = pyinstaller + pytest（测试/打包）
```

> **⚠ 打包必须用上面这个 venv（v1.0.689 实测踩过）**：`buildlib/env.py` 的
> `_find_pyinstaller()` 按「项目 venv → PATH 里的 pyinstaller」顺序找构建解释器。
> **本机没有这个 venv 时会静默落到 PATH 上的 pyinstaller**——可能是个**别的 Python 版本**
> （实测落到 Python 3.12，而项目是 3.13），于是：
> ① `sync_ocr_runtime` 把**那个版本**的 OCR 组件拷进 `dist/产物库/ocr/`（cp312 二进制），
>    而 exe 跑在 3.13 上 → **OCR 在打包产物里静默不可用**
>    （`test_ocr_runtime_is_self_sufficient` 会红：numpy C 扩展不兼容）；
> ② 打包出来的 exe 内嵌的 Python 版本也跟着错。
>
> 判据：`python -c "import buildlib.env as e; print(e.PYINSTALLER)"` 打印的路径
> **不在 `~/.workbuddy/binaries/python/envs/wecom/Scripts/` 下**就是错了。
> 修法：按上面那段命令把 venv 建出来，用 `$PY` 跑 `build.py`（别用 PATH 上的 python）。

### 每次改代码后的标准验证栈（必须全过）

```bash
# 0. 全程带 PYTHONUTF8=1（Windows 默认 cp936，中文断言/输出会假红）
export PYTHONUTF8=1
PY="$HOME/.workbuddy/binaries/python/envs/wecom/Scripts/python.exe"

# 1. Python 语法（全模块；v1.0.401/402 归位后的新路径）
"$PY" -m compileall -q core services desktop routes agent agent_tools wecom_bridge tools tests

# 1.5 未定义名门禁（v1.0.588 起 build.py 内置，F821 命中即拒打包；开发时也先跑）
"$PY" -c "import build,sys; sys.exit(1 if build.check_undefined_names() else 0)"

# 1.6 开发期目录引用门禁（v1.0.619：生产代码 import tools/tests 这类冻结包里不存在的
#     目录 → 运行期静默失效，--smoke 只导包内已有的模块、发现不了，只能打包前静态判）
"$PY" -c "import build,sys; sys.exit(1 if build.check_dev_only_imports() else 0)"

# 2. 前端结构一键校验：div 配对 / 重复 id / CSS 花括号 /（有 node 时）JS 语法
#    实现已上移到 core.frontend_check（随包发布），tools/check_frontend.py 只是 CLI 壳
"$PY" tools/check_frontend.py .

# 3. 全量回归（v1.0.663：test_dual_model 已重建为标准测试模块——实调测试带
#    @requires_ollama，Ollama 不可达自动 skip 不 fail，无需 --ignore）
#    预期 **0 failed**；本机没装 faster-whisper 时 tests/test_asr.py 有 2 条 skip
#    （缺环境依赖走 skip 不走 fail——v1.0.619 前这里长期挂着 3 条"环境性红"，
#     真回归混在固定噪音里就看不出来）
"$PY" -m pytest tests/ -q
#    提速可选（v1.0.663 实测）：装 pytest-xdist 后加 `-n 4`，5:04 → 1:42（结果集一致 1265/4）

# 4. 杀旧进程 → 重新打包（自动版本 +1，内置前端校验 + except-pass 计数 + dist 备份还原）
#    v1.0.663 起打包门禁默认恒跑冒烟前 pytest（第 3 步口径），跳过加 --no-tests
taskkill /f /im Lingban.exe
python build.py            # 常规升级包（保留用户 config/产物库；含 pytest 门禁）
python build.py --release  # 对外分发包（剥离开发机 config.json/产物库/日志，防内网拓扑泄露）

# 5. 实跑验证（v1.0.405 起 /api/* 需鉴权 cookie：先访问 / 拿 Set-Cookie）
TOK=$(cat "dist/产物库/系统/api_token.txt")
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8787/api/status   # 无 cookie → 401（正确）
curl -s -H "Cookie: __wc_token=$TOK" http://127.0.0.1:8787/api/status       # 200
curl -s -H "Cookie: __wc_token=$TOK" http://127.0.0.1:8787/api/skills       # 200
curl -s -X POST http://127.0.0.1:8787/api/chat -H "Cookie: __wc_token=$TOK" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"回一个字：好"}],"use_wecom_context":false}'
```

### 构建注意

- `build.py` 每次自动 `version.py` 版本号 +1；spec 内嵌全模块 hiddenimports
  （v1.0.555 起由 `buildlib.spec_refresh._autogen_hiddenimports()` 在构建前**自动重写** spec——
  新增模块不用再手工补登，手工双写反而会漏；历史上 tray/version 就是漏登导致冻结态
  ModuleNotFoundError 的。真要排查用 `python -m PyInstaller --log-level WARN` 看缺模块）。
- 打包前务必 `Stop-Process -Name "Lingban" -Force`（或 `taskkill /f /im Lingban.exe`），
  否则 PyInstaller 可能因文件占用失败；**绝不手动 `rm -rf dist`**（产物库/config.json/companion.log
  会被清掉，build.py 有备份-还原逻辑，交给它处理）。
- 产物库有两个位置：源码 `企微伴侣/产物库`（venv 直跑）vs `dist/产物库`（exe 实际用）。
  **测试/恢复目录/验证文件都要落 dist**，否则 exe 里看不到。
- `--release` 模式会把开发机的 config.json/产物库/日志移到 `build/_release_excluded/<时间戳>/`
  （不是删除，可取回）；发布产物首次运行自动生成默认 config。
  **每次剥离都新增一份**，v1.0.619 起剥离完会报出累计份数与体积（那里面是 PII，别整目录外发）；
  确认不再回取时清旧份：`python build.py --prune-release --yes`（始终保留最新一份；
  `_dist_backup/_rollback/_chat_archive` 三处属回滚/存档，这个命令一律不碰）。

### Git 版本管理（v1.0.721 起建库）

- 仓库 2026-11-20 在**项目根**（本 README 所在目录）初始化，首提交即 v1.0.721
  源码基线，并打 tag `v1.0.721`。此前的版本史不在 git 里——以 README 顶部逐版本发版
  说明（v1.0.545+）与 CHANGELOG.md（≤v1.0.544）、`产物库/版本/versions.json` 为准。
- **入库边界**：只提交源码 + 测试 + 文档（py/js/css/html/spec/requirements/*.md/*.ico）。
  不入库（`.gitignore` 兜住）：`dist/`（1.9G exe+产物库）、`build/`（PyInstaller 缓存）、
  `产物库/`（运行期数据/PII）、`config.json`/`api_token*`（密钥）、`Fisheep.rar`（发布压缩包）、
  `version_info.txt`（build 每次重写）、「会话/对话记录」类目录。
- **tag 约定**：每次打包验证通过后 `git tag v1.0.7xx`（与 version.py 同号），
  发版流程 = 验证栈 → build.py（自动 +1）→ 实跑冒烟 → commit + tag。
- 常用：`git status`（看改动）/ `git add -A && git commit -m "..."` /
  `git log --oneline v1.0.720..v1.0.721`（两版之间改了什么）。


## 开发习惯（本项目的约定）

这些是长期迭代中形成的工作方式，改代码时默认遵守：

1. **验证栈固定**：任何改动都必须过「py_compile + node --check + 重打包 + 实跑 curl」四步，缺一不可。
2. **根因优先**：问题先查 `dist/产物库/日志/companion.log` 和真实 DOM/CDP，不靠猜；用日志证据链定位（对比时间戳）。
3. **工具选择器要覆盖现代组件**：`querySelectorAll` 的选择器要包含 `[role="button"]/[role="textbox"]/label` 等，
   不能只依赖原生 tag（input/select）——MUI/React 组件大量用 div+role。
4. **拼接 JS 禁止 `//` 注释**：Python 字符串拼出的单行 JS 里 `//` 会把后续所有代码注释掉，只能用 `/* */` 或不写。
5. **交互类操作默认"不自动提交"**：agent 填完表单不点提交/保存，必须用户确认——不可逆操作边界。
6. **技能 prompt 要"硬约束"**：把实测跑通的步骤、异常兜底（重读/跳过/如实报告）、禁止行为（如"未答 0 前不许交卷"）写死。
7. **长任务防呆**：SSE 加看门狗、LLM 加流式读超时、工具加重试——任何单点挂起都不该让前端白等或断连。
8. **前端消息顺序**：工具块要 insertBefore 到 agent 消息之前，最终总结保持在底部（滚动必见）。
9. **多轮文本隔离**：非首轮 LLM 输出前插分隔线，最终汇报前插【完成】分隔，避免文本粘连。
10. **产物落地**：报告/下载/技能等一切产出必须 save_file 落产物库，并在 final_answer 里给出路径。

11. **清理只碰可再生物，且默认只预览**：`python tools/junk_clean.py` 列清单（不删），加 `--apply` 才执行。
    白名单 = 字节码/测试缓存、台子的 headless Chrome profile、TEMP 里台子与测试自己 `mkdtemp` 留下的工作区、台子截图；
    `build/_pack_*.log`（历次打包取证）要 `--include-logs` 才动。**黑名单写死不可删**：`dist/`、`产物库/`、`docs/`、
    `build/_rollback/`（无 git 时唯一回滚手段）、`build/_dist_backup/`（普通打包靠它还原）、`build/_chat_archive/`（归档对话）；
    判据与"删错就红"的锁在 `tests/test_tools_junk_clean.py`（本机实测一次清出 334.4 MB + 3390 个测试泄漏的合成工作区）。

12. **发版台账先写再打包**：任何新版本号进 `产物库/版本/versions.json` 之前，**必须先在 README 顶部写发版条目**——
    `test_shipped_versions_have_readme_entry` 会拦（README 里搜不到 `v<版本号>` 就红），build 自动回滚且**不产 dist**。
    漏写过一次（v1.0.672 登记了但 README 无条目 → 门禁红、构建回滚）。
13. **语义色不随主题**：成功绿 / 警告橙 / 错误红是**语义**，不能被深浅主题染（`--ok-*`/`--warn-*`/`--err-*` 族有回归反向锁）。
    图标体系同理：`data-ic` 占位由 `js/16-icons.js` 水合成 SVG 线条图标，跟随 `currentColor` 自动适配主题——
    **不要用 emoji 或滤镜 hack 去做图标**（`--icon-shadow`/`filter:brightness` 那套已撤）。
14. **不得替换的 emoji**：①语义状态徽章（✅❌🟢🟡——颜色即语义，且被测试锁死）；②用户自填的快捷入口 emoji（属**用户内容**，
    不是产品资源）；③空态大 emoji。这三类不参与 SVG 替换。
15. **同一语义只留一份实现**：脱敏判据、完成标记抽取、体积口径、portable 路径解析……本项目已因"两份实现各自演化"吃过多次亏
    （详见 6.22 最后一条）。新增实现前先搜有没有现成的；确需并存时，让新的一份**引用**旧的一份，别各写各的。

---


### v1.0.470-488 增量能力

- **多任务并行（v1.0.488）**：工作台对话容器化——每个会话独立容器/断点/中断控制器，切换会话不断流（后台任务继续跑，列表 ⏳ 标记）；工作台 + 助手对话可同时各跑多个
- **每任务独立模型（v1.0.488）**：输入框模型下拉按会话记忆，任务 A 用本地小模型跑批、任务 B 用远端大模型出方案；「继续」恢复断点时保持该会话的模型
- **三模式加固（v1.0.472-486）**：先出方案流式化+推理过程实时可见+SSE 序列化修复（端到端契约测试锁定）；停止秒断+状态条永不卡「停止中」；只问答 readonly 随断点持久化（ask_user 追问/重启/继续后写类工具仍全部拦截）
- **企微文件知识库（v1.0.478）**：聊天附件与企业网盘明文文档静默解析入库（增量指纹台账，每 6 小时），消息文本加密属合规边界不做破解
- **组件体检中心（v1.0.474-477）**：七类组件状态/版本/一键修复（OCR/语音/dsh 软件内装、Git/Node 国内源链接、打包环境一键 pip 升级）
- **可靠性专项（v1.0.470-483）**：LLM 读超时 90s（根治响应头等待永挂）+ 重试感知停止；知识库消化给用户对话让路（在途 LLM 检测 + 启动缓冲）；停止后状态条强制复位；产物库月报/宏卡片死引用修复
- **发布与安全（v1.0.474/486）**：--lean 精简发布（OCR 旁挂出 exe，首用自动国内镜像下载）；OCR wheel Requires-Python 校验 + zip-slip 防护；只问答安全加固；全量代码审查 79 项发现修 42 项（含 PII 出源码、工具双定义、批处理互斥+断点续跑）

### v1.0.242-313 增量能力（功能总览更新至当前版本）

- **跑课引擎（v1.0.276+）**：学习中心无人值守逐门完成学习平台课程（视频拖尾/文档/考试试错提分），考试闭环（考前自动采集课件+KB 模糊匹配、无限次数单变量试错、Bing RSS 检索兜底），断点续跑与空转熔断
- **PC 操控与接管（v1.0.297+）**：6 个 OS 级工具（截屏/文件/命令/程序/剪贴板/系统信息，删除只进回收站）+ deepseek-harness 接管通道（复杂电脑任务委托独立 agent），未启动企微也能对话并操控电脑
- **知识库（v1.0.294+）**：课件/题库/制度三分类归档、空闲自动整理（INDEX.md）与每日消化摘要；经验库跨任务沉淀
- **办公三件套（v1.0.313）**：`create_excel`（多表/表头样式/公式）/ `create_docx`（标题/段落/列表/表格）/ `create_pptx`（标题+要点/备注）——Agent 直接产出可验收的交付文件到产物库
- **三工作模式（v1.0.313）**：🤖 直接干 / 📋 先出方案（单次 LLM 产出可确认方案+一键按方案执行）/ 💬 只问答（写类工具全部拦截）
- **智能体团队（v1.0.312）**：聊天行 👥 触发「规划 → 执行 → 审查」三角色流水线（judge 模型当规划/审查者，审查不通过自动带意见补跑一轮），阶段卡片实时可见
- **三层记忆（v1.0.312-313）**：用户固定规则（设置页手编、最高优先）+ 全局记忆（对话完成后 LLM 窄口径自动沉淀到 产物库/记忆/，跨会话注入）+ 项目上下文（知识库/经验库）
- **定时自动化（v1.0.313）**：设置页创建定时任务（daily HH:MM / every N minutes），到点完整执行一轮 Agent，结果落 产物库/产出/定时_*.md
- **技能中心扩展（v1.0.303-313）**：自定义技能 CRUD、公共技能库（12 个精选技能内置快照 + 远程清单 URL，一键安装）、翻译润色/代码助手/每日简报
- **设置页与功能管理（v1.0.306-312）**：界面偏好/功能管理四总闸（技能/连接器/团队/记忆，停用立即生效）/用户规则/定时任务/OCR 组件状态
- **性能与分发（v1.0.306-307）**：OCR 栈旁挂 dist/产物库/ocr + 按需在线安装（4 镜像回退），exe 146.8→43.7MB、冷启动 3.4→1.8s；只发一个 exe 即全功能
- **聊天体验（v1.0.304-313）**：打字机匀速出字、气泡按内容收缩、推理过程可折叠、SSE 终止事件兜底（不再出现「每字一行」窄气泡）
- **创作类先澄清（v1.0.311）**：让 Agent 做 PPT/文档/报告时先一轮问齐用途/风格/篇幅/素材/交付格式，再开工

---

