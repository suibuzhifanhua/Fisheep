// v1.0.756（评审 P0-3 字面补做）：ES Module 单一入口。
// index.html 只保留这一条 <script type="module">；18 个功能模块按
// JS_LOAD_ORDER 顺序做「副作用 import」——它们彼此没有 import（靠 window
// 全局互调），因此 ESM 规范保证求值顺序 == 原 classic <script src> 顺序，
// 顶层立即执行代码无需重构成 init()，也不存在 TDZ / 循环依赖风险。
// 删掉任一模块 → 模块图解析期即失败并报出本文件这个依赖方（见 tools/esm_verify.js）。
import "./16-icons.js";
import "./00-boot.js";
import "./17-composer.js";
import "./01-core.js";
import "./02-nav-quicklinks.js";
import "./03-experiences.js";
import "./04-skills.js";
import "./05-connectors-status.js";
import "./06-windows.js";
import "./07-usage-models.js";
import "./08-templates.js";
import "./09-chat-render.js";
import "./10-chat.js";
import "./11-workspace-kb-study.js";
import "./15-asr-component.js";
import "./13-dashboard.js";
import "./14-mic.js";
import "./12-init.js";
export {};
