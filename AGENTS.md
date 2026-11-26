# AGENTS.md — 灵伴（WeComCompanion / Lingban）开发指南

> 面向**接手本项目的 AI 编码代理**。人类开发者读 README.md 即可；本文件是给模型的速查。
> 详细的踩坑经验在 [docs/踩坑经验库.md](docs/踩坑经验库.md)（6.1–6.27，v1.0.756 由 README 拆出，内容逐字保留），**改代码前至少扫一遍相关小节**；架构 / 构建细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。
> ⚠️ 本文件随 `git ls-files` 导出进公共镜像（`../待发布版本/`）——**不要在这里写明文敏感词**
> （个人路径、内网 IP、服务商/公司/模型具体版本标识、雇主平台名），门禁会拦，见第六节。

## 一、这是什么项目

Windows 桌面应用（PyInstaller onefile 打包）：通过 CDP 接管企业微信页面，
用大模型驱动一套工具链（读页面 / 点击 / 输入 / 上传 / 跑脚本），做批审、答题、月报等任务。

- **运行时**：Flask + waitress（8787 端口）+ pywebview（WinForms/Chromium）前端（原生 JS，无框架）
- **入口**：`backend.py` → `core/` `routes/` `services/` `agent/` `agent_tools/` `bridge/`（v1.0.761 由 `wecom_bridge` 改名，兼容 shim 已撤销）
- **前端**：`index.html` + `js/*.js`（按序号分模块）+ `css/style.css`（令牌化，深浅双档）
- **规模**：253 个 .py / 23 个 .js / 104 个测试文件（2026-11-22）
- **主目录含两份代码**——先读下一节「目录模型」，别把公共镜像当源码副本改。

## 二、目录模型（最易误解，先读）

两级布局（v1.0.754 起）：**Fisheep 主目录只是容器**，里面只有两个次级目录：

```
Fisheep/                    ← 主目录（容器，另有一行 README 指路）
├── 本地开发版本/              ← 项目本体 = git 仓库根（平时改/维护就改这里），本目录本身
│   ├── 源码                  ← backend.py core/ agent/ agent_tools/ bridge/ routes/
│   │                           services/ buildlib/ tools/ tests/ js/ css/ index.html
│   ├── publish/             ← 公共资产源：public README.md / CHANGELOG.md（覆盖镜像同名单元）
│   ├── build/               ← 构建缓存（spec/备份/gc 暂存，非源码）
│   ├── dist/                ← 打包产物（Fisheep.exe）
│   └── 产物库/               ← 运行时数据（日志/技能/组件/webview_data）
└── 待发布版本/                ← 自动生成的「GitHub 待上传镜像」（脱敏后），只读，勿手改
```

- 本文件所在目录 = `本地开发版本/` = 仓库根；**所有命令都在这里执行**
  （`cd "$HOME/Desktop/Fisheep/本地开发版本"`）。
- `待发布版本/` 每次 `build.py` 收尾自动重建：以仓库根扁平导出 tracked 文件（公共仓库
  看不到中文子目录）→ 脱敏 → 装公共资产 → 门禁扫描 → orphan 单次提交 → 原子换入
  （六步，`tools/publish_github.py`）。它在仓库根**外面**（主目录级），不进本地 git。
- **镜像里出现「重复文件」（tests/、core/ 等）是正常的**——它是仓库的导出副本。
  pytest 与 8 处静态扫描排除名单都按「待发布版本」名字保底排除；**别删这些排除**，
  否则棘轮计数翻倍假红 / 测试收集撞 `import file mismatch`。
- 换入失败的暂存残留（`待发布版本_stale*`）落在**主目录**级，跨轮自动重试清理；
  极端锁死时手动删（内容是脱敏副本，可复现，删了无损）。
- 改公开展示文案 → 改 `publish/` 里的源，下次 build 自动进镜像；
  直接改 `待发布版本/` 里的文件 = 改缓存，下次 build 就被覆盖。

## 三、动手前必读的 5 条

1. **验证栈固定**（README「开发环境与构建」有完整命令）：
   `py_compile` → `node --check`（js）→ 全量 pytest（**0 failed**）→ 重打包 → 实跑 curl。
   **少一步都不算改完**——本项目历史上多次出现"只跑了单元测试就发包"导致的白屏/静默失效。
   构建收尾还会自动刷公共镜像（门禁红不阻塞打包，只打 ⚠️）。
2. **打包有硬门禁**（`build.py` 内置，任一不过就中止发包）：
   F821 未定义名 / 开发期目录引用 / 巨型函数棘轮 / 静默 pass 棘轮 / 数据安全静默声明 /
   依赖一致性 / 前端结构 / **pytest 全量** / frozen 冒烟（包内模块逐个 import）/ 包内资源逐字节一致。
   **门禁红了不要改基线**——两条棘轮（巨型函数数、裸 except-pass 数）的设计意图是
   "**要么把代码改小，要么把 except 改成留痕**"，改数字等于把闸门焊死。
3. **踩坑经验库 6.1–6.23 是事实源**。本项目 90% 的坑在 README 里有对应条目，
   改之前先搜关键词（见下节"快速定位"）。**同一语义只留一份实现**——
   脱敏、完成标记抽取、体积口径这三处都栽过"两份实现各自演化"。
4. **Windows 环境特有坑很多**（cp936 编码、行尾 CRLF/LF 混用、DPAPI 绑定用户+机器、
   `setx` 截断 PATH、共享 `windll` 单例）——详见 6.8 / 6.10 / 6.16 / 6.21。
5. **不要在生产代码里 import tools/ tests/ docs/**——冻结包里没有这些目录，
   运行期静默失效（`check_dev_only_imports` 门禁会拦，但别依赖它兜底）。

## 四、快速定位：从症状找经验条目

| 症状 | 看 README 哪节 |
|---|---|
| 窗口黑屏 / 白屏 | 6.1、6.2 |
| 工具调用没反应 / read 正常但 click 无效 | 6.3、6.4 |
| 前端不跟随滚动 / 消息顺序错乱 / 流式中断 | 6.5、6.6 |
| 排查不动 / 日志无痕 / 测试假绿 | 6.22 |
| 沙箱 / Git Bash / PowerShell 怪问题 | 6.8、6.11 |
| 产物库读不到 / 两个目录 | 6.9 |
| ctypes / Win32 弹窗 / 签名错乱 | 6.10、6.14 |
| iframe 点不到 / 跨域读不到 | 6.12、6.13 |
| 线程竞态 / 内存对象被串改 | **6.14（最危险，先看）** |
| 安全闸门被绕过 / 危险命令没拦住 | **6.15** |
| 密钥丢了 / 密文被销毁 | **6.16** |
| 端口被占 / 页面重复标签 / 点击点两下 | 6.17 |
| 打包慢 / 测试慢 / 门禁误报 | 6.18 |
| 组件装不上 / 体检超时 / 版本号不对 | 6.19 |
| 长任务被掐断 / 预算乱触顶 | 6.20 |
| 中文乱码 / 零宽空格 / 文件名 | 6.21 |
| 改 exe 名 / 目录名 / 注册表值名 | **6.23** |

## 五、代码地图（改哪块看哪块）

| 目录 | 职责 | 注意 |
|---|---|---|
| `core/` | 基础能力：paths / config / secrets / masking / logging / instance_lock / notify / ocr / smoke | `core/branding.py` 是**品牌标识唯一事实源**（新名 + LEGACY_ 旧名） |
| `agent/` | agent 主循环：loop / team（团队模式）/ auto_loop / context / loop_guard | `loop.py` 是核心，改动影响所有任务 |
| `agent_tools/` | 工具实现 + registry（工具定义↔执行器必须一一对应） | 写操作都要过 `security_ops.gate_write_tool` |
| `bridge/` | CDP 客户端、窗口管理、frames（iframe）、下载 | 端口策略在 `wecom_proc.py`，探活在 `cdp_client.py`；v1.0.761 由 `wecom_bridge` 改名，兼容 shim 已随全仓 import 迁移撤销 |
| `routes/` | Flask 蓝图（b_admin / chat / wecom / skills / models / …） | `routes/chat.py` 负责 SSE 流与落库 |
| `services/` | version_store / self_repair / scheduler / skill_evolve（study_engine 已随学习模块移除，v1.0.768） | 版本回滚在 `version_store.py` |
| `buildlib/` | 构建期工具（门禁、spec 刷新、备份、版本资源） | **不进 exe**；`buildlib` 必须排除 hiddenimports |
| `tools/` | 开发期 CLI + 公共发布工具链 | `publish_github.py` = 脱敏发布六步流水线（见第六节）；生产代码不许 import |

## 六、脱敏发布工具链（碰任何公开内容前必读）

`tools/publish_github.py`：导出 tracked 文件 → 脱敏（MASK_RULES）→ 公共资产（publish/）→
门禁（GATE_PATTERNS）→ orphan 单次提交 → 原子换入。**build 收尾自动触发（`--rebuild`）**；
手动重建：`"$PY" tools/publish_github.py --rebuild`。

- **门禁 7 类**：公司名 / 服务商名 / 模型样例名 / 内部 IP / 个人域名 / 个人路径 / 雇主平台。
  新增敏感词 → MASK_RULES（替换）与 GATE_PATTERNS（拦截）**成对加**，
  只加一半会出现"拦了没换"或"换了没拦"。
- **工具链文件 / 测试 / 本文档里的敏感字面量一律写运行时常量拼装**
  （如服务商名 = `"D" + "aoPro"`）。因为这些文件自身被 `git ls-files` 导出进公共镜像，
  写明文 = 门禁自伤。**注释同属扫描面**——注释里的示例也不得出现明文。
- **「宁可旧不可脏」**：门禁红 → 删 staging、保留上一版镜像、不阻塞构建（只打 ⚠️）。
  所以公共镜像恒处「门禁通过态」；看到 ⚠️ 就是该修规则重跑了，不是可以忽略的警告。
- 换入走 rename（旧镜像 `os.replace` 到 `_stale` 暂存位再清）——免疫实时扫描瞬时锁
  （直接 rmtree 撞 WinError 5 是 749 的根因），**别改回 rmtree**。
- **在根下新增「含源码/测试的目录」时**，必须同步：`pytest.ini` 的 norecursedirs
  （**显式配置覆盖整个默认列表**——要写默认并集+新增，漏一项就收进第三方测试目录）
  + 8 处静态扫描排除名单（skip_dirs / SKIP_DIRS 一族，buildlib×2 处与 4 个测试文件）
  + `.gitignore`（若属生成物）。漏一处 = 字面量/except 计数翻倍，棘轮假红。
- **v1.0.756 起 `tests/` 已按能力域物理重组**：`tests/unit|contract|e2e/`
  （124 个测试文件 + 1 个布局锁）。分类的单一事实源是 `tests/layers.py`，
  **新增测试文件先看它归哪层**，别再往 `tests/` 根放 `test_*.py`——
  `tests/contract/test_layer_layout.py` 有行为锁会红。三层是 `tests/` 的
  **子目录**、静态扫描按 `tests` 整棵跳过，故不需要新增排除项；`pytest.ini`
  的 `norecursedirs` 也不得把这三层排掉（同一条行为锁在盯）。

## 七、版本机制

- `build.py --yes`：自动版本 +1 → 全门禁 → release 提交（`v1.0.NNN …`）。
  **release 提交后 `git show --stat` 核对一遍**（文件清单与预期一致）。
- **README 棘轮**：`test_shipped_versions_have_readme_entry`——versions.json 已登记
  的版本必须在内部 README 有对应条目；**发版条目必须在打包前写**
  （先打包后补条目会让打包后全量 pytest 红，747 的教训）。
- 版本回滚走 `services/version_store.py`（不是 git reset）；`build/` 缓存由 gc 自清，
  别手工动缓存（745 教训：gc 判定口径曾误删回滚备份，已收紧为「版本管理引用的一律保留」）。
- 每次发版自带一次公共镜像刷新（第七节工具链）；远端推送规则见第十节。

## 八、模型接手时的三条纪律

1. **先复现再改**。本项目几乎所有 P0 都是"日志证据链定位"出来的，
   不是推断出来的。先看 `产物库/日志/companion.log`，再用 CDP 抓真实 DOM/异常。
2. **改动要配行为断言，不要 grep 源码文案**。
   本项目有个洞活了十几个版本——回归测试只 grep 源码里有没有那句错误文案、不验行为，
   结果"只问答模式"的写操作实际照常执行。**用行为锁**（真跑 agent_loop，断言执行器零调用）。
3. **改完必须自己审一遍**。本项目的教训是"改完自己审"能抓出一批 P0：
   参数解析失败仍照常执行、流截断被当成功、读侧无根目录约束。
   提交前问自己：**这个改动在最坏输入下会怎样？**

## 九、常用命令

```bash
PY="$HOME/.workbuddy/binaries/python/envs/wecom/Scripts/python.exe"
export PYTHONUTF8=1   # Windows 默认 cp936，中文断言/输出会假红

# 全量回归（并行，约 80s）
"$PY" -m pytest tests -n 8 --dist loadfile -q -p no:cacheprovider

# 单跑一个文件
"$PY" -m pytest tests/unit/test_xxx.py -q -p no:cacheprovider

# 前端结构校验
"$PY" tools/check_frontend.py .

# 打包（自动版本 +1，全门禁，收尾自动刷公共镜像；日志务必重定向落盘）
"$PY" build.py --yes

# 手动重建公共镜像（修了脱敏规则/公共资产后）
"$PY" tools/publish_github.py --rebuild

# 查产物
ls -la dist/*.exe
```

环境硬约定（全部实测过）：
- **只用上面的 wecom venv**，不要用 PATH 上的 python——版本/依赖不一致会静默出
  不可用的打包产物（本项目踩过：旁挂组件 ABI 与 exe 不匹配，打包日志全绿但功能不可用）。
- `build.py` 必须带 `--yes`，否则停在确认提示。
- 后台/长命令结束时 status 显示 "failed" 是 **atexit 噪声**——以 `EXIT=0` 和
  journal 的 done 行为准，不要据 status 重跑。
- venv 的 pytest 无 `--timeout` 参数，别加。

## 十、远程仓库与凭据

- 公共仓库：`github.com/suibuzhifanhua/Fisheep`。
  **远端长期落后于本地**（截至 753 仍 = 749 态：2 个公开 commit + tag v1.0.749
  + Release exe）；本地主仓未推。接手时先 `git -C ../待发布版本 log -1` 看镜像
  版本，`git log -1` 看本地版本，二者差多少都不意外。
- 补推任何版本需要：新短命凭据（classic token 勾 `repo` scope，或 SSH key）+
  **force push**（新 orphan 镜像与远端无共同历史）+ tag / Release 处理。
  外部可见动作（推送/建 tag/传 Release）必须先经用户确认。
- 凭据纪律：token **只内联在单条 push 命令的 URL 里**（`user:TOKEN@host`），
  不写 remote、不落盘；用后立即提醒用户吊销。
- 镜像仓提交身份走 `git -c user.name=… -c user.email=… commit`
  （镜像是新建仓，无 local config，直接 commit 会报 identity unknown）。

## 十一、发给模型的任务描述模板

> **目标**：一句话说清要达成什么用户可见的效果。
> **边界**：明确不能动什么（如"不许改 `core/secrets.py`"）。
> **约束**：引用 README 踩坑经验库的条目号（如"注意 6.14 的并发约束"）。
> **验收**：可执行的判据（如"新 exe 启动后 `webview_data` 仍落在老目录且有新写入"）。

**别只说"修一下 X"**——本项目 90% 的坑都源于"没给约束"，
模型不知道 `windll` 是共享单例、不知道 mutex 名不能随便改、不知道 `cdp_up` 不能整个桩掉。
