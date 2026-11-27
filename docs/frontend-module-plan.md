# 前端 ES Module 全量迁移（已落地）

> v1.0.755 建立本文件为「分阶段迁移路径」，当时的结论是**本轮不做全量迁移**。
> v1.0.756（评审 P0-3「全部字面补做」）已完成全量迁移，本文件改为记录
> **最终方案、为什么没有按原三步走、构建期契约与验收结果**。

## 1. 原判断（v1.0.755）与它为什么被推翻

当时判断：

- `index.html` 用 18 个 `<script src="js/NN-*.js">` 按数字前缀顺序加载；
- `js/` 里是全局函数 + `window.X =`，无 `import/export`、无构建工具；
- `index.html`（1866 行）有 211 处内联 `onclick="fn()"` / `oninput="..."`，
  处理器只能看到全局作用域；
- 直接改成 `type="module"` 会把符号收进模块作用域，内联事件处理器**全部失效**——
  不是语法错误，`node --check` 查不出来，只有实跑界面才暴露；本项目历史上黑屏 /
  白屏（README 6.1、6.2）正是这类加载链事故。

所以 v1.0.755 只落地「顺序契约」地基，并规划了三步：① 内联事件清零 →
② 命名空间化 IIFE → ③ ES Module 化。

**v1.0.756 的推翻点**：真正让内联事件失效的不是 `type="module"`，而是
「顶层绑定不再出现在 `window` 上」。只要把每个模块的顶层绑定用
**getter/setter 镜像回 `window`**，全局可见性就与 classic 完全等价，
211 处内联处理器一行都不用改。于是第 ① ② 步（改 HTML / 包 IIFE）
不再必要——那两步恰恰是风险最高、回归面最大的部分。

## 2. 最终方案（v1.0.756）

### 2.1 单一入口 + 副作用 import 保序

`index.html` 只保留一个：

```html
<script type="module" src="js/_entry.js"></script>
```

`js/_entry.js` 按 `core/frontend_check.py::JS_LOAD_ORDER` 顺序写 18 条
**副作用 import**：

```js
import "./16-icons.js";
import "./00-boot.js";
// ... 共 18 条，12-init.js 最后
import "./12-init.js";
export {};
```

关键性质：这 18 个模块彼此**没有 import**（历史上靠 `window` 全局互调），
因此 ECMAScript 规范保证「模块求值顺序 == import 声明顺序」，恰好复现原来
18 个 classic `<script src>` 的按序阻塞执行。于是：

- 顶层立即执行代码（事件监听、`DOMContentLoaded`、`setTimeout`、初始化链）
  **一行都不用动**，不需要重构成 `init()` 再手动排序；
- 不存在静态 import 图的 TDZ / 循环依赖风险（探查发现 `02 ↔ 05`、
  `__presetDirect` 跨文件重复定义等互相引用，用静态 import 图会很难收口）；
- `12-init.js` 仍是最后一个求值，v1.0.526 那类「初始化链提前」事故继续被契约拦住；
- 删掉任一模块文件，`_entry.js` 的 import 在**模块图解析期**就失败并报出
  「`Cannot find module './xx.js' imported from js/_entry.js`」——验收判据成立。

### 2.2 getter/setter 桥接（不是 `Object.assign` 快照）

每个 `js/NN-*.js` 末尾追加自动生成、勿手改的桥接块：

```js
export { fnA, varB, constC };
Object.defineProperties(window, {
  fnA:   { configurable: true, enumerable: true, get(){ return fnA; },   set(v){ fnA = v; } },
  varB:  { configurable: true, enumerable: true, get(){ return varB; },  set(v){ varB = v; } },
  // ...
});
```

为什么必须用 getter/setter 而不是 `Object.assign(window, { ... })`：后者只是
**一次性值快照**。本项目大量共享可变全局（如 `__opPick`、`__followOn`、
`FOLLOW_MS`、`$`）会被某模块重新赋值；快照会让另一个模块读到旧值，且
`window.X = ...` 的写再也回不到模块内绑定。getter/setter 让读走 getter、
写走 setter，`window.X` 与模块局部 `X` 双向同步，共享可变状态与 classic 等价。

桥接还覆盖了 classic 下**只在全局词法环境、不挂在 `window` 上**的顶层
`let/const`（如 `01-core.js` 的 `const $`、`06-windows.js` 的 `FOLLOW_MS`）。
这些符号在 classic 下能被内联处理器和别的脚本读到，迁移后同样必须可见——
实测 ESM 桥接后 `window` 比 classic 全局表多出 153 个这类符号，全部是显式化后的
顶层绑定，而非泄漏。

### 2.3 桥接清单如何生成与校验（零人工猜测）

`export { ... }` 里的每个名字都必须是该模块**真实的顶层绑定**，多一个就会
SyntaxError。生成方式：

1. 宽松正则提取文件里所有 `function` / `var|let|const` / `class` / 解构声明名
   （会带上大量函数内局部变量）；
2. 追加桥接块后反复用 `node --check --input-type=module` 校验，按
   `Export 'X' is not defined in module` 逐个剔除「非顶层绑定」，直到通过；
3. 排除 `window` 原生属性名（`name` / `length` / `top` / `location` …），
   绝不覆盖 DOM/BOM；
4. 最终 18 个模块共 **712 个顶层绑定**被 export + 桥接，`00-boot.js` 与
   `17-composer.js` 是 IIFE 文件（无顶层绑定），追加 `export {};` 即为合法模块，
   它们原本的 `window.renderComposers = ...` 等显式导出照常生效。

生成器与校验器的逻辑固化在 `tools/esm_bridge.py`（迁移脚本）与
`tools/esm_verify.js`（等价验证器）；桥接块带
`/* ===== v1.0.756 ESM BRIDGE BEGIN ... END ===== */` 标记，便于剥离出原始
classic 源码做对比。

## 3. 验收判据与实际结果

| 评审 P0-3 验收判据 | 落地方式 | 实测 |
|---|---|---|
| 任意删掉一个模块文件，构建期 import 解析失败并明确报出依赖方 | `_entry.js` 静态 import + `check_js_load_order` + `esm_verify.js` 的 vm linker | 18/18 模块删除均报 `Cannot find module './xx.js' imported from js/_entry.js` |
| 脚本顺序打乱后初始化仍完整 | `JS_LOAD_ORDER` 单一事实源 + `_entry.js` import 清单逐项比对 | 打乱 / 12-init 提前 / 漏一项 / 多一项，变异锁全红 |
| 内联事件处理器不失效 | getter/setter 桥接 | 147 个内联符号全部在 `window` 可见；`inline_missing=[]` |
| 不引入打包器 | 原生 ESM | 无 Vite / webpack / npm 构建链 |

`tools/esm_verify.js` 用 Node `vm.SourceTextModule`（真实 ESM 语义：严格模式、
TDZ、模块求值顺序）在 mock `window` 上跑两遍：

- Phase A：剥离桥接块后的 17 个文件当 classic `<script>` 顺序执行 → `G_classic`；
- Phase B：17 个模块当 ES Module 顺序求值（桥接生效）→ `G_esm`；
- 断言 `G_esm ⊇ G_classic`、内联符号全覆盖、零求值异常。

实测：`classic=552` / `esm=705` / `missing_in_esm=[]` / `inline=147` /
`inline_missing=[]` / `eval_errors={}`。
`12-init.js` 是纯初始化链（顶层直接调 `refreshStatus` / `loadConvList` 等），
在 mock DOM 下会撞 `while(el.firstChild)` 型死循环——这是 mock 的固有局限、
不是代码缺陷，它的语法 / 桥接 / import 依赖由其余检查单独覆盖，不参与求值对比。

## 4. 构建期契约与行为锁

| 项 | 位置 |
|---|---|
| 求值顺序单一事实源 | `core/frontend_check.py::JS_LOAD_ORDER`（18 项） |
| 单一入口 + import 清单契约 | `core/frontend_check.py::check_js_load_order()`（接进 `run_all` → 打包门禁） |
| 单文件 module 语法校验 | `core/frontend_check.py::check_js_file()`（`node --check --input-type=module`，stdin 传源码；否则 `.js` 会被当 CommonJS 误报 `export`） |
| CLI | `tools/check_js_deps.py` |
| 端到端等价验证器 | `tools/esm_verify.js` |
| ESM 契约变异锁 | `tests/test_js_load_order.py`（改回 classic / 删加 import / 打乱 / 删文件 / 删桥接 / 删入口，必红） |
| 等价性与验证器变异锁 | `tests/test_esm_migration.py`（删桥接 / 改回 classic script / 删模块 / 塞不存在的内联符号，验证器必须 `ok=False`） |

## 5. 明确不做 / 后续另开条目

- 不引入 Vite / webpack / npm 构建链（原生 ESM 够用，且项目已有严格的
  PyInstaller 资源逐字节一致性门禁，多一层构建就多一层漂移面）；
- 不改 SSE 事件契约、不改 `/api/*` 路由；
- 不做「内联事件清零 → addEventListener」与「IIFE 命名空间化」：桥接方案已让
  它们失去必要性，且这两步回归面最大；若将来确实要清零，另开条目；
- 不为拆分而拆分 2809 行的 `10-chat.js` / 2111 行的 `11-workspace-kb-study.js`，
  按功能改动顺带做（README 6.x 的童子军军规）。
