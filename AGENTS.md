# AGENTS.md — 灵伴（WeComCompanion / Lingban）开发指南

> 面向**接手本项目的 AI 编码代理**。人类开发者读 README.md 即可；本文件是给模型的速查。
> 详细的踩坑经验在 [README.md 的「踩坑经验库」](README.md)（6.1–6.23），**改代码前至少扫一遍相关小节**。

## 一、这是什么项目

Windows 桌面应用（PyInstaller onefile 打包）：通过 CDP 接管企业微信页面，
用大模型驱动一套工具链（读页面 / 点击 / 输入 / 上传 / 跑脚本），做批审、跑课、答题、月报等任务。

- **运行时**：Flask + waitress（8787 端口）+ pywebview（WinForms/Chromium）前端（原生 JS，无框架）
- **入口**：`backend.py` → `core/` `routes/` `services/` `agent/` `agent_tools/` `wecom_bridge/`
- **前端**：`index.html` + `js/*.js`（按序号分模块）+ `css/style.css`（令牌化，深浅双档）
- **规模**：223 个 .py / 18 个 .js / 84 个测试文件

## 二、动手前必读的 5 条

1. **验证栈固定**（README「开发环境与构建」有完整命令）：
   `py_compile` → `node --check`（js）→ 全量 pytest（**0 failed**）→ 重打包 → 实跑 curl。
   **少一步都不算改完**——本项目历史上多次出现"只跑了单元测试就发包"导致的白屏/静默失效。
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

## 三、快速定位：从症状找经验条目

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

## 四、代码地图（改哪块看哪块）

| 目录 | 职责 | 注意 |
|---|---|---|
| `core/` | 基础能力：paths / config / secrets / masking / logging / instance_lock / notify / ocr / smoke | `core/branding.py` 是**品牌标识唯一事实源**（新名 + LEGACY_ 旧名） |
| `agent/` | agent 主循环：loop / team（团队模式）/ auto_loop / context / loop_guard | `loop.py` 是核心，改动影响所有任务 |
| `agent_tools/` | 工具实现 + registry（工具定义↔执行器必须一一对应） | 写操作都要过 `security_ops.gate_write_tool` |
| `wecom_bridge/` | CDP 客户端、窗口管理、frames（iframe）、下载 | 端口策略在 `wecom_proc.py`，探活在 `cdp_client.py` |
| `routes/` | Flask 蓝图（b_admin / chat / wecom / skills / models / …） | `routes/chat.py` 负责 SSE 流与落库 |
| `services/` | version_store / self_repair / scheduler / study_engine / skill_evolve | 版本回滚在 `version_store.py` |
| `buildlib/` | 构建期工具（门禁、spec 刷新、备份、版本资源） | **不进 exe**；`buildlib` 必须排除 hiddenimports |
| `tools/` | 开发期 CLI（junk_clean / contrast_scan / ui_*_probe / ui_button_sweep） | 生产代码不许 import |

## 五、模型接手时的三条纪律

1. **先复现再改**。本项目几乎所有 P0 都是"日志证据链定位"出来的，
   不是推断出来的。先看 `产物库/日志/companion.log`，再用 CDP 抓真实 DOM/异常。
2. **改动要配行为断言，不要 grep 源码文案**。
   本项目有个洞活了十几个版本——回归测试只 grep 源码里有没有那句错误文案、不验行为，
   结果"只问答模式"的写操作实际照常执行。**用行为锁**（真跑 agent_loop，断言执行器零调用）。
3. **改完必须自己审一遍**。本项目的教训是"改完自己审"能抓出一批 P0：
   参数解析失败仍照常执行、流截断被当成功、读侧无根目录约束。
   提交前问自己：**这个改动在最坏输入下会怎样？**

## 六、常用命令

```bash
PY="$HOME/.workbuddy/binaries/python/envs/wecom/Scripts/python.exe"
export PYTHONUTF8=1   # Windows 默认 cp936，中文断言/输出会假红

# 全量回归（并行，约 80s）
"$PY" -m pytest tests -n 8 --dist loadfile -q -p no:cacheprovider

# 单跑一个文件
"$PY" -m pytest tests/test_xxx.py -q -p no:cacheprovider

# 前端结构校验
"$PY" tools/check_frontend.py .

# 打包（自动版本 +1，全门禁）
"$PY" build.py --yes

# 查产物
ls -la dist/*.exe
```

## 七、发给模型的任务描述模板

> **目标**：一句话说清要达成什么用户可见的效果。
> **边界**：明确不能动什么（如"不许改 `core/secrets.py`"）。
> **约束**：引用 README 踩坑经验库的条目号（如"注意 6.14 的并发约束"）。
> **验收**：可执行的判据（如"新 exe 启动后 `webview_data` 仍落在老目录且有新写入"）。

**别只说"修一下 X"**——本项目 90% 的坑都源于"没给约束"，
模型不知道 `windll` 是共享单例、不知道 mutex 名不能随便改、不知道 `cdp_up` 不能整个桩掉。
