# -*- coding: utf-8 -*-
"""core.frontend_check — index.html / js 结构校验的**实现**（v1.0.619 自 tools/ 上移）。

为什么在 core 而不是 tools：`services/self_repair.py` 的补丁验证分流要调这里
（.js → 语法校验，.html → div/id/CSS 全套）。tools/ 是**开发期目录**，build.py 的
hiddenimports 扫描显式跳过它（_SPEC_SCAN_PKGS 里没有 tools，且 tools/ 无 __init__.py），
所以冻结包里 `from tools import check_frontend` 必然 ModuleNotFoundError →
被 except 吞掉后伪装成「JS 语法校验失败：<导入错误>」，正式包的自我修复从此永远修不了前端，
报错还指着错的地方。上移进 core 后它随包一起被扫描、被 `--smoke` 探针导入。

tools/check_frontend.py 保留为 **CLI 壳**（build.py 的打包门禁仍按 subprocess 调用它），
函数一律从这里 re-export，不再有两份实现。

校验项（与原 tools 版逐字一致）：
  check_div_balance        div 开/闭配对 + 嵌套栈校验
  check_dup_ids            id 属性重复检测
  check_css_braces         <style> 内花括号配对
  check_script_style_close <script>/<style> 标签闭合
  check_js_file            单文件 JS 语法（node --check）
"""
import io
import os
import re


def check_div_balance(html, errors):
    """div 开/闭配对：计数一致 + 嵌套栈不出现'先闭后开'。自闭合 <div/> 不存在，
    但需跳过注释内的 div（<!-- --> 中的标记不参与）。"""
    # 去掉 HTML 注释，避免注释里的示例标记干扰
    txt = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    stack = []
    for m in re.finditer(r"<(/?)div\b[^>]*?(/?)>", txt, re.I):
        closing, selfclose = m.group(1) == "/", m.group(2) == "/"
        if not closing and not selfclose:
            stack.append(m.start())
        elif closing:
            if not stack:
                errors.append(f"多余的 </div>（位置 {m.start()}）：没有对应的 <div>")
                return
            stack.pop()
    if stack:
        errors.append(f"有 {len(stack)} 个 <div> 未闭合（首个位置 {stack[0]}）")


def check_dup_ids(html, errors):
    """id 属性重复检测：同一文档内 id 必须唯一（导航/锚点/getElementById 依赖）。"""
    ids = re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', html)
    seen, dups = set(), set()
    for i in ids:
        if i in seen:
            dups.add(i)
        seen.add(i)
    if dups:
        errors.append(f"重复 id：{', '.join(sorted(dups))}")


def _style_blocks(html):
    """提取全部 <style>...</style> 内容。"""
    return re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.S | re.I)


def check_css_braces(html, errors):
    """<style> 内花括号配对：{ 与 } 数量一致（逐块检查，报告哪一块错位）。"""
    for idx, css in enumerate(_style_blocks(html), 1):
        o, c = css.count("{"), css.count("}")
        if o != c:
            errors.append(f"<style> #{idx} 花括号不配对：{{={o} 个, }}={c} 个")


def check_script_style_close(html, errors):
    """<script>/<style> 开闭标签数量一致（漏写 </script> 会吞掉后续整个文档）。"""
    for tag in ("script", "style"):
        opens = len(re.findall(rf"<{tag}\b", html, flags=re.I))
        closes = len(re.findall(rf"</{tag}>", html, flags=re.I))
        if opens != closes:
            errors.append(f"<{tag}> 标签开闭数量不一致：开 {opens} / 闭 {closes}")


# v1.0.702（A1）：index.html 内联 style 棘轮——基线只降不升。
# 基线 7 是「必须留内联」集：JS 按 el.style.display 读**初始态**判定（02:47/470、
# 04:110/179、06:320/453、11:598/1615）——navMore/closeAskOverlay/setSearchPanel
# 只有 display:none 一条属性；liveShot/studyDetailBox/hubPanel/hubUrlForm 保留
# display、其余属性已抽为 css 布局类（v1.0.702 inl-NNN，v1.0.776 收口为
# u-*/容器前缀类）。新增内联样式 → 门禁红 → 先抽成 css/style.css 的类
# （布局类 u-* / 容器前缀类范式）再落地。
INLINE_STYLE_BASELINE = 7

# v1.0.755 建立、v1.0.756（评审 P0-3 字面补做）改为 **ES Module 求值顺序**单一事实源。
#   v1.0.755：classic「全局函数 + 数字前缀顺序 <script src>」时代锁 18 个脚本顺序。
#   v1.0.756：18 个文件已全量改成 ES Module，index.html 只留一个
#             <script type="module" src="js/_entry.js">；_entry.js 按本表顺序写
#             「副作用 import」。模块之间本来就没有 import（靠 window 全局互调），
#             ESM 规范保证求值顺序 == import 声明顺序，所以这张表仍是唯一事实源。
# 每个模块末尾用 `export { ... }` + getter/setter 把顶层绑定镜像回 window，保住
# index.html 的 211 处内联 on*= 处理器与跨文件共享变量（见 tools/esm_verify.js 的
# classic-vs-ESM 行为等价验证）。删文件 / 加文件 / 打乱顺序 / 改回 classic 形状，
# 打包期直接红并报出具体项——不再是「上线后用户截图才发现」。
JS_LOAD_ORDER = (
    "js/16-icons.js", "js/00-boot.js",
    "js/17-composer.js", "js/01-core.js", "js/02-nav-quicklinks.js",
    "js/03-experiences.js", "js/04-skills.js", "js/05-connectors-status.js",
    "js/06-windows.js", "js/07-usage-models.js", "js/08-templates.js",
    "js/09-chat-render.js", "js/10-chat.js", "js/11-workspace-kb-study.js",
    "js/15-asr-component.js", "js/13-dashboard.js", "js/14-mic.js",
    "js/12-init.js",
)

# v1.0.756：唯一 ES Module 入口（不是功能域文件，故不计入上面 18 项）。
ESM_ENTRY = "js/_entry.js"
ESM_BRIDGE_MARK = "ESM BRIDGE BEGIN"


def check_js_load_order(html, errors, js_dir=None):
    """v1.0.756（P0-3 全量迁移）：单一 ES Module 入口 + import 顺序契约。

    判据（任一不满足即 error）：
      ① index.html 恰好 1 个 <script ... src=...>，且 type=module、src == ESM_ENTRY；
         残留 classic <script src="js/..."> 一律红（改回坏形状必红）；
      ② _entry.js 的静态 import 清单逐项 == JS_LOAD_ORDER（打乱/漏/多都红）；
      ③ 12-init.js 必须是清单最后一项（初始化链依赖前面模块就绪）；
      ④ 清单里的模块文件实际存在（删文件 → 红）；
      ⑤ js/ 下所有 .js 必须在 JS_LOAD_ORDER ∪ {_entry.js}（新增未登记 → 红）；
      ⑥ 每个功能模块含显式 export 与 ESM 桥接标记（去掉桥接 → 红）。
    """
    tags = re.findall(r"<script\b([^>]*)>", html or "", flags=re.I)
    srcs = [x.strip() for x in re.findall(
        r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']', html or "", flags=re.I)]
    if len(tags) != 1 or len(srcs) != 1:
        errors.append("index.html 应只有 1 个 <script>（ES Module 单一入口），"
                      "实际 script 标签 %d 个 / 带 src 的 %d 个" % (len(tags), len(srcs)))
    else:
        if not re.search(r'type\s*=\s*["\']module["\']', tags[0], flags=re.I):
            errors.append('唯一 <script> 必须是 type="module"'
                          '（改回 classic <script src> 会让内联事件与桥接全部失效）')
        if srcs[0] != ESM_ENTRY:
            errors.append("唯一入口必须是 %s，实际 %s" % (ESM_ENTRY, srcs[0]))
    if re.search(r'<script[^>]+src\s*=\s*["\']js/(?!_entry\.js)', html or "", flags=re.I):
        errors.append('index.html 仍残留 classic <script src="js/...">')
    if JS_LOAD_ORDER[-1] != "js/12-init.js":
        errors.append("契约表尾必须是 12-init.js（初始化链必须最后执行）")

    if not (js_dir and os.path.isdir(js_dir)):
        return
    entry_path = os.path.join(js_dir, "_entry.js")
    if not os.path.isfile(entry_path):
        errors.append("缺少单一 ES Module 入口：%s" % ESM_ENTRY)
    else:
        with io.open(entry_path, encoding="utf-8") as _f:
            entry_src = _f.read()
        imports = ["js/" + m for m in re.findall(
            r'^\s*import\s+["\']\./([^"\']+)["\']\s*;?\s*$', entry_src, flags=re.M)]
        if imports != list(JS_LOAD_ORDER):
            errors.append("_entry.js import 清单与 JS_LOAD_ORDER 不一致："
                          "实际 %d 项 / 契约 %d 项" % (len(imports), len(JS_LOAD_ORDER)))
            for i, (got, want) in enumerate(zip(imports, JS_LOAD_ORDER)):
                if got != want:
                    errors.append("  第 %d 项：实际 %s / 契约 %s" % (i + 1, got, want))
                    break
            missing = set(JS_LOAD_ORDER) - set(imports)
            extra = set(imports) - set(JS_LOAD_ORDER)
            if missing:
                errors.append("  _entry.js 缺少 import：" + ", ".join(sorted(missing)))
            if extra:
                errors.append("  _entry.js 多出 import：" + ", ".join(sorted(extra)))
    declared = {os.path.basename(x) for x in JS_LOAD_ORDER} | {"_entry.js"}
    for rel in JS_LOAD_ORDER:
        fp = os.path.join(js_dir, os.path.basename(rel))
        if not os.path.isfile(fp):
            errors.append("清单里的模块不存在：%s" % rel)
            continue
        with io.open(fp, encoding="utf-8") as _f:
            body = _f.read()
        if ESM_BRIDGE_MARK not in body or not re.search(r"\bexport\s*\{", body):
            errors.append("%s 缺少显式 export / ESM 桥接块"
                          "（改回 classic 全局脚本形状）" % rel)
    for fn in sorted(os.listdir(js_dir)):
        if fn.endswith(".js") and fn not in declared:
            errors.append("js/ 新增文件 %s 未登记进 JS_LOAD_ORDER"
                          "（漏登记会让 import 顺序契约失效）" % fn)




def check_inline_style_ratchet(html, errors):
    """内联 style 计数棘轮：超过基线即红（基线只允许下调，不许上调）。"""
    n = len(re.findall(r'\s+style\s*=\s*["\']', html))
    if n > INLINE_STYLE_BASELINE:
        errors.append(
            f"index.html 内联 style {n} 处 > 基线 {INLINE_STYLE_BASELINE}：新增内联样式应抽成 "
            f"css/style.css 的类（u-* / 容器前缀范式）；仅 7 处 JS 读初始态的 display 允许留内联")


def check_js_file(path, strict=False):
    """单文件 JS 语法校验（node --check）。返回 (ok, 输出尾部)。

    v1.0.619 两处改动：
      ① 不再假设 `node` 在 PATH：走 core.portable.find_node()（系统 PATH → 软件自装的
         便携运行时），与 dsh/npm 同一套定位口径。
      ② 找不到校验器时，strict 模式返回 **False**。原实现一律返回 True 并附一句
         "node 不在 PATH（跳过）"，而 self_repair 把返回值当"补丁已通过"用——
         没有 node 的机器上，前端补丁是**未经任何校验就落地**的（测试断言 ok is False
         也因此长期红）。打包门禁（人在看输出）继续用非 strict：跳过但不拦。
      ③ v1.0.756：改走 `node --check --input-type=module`（stdin 传源码）——
         18 个 js 已是 ES Module，按 .js 路径校验会被 Node 当 CommonJS 解析，
         遇 export 误报语法错，导致所有前端补丁与打包门禁假红。
    """
    import subprocess
    from core import portable as _pb
    node = _pb.find_node()
    if not node:
        msg = "未找到 node（系统 PATH 与便携运行时均无），无法校验 JS 语法"
        return (False if strict else True), msg
    try:
        # v1.0.756（评审 P0-3）：前端已全量 ES Module。`node --check path.js` 会把
        # .js 按 CommonJS 解析，遇 export 直接误报 SyntaxError；改走 stdin +
        # --input-type=module，Node 才会用模块语法校验。仍按路径取文件内容，
        # 调用方（打包门禁 / self_repair 补丁分流）接口不变。
        with io.open(path, encoding="utf-8") as _f:
            code = _f.read()
        r = subprocess.run([node, "--check", "--input-type=module"],
                           input=code, capture_output=True, text=True, timeout=30)
        out = (r.stderr or r.stdout or "").strip()
        return r.returncode == 0, out[-400:]
    except FileNotFoundError:
        # find_node() 之后仍报缺文件（并发卸载/权限），语义与"没有校验器"一致
        msg = "node 不可执行：" + str(node)
        return (False if strict else True), msg
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:400]


def run_all(root):
    """跑全套前端校验，返回 errors 列表（空=全部通过）。
    v1.0.652（审计 P1-14）：补 js/*.js 语法校验——旧实现只查 index.html 四项，
    而 build.py 注释与 README 都宣称"JS 语法不合格直接中止打包"，16 个 js 文件
    任一语法错误都会直达用户（前端整体瘫痪；index.html 结构坏过 3 次的同一暴露面）。
    有 node 必须全过；无 node 跳过并打 ⚠️（环境性红不进基线，不误伤无 node 构建机）。"""
    p = os.path.join(root, "index.html")
    with open(p, encoding="utf-8") as f:
        html = f.read()
    errors = []
    check_div_balance(html, errors)
    check_dup_ids(html, errors)
    check_css_braces(html, errors)
    check_script_style_close(html, errors)
    check_inline_style_ratchet(html, errors)
    # v1.0.755（评审 P0-3）：脚本顺序契约，接进 run_all 即自动接进打包门禁。
    js_dir = os.path.join(root, "js")
    check_js_load_order(html, errors, js_dir)
    if os.path.isdir(js_dir):
        _node_missing_warned = False
        for fn in sorted(os.listdir(js_dir)):
            if not fn.endswith(".js"):
                continue
            okj, tailj = check_js_file(os.path.join(js_dir, fn), strict=False)
            if not okj:
                errors.append(f"js/{fn} 语法校验失败：{tailj}")
            elif (not _node_missing_warned) and tailj and ("未找到 node" in tailj or "node 不可执行" in tailj):
                _node_missing_warned = True
                print(f"[frontend-check] ⚠️ {tailj}——js/ 语法校验整体跳过（有 node 的环境会强制校验）")
    return errors
