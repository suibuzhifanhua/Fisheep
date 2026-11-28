# -*- coding: utf-8 -*-
"""buildlib/public_exe.py — 脱敏公开 exe 构建 + exe 级脱敏门禁（v1.0.764）。

背景（为什么需要本模块）：
  build.py 打的是「开发版 exe」——由**未脱敏**源码构建，载荷里含雇主内部配置
  （学习平台域 study.example.com 等硬编码敏感串），**绝不能直接发 public**。
  tools/publish_github.py --rebuild 产出的是「脱敏源码镜像」（待发布版本/）——
  GATE_EXTS 屏蔽 .exe，镜像里**不含 exe**。本模块补齐最后一环：

    从脱敏镜像树 PyInstaller 重打包出「脱敏 exe」，并用 PyInstaller 官方
    CArchiveReader 逐 entry 解压做 exe 级脱敏门禁。

为何必须「解压后扫」：
  PyInstaller onefile 载荷 zlib 压缩，明文扫 exe 二进制会**假阴性**（业务串
  wecom/flask/127.0.0.1 明文扫不到）——必须 CArchiveReader 逐 entry 解压成明文
  再扫，才是 exe 级铁证。

为何必须**下钻 PYZ**：
  硬编码敏感串（历史案例：tucker.py 的 study.example.com、技能提示词的雇主平台名；
  v1.0.768 学习模块移除后打包源码已无学习平台域，W_ZN 规则保留为历史防护）
  编译后落在**冻结代码的 co_consts 里**（.pyc），而 .pyc 打包在**顶层 CArchive 的
  PYZ entry 内**（不是顶层独立 entry）。只扫顶层 entry 会漏掉冻结代码——
  实测脱敏门禁 雇主平台 命中 31 处里 19 处在 PYZ 冻结代码、仅 12 处在数据文件。
  故本模块对 PYZ entry 打开嵌套 ZlibArchive、逐模块 marshal.loads、遍历 co_consts
  拿**真实 Python str** 再匹配（编码无关：CJK 在 marshal 里是 UCS2/UCS4，直接按
  str 匹配，不踩 UTF-8 字节误判）。

门禁 fail-closed：任一敏感串命中 → problems 非空 → 调用方判红，阻塞发布。
阳性对照自检：若载荷里连 wecom/flask/Fisheep 都扫不到，说明解压/遍历失败
（假「干净」）→ 同样判红——防「没解出来所以啥都没扫到」的假阴性。

脱敏词表复用 tools.publish_github.GATE_PATTERNS（单一事实源；词表运行时拼装、
不在本文件写明文字面量，避免本文件被导出进镜像时自扫命中）。
"""
import marshal
import os
import shutil
import subprocess

from buildlib.env import BASE, PYINSTALLER, _SANDBOX_VARS   # noqa: E402
from buildlib.mask_gate import GATE_PATTERNS                # noqa: E402  脱敏词表单源

# exe 名从 core.branding 取单一事实源（Fisheep.exe）；构建期 import 安全兜底。
try:
    from core.branding import EXE_NAME
except Exception:  # noqa: BLE001
    EXE_NAME = "Fisheep.exe"

# 镜像树 = 仓库根（BASE）的父级下「待发布版本/」（与 publish_github.MIRROR_DIR 同义）。
MIRROR_DIR = os.path.join(os.path.dirname(BASE), "待发布版本")

# 阳性对照：脱敏 exe 载荷里**必须能扫到**的业务串（跨数据文件与冻结代码）。
# 一个都扫不到 = 解压/遍历失败（假「干净」）→ 门禁判红。
POSITIVE_CONTROLS = ("wecom", "flask", "Fisheep")


def _code_strings(code, out):
    """递归收集 code object 里所有 str 常量（含嵌套 code 的 co_consts）。"""
    for c in getattr(code, "co_consts", ()):
        if isinstance(c, str):
            out.append(c)
        elif hasattr(c, "co_consts"):   # 嵌套函数/方法/类体
            _code_strings(c, out)


def _scan_text(text, patterns):
    """扫一段**明文**文本（str）。返回命中 [(label, snippet)]。

    数据文件（index.html/js/css/…）解压后按 UTF-8 解码即得明文，直接正则匹配。
    """
    if not text:
        return []
    hits = []
    for label, pat in patterns:
        for m in pat.finditer(text):
            s = max(0, m.start() - 12)
            hits.append((label, text[s:m.end() + 12]))
    return hits


def _scan_module_blob(blob, patterns):
    """扫一个 PYZ 模块的 marshal 字节（marshal.dumps(code)）。

    marshal.loads → code object → 遍历 co_consts 拿真实 Python str → 逐串匹配。
    编码无关（CJK 在 marshal 里是 UCS2/UCS4，反序列化后是真实 str，直接按 str 匹配）。
    反序列化失败（非代码 blob）→ 返回空（不误报）。
    """
    try:
        code = marshal.loads(blob)
    except Exception:  # noqa: BLE001（该 entry 不是可反序列化 code——跳过）
        return []
    strs = []
    _code_strings(code, strs)
    hits = []
    for s in strs:
        for label, pat in patterns:
            if pat.search(s):
                hits.append((label, s[:48]))
    return hits


def _module_strings(blob):
    """marshal 字节 → 该模块所有 str 常量（反序列化失败 → 空列表）。

    阳性对照自检对冻结代码用：对照串（wecom/flask/Fisheep）若落在冻结代码里，
    它在 **co_consts 的真实 str** 里，**不是**原始 marshal bytes——`str in bytes`
    会直接 TypeError。故取字符串常量集再判（编码无关，同 _scan_module_blob）。
    """
    try:
        code = marshal.loads(blob)
    except Exception:  # noqa: BLE001（非可反序列化 code——无字符串常量）
        return []
    out = []
    _code_strings(code, out)
    return out


def _iter_entries(exe_path):
    """生成器：逐 entry 产出 (kind, name, scan_target)。

    kind='code' → scan_target 是该 PYZ 模块 marshal 字节（交给 _scan_module_blob）；
    kind='data' → scan_target 是该顶层 entry 解压后的 UTF-8 文本（交给 _scan_text）。
    顶层 CArchive 的 PYZ entry（typecode 'z'）打开嵌套 ZlibArchive、逐模块下钻。
    """
    from PyInstaller.archive.readers import CArchiveReader   # 延迟 import（构建期才有）
    reader = CArchiveReader(exe_path)
    for name, entry in reader.toc.items():
        typecode = entry[4]
        if typecode == "z":        # PKG_ITEM_PYZ —— 嵌套冻结代码归档
            try:
                pyz = reader.open_embedded_archive(name)
            except Exception:  # noqa: BLE001（PYZ 结构异常——跳过该 entry）
                continue
            for mname in pyz.toc:
                try:
                    yield "code", mname, pyz.extract(mname, raw=True)
                except Exception:  # noqa: BLE001（单模块解压失败——跳过）
                    continue
        else:
            try:
                yield "data", name, reader.extract(name).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                yield "data", name, ""


def verify_public_exe(exe_path, patterns=None, controls=None):
    """exe 级脱敏门禁：逐 entry（含下钻 PYZ）解压扫敏感模式 + 阳性对照自检。

    返回 (problems, stats)：
      problems  非空 = 红（敏感残留 / 阳性对照缺失 / 无 entry 可解）。
      stats     dict：entries/code_modules/data_files/sensitive_hits/controls_found。
    fail-closed：任一敏感串命中即红；载荷整体解不出来（0 entry 或 0 对照）也红。
    """
    patterns = patterns if patterns is not None else GATE_PATTERNS
    controls = controls if controls is not None else POSITIVE_CONTROLS
    problems = []
    n_entries = 0
    n_code = 0
    n_data = 0
    sensitive = 0
    found = []
    for kind, name, target in _iter_entries(exe_path):
        n_entries += 1
        if kind == "code":
            n_code += 1
            hits = _scan_module_blob(target, patterns)
            # 冻结代码里的对照串在 co_consts（真实 str），非原始 marshal bytes
            found_src = _module_strings(target)
        else:
            n_data += 1
            hits = _scan_text(target, patterns)
            found_src = [target] if target else []
        for c in controls:
            if c not in found and any(c in s for s in found_src):
                found.append(c)
        for label, snippet in hits:
            sensitive += 1
            problems.append("[敏感残留 %s] %s …%r…" % (label, name, snippet))
    stats = {"entries": n_entries, "code_modules": n_code, "data_files": n_data,
             "sensitive_hits": sensitive, "controls_found": found}
    if n_entries == 0:
        problems.append("未解出任何 entry——CArchiveReader 打开失败，结果不可信")
    elif not found:
        problems.append("阳性对照全缺失（%s 一个都没扫到）——载荷未成功解析，结果不可信"
                        % "/".join(controls))
    return problems, stats


def build_public_exe(mirror_dir=None, base=None, pyinstaller=None):
    """从脱敏镜像树 PyInstaller 重打包 exe。返回 (exe_path, ok, msg)。

    ① version_info.txt 是 build 生成物（不在镜像 git 里）→ 从 dev 树拷入镜像；
    ② 在镜像树内跑 `pyinstaller --noconfirm --clean Fisheep.spec`（cwd=镜像树，
       让 spec 相对 datas 指向镜像树的脱敏资源）；③ 校验 dist/ 下产出本次 exe。
    spec 自包含（自己 collect_all、用 baked hiddenimports）——镜像与 dev 源码同模块集
    （仅字符串脱敏），baked 清单仍有效，无需重新 refresh_spec_hiddenimports。
    """
    base = base or BASE
    mirror_dir = mirror_dir or MIRROR_DIR
    pyinstaller = pyinstaller or PYINSTALLER
    spec = os.path.join(mirror_dir, "Fisheep.spec")
    if not os.path.isfile(spec):
        return None, False, "镜像树缺 Fisheep.spec：%s" % spec
    vi_src = os.path.join(base, "version_info.txt")
    vi_dst = os.path.join(mirror_dir, "version_info.txt")
    if os.path.isfile(vi_src):
        shutil.copyfile(vi_src, vi_dst)
    elif not os.path.isfile(vi_dst):
        return None, False, "缺 version_info.txt（dev %s 与镜像树均无）" % vi_src
    env = {k: v for k, v in os.environ.items() if k not in _SANDBOX_VARS}
    # 只传 spec + --noconfirm + --clean（与 dev 构建 build.py 一致）：**不传** --specpath /
    # --distpath / --workpath——传 .spec 时它们是 makespec 选项，值不等于默认（specpath 默认
    # None、dist/work 默认 <cwd 绝对路径>）会被 PyInstaller 拦「makespec options not valid」。
    # dist/work 落 (subprocess cwd)/dist = 镜像树（cwd=mirror_dir）；spec 相对 datas 也按镜像树解析。
    cmd = [pyinstaller, spec, "--noconfirm", "--clean"]
    r = subprocess.run(cmd, cwd=mirror_dir, env=env,
                       capture_output=True, timeout=1800)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    if r.returncode != 0:
        return None, False, "pyinstaller 失败 rc=%s：\n%s" % (r.returncode, out[-1500:])
    exe = os.path.join(mirror_dir, "dist", EXE_NAME)
    if not os.path.isfile(exe):
        return None, False, "pyinstaller 报成功但 %s 不存在" % exe
    return exe, True, "OK %s（%.1f MB）" % (exe, os.path.getsize(exe) / 1e6)


def run_public_release(mirror_dir=None, base=None, pyinstaller=None):
    """脱敏打包一条龙：build_public_exe + verify_public_exe。返回 (ok, report_lines)。

    `build.py --public-exe` 与 `tools/build_public_exe.py` 共用的执行入口。
    门禁红（ok=False）→ 该 exe 不可发 public。
    """
    lines = []
    exe, ok, msg = build_public_exe(mirror_dir, base=base, pyinstaller=pyinstaller)
    lines.append("打包：" + msg)
    if not ok:
        return False, lines
    problems, stats = verify_public_exe(exe)
    lines.append("门禁：entries=%(entries)d 模块=%(code_modules)d 数据=%(data_files)d "
                 "敏感命中=%(sensitive_hits)d 对照=%(controls_found)s" % stats)
    if problems:
        for p in problems[:20]:
            lines.append("  ❌ " + p)
        if len(problems) > 20:
            lines.append("  …另有 %d 项" % (len(problems) - 20))
        return False, lines
    lines.append("✅ 脱敏门禁通过（0 敏感残留；阳性对照 %s）"
                 % "/".join(stats["controls_found"]))
    return True, lines
