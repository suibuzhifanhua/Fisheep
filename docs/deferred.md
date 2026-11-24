# 降级保留项登记（Deferred Items）

> v1.0.755 建立（评审 P0-2 / P1-1）。**本文件是「有意不做」的唯一事实源**。
>
> 起因：`docs/优化审查_v1.0.613.md` 起，一批条目被逐轮标记「未做 / 降级保留 /
> 需产品决策 / 另立排期」，但只在各报告正文里留一句话。到 v1.0.666 时，同一条
> `os_ops.py shell=True` 已被 **6 轮审查反复当新发现重写一遍**，每轮都要重新论证
> 「为什么不改」。没有到期机制，降级保留就会永久沉积成隐形待办。
>
> 登记规则：
> 1. 每条必须写「理由 / 威胁模型 / 复核触发条件 / **复核版本** / 责任人视角的处置动作」；
> 2. 每次优化审查先读本文件，**已登记且触发条件未到的条目不得当作新发现**；
> 3. 触发条件到了仍不动，必须在下一轮报告里显式写「继续保留 + 新理由」；
> 4. 代码里指向本文件的注释不得删除——它是「这条不是遗漏」的证据链。

---

## D1 · `os_ops.pc_run` 的 `shell=True`

| 项 | 内容 |
|---|---|
| 位置 | `agent_tools/os_ops.py` → `pc_run()` 内的 `subprocess.Popen(command, shell=True, ...)` |
| 状态 | **保留**，v1.0.755 起同时提供推荐替代通道 `pc_run_argv`（`shell=False` 零展开）；v1.0.756 起威胁模型 / sunset 条件已写死进 `security_ops.SHELL_CHANNEL_POLICY` |
| 代码内策略 | `security_ops.py::SHELL_CHANNEL_POLICY`（威胁模型 / 四道强制闸门 / sunset 条件 / 复核版本）+ `assert_shell_channel_guard()` 运行时 fail-closed；`pc_run` 执行前强制消费，打包门禁 `check_shell_channel_policy` 校验 |
| 首次登记 | 优化审查 v1.0.613（「改成参数数组会打断现有拼接命令的用法」） |
| 反复出现 | 613 → 615 → 617 → 618 → 631 → 666，共 6 轮，每轮结论相同 |
| 理由 | 现有技能/用户任务大量依赖「一整条拼接命令」（管道 `|`、重定向 `>`、`&&`、环境变量展开、`for` 循环）。改成参数数组会静默打断这些用法，属于**功能回归而非安全加固**。 |
| 威胁模型 | 命令字符串由 LLM 生成，可能被页面提示注入诱导；`cmd.exe` 会展开元字符与 `%VAR%`。既有缓解：`security_ops.check_command`（含 LOLBin / `-EncodedCommand` 多层解码 / 危险模式三档）、`_extract_path_tokens` + `check_paths`（保护路径 fail-closed）、`args_hash` 一次性审批令牌、安全审计 JSONL、`gate_write_tool` 权限档。**已知残余风险**：shell 元字符展开本身仍存在，只是被上述闸门包围。 |
| 缓解措施（v1.0.755） | 新增 `pc_run_argv` 参数数组工具，registry/executors 已注册并在描述里标注【推荐】；能用数组表达的命令不再需要字符串拼接。`pc_run` docstring 明确标注「shell 兼容模式」并指向本文件。 |
| 复核触发条件 | 满足任一条即启动收口：① 连续两个版本内新任务调用 `pc_run` 占比 < 10%（用量数据 `services/usage_stats.py` 可统计）；② 出现一次与 shell 展开相关的真实安全事件；③ 用户明确要求去掉 shell 通道。 |
| 处置动作 | 触发后：把 `pc_run` 降级为「显式 `allow_shell=true` 才可用」的兼容开关，默认拒；再下一版视用量删除。删除前必须确认所有内置技能的提示词已改用 `pc_run_argv`。 |
| v1.0.757 复核 | **继续保留**：触发 ①（pc_run 占比 <10%）当前不可度量——`usage_stats.py` 只记 token 用量、不含工具调用分布，需先补工具调用埋点方能启用该量化判据；触发 ②（shell 展开真实安全事件）、③（用户要求去 shell 通道）均未发生。`SHELL_CHANNEL_POLICY` 四闸门 + 运行时 `assert_shell_channel_guard` + 打包门禁 `check_shell_channel_policy` 复核仍在位、全绿。复核版本后移至 v1.0.767，`security_ops.py::review_version` 已同步递增（本条注释强制）。 |
| 复核版本 | v1.0.767 |

## D2 · `conversations` 增量列表

| 项 | 内容 |
|---|---|
| 位置 | `routes/conversations.py` 会话列表端点 |
| 状态 | **保留**（613 首次登记；615/617/666 均「按 615 结论保留」） |
| 理由 | 现有全量列表在会话量级（本机单机、数百条）下性能足够；增量拉取要引入游标/版本号与前端分页状态，属于**新功能**而非缺陷修复。 |
| 复核触发条件 | 会话数 > 2000 且列表接口 P95 > 500ms，或用户提出多端同步需求。 |
| 处置动作 | 立项时与 `services/conversations.py` 的落库格式一起设计，不单独改路由。 |
| v1.0.757 复核 | **继续保留**：本机会话存档远低于 2000；且 `services/conversations.py::_MAX_KEEP=500` 硬上限使会话文件数结构性无法超过 500，故「会话数 > 2000 且列表 P95 > 500ms」分支在现有落库模型下不可达，唯一可行触发是「用户提出多端同步需求」（未发生）。复核版本后移至 v1.0.767。 |
| 复核版本 | v1.0.767 |

## D3 · 产物库删除接口

| 项 | 内容 |
|---|---|
| 状态 | **保留**（613「不是一次全修该顺手做的事」→ 617/631 保留） |
| 理由 | 删除接口必须与 `security_ops` 保护路径、回收站语义（`pc_files(delete)` 只进回收站）、备份前置三者一起设计；单独开放会造成「界面能删、审计没记」的口径分裂。 |
| 复核触发条件 | 用户明确要求清理产物库文件，且接受「先备份 → 回收站 → 审计」三步语义。 |
| 处置动作 | 复用 `pc_files` 的删除实现，禁止另写一套 `os.remove`。 |
| v1.0.757 复核 | **继续保留**：用户未提出清理产物库文件的诉求，也未确认接受「先备份 → 回收站 → 审计」三步语义；`security_ops` 保护路径 / 回收站 / 备份前置三者联动仍未设计，「禁止另写 `os.remove`、须复用 `pc_files(delete)`」的约束维持不变。复核版本后移至 v1.0.767。 |
| 复核版本 | v1.0.767 |

## D4 · 前端 ES Module 全量迁移

| 项 | 内容 |
|---|---|
| 位置 | `index.html` 单一 `<script type="module" src="js/_entry.js">` + `js/*.js`（18 个功能模块显式 `export`） |
| 状态 | **已闭环**（v1.0.756，评审 P0-3 字面补做）：18 个 js 全量 ES Module 化 |
| 首次登记 | v1.0.755（地基：顺序契约 + CLI + 行为锁 + 三步迁移路径） |
| 理由 |（历史理由，v1.0.756 已不成立）曾认为必须先清零内联 `onclick` 才能改 `type="module"`。实际用「getter/setter 把顶层绑定镜像回 window」的桥接方案，211 处内联处理器与跨文件共享变量零改动即可继续工作。 |
| 已落地 | `js/_entry.js` 单一入口按 `JS_LOAD_ORDER` 顺序副作用 import（ESM 规范保证求值顺序 == import 声明顺序，顶层立即执行代码零改动，规避 TDZ / 循环依赖）；18 个模块末尾 `export` + `Object.defineProperties(window, ...)` getter/setter 桥接 712 个顶层绑定（含 classic 下只在全局词法环境的 `let/const`，如 `$` / `FOLLOW_MS`）；`core/frontend_check.py::check_js_load_order` 改为「单一入口 + import 清单」契约，`check_js_file` 改走 `--input-type=module`；`tools/esm_verify.js` 用 `vm.SourceTextModule` 做 classic-vs-ESM 全局符号等价 / 内联符号可见性 / 删模块报依赖方验证（实测 552 个 classic 符号全覆盖、147 个内联符号全可见、零求值异常）；`tests/contract/test_js_load_order.py` 重写为 ESM 变异锁、`tests/contract/test_esm_migration.py` 新增端到端等价与验证器变异锁；`docs/frontend-module-plan.md` 记录最终方案与「为什么不按原三步走」。 |
| 复核触发条件 | —（已闭环；后续若引入打包器或把内联事件全部改成 addEventListener，另开条目） |
| 处置动作 | — |
| 复核版本 | v1.0.756 |

---

## D5 · 真实行覆盖率「新增代码行 ≥70%」增量门槛

| 项 | 内容 |
|---|---|
| 位置 | `buildlib/coverage_gap.py::check_incremental_coverage()` + `build.py::_run_pytest_gate` |
| 状态 | **已闭环**（v1.0.756，评审 P1-2 字面补做）：真实「新增代码行 ≥70%」增量门槛已硬接入打包，`pytest-cov` / `diff-cover` 已钉版装入构建 venv |
| 首次登记 | v1.0.755（本轮随 P1-2 一起登记，避免下轮审查当新发现重开） |
| 理由 |（历史理由，v1.0.756 已不成立）构建 venv 原先未装覆盖率工具；现依赖已钉版且 `pip check` 干净。**仍不设 `--cov-fail-under`**：70% 只针对新增代码行，全仓绝对值会误杀合格构建，这一条作为口径保留。 |
| 已落地 | `requirements-dev.txt` 钉入 `pytest-cov==7.0.0` / `diff-cover==9.7.1`；`coverage_gap.py` 新增 `parse_diff_added_lines` / `untracked_py_lines` / `parse_coverage_xml` / `incremental_report` / `check_incremental_coverage`；`build.py::_run_pytest_gate` 返回码并入；`tests/contract/test_incremental_coverage.py` 行为锁；`docs/testing-contracts.md` 第 5 节改为「已启用」。 |
| 复核触发条件 | 构建 venv 可钉版安装 `pytest-cov` + `diff-cover`，且全量 pytest 能稳定产出 `.coverage` 与 `git diff` 基线。 |
| 处置动作 |（已执行，v1.0.756）`--cov-report=xml:.coverage.xml` 采集 → 主判据算新增行覆盖率 → `diff-cover --diff-file --fail-under=70` 交叉 → 双判据返回码并入 `_run_pytest_gate`；工具 / XML / git 任一缺失 fail-closed。不再「先采集后开门槛」。 |
| 复核版本 | v1.0.756 |

## D6 · `tests/unit|contract|e2e/` 物理目录重组

| 项 | 内容 |
|---|---|
| 状态 | **已闭环**（v1.0.756，评审 P1-2① 字面补做）：124 个 `test_*.py` 已物理迁入三层目录 |
| 首次登记 | v1.0.755（当时以命名约定替代，理由是目录同步税与搬迁风险） |
| 理由 |（历史理由，v1.0.756 已不成立）用户明确要求改进意见全部字面补做，不接受规范 / 降级登记代替实现。用「完整备份 + 两阶段提交 + 零落盘锚点断言 + 11 条布局行为锁」把搬迁风险压到可验证。 |
| 已落地 | `tests/layers.py` 分类单一事实源（unit 86 / contract 33 / e2e 6）；`tests/contract/test_layer_layout.py` 11 条行为锁（放错层 / 根级冒出 test_*.py / FAST_FILES 失效 / pytest.ini 排掉三层 / 契约目录没接线 / ignore 没跟随 / 历史版本文件被删空 / ROOT 仍是两层，任一必红）；`buildlib/gate_scope.py::FAST_FILES` 8 项同步到 `tests/contract/`；`buildlib/coverage_gap.py::CONTRACT_DIR` → `tests/contract`；`services/self_repair.py` 的 `--ignore` → `tests/unit/test_dual_model.py`；162 处两层 `ROOT`（含跨行写法）改三层；`test_build_guard.py` 同名检测改扫三层；23 个 `test_vNNN_*` 历史文件全部保留且仍含断言。 |
| 复核触发条件 | —（已闭环；`test_layer_layout.py` 常驻防回退） |
| 处置动作 | — |
| 复核版本 | v1.0.756 |

---

## D7 · run_id 的前端回放 UI 与细粒度埋点

| 项 | 内容 |
|---|---|
| 状态 | **已闭环**（v1.0.756，评审 P1-4 字面补做）：前端回放页 + 细粒度埋点均已落地 |
| 首次登记 | v1.0.755 |
| 已落地 | 后端地基（v1.0.755，同上）+ `core/run_replay_page.py` 独立回放页 `GET /runs`（自带 CSS/JS、不引用 js/*.js）、`index.html`「更多」组入口；`record_sse` 抽 token/耗时/判定依据、`summarize` 增 failed/tokens/tool_calls/first_error/slowest_gap、`list_runs_with_status` 列表标红失败任务；`_sse_stream` 增 run_start/elapsed_ms/run_end(duration_ms,events,ok)；`GET /api/runs` 增 status；`tests/contract/test_run_replay.py` 行为锁。 |
| 理由 |（历史理由，v1.0.756 已不成立）改用「独立 HTML 页」范式（同 `core/migration_guide.py`）：不引用 18 个前端 js、不改 JS_LOAD_ORDER，故与 P0-3 的 ES Module 迁移零冲突；细粒度埋点全部在 `_sse_stream` 单收口旁路完成，未侵入业务代码。 |
| 复核触发条件 |（已触发，v1.0.756）用户明确要求改进意见全部字面补做。 |
| 处置动作 |（已执行，v1.0.756）`/runs` 直接消费 `/api/runs` 的 status 与 `/api/runs/<id>` 的 summary+events 渲染列表与时间轴；`record_sse` 保留 token/耗时/判定依据字段，`_sse_stream` 注入 elapsed_ms / duration_ms / events / ok。 |
| 复核版本 | v1.0.756 |

## D8 · DOM 硬闸的「弹确认管道」与数字索引真实 DOM 属性

| 项 | 内容 |
|---|---|
| 状态 | **已全闭环**（v1.0.759）：评审 P1-5 要求的「用户预先登记的白名单放行」路径（v1.0.756）+ 原「后续」列两项增强——「弹确认管道」与「数字索引真实 DOM 属性」（v1.0.759）——均已落地，D8 无遗留 |
| 首次登记 | v1.0.755 |
| 理由 | 评审建议明确允许「强制弹确认**或**按用户预先登记的白名单放行」。弹确认需复用 `__approval_needed__` 管道，而批准后 loop「原样重试」会再次命中硬闸 → 必须像 `wecom_run_js` 的 `_JS_APPROVED`（v1.0.660 H4）那样配一份短时凭据 + 同 hash 登记，否则无限弹窗。v1.0.756 先落零循环风险的「执行器层拒绝 + 白名单 + 审计」；v1.0.759 补齐两项增强。 |
| 已落地 | ① v1.0.756：`core/approval_dom.py`（同义词表 / 归一化 / 审计 / 白名单）、`wecom_bridge/read.py::click_element` 执行器层拦截、`_run_wecom_js_safe` 页面 JS 路径、`tests/contract/test_approval_dom.py` 行为锁（含「同义文案被拦且审计留痕」「硬闸在 CDP 调用之前」）。② **v1.0.759（原「后续」增强，已落地）**：`guard_click` 命中未放行时带 `__approval_needed__`+`kind:"dom"`+`hash` 复用通用审批弹窗（不新增第二套机制）；`security_ops._DOM_APPROVED` 短时凭据（`dom_approval_hash`/`dom_approval_state`/`_mark_dom_approved`，与 `_JS_APPROVED` 同构，一次批准不弹两遍，凭据键只用 `(name,target)`）；前端 `showApprovalModal` 补 `dom` 分支；`click_element` 对 int 数字索引 target 做**只读** CDP 预取 `_prefetch_element_meta` 填 element_meta（fail-open）；`test_approval_dom.py` 再补 5 条行为锁。 |
| 复核触发条件 | （两项增强已落地，暂无未决项）若后续要求把白名单从配置项迁到设置界面、或对纯数字文本 target 也做属性预取，另行登记。 |
| 处置动作 | （已在 v1.0.759 完成）照 `_JS_APPROVED` 模式加短时凭据与同 hash 登记；`click_element` 增加点击前属性预取并把结果传进 `element_meta`。 |
| 复核版本 | v1.0.767 |

---

## D9 · 迁移包口令加密 + 首次启动解密失败引导

| 项 | 内容 |
|---|---|
| 位置 | `core/profile_bundle.py`、`core/config_store.py` 启动路径、首启界面 |
| 状态 | **已闭环**（v1.0.756）：迁移包核心 + `--password` 口令加密（PBKDF2+Fernet）+ 首启解密失败三选一引导全部字面落地；不再以规范 / 降级登记替代实现 |
| 首次登记 | v1.0.755 |
| 理由 | 构建 venv 未装 `cryptography`，自己实现弱口令加密比不加密更危险；界面引导要同时改 `config_store` 启动解密路径与前端，在没有界面承接的情况下改坏启动链路风险高。当前产出为**脱敏明文 zip**，规范里明确要求放进加密介质。 |
| 已落地 | `core/profile_bundle.py`（递归脱敏 / 逐条 sha256 / 导入前备份 / 路径穿越拒绝 / dry-run / CLI）、`tests/contract/test_profile_bundle.py` 行为锁、`docs/migration.md` 规范与安全边界。 |
| 复核触发条件 | 可钉版安装 `cryptography`（同步 `requirements-dev.txt` 与依赖一致性门禁），或用户明确要求「导出包必须口令加密」；首启界面专项立项。 |
| 处置动作 | 加 `--password`（PBKDF2 + Fernet，依赖缺失时 fail-closed 拒绝导出而非静默明文）；`config_store` 解密失败改为显式三选一（导入包 / 保留副本后默认启动 / 明确知晓风险后 `enc:weak:`）并先备份原配置。 |
| 复核版本 | v1.0.756 |

## D10 · 发版元数据自动化

| 项 | 内容 |
|---|---|
| 位置 | `buildlib/release.py` 镜像生成主流程、`待发布版本/RELEASE.json`、公共 README 版本标记 |
| 状态 | **已闭环**（v1.0.756，评审 P2-1 字面补做）：自动化已接入镜像生成主流程 |
| 首次登记 | v1.0.755 |
| 理由 |（历史理由，v1.0.756 已不成立）构建号 / 发布号已分离，`RELEASE.json` 是人工发布号的单一事实源，写入时机由 `rebuild_mirror()` 严格同步，不存在「下次构建覆盖」问题。 |
| 已落地 | `buildlib/release.py::write_release_metadata()`（幂等写 `RELEASE.json` + 公共 README 版本标记 + 镜像树 sha256）；`tools/publish_github.py::rebuild_mirror()` 在 `install_public_assets()` 后、`gate_scan()` 前调用；`publish/RELEASE.json` 人工发布号单一事实源；`tests/contract/test_release_metadata.py` 行为锁；`docs/release-policy.md` 发布清单第 6、8 步改「自动」，第 7 步保留人工发布号。 |
| 复核触发条件 | —（已闭环；推送仍保留人工确认，绝不自动 force push） |
| 处置动作 | — |
| 复核版本 | v1.0.756 |

---

## 已闭环、不再登记

以下项曾被多轮列在「未做」清单，现已落地，**不要再当遗留项重开**：

- `feature_flags` 名单漂移 → v1.0.617 收口到 `FEATURE_FLAG_DEFAULTS`（`core/config_store.py`）；
- `_WS_POOL` 无淘汰 / `portable` rmtree 顺序 / 后台前缀手抄 → v1.0.632；
- 空闲枚举 72,000 线程/天 → v1.0.632 归零；
- CDP Origin 白名单 → v1.0.646（README 关键链路章节曾长期留着 `*` 的过时文案，v1.0.755 已修）；
- 上下文预算器 → v1.0.677 动态阈值；
- 后台审批前端断头 → `js/11-workspace-kb-study.js` 已有完整入口；
- `_BIND_CACHE` 清理留尾 → 已随主线收口。
