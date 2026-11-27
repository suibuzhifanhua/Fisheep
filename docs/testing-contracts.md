# 测试契约约定与覆盖缺口门槛

> v1.0.755 建立、v1.0.756 更新（评审 P1-2）。**本文件是 `test_contract_*` 与
> `buildlib/coverage_gap.py` 的说明文档**，与 `docs/deferred.md` 配套：这里写「怎么做」，
> 那里写「哪些还有意没做」。

## 1. 三层目录已物理重组（v1.0.756）

评审建议按能力域重组测试目录，v1.0.756 已**字面落地**：124 个既有 `test_*.py`
从 `tests/` 根物理迁入 `tests/unit|contract|e2e/`，另加 1 个布局行为锁：

| 层 | 文件数 | 口径 |
|---|---|---|
| `unit` | 86 | 纯逻辑 / 解析 / 状态机 / 工具函数，不依赖真实外部环境或打包产物 |
| `contract` | 33 | 行为锁：门禁 / 契约 / 棘轮 / 常量一致性 / 前端结构 / 资源一致性 |
| `e2e` | 6 | 真实运行时 / 子进程 / 便携运行时 / 自修复 / HTTP / 打包产物 |

- 分类单一事实源：`tests/layers.py::layer_of()`；迁移脚本、布局行为锁、
  `buildlib/gate_scope.py::FAST_FILES` 口径都从这里取。
- 同步过的引用：`coverage_gap.CONTRACT_DIR` → `tests/contract`、
  `services/self_repair.py` 的 `--ignore` → `tests/unit/test_dual_model.py`、
  `FAST_FILES` 8 项全部改到 `tests/contract/`、162 处两层 `ROOT` 改三层
  （含把两层 dirname 拆成两行的跨行写法）、`test_build_guard.py` 同名检测改扫三层。
- `pytest.ini` 的 `norecursedirs` 不排除这三层；`tests/conftest.py` / `layers.py`
  仍留在 `tests/` 根（conftest 的两层 dirname 正好指向仓库根，**不可改**）。
- `test_vNNN_*` 历史版本文件**全部保留**（23 个）并仍在三层中，未删历史；
  `tests/contract/test_layer_layout.py` 用 11 条行为锁把「放错层 / 根级又冒出
  test_*.py / FAST_FILES 失效 / 契约目录没接线 / ignore 没跟随 / 历史文件被删空」
  全部钉死。

`docs/deferred.md` **D6 已闭环**，不再是待办。

## 2. 约定

| 项 | 规则 |
|---|---|
| 契约测试命名 | `tests/contract/test_contract_<能力域>.py`（按红线命名，不按模块名硬套） |
| 登记 | 在 `buildlib/coverage_gap.py::HIGH_RISK` 显式登记 `(契约测试文件名, 关键符号元组)` |
| 硬门槛 | `find_gaps(root)` 非空 → CLI `exit 1`；`tests/contract/test_coverage_gap.py` 在全量 pytest 里断言 `find_gaps(ROOT) == []` |
| 防空壳 | 契约测试必须引用登记表里的**全部**关键符号，且不少于 25 行 |
| 行为锁要求 | 每条契约必须满足「改回坏形状必红」——不是断言「函数存在」，而是断言**行为**（放行/拒绝/重试/关 socket） |

运行：

```bash
python -m buildlib.coverage_gap          # 覆盖矩阵 + 硬缺口，exit 0/1
python -m pytest tests/test_contract_*.py -q   # 只跑契约层
```

## 3. 本轮纳入硬门槛的 5 个高风险模块

评审点名的「>1000 行且无同名专测」文件，各自锁住历史上真实出过事故的红线：

| 模块 | 契约测试 | 锁住的红线 |
|---|---|---|
| `security_ops.py` (1504) | `test_contract_security_gate.py` | `_ALWAYS_ASK` 参数级红线 auto 档也问、full 是唯一豁免、background 直接拒绝、一次性令牌、JS 两道关共用凭据 |
| `llm_client.py` (1311) | `test_contract_llm_stream.py` | `is_stream_interrupt` 单一事实来源、aborted 不重试、中断重试有上限且感知停止、`_ACTIVE_RESPONSES` 必须是强引用 set（WeakSet 会让硬停止永不生效） |
| `agent_tools/executors.py` (1133) | `test_contract_executors.py` | TOOLS ↔ `_TOOL_HANDLERS` 完备性（`LOOP_HANDLED_TOOLS` 显式豁免）、未知工具结构化兜底、非 dict 结果包装 |
| `routes/b_admin.py` (1058) | `test_contract_b_admin.py` | 蓝图名 `mgmt_admin`、(rule, methods) 不重复、endpoint 唯一、孤儿 `/models` 装饰器不得回来 |
| `desktop/close_guard.py` (1430) | `test_contract_close_guard.py` | `apply_web_choice` 投递 WM_CLOSE 而不在 HTTP 线程直接操作窗口、锁内清 pending、失败降级 |

## 4. 观察项与棘轮

`scan_large_sources()` 目前列出 **11 个 >1000 行的生产文件**，其中 5 个已纳入
`HIGH_RISK`（硬门槛），其余 6 个是**观察项**（软提醒，不阻塞打包）：

```
services/batch_flow.py   1990      backend.py            1392
agent/loop.py            1597      agent_tools/os_ops.py 1306
bridge/highlight.py       1111     services/self_repair.py 1039
```

纳入规则与项目巨型函数/裸 except 棘轮同款：**只许往 `HIGH_RISK` 里加，不许删**。
每轮优化审查挑 1–2 个观察项补契约测试并登记，逐步收敛；不要一次性把 11 个
全塞进硬门槛（那等于用一堆空壳契约换一个好看的数字）。

## 5. 真实行覆盖率（增量 70%）—— v1.0.756 已启用

`requirements-dev.txt` 已钉版 `pytest-cov==7.0.0` / `diff-cover==9.7.1`，构建
venv 已安装（`pip check` 干净）。`docs/deferred.md` **D5 已闭环**，不再是待办。

链路（每次 `python build.py --yes` 都会跑）：

1. `build.py::_run_pytest_gate` 追加 `pytest_cov_args()`：
   `--cov=. --cov-report=term-missing:skip-covered --cov-report=xml:.coverage.xml`；
2. pytest 全绿后调用 `coverage_gap.check_incremental_coverage(base=BASE)`，
   返回值**直接作为放行判定**（非 0 即中止发包，不再 fail-open 降级）；
3. `check_incremental_coverage()` 取 `git diff --unified=0 HEAD -- '*.py'` 的新增行
   + `git ls-files --others` 的未跟踪新 `.py` 全部行，与 `.coverage.xml` 的可测行
   取交集，算出「新增代码行覆盖率」并逐文件打印；
4. 另用 `diff-cover --diff-file=<git diff> --fail-under=70` 跑交叉报告，返回码并入判定。

口径：只统计 coverage 认为**可测**的行（Cobertura XML 里的 `line` 节点，含
`hits=0`），注释 / 空行 / docstring 自动不计入分母；生产源码范围复用
`buildlib/scope.py` 单一事实源（镜像 / tests / docs / tools 不算）。
**不设 `--cov-fail-under`**——70% 只针对新增代码行，全仓绝对值会误杀合格构建。

手动验证：

```bash
python -m buildlib.coverage_gap --incremental   # 单独跑门槛，打印逐文件覆盖率
python -m pytest tests/contract/test_incremental_coverage.py -q   # 门槛自身的行为锁
```

行为锁（`tests/contract/test_incremental_coverage.py`）保证：把 70% 调低、删掉 XML 采集、
把返回值改成只打印不 `return`、改回 fail-open 文案，都会红。

## 6. 审批 DOM 级硬闸的边界（评审 P1-5）

`core/approval_dom.py` 是执行器层最后兜底，接线在 `bridge/read.py::click_element`
与 `agent_tools/executors.py::_run_wecom_js_safe`，行为锁在 `tests/contract/test_approval_dom.py`。
当前边界如实登记，避免过度承诺：

| 边界 | 现状 | 后续 |
|---|---|---|
| 判定来源 | `target` 文字 + `element_meta`（text/id/name/aria_label/value/title）；**int 数字索引 target 由 `click_element` 在硬闸前自动做只读 CDP 预取**（`_prefetch_element_meta`，解析顺序与真实点击一致、失败 fail-open）填进 `element_meta`（v1.0.759，D8 闭环） | 纯数字**文本** target（答题卡题号）仍走专门防护，不做预取；纯文本 target 直接用 `target` 本身判定 |
| 命中后行为 | 命中且未放行 → 带 `__approval_needed__`+`kind:"dom"`+`hash` 走通用审批弹窗（挂起→批准→原样重试）并 `security_ops.audit` 留痕；闸门放行时登记 `_DOM_APPROVED` 短时凭据，**一次批准不弹两遍**（v1.0.759，D8 闭环） | 无（弹确认 + 短时凭据已落地） |
| 放行方式 | 配置 `security.dom_approval_allowlist`（`*` 或归一化文案，5s 缓存）；或闸门/硬闸审批弹窗一次性放行 | 与设置界面联动 |
| 误报 | 词表走安全侧（如「不通过」也会命中），由白名单兜底 | 需要时再加否定语境豁免 |

提示词语义闸门（`security_ops.approval_word_hit`）继续做「提前劝阻」，本层做
「最后兜底」——两层互不替代，也不要把拦截逻辑写回提示词里。

