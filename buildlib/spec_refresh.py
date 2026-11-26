# -*- coding: utf-8 -*-
"""buildlib.spec_refresh — hiddenimports 自动生成/写回、onedir spec 派生、冒烟清单。

自 build.py 逐字迁入（v1.0.65x 拆分）。
"""
import ast as _ast
import io
import os

from buildlib.env import BASE, SPEC
from buildlib.scope import skip_dirs as _scope_skip_dirs

# ---------- v1.0.555（P0-11）：hiddenimports 自动生成 ----------
# 原手工列举漏模块：services.asr/todo_watch/daily_digest/wecom_archive 不在 spec
# hiddenimports 里，PyInstaller 缺模块只出 warning 不失败，发货 exe 里 ASR 三端点
# 连续 14 次 ImportError（日志实证）——而开发环境永远测不出（同根因事故已两次：
# memory_ops 10-07/10-09 亦然）。构建时扫描源码 import + 包内全子模块，写回 spec。
# v1.0.655：buildlib 不进此清单——构建期工具（同 build.py），运行包不需要；拖进来会把
# gates→scan_silent_except→tools/ 一并分析，PyInstaller 判自家缺失中止打包（实测）。
# v1.0.761 起包名 bridge（原 wecom_bridge，方向1 批次2 改名）；兼容 shim 已随
# 全仓 import 迁移撤销（评审批7）——只剩实体包，全子模块进 hiddenimports。
_SPEC_SCAN_PKGS = ("services", "core", "desktop", "agent", "agent_tools", "routes", "bridge")
_SPEC_SKIP_DIRS = _scope_skip_dirs("spec_refresh")

# v1.0.660（审查修复 B2）：onedir 派生 spec 的落点——放 build/specs/ 而不是项目根。
# 项目根那份是**过期生成物**（缺 exclude_binaries=True → onedir 启动即无 UI；
# datas 也缺主 spec 后加的 bridge/js），躺在根目录会被误当成正式 spec 使用。
_ONEDIR_SPEC_DIR = os.path.join(BASE, "build", "specs")
# v1.0.680（改名 WeComCompanion → Lingban）：onedir 产物目录名随之改名。
from core.branding import ONEDIR_NAME as _ONEDIR_NAME  # noqa: E402
_ONEDIR_SPEC_NAME = _ONEDIR_NAME + ".spec"


def _module_exists(dotted):
    """大小写敏感的模块存在性检查（Windows FS 大小写不敏感，os.path.isfile 会把
    agent_tools.CONNECTORS 误判为 connectors.py → 无效 hiddenimport 触发缺模块
    构建失败）。逐级校验真实文件名。"""
    parts = dotted.split(".")
    cur = BASE
    for i, part in enumerate(parts):
        cur = os.path.join(cur, part)
        parent_listing = os.listdir(os.path.dirname(cur))
        base_name = os.path.basename(cur)
        if base_name + ".py" not in parent_listing and base_name not in parent_listing:
            return False
        if i < len(parts) - 1:
            # 中间段必须是包（目录）
            if not os.path.isdir(cur):
                return False
    return os.path.isfile(cur + ".py") or os.path.isfile(os.path.join(cur, "__init__.py"))


def _autogen_hiddenimports():
    """扫描源码中 from/import 本项目包的语句（含 from pkg import mod 形式的模块名
    判定），加各包全部子模块兜底，返回排序后的 hiddenimports 列表。"""
    import re as _re
    mods = set()
    pat_from = _re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+(.+?)(?:#.*)?$", _re.M)
    pat_import = _re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", _re.M)
    for root, dirs, files in os.walk(BASE):
        rel = os.path.relpath(root, BASE)
        if rel.split(os.sep)[0] in _SPEC_SKIP_DIRS:
            continue
        # v1.0.677（性能）：**剪枝**——原实现用 continue 跳过 dist/build/产物库 的
        # "处理"，但 os.walk 照样往里钻（continue 只跳过当前层的文件处理，
        # 不阻止下潜）。于是每次扫描都要遍历便携 node 运行时（产物库/组件/node）
        # 的上万个子目录：实测 26,442 次 scandir / 12.6 秒 —— 而这三个目录里
        # 本来就不含任何待扫描的源码。改成 dirnames[:] 剪枝（与 buildlib.gates.py
        # 的 check_silent_pass 同一写法），扫描降到 0.2 秒级。
        # 一处踩坑两处受益：本函数每次打包调 1 次，tests/contract/test_buildlib_units.py
        # 每次调用都调 1 次——两个最慢的用例都由它造成。
        dirs[:] = [d for d in dirs if d not in _SPEC_SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            try:
                txt = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in pat_from.finditer(txt):
                base = m.group(1)
                if base.split(".")[0] not in _SPEC_SCAN_PKGS:
                    continue
                if _module_exists(base):
                    mods.add(base)
                if "." not in base:   # from pkg import X → X 可能是子模块
                    for nm in _re.split(r"[,\s()]+", m.group(2).replace("(", " ")):
                        nm = nm.strip()
                        if not nm or nm == "as" or not (nm[0].isalpha() or nm[0] == "_"):
                            continue
                        nm = nm.split(" as ")[0].strip()
                        cand = f"{base}.{nm}"
                        if _module_exists(cand):
                            mods.add(cand)
            for m in pat_import.finditer(txt):
                base = m.group(1)
                if base.split(".")[0] in _SPEC_SCAN_PKGS and _module_exists(base):
                    mods.add(base)
    # 包内全部子模块兜底（相对 import / 动态 import 扫不到的也进包；
    # __init__ 跳过——包本身已由 mods.add(pkg) 覆盖，pkg.__init__ 是冗余别名）
    for pkg in _SPEC_SCAN_PKGS:
        pkg_dir = os.path.join(BASE, pkg)
        if not os.path.isdir(pkg_dir):
            continue
        mods.add(pkg)
        for root, _dirs, files in os.walk(pkg_dir):
            if "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith(".py") and fn != "__init__.py":
                    rel = os.path.relpath(os.path.join(root, fn), BASE)
                    mods.add(rel[:-3].replace(os.sep, "."))
    return sorted(mods)


def _source_module_list():
    """本项目包的全部模块（与 hiddenimports 同源，供 frozen 冒烟当清单用）。
    冻结态里 exe 自己枚举包内模块不可靠（PyiFrozenFinder 是 path finder，登记在
    sys.path_importer_cache 而不是 sys.meta_path）——620/621 两次都因此拿到空清单，
    冒烟只报 9 个模块却打「import 全通」。源码侧清单是现成的，由 build 传进去。"""
    return [m for m in _autogen_hiddenimports() if m.split(".")[0] in _SPEC_SCAN_PKGS]


def refresh_spec_hiddenimports():
    """把扫描结果写回 spec 的 hiddenimports 赋值（保留原手工第三方项，ast 定位
    赋值语句的起止行，避免正则切错嵌套括号）。返回新增模块数（用于构建日志）。"""
    import ast as _ast
    with open(SPEC, encoding="utf-8") as f:
        src = f.read()
    tree = _ast.parse(src)
    node = None
    for stmt in tree.body:
        if isinstance(stmt, _ast.Assign) and any(
                isinstance(t, _ast.Name) and t.id == "hiddenimports" for t in stmt.targets):
            node = stmt
            break
    if node is None:
        raise SystemExit("[build] spec 中未找到 hiddenimports 赋值")
    old = set(_ast.literal_eval(node.value))
    auto = set(_autogen_hiddenimports())
    # 本项目包内条目一律以本次扫描为准（自愈：清掉历史误入的大小写幻影如
    # agent_tools.CONNECTORS / __init__ 别名）；第三方手工项原样保留。
    old_own = {m for m in old if m.split(".")[0] in _SPEC_SCAN_PKGS}
    merged = sorted((old - old_own) | auto)
    added = sorted(auto - old)
    lines = src.splitlines(keepends=True)
    _cmt = ("# v1.0.660（审查修复 B19）：本项目包内模块由 buildlib.spec_refresh._autogen_hiddenimports() "
            "构建时自动生成（第三方项保留手工清单）")
    new_stmt = _cmt + "\n" + "hiddenimports = " + repr(merged) + "\n"
    # v1.0.592 修复：替换区间原来只覆盖 `hiddenimports = ...` 那几行，注释行写在它上面、
    # 不在 ast 节点范围内 → 每次构建都在旧注释上方**再插一条同样的注释**，
    # 实测 Lingban.spec 已堆到 41 行重复注释。现向上吸收连续同文注释行，
    # 一并纳入替换区间（本次构建即会把历史堆叠收敛成 1 行）。
    _start = node.lineno - 1
    while _start > 0 and lines[_start - 1].rstrip("\r\n") == _cmt:
        _start -= 1
    lines[_start:node.end_lineno] = [new_stmt]
    with open(SPEC, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    return added


def _abs_path(p):
    """把 spec 里的相对路径锚成项目根（BASE）下的绝对路径。"""
    p = str(p)
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(BASE, p))


def _absolutize_spec_paths(src):
    """把 spec 文本里的**相对路径**改写成项目根（BASE）下的绝对路径。

    v1.0.660（审查修复 B2）：PyInstaller 以 **spec 文件所在目录**（CONF['specpath']）
    为相对路径基准——Analysis 的 scripts、datas 的源路径、EXE 的 icon/version 全按它
    解析。onedir 派生 spec 落到 build/specs/ 下后，项目根的相对路径会指向
    build/specs/（那里什么都没有），构建直接失败。故派生时统一锚成绝对路径。

    只改「路径类」字面量，逐项用 AST 定位（不靠正则猜）：datas 的源路径、
    Analysis 的 scripts/pathex、EXE 的 icon/version。任一项定位不到就抛错——
    宁可构建失败，也不要产出一个路径错位的 spec（那会打成"看起来成功"的坏包）。

    实现要点：按 ast 给的 (lineno, col_offset) 做**字节级切片替换**——col_offset 是
    UTF-8 字节偏移（不是字符偏移），整行重写会把 `pathex=`/`icon=`/`version=` 这类
    关键字参数名一起吃掉（派生出语法错误的 spec）。故只替换**值节点**那一段。
    """
    tree = _ast.parse(src)
    raw = src.encode("utf-8")
    lines = src.splitlines(keepends=True)
    # 每行的起始字节偏移
    starts = []
    _off = 0
    for _ln in lines:
        starts.append(_off)
        _off += len(_ln.encode("utf-8"))
    edits = []      # (start_byte, end_byte, new_bytes)

    def _span(node):
        """节点在 raw 里的 (起, 止) 字节区间。"""
        s = starts[node.lineno - 1] + node.col_offset
        e = starts[node.end_lineno - 1] + node.end_col_offset
        return s, e

    def _replace(node, new_text):
        # 跨行也允许：edits 是按字节区间统一替换、且**倒序**应用，
        # 前面的节点偏移不受后面改动影响（行数变了也不影响）。
        # 前提是被替换的节点是纯字面量（datas/scripts/pathex/icon/version），
        # 不含注释——事实如此（spec 里这些位置只有字面量）。
        s, e = _span(node)
        edits.append((s, e, new_text.encode("utf-8")))

    for node in tree.body:
        if not isinstance(node, _ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, _ast.Name)]
        if "datas" in names and isinstance(node.value, _ast.List):
            items = []
            for elt in node.value.elts:
                if not (isinstance(elt, _ast.Tuple) and len(elt.elts) == 2):
                    raise RuntimeError("spec 的 datas 项不是 (src, dest) 二元组，无法改写")
                s = _ast.literal_eval(elt.elts[0])
                d = _ast.literal_eval(elt.elts[1])
                items.append((_abs_path(s), d))
            _replace(node.value, repr(items))
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        fname = getattr(node.func, "id", None)
        if fname == "Analysis":
            if node.args and isinstance(node.args[0], _ast.List):
                _replace(node.args[0], "[" + ", ".join(
                    repr(_abs_path(_ast.literal_eval(e))) for e in node.args[0].elts) + "]")
            for kw in node.keywords:
                if kw.arg == "pathex" and isinstance(kw.value, _ast.List):
                    _replace(kw.value, "[" + ", ".join(
                        repr(_abs_path(_ast.literal_eval(e))) for e in kw.value.elts) + "]")
        elif fname == "EXE":
            for kw in node.keywords:
                if kw.arg == "icon" and isinstance(kw.value, _ast.List):
                    _replace(kw.value, "[" + ", ".join(
                        repr(_abs_path(_ast.literal_eval(e))) for e in kw.value.elts) + "]")
                elif kw.arg == "version" and isinstance(kw.value, _ast.Constant):
                    _replace(kw.value, repr(_abs_path(_ast.literal_eval(kw.value))))
    for s, e, new in sorted(edits, key=lambda x: -x[0]):
        raw = raw[:s] + new + raw[e:]
    return raw.decode("utf-8")


def _write_onedir_spec():
    """由 onefile spec 派生出 onedir spec（v1.0.646）。

    不另起一份 spec：两份 spec 必然漂移（本项目已因"两份判据"吃过多次亏）。
    做法是把 EXE(...) 里的 a.binaries/a.datas 摘出来交给 COLLECT——除此之外
    与 onefile 完全同源（Analysis/打包/UPX 规则一致），后续 spec 改动自动继承。

    v1.0.660（审查修复 B2）：
      ① 生成物落到 build/specs/ 下，不再躺在项目根（根目录那份是过期生成物：
         缺 exclude_binaries=True → onedir 启动即无 UI；datas 也缺主 spec 后加的
         bridge/js），且文件头写明"禁止直接使用，此为生成物"；
      ② 路径绝对化——spec 换了目录，相对路径基准随之改变（见 _absolutize_spec_paths）；
      ③ 补 exclude_binaries=True——官方 onedir 模板必带，旧派生只剥 a.binaries/a.datas
         实参、exe 仍按 one-file 语义构建（启动解压到 _MEIPASS），而 COLLECT 把
         index.html/js/css 放进 _internal/，resource_path 只查 _MEIPASS →
         打出来的 onedir 启动即无 UI（白屏/404）。
    """
    src = io.open(SPEC, encoding="utf-8").read()
    src = _absolutize_spec_paths(src)
    i = src.index("exe = EXE(")
    exe_block = src[i:]
    if "a.binaries," not in exe_block or "a.datas," not in exe_block:
        raise RuntimeError("spec 的 EXE 块结构变了（找不到 a.binaries/a.datas），无法派生 onedir 版")
    exe_block = exe_block.replace("    a.binaries,\n", "", 1).replace("    a.datas,\n", "", 1)
    # v1.0.652（审计 D3）：补 exclude_binaries=True——PyInstaller 官方 onedir 模板的
    # EXE 必带此标志（api.py：该标志决定 exe 落 workpath/distpath、PKG 是否 onedir 形态）。
    # 旧派生只剥 a.binaries/a.datas 实参，exe 仍按 one-file 语义构建（启动解压到 _MEIPASS），
    # 而 COLLECT 把 index.html/js/css 放进 _internal/，resource_path 只查 _MEIPASS →
    # 打出来的 onedir 启动即无 UI（白屏/404）。
    # v1.0.660：主 spec 的 EXE 是**多行**写法（"exe = EXE(\n    pyz,\n ..."），
    # 旧实现的单行替换串匹配不到 → exclude_binaries 静默没注入（正是磁盘上那份
    # 过期生成物缺标志的根因）。改为按行定位：在第一个关键字参数（name=）之前
    # 插入 exclude_binaries=True，并**断言**确实插进去了。
    if "exclude_binaries=True" not in exe_block:
        _lines = exe_block.splitlines(keepends=True)
        _ins = None
        for _i, _ln in enumerate(_lines):
            if _ln.lstrip().startswith("name="):
                _ins = _i
                break
        if _ins is None:
            raise RuntimeError("spec 的 EXE 块里找不到 name= 参数，无法插入 exclude_binaries=True")
        _lines.insert(_ins, "    exclude_binaries=True,\n")
        exe_block = "".join(_lines)
    if "exclude_binaries=True" not in exe_block:
        raise RuntimeError("onedir spec 派生失败：exclude_binaries=True 未注入")
    collect = """


# ---- onedir 派生块（v1.0.660，由 buildlib.spec_refresh._write_onedir_spec 生成，勿手改）----
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["Python.Runtime.dll", "clr_loader/*", "pythonnet/*", "WebView2*", "e_sqlite3.dll"],
    name='%s',
)
"""
    from core.branding import ONEDIR_NAME as _od
    collect = collect.replace("name='%s',", "name='%s'," % _od)
    banner = (
        "# ============================================================================\n"
        "# 【生成物 · 禁止直接使用】\n"
        "# 本文件由 buildlib/spec_refresh.py::_write_onedir_spec() 从主 spec\n"
        "# （Fisheep.spec）自动派生，每次 --onedir 打包都会重新生成并覆盖。\n"
        "# 要改打包内容请改主 spec —— 直接改这里会在下次打包时被覆盖掉。\n"
        "# ============================================================================\n"
    )
    # coding 声明必须留在前两行 → banner 插在主 spec 首行（coding 行）之后。
    _lines0 = src[:i].splitlines(keepends=True)
    _cut = 1 if (_lines0 and "coding" in _lines0[0]) else 0
    out = "".join(_lines0[:_cut]) + banner + "".join(_lines0[_cut:]) + exe_block.rstrip() + "\n" + collect
    os.makedirs(_ONEDIR_SPEC_DIR, exist_ok=True)
    p = os.path.join(_ONEDIR_SPEC_DIR, _ONEDIR_SPEC_NAME)
    io.open(p, "w", encoding="utf-8", newline="").write(out)
    return p
