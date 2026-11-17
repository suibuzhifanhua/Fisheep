"""agent_tools.os_ops — PC 操控工具底层实现（v1.0.297）。

六类能力（全部零新依赖：PIL 已在 venv，其余 ctypes/stdlib）：
  pc_screenshot  屏幕截图（PIL.ImageGrab，全屏或按窗口名裁剪）→ 产物库/产出/截图/
  pc_files       文件操作 list/read/write/copy/move/delete（delete 仅进回收站，绝不硬删）
  pc_run         执行命令（cmd 默认 / powershell 可选，超时保护，输出截断，GBK 兼容）
  pc_apps        进程管理 list（tasklist CSV）/ start / open / kill
  pc_clipboard   剪贴板 get/set（ctypes，UTF-16 文本）
  pc_system_info 系统状态（内存/磁盘/IP/运行时长/Top 进程）

安全口径（写进工具 description + 技能 prompt，双保险）：
  - 破坏性操作（delete/kill/覆盖写）只进回收站或需用户确认，永不 os.remove
  - pc_run 输出截断防上下文爆炸；超时强杀
  - 仅 Windows 可用，非 win32 全部返回结构化错误（源码托管跨平台跑测试不崩）
"""

import os
import re
import sys
import shutil
import platform
import subprocess
import ctypes
from ctypes import wintypes

from core.logging import log_event
from core import paths  # noqa: F401（路径函数属性查找，可被测试 patch）

# ---------- 常量 ----------
IS_WIN = (sys.platform == "win32")

# v1.0.555（P0-6）：命令文本路径 token 抽取——引号段 + 盘符开头的路径。
# 供 pc_run 复用 pc_files 的保护路径校验（check_paths）。
# v1.0.652（审计 P2-6）：补 %VAR%/$env:VAR/UNC 三类 token——cmd shell=True 会原样
# 展开环境变量，`echo x > %SystemRoot%\System32\evil.dll` / UNC 写法旧正则抽不到
# → 不过 check_paths → 保护路径校验被旁路。
# v1.0.660（审查修复 H1）：补**无盘符绝对路径**（\Windows\System32\...、
# \Program Files\...）——旧正则只认盘符开头，`echo x > \Windows\System32\evil.dll`
# 抽不到 token → 不过 check_paths → 保护路径被旁路。UNC（\\server\share）此前
# 已有分支，但 \\?\ 前缀形式（\\?\C:\Windows\...）没做归一 → 同样绕过。
_PATH_TOKEN_RE = re.compile(
    r'"[^"]+"|\'[^\']+\''
    r'|%[A-Za-z_][A-Za-z0-9_]*%[^\s"\'|>;]*'          # %SystemRoot%\...（cmd 展开）
    r'|\$env:[A-Za-z_][A-Za-z0-9_]*[^\s"\'|>;]*'      # $env:ProgramFiles\...（PowerShell）
    r'|\\\\[^\s"\'|>;]+'                              # UNC \\server\share\...（含 \\?\ 前缀）
    r'|[A-Za-z]:\\[^\s"\'|>;]*|[A-Za-z]:/[^\s"\'|>;]*'
    # 无盘符绝对路径 \Windows\System32\... 、\Program Files\... —— 旧正则只认盘符
    # 开头，这类写法抽不到 token → 不过 check_paths → 保护路径被旁路。
    # （UNC 已在上一条分支命中；此处排除 \\ 开头，避免同一段文本被两条分支重复解析。）
    # 允许路径内部含空格：\Program Files\... 的空格是路径的一部分，漏掉就等于漏检。
    # 代价是可能把后续单词一起吞进 token——check_paths 只做前缀比对，
    # 吞多了不会误拦（\Windows is a folder 归一后不是 c:\windows\ 前缀）。
    r'|(?<![\w\\])(?!\\\\)\\[^\s"\'<>|;&]+(?:[ ][^\s"\'<>|;&]+)*')

# v1.0.660（审查修复 H1）：powershell -EncodedCommand 的**合法缩写前缀**。
# PowerShell 允许参数唯一前缀缩写：-e / -en / -enc / -enco / -encod … 直到
# -encodedcommand。旧正则只认全拼 `-enc(?:oded)?command`（还把 -encodedcommand
# 写成了 -enc 命令），于是 `powershell -e <base64>` 完全绕过"解码 + 黑名单"双重
# 检查——base64 载荷在明文里看不出任何危险特征。
# 判定口径（避免误伤 `powershell -ExecutionPolicy Bypass` 这类正常用法）：
#   ① 开关是**独立 token**（前后不粘字母/连字符），且其字母部分确为
#      "encodedcommand" 的前缀（PowerShell 的唯一前缀缩写规则）；
#   ② 开关后紧跟空白（或行尾）——`-e` 后面直接跟别的字母不算开关；
#   ③ 其后第一个参数是**合法 base64 形态**（长度 >= 16、字符集 [A-Za-z0-9+/=]、
#      且长度不可能是 4k+1——base64 长度模 4 只能是 0/2/3，4k+1 必非合法 base64）。
# 三条都满足才解码；否则按原样处理（不当成 EncodedCommand）。
_ENC_WORD = "encodedcommand"
# 合法前缀全集（PowerShell 唯一前缀缩写）+ 常见手写变体。
# "ec" 不是 "encodedcommand" 的前缀（PowerShell 里 -e 有歧义、-ec 才唯一），
# 但它是公开的攻击写法，一并纳入——解码侧宁可多解一次（多解只会多扫一遍黑名单），
# 也不可漏一次（漏一次就是一条免检通道）。
_ENC_SWITCHES = frozenset(
    [_ENC_WORD[:i] for i in range(1, len(_ENC_WORD) + 1)] + ["ec"])
_ENC_SWITCH_RE = re.compile(r"(?<![\w\\-])-(?P<sw>[A-Za-z]+)(?![\w-])")
_ENC_B64_RE = re.compile(r"^[A-Za-z0-9+/=]{16,}$")


def _iter_encoded_payloads(text):
    """找出命令文本里所有 EncodedCommand 的 base64 载荷（含缩写形态）。

    v1.0.660（H1）：旧实现只认全拼 `-enc(?:oded)?command`，`powershell -e <base64>`
    直接绕过解码；这里按"合法缩写前缀 + 独立 token + 后随合法 base64"三条判定。
    返回 base64 字符串列表（可能是嵌套的，靠调用方逐层解码）。"""
    out = []
    s = str(text or "")
    for m in _ENC_SWITCH_RE.finditer(s):
        sw = m.group("sw").lower()
        if sw not in _ENC_SWITCHES:
            continue
        rest = s[m.end():]
        # ② 开关后必须是空白（或行尾）——`-exec`/`-ExecutionPolicy` 不算
        if rest and not rest[0].isspace():
            continue
        tail = rest.lstrip()
        if not tail:
            continue
        if tail[0] in "\"'":
            q = tail[0]
            k = tail.find(q, 1)
            tok = tail[1:k] if k > 0 else tail[1:]
        else:
            tok = tail.split()[0] if tail.split() else ""
        tok = tok.strip()
        # ③ 合法 base64 形态：字符集 + 长度下限 + 长度模 4 不为 1（4k+1 必非合法 base64）
        if not _ENC_B64_RE.match(tok) or len(tok) % 4 == 1:
            continue
        out.append(tok)
    return out


def _extract_path_tokens(text):
    """从命令文本抽取路径样 token（去引号、去重、保序）。
    误召代价：非路径 token 过 check_paths 只是多一次前缀比对（无副作用）；
    漏召代价：系统目录写入旁路（P0-6）——故宁可宽不可窄。
    v1.0.652：环境变量 token 额外附**展开后**的变体进比对集——%SystemRoot% 本身
    不是保护路径，展开成 C:\\Windows 才能命中前缀（cmd 执行时同样会展开）。
    v1.0.660（审查修复 H1）：补两类此前抽不到的形态——
      ① 无盘符绝对路径（\\Windows\\System32\\...）：check_paths 侧走 os.path.abspath，
         会按"当前盘符根"还原成 C:\\Windows\\...，本函数额外附一份 "C:"+token 变体，
         避免当前工作目录在别的盘时前缀比对落空。
      ② \\?\\ 与 \\\\.\\ 前缀（\\?\\C:\\Windows\\...）：剥掉前缀再送一份，
         否则 abspath 保留原形、前缀比对永远对不上 C:\\Windows。"""
    out = []

    def _add(t):
        if t and t not in out:
            out.append(t)

    for m in _PATH_TOKEN_RE.finditer(text or ""):
        tok = m.group(0).strip("\"'")
        _add(tok)
        try:
            exp = os.path.expandvars(tok)
            _add(exp)
        except Exception:  # noqa: BLE001
            pass
        em = re.match(r"\$env:([A-Za-z_][A-Za-z0-9_]*)", tok)
        if em:
            v = os.environ.get(em.group(1), "")
            if v:
                _add(v + tok[em.end():])
        # v1.0.660（H1）：无盘符绝对路径 → 附一份带系统盘符的变体
        if tok.startswith("\\") and not tok.startswith("\\\\"):
            _add("C:" + tok)
        # v1.0.660（H1）：\\?\ / \\.\ 前缀 → 剥前缀再送一份
        if tok.startswith("\\\\?\\") or tok.startswith("\\\\.\\"):
            _add(tok[4:])
    return out

RUN_OUTPUT_LIMIT = 8000        # pc_run stdout/stderr 各自截断字符数
RUN_TIMEOUT_DEFAULT = 60       # 秒
RUN_TIMEOUT_MAX = 300
FILE_READ_LIMIT = 20000        # pc_files read 文本上限
LIST_LIMIT = 200               # list 条目上限
APPS_TOP = 30                  # 进程清单 Top N（按内存降序）
SHOT_DIR = os.path.join("产出", "截图")
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
# v1.0.299：所有子进程强制无窗口。跑课时 agent 会用 pc_run 做长等待
# （Start-Sleep/ping -n），旧版裸 Popen 每次都弹可见 powershell/cmd 黑框常驻桌面。

# 常用程序短名 → 启动方式（startfile 路径 / 系统命令）
_APP_SHORTNAMES = {
    "explorer": "explorer.exe",
    "资源管理器": "explorer.exe",
    "notepad": "notepad.exe",
    "记事本": "notepad.exe",
    "calc": "calc.exe",
    "计算器": "calc.exe",
    "mspaint": "mspaint.exe",
    "画图": "mspaint.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "浏览器": "explorer.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
}

# v1.0.660（审查修复 M1）：start/open 的**可执行/脚本扩展名**清单。
# 事故：pc_apps start/open 直接 os.startfile(target) —— 这是绕过 check_command 的
# 第二条执行通道：模型给一个 .bat/.ps1/.js/.lnk（甚至 UNC 上的可执行文件），
# 系统照单全收地跑起来，危险命令黑名单完全看不见（黑名单只管 pc_run 的命令文本）。
# 口径：只放行「文档/图片/URL/目录」；可执行与脚本类一律拒，并指引改走 pc_run
# （那条路有黑名单、有审批、有审计）。
#
# v1.0.677（P0-4 配套 · 单一事实源）：判据改为引用 core.safe_open.EXEC_EXT。
# 此前本文件就地维护了一份**更短**的清单，漏掉 .url（快捷方式，可指向
# file:// 本地文件或钓鱼 URL）、.reg（双击即导入注册表）、.py/.pyw/.pyz、
# .dll/.sys/.msu/.inf/.application/.gadget/.scf —— 这些同样会被 os.startfile
# 交给关联程序**执行**，正是本闸门要挡的那一类。判据只留一份（core.safe_open
# 本来就是"用系统关联程序打开文件"的唯一收口），两份清单必然漂移。
try:
    from core.safe_open import EXEC_EXT as _SAFE_EXEC_EXT
    _EXEC_EXT_DENY = tuple("." + e for e in sorted(_SAFE_EXEC_EXT))
except Exception:  # noqa: BLE001（core.safe_open 不可用时的兜底清单）
    _EXEC_EXT_DENY = (".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
                      ".wsf", ".wsh", ".hta", ".jar", ".msi", ".msp", ".msc", ".scr",
                      ".com", ".pif", ".cpl", ".lnk", ".exe", ".url", ".reg", ".py",
                      ".pyw", ".pyz", ".dll", ".sys", ".msu", ".inf", ".application",
                      ".gadget", ".scf")

def _not_win():
    return {"ok": False, "error": "PC 操控工具仅支持 Windows（当前 " + sys.platform + "）"}


def _ts():
    import time
    return time.strftime("%Y%m%d_%H%M%S")


def _resolve_path(path):
    """归一化用户给的路径：~ 展开、反斜杠统一、相对路径基于用户主目录。"""
    p = str(path or "").strip().strip('"').strip("'")
    if not p:
        return None
    p = p.replace("/", "\\")
    if p.startswith("~"):
        p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(os.path.expanduser("~"), p)
    return os.path.normpath(p)


# ============================================================
# 截图
# ============================================================
def pc_screenshot(window=None):
    """全屏或指定窗口截图，落盘 产物库/产出/截图/<时间戳>.png。

    window 传窗口标题关键词时，用 EnumWindows+GetWindowRect 找矩形做 bbox 裁剪；
    找不到就截全屏并在返回里注明。
    """
    if not IS_WIN:
        return _not_win()
    try:
        from PIL import ImageGrab
    except ImportError:
        return {"ok": False, "error": "缺少 Pillow，无法截图（venv 应已内置 PIL）"}

    bbox = None
    matched = None
    if window:
        bbox, matched = _find_window_rect(str(window))
    try:
        img = ImageGrab.grab(bbox=bbox)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"截图失败：{e}"}

    root = paths.workspace_root()
    d = os.path.join(root, SHOT_DIR)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"screen_{_ts()}.png")
    img.save(path, "PNG")
    out = {
        "ok": True,
        "path": path,
        "width": img.width,
        "height": img.height,
        "bytes": os.path.getsize(path),
    }
    if window:
        out["window"] = matched or f"未找到标题含「{window}」的窗口，已截全屏"
    return out


def _find_window_rect(keyword):
    """EnumWindows 遍历可见顶层窗口，标题含 keyword 的第一个 → (bbox, 标题)。"""
    user32 = ctypes.windll.user32
    res = {"bbox": None, "title": None, "err": None}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        # 回调里抛异常会被 ctypes 吞成 Unraisable（无控制台的 exe 里根本看不见），
        # 枚举还会中途停住 → 症状是"找不到窗口"而没有任何原因。逐窗兜住并留一条痕。
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if keyword in title:
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                res["bbox"] = (rect.left, rect.top, rect.right, rect.bottom)
                res["title"] = title
                return False  # 找到即停
            return True
        except Exception as e:  # noqa: BLE001
            if not res["err"]:
                res["err"] = f"{type(e).__name__}: {e}"
            return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception as e:  # noqa: BLE001
        res["err"] = res["err"] or f"EnumWindows {type(e).__name__}: {e}"
    if res["err"]:
        try:
            log_event(f"[os_ops] 枚举窗口异常（按未找到处理）：{res['err']}")
        except Exception:  # noqa: BLE001
            pass
    return res["bbox"], res["title"]


# ============================================================
# 文件操作
# ============================================================
_FOF_ALLOWUNDO = 0x40
_FOF_NOCONFIRMATION = 0x10
_FOF_SILENT = 0x4
_FOF_NOERRORUI = 0x400
_FO_DELETE = 3

# v1.0.470 修复（P1-9）：读侧敏感路径黑名单——pc_files read/list 原本只判存在性，
# 模型可读 .ssh 私钥、浏览器凭据库、KeePass 数据库等任意敏感文件并回传给 LLM。
# 写/删已有 check_paths 保护路径校验，读侧补 deny 语义（黑名单 + 明确报错，不静默）。
_READ_DENY_PAT = [
    r"(^|\\)\.ssh($|\\)", r"(^|\\)\.gnupg($|\\)", r"(^|\\)\.aws($|\\)",
    r"id_rsa", r"id_ed25519", r"id_ecdsa", r"\.pem$", r"\.ppk$", r"\.kdbx?$",
    r"login data", r"cookies(\.sqlite)?$",
    r"appdata\\(local|roaming)\\(google|chrome|mozilla|firefox|microsoft)\\",
    # v1.0.660（审查修复 H2）：旧规则 `sam($|\\)` 是**子串**匹配——用户目录恰好
    # 叫 sam（如 C:\Users\sam\Documents\a.txt）就被当成凭据文件拒读，属误伤。
    # SAM/SECURITY/SYSTEM 三个注册表蜂巢的真实位置是 %SystemRoot%\System32\config\，
    # 下面这条 system32\config 已经覆盖；这里把 sam 收紧成**精确路径**。
    r"\\config\\sam($|\\)",
    # 同理 lsass：旧规则是裸子串 "lsass"，同样会误伤同名目录；真正要挡的是
    # 系统目录下的 lsass.exe 本体（进程凭据所在），改成精确路径。
    r"\\system32\\lsass\.exe$",
    r"system32\\config",
    # v1.0.660（H2）：补齐**常见凭据文件**——读侧只黑了 .ssh/.aws 这类"教科书级"
    # 目标，日常真正会泄露的一批全在下面，且都是**路径级精确匹配**
    # （不做同名通杀：用户项目里到处是 .npmrc/.pypirc，通杀等于 pc_files read 不可用）。
    r"(^|\\)\.netrc$", r"(^|\\)_netrc$",           # curl/netrc 明文账号口令
    r"(^|\\)\.git-credentials$",                  # git push 的明文凭据
    r"(^|\\)\.npmrc$",                            # npm 源配置里的 _authToken
    r"(^|\\)\.pypirc$",                           # PyPI 上传令牌
    r"(^|\\)\.docker\\config\.json$",             # docker 登录凭据
    r"(^|\\)filezilla\\(?:sitemanager|recentservers|filezilla)\.xml$",   # FTP 站点口令
    r"(^|\\)winscp\.ini$", r"(^|\\)winscp\\[^\\]+\.ini$",               # WinSCP 会话口令
]

# ---------- v1.0.660（审查修复 H2）：读侧"自提权链"黑名单 ----------
# 事故：读侧只黑名单了 .ssh/.aws 这类**外部**凭据，本项目自己的敏感资产全是敞口——
#   · 产物库/系统/api_token.txt  = 本机 API 令牌，读到就能直接调本机 /api/*，
#     完全绕过审批闸与前端（自提权的最短路径）；
#   · 项目根 config.json         = 模型密钥/接口配置；
#   · 产物库/断点/*.json         = 完整对话与页面快照（含别的任务的上下文）；
#   · 产物库/日志/安全审计.jsonl = 审计原文（谁批准过什么、黑名单命中记录）。
# 口径：做成**路径级精确匹配**，不做"任意目录下同名文件全禁"——用户自己的项目里
# 到处是 config.json，全禁等于 pc_files read 直接不可用。只禁下列精确位置：
#   ① 产物库/系统/、产物库/断点/ 两个敏感段（按**目录段**匹配，dist/产物库/… 也覆盖）；
#   ② 产物库/日志/安全审计.jsonl（含轮转档 安全审计.1.jsonl）；
#   ③ 项目根（开发态=源码根 / 打包态=exe 同级）的 config.json、api_token(.txt)；
#   ④ 产物库根下的 config.json；
#   ⑤ 配置回退目录（%LOCALAPPDATA%\<品牌目录>\config.json）——同一份密钥的落盘副本。
_WS_NAME_RE = re.escape(getattr(paths, "WORKSPACE_NAME", "产物库"))
_READ_DENY_WS_DIR_RE = re.compile(r"(^|\\)" + _WS_NAME_RE + r"\\(系统|断点)(\\|$)")
_READ_DENY_AUDIT_RE = re.compile(
    r"(^|\\)" + _WS_NAME_RE + r"\\日志\\安全审计(\.1)?\.jsonl$")


def _read_denied_project_asset(p):
    """本项目自有敏感资产的路径级拒读判定（命中返回 True）。

    v1.0.660（H2）：见上方说明——按精确路径/目录段匹配，不做全局同名文件封杀。
    v1.0.663（审查 P0-2）：比对前先剥扩展长路径/设备前缀（\\?\\ / \\.\\ / \\?\\UNC\\），
    否则 `\\?\\C:\\...\\config.json` 与精确路径 `C:\\...\\config.json` 对不上 → 拒读被绕过。
    剥离函数与写删侧（security_ops.check_paths）单源共用。"""
    try:
        from security_ops import _strip_extended_prefix
        low = _strip_extended_prefix(str(p or "")).replace("/", "\\").lower()
    except Exception:  # noqa: BLE001
        return False
    if not low:
        return False
    # ① 产物库/系统/、产物库/断点/（目录段级，含 dist/产物库/… 等其它部署位置）
    if _READ_DENY_WS_DIR_RE.search(low):
        return True
    # ② 安全审计原文
    if _READ_DENY_AUDIT_RE.search(low):
        return True
    # ③ 项目根 / 产物库根 的 config.json、api_token(.txt)
    roots = []
    try:
        app_dir = paths._app_dir()
    except Exception:  # noqa: BLE001
        app_dir = ""
    if not app_dir:
        app_dir = getattr(paths, "BASE", "") or ""
    if app_dir:
        app_dir = os.path.normcase(os.path.abspath(str(app_dir))).replace("/", "\\").lower()
        roots.append(app_dir)
        roots.append(os.path.join(app_dir, getattr(paths, "WORKSPACE_NAME", "产物库")))
    # ⑤ 配置回退目录（不调用 paths._fallback_dir()——它会建目录，读判定不该有副作用）
    # v1.0.680（改名）：新旧目录名都要列入受保护路径——老用户的密钥副本可能
    # 还在旧目录里（core.paths._fallback_dir 兼容期沿用老目录），漏了它
    # 就等于给模型开了个读密钥的口子。
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    if local:
        from core.branding import ALL_DATA_DIRS   # v1.0.691：三代目录名（WeComCompanion/Lingban/Fisheep）全列
        for _dname in ALL_DATA_DIRS:
            roots.append(os.path.join(os.path.normcase(os.path.abspath(local)), _dname))
    for r in roots:
        for name in ("config.json", "api_token.txt", "api_token"):
            if low == os.path.join(r, name):
                return True
    return False


def _read_denied(p):
    low = str(p).lower()
    if any(re.search(pat, low) for pat in _READ_DENY_PAT):
        return True
    # v1.0.660（H2）：并入本项目自有敏感资产（api_token/config.json/断点/审计原文）
    return _read_denied_project_asset(p)


def _read_root_verdict(path, cfg=None):
    """读侧根目录判定 → (verdict, reason)。verdict ∈ pass|ask|block。

    v1.0.677（P0-4 修复）：读侧此前**只有凭据黑名单**、没有根目录约束——黑名单
    永远不完整，模型只要读到任意一个不在名单上的文本文件，内容就进了上下文，
    而上下文会回传给 LLM（"提示词注入 → 读本机文件 → 内容出境"是一条通的链）。
    现在叠加一层根白名单：落在允许根外 → 转人工审批（用户点批准才继续）。

    安全模块不可用时按**转人工审批**处理（fail-closed）：一次环境异常不该让
    agent 什么都读不了，但也不该在安全判定失效时**静默放行**根外读取——
    凭据黑名单是更早的一道硬拒，不受这里影响。
    """
    try:
        from security_ops import check_read_path
        return check_read_path(path, cfg)
    except Exception as e:  # noqa: BLE001
        log_event(f"[security] 读侧根目录判定异常（按转人工审批处理，硬拒名单仍在）：{e}")
        return "ask", (f"读取路径 {path} 的安全判定异常，需人工确认是否允许读取该路径。")


class SHFILEOPSTRUCTW(ctypes.Structure):
    """SHFileOperationW 参数结构（回收站删除用）。"""
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def pc_files(op, path=None, target=None, content=None, cfg=None):
    """文件系统操作。delete 只进回收站（SHFileMoveW FOF_ALLOWUNDO），永不硬删。
    v1.0.320 安全层：写/删/移动目标保护路径校验；覆盖/删除前自动备份；
    删除超阈值转审批（__approval_needed__，loop 挂起弹窗）。"""
    if not IS_WIN:
        return _not_win()
    op = str(op or "").strip().lower()
    if op not in ("list", "read", "write", "copy", "move", "delete"):
        return {"ok": False, "error": f"未知 op：{op}（可选 list/read/write/copy/move/delete）"}

    p = _resolve_path(path)
    if not p:
        return {"ok": False, "error": "缺少 path 参数"}
    t = _resolve_path(target) if target else None

    # —— 安全层（v1.0.320）：修改类操作先过保护路径；备份；删除审批 ——
    if op in ("write", "delete", "copy", "move"):
        try:
            from security_ops import check_paths, audit, args_hash
            targets = [p] + ([t] if t else [])
            allowed, hit = check_paths(targets, cfg)
            audit(f"pc_files:{op}", f"{p}" + (f" → {t}" if t else ""), allowed,
                  "" if allowed else f"保护路径 {hit}")
            if not allowed:
                return {"ok": False, "error": f"已拦截：目标命中保护路径「{hit}」。系统目录不允许 agent 写入/删除。"}
        except Exception as e:
            # v1.0.361 审查修复（O7）：路径检查异常 fail-closed（转人工审批）
            log_event(f"[security] 路径检查异常（转人工审批，不再放行）：{e}")
            return {"__approval_needed__": True, "kind": "file", "path": p,
                    "hash": args_hash(f"pc_files:{op}", p), "reason": f"安全检查异常，需人工确认：{e}"}
    if op == "delete":
        try:
            from security_ops import check_delete, backup_file, audit
            verdict, reason, h = check_delete(p, cfg)
            audit("pc_files:delete", p, verdict == "pass", reason)
            if verdict == "block":
                return {"ok": False, "error": f"已拦截：{reason}"}
            if verdict == "ask":
                return {"__approval_needed__": True, "kind": "delete", "path": p,
                        "hash": h, "reason": reason}
            # v1.0.555（P1-10）：删除前额外备份的返回值也要看——原只调不看，失败
            # （磁盘满/权限）时无痕。回收站仍是主保险，此处仅记日志明示。
            if backup_file(p, cfg) is None:      # 回收站之外的额外保险
                log_event(f"[security] 删除前额外备份失败（回收站仍生效）：{p}")
        except Exception as e:
            # v1.0.361（O7）：删除检查异常 fail-closed（删除是不可逆操作）
            log_event(f"[security] 删除检查异常（拦截并转人工，不再放行）：{e}")
            return {"__approval_needed__": True, "kind": "delete", "path": p,
                    "hash": args_hash("pc_files:delete", p), "reason": f"删除检查异常，需人工确认：{e}"}

    try:
        if op == "list":
            if not os.path.isdir(p):
                return {"ok": False, "error": f"目录不存在：{p}"}
            if _read_denied(p):
                from security_ops import audit
                audit("pc_files:list", p, False, "命中敏感路径黑名单")
                return {"ok": False, "error": f"目录命中敏感路径黑名单，拒绝列出：{p}"}
            # v1.0.677（P0-4）：根目录约束——列出根外目录同样会泄漏文件名。
            _rv, _rr = _read_root_verdict(p, cfg)
            if _rv == "block":
                return {"ok": False, "error": f"已拦截：{_rr}"}
            if _rv == "ask":
                from security_ops import args_hash, audit, consume
                _h = args_hash("pc_files:list", p)
                if not consume(_h):
                    audit("pc_files:list", p, False, "根外目录待人工确认")
                    return {"__approval_needed__": True, "kind": "file_read", "path": p,
                            "hash": _h, "reason": _rr}
                audit("pc_files:list", p, True, "用户已批准（一次性令牌）")
            entries = []
            with os.scandir(p) as it:
                for i, e in enumerate(it):
                    if i >= LIST_LIMIT:
                        break
                    try:
                        st = e.stat()
                        entries.append({"name": e.name, "is_dir": e.is_dir(),
                                        "size": 0 if e.is_dir() else st.st_size,
                                        "mtime": int(st.st_mtime)})
                    except OSError:
                        entries.append({"name": e.name, "is_dir": False, "size": -1, "mtime": 0})
            return {"ok": True, "dir": p, "count": len(entries),
                    "truncated": len(entries) >= LIST_LIMIT, "entries": entries}

        if op == "read":
            if not os.path.isfile(p):
                return {"ok": False, "error": f"文件不存在：{p}"}
            if _read_denied(p):
                from security_ops import audit
                audit("pc_files:read", p, False, "命中敏感路径黑名单")
                return {"ok": False, "error": f"文件命中敏感路径黑名单（凭据/密钥类），拒绝读取：{p}"}
            # v1.0.677（P0-4）：根目录约束——凭据黑名单之外的文本文件（业务文档、
            # 配置、日志）此前**任意路径可读**，内容进上下文后会回传 LLM。
            _rv, _rr = _read_root_verdict(p, cfg)
            if _rv == "block":
                return {"ok": False, "error": f"已拦截：{_rr}"}
            if _rv == "ask":
                from security_ops import args_hash, audit, consume
                _h = args_hash("pc_files:read", p)
                if not consume(_h):
                    audit("pc_files:read", p, False, "根外路径待人工确认")
                    return {"__approval_needed__": True, "kind": "file_read", "path": p,
                            "hash": _h, "reason": _rr}
                audit("pc_files:read", p, True, "用户已批准（一次性令牌）")
            size = os.path.getsize(p)
            if size > 10 * 1024 * 1024:
                return {"ok": False, "error": f"文件过大（{size}B > 10MB），请用 pc_run 处理或先复制小片段"}
            with open(p, "rb") as f:
                raw = f.read()
            for enc in ("utf-8", "gbk"):
                try:
                    text = raw.decode(enc)
                    truncated = len(text) > FILE_READ_LIMIT
                    return {"ok": True, "path": p, "size": size, "encoding": enc,
                            "truncated": truncated, "content": text[:FILE_READ_LIMIT]}
                except (UnicodeDecodeError, UnicodeError):
                    continue
            return {"ok": True, "path": p, "size": size, "encoding": None,
                    "content": f"（二进制文件，无可用文本，{size} 字节）"}

        if op == "write":
            if content is None:
                return {"ok": False, "error": "write 需要 content 参数"}
            existed = os.path.exists(p)
            _backup_failed = False
            if existed:
                # v1.0.660（M4）：备份失败 → **拒写并转人工审批**（fail-closed）。
                # 事故：备份失败（磁盘满/权限/文件被占用）时覆盖照旧执行，只在返回体
                # 标一个 backup_failed —— 用户的原文件被覆盖后**没有任何第二副本**，
                # 而工具 description 承诺"覆盖前自动备份"。备份是这套机制里唯一
                # 的可逆保障，它失败就不能当没发生过（与项目各处 fail-closed 一致）。
                # 导入放在最前：安全模块不可用时也要能算出审批 hash，一并走 fail-closed。
                from security_ops import (backup_file, sec_cfg, args_hash,
                                          consume, audit)
                _h = args_hash("pc_files:write:backup", p)
                _auto_backup = True
                try:
                    _auto_backup = bool(sec_cfg(cfg).get("auto_backup", True))
                    _bak = backup_file(p, cfg)   # v1.0.320：覆盖前自动备份
                except Exception as _e:  # noqa: BLE001
                    _bak = None
                    log_event(f"[security] 覆盖前备份异常（按未备份处理，转人工审批）：{_e}")
                if _bak is None and _auto_backup:
                    # 备份失败 → 先问用户（复用既有审批管道：一次性令牌 + 原样重试）
                    if not consume(_h):
                        audit("pc_files:write", p, False,
                              "覆盖前自动备份失败，已按「先备份后写」拒绝覆盖")
                        return {"__approval_needed__": True, "kind": "file", "path": p,
                                "hash": _h,
                                "reason": "覆盖前自动备份失败（磁盘满/权限/文件被占用）——"
                                          "按「先备份后写」原则已拒绝覆盖，"
                                          "请确认是否在无备份的情况下继续写入"}
                    _backup_failed = True
                    log_event(f"[security] 覆盖前备份失败，用户已批准无备份覆盖：{p}")
                elif _bak is None:
                    # auto_backup 被用户关掉：不是故障，但覆盖确实没有第二副本，明示。
                    _backup_failed = True
                    log_event(f"[security] 覆盖前未生成备份（auto_backup 已关闭）：{p}")
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(str(content))
            return {"ok": True, "path": p, "bytes": os.path.getsize(p),
                    "note": "覆盖已有文件" if existed else "新建文件",
                    **({"backup_failed": True} if _backup_failed else {})}

        if op in ("copy", "move"):
            if not t:
                return {"ok": False, "error": f"{op} 需要 target 参数（目标路径）"}
            if not os.path.exists(p):
                return {"ok": False, "error": f"源不存在：{p}"}
            os.makedirs(os.path.dirname(t) or ".", exist_ok=True)
            if op == "copy":
                if os.path.isdir(p):
                    shutil.copytree(p, t, dirs_exist_ok=True)
                else:
                    shutil.copy2(p, t)
            else:
                shutil.move(p, t)
            return {"ok": True, "op": op, "from": p, "to": t}

        if op == "delete":
            if not os.path.exists(p):
                return {"ok": False, "error": f"不存在：{p}"}
            # 仅回收站（SHFileOperationW + FOF_ALLOWUNDO），永不硬删
            if not IS_WIN:
                return {"ok": False, "error": "非 Windows 平台不支持删除（安全口径：不硬删）"}
            shell32 = ctypes.windll.shell32
            so = SHFILEOPSTRUCTW()
            so.wFunc = _FO_DELETE
            # SHFileOperation 要求 pFrom 双 null 结尾；ctypes 字符串赋值只带单终止符，
            # 必须显式 buffer（len+2 wchar）+ cast，且调用期间保持 buf 引用防 GC 悬空
            _pfrom_buf = ctypes.create_unicode_buffer(p, len(p) + 2)
            so.pFrom = ctypes.cast(_pfrom_buf, wintypes.LPCWSTR)
            so.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION
                         | _FOF_SILENT | _FOF_NOERRORUI)
            ret = shell32.SHFileOperationW(ctypes.byref(so))
            if ret == 0 and not so.fAnyOperationsAborted:
                return {"ok": True, "path": p, "note": "已移入回收站（可还原）"}
            return {"ok": False, "error": f"移入回收站失败（SHFileOperationW ret={ret}"
                                 f"{'，操作被中止' if so.fAnyOperationsAborted else ''}）。"
                                 f"文件仍在原位：{p}，请手动删除或换 pc_run 处理。"}
    except PermissionError as e:
        return {"ok": False, "error": f"权限不足：{e}（文件可能被占用，或用管理员权限）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"操作失败：{e}"}


# ============================================================
# 命令执行
# ============================================================
def pc_run(command, shell="cmd", timeout=None, cfg=None):
    """执行一条命令（cmd 或 powershell），返回 exit_code/stdout/stderr（截断）。
    v1.0.320 安全层：危险命令黑白名单+三模式（block/ask/pass），ask 转审批挂起。"""
    if not IS_WIN:
        return _not_win()
    command = str(command or "").strip()
    if not command:
        return {"ok": False, "error": "缺少 command 参数"}
    # —— 安全层（v1.0.320）——
    try:
        from security_ops import check_command, audit, args_hash, consume
        # v1.0.373 审查修复（P1-1）：powershell -EncodedCommand 的 base64 体内含危险命令时
        # 黑名单看不到（原只扫明文，-enc <base64> 直接 pass）。解码后拼接进扫描文本；
        # 解码失败按 fail-closed 转人工审批。
        scan_text = command
        # v1.0.588：多层嵌套解码。原实现只解一层，`powershell -enc <里面又是 -enc>`
        # 的二重包装会让黑名单看不到真正的载荷（经典 AMSI/AV 绕过手法）。
        # v1.0.660（审查修复 H1）：解码入口改为 _iter_encoded_payloads——旧正则只认全拼
        # `-enc(?:oded)?command`，`powershell -e <base64>` 这类**合法缩写**形态整个漏检，
        # 等于给了一条免检通道。新入口要求"开关是 encodedcommand 的前缀 + 独立 token +
        # 后随合法 base64"，既堵住缩写绕过，又不误伤 `-ExecutionPolicy Bypass`。
        import base64 as _b64
        _pending = _iter_encoded_payloads(command)
        layers, seen = list(_pending), 0
        while layers and seen < 3:
            b64 = layers.pop()
            try:
                b64 += "=" * (-len(b64) % 4)
                decoded = _b64.b64decode(b64).decode("utf-16-le", errors="replace")
            except Exception:
                return {"__approval_needed__": True, "kind": "command", "command": command,
                        "hash": args_hash("pc_run", command),
                        "reason": "检测到 -EncodedCommand 但无法解码，需人工确认内容"}
            # v1.0.660（H1）：解码后的**真实命令**并入扫描文本 —— 下面 check_command
            # 扫的就是这段拼接结果，黑名单因此能看到 base64 里的原始命令。
            scan_text += "\n" + decoded
            seen += 1
            layers.extend(_iter_encoded_payloads(decoded))
        verdict, reason = check_command(scan_text, cfg)
        audit("pc_run", command, verdict == "pass", reason)
        if verdict == "block":
            return {"ok": False, "error": f"已拦截危险命令：{reason}。如确需执行，请到「设置 → 安全中心」调整命令模式或加入放行名单。"}
        if verdict == "ask":
            h = args_hash("pc_run", command)
            # v1.0.660（M1 配套修复）：**消费一次性令牌**——旧实现发出审批请求后
            # 从不消费这枚 hash，用户点「批准」后 agent 重试仍会再弹一次同样的窗
            # （无限弹窗），批准形同无效。口径与 check_delete 一致：先看令牌，
            # 没有才发审批请求。M1 的"杀关键进程转审批"复用同一条路径，故必须先修。
            if consume(h):
                audit("pc_run", command, True, "用户已批准（一次性令牌）")
            else:
                return {"__approval_needed__": True, "kind": "command", "command": command,
                        "hash": h, "reason": reason}
        # v1.0.555（P0-6 修复）：命令通道补保护路径校验——原只过 check_command
        # 危险正则，对"写文件到系统目录"毫无防护：`echo x > C:\Windows\System32\...`、
        # `copy evil.dll "C:\Program Files\..."`、`curl -o C:\Windows\...`、
        # `certutil -urlcache -f ...` 全部 verdict=pass 直接执行。产品有管理员
        # 运行场景（pc_system_info 上报 is_admin），模型被诱导/幻觉时可达系统级
        # 持久化。pc_files 的 check_paths（覆盖 C:\Windows/Program Files/
        # ProgramData + realpath 双路 + 8.3 短名展开）复用到命令层：抽取命令文本
        # 中的路径 token（引号段 + 盘符开头路径）过同一套保护前缀。
        _cmd_paths = _extract_path_tokens(scan_text)
        if _cmd_paths:
            try:
                from security_ops import check_paths as _check_paths
                _allowed, _hit = _check_paths(_cmd_paths, cfg)
                if not _allowed:
                    audit("pc_run:protected_path", command, False, f"保护路径 {_hit}")
                    return {"ok": False, "error": f"已拦截：命令目标命中保护路径「{_hit}」。"
                                         "系统目录不允许 agent 写入/删除（pc_files 同口径）。"}
            except Exception as _pe:  # noqa: BLE001
                # fail-closed：路径检查异常转人工审批（与命令检查异常同口径）
                log_event(f"[security] 命令路径检查异常（转人工审批，不再放行）：{_pe}")
                return {"__approval_needed__": True, "kind": "command", "command": command,
                        "hash": args_hash("pc_run", command),
                        "reason": f"命令路径检查异常，需人工确认：{_pe}"}
    except Exception as e:
        # v1.0.361 审查修复（O7）：安全检查异常 fail-closed——原"放行并继续"让整个
        # 安全层单点失效（一次异常即全裸奔）。转人工审批（fail-closed 的最小代价形态）。
        log_event(f"[security] 命令检查异常（转人工审批，不再放行）：{e}")
        return {"__approval_needed__": True, "kind": "command", "command": command,
                "hash": args_hash("pc_run", command), "reason": f"安全检查异常，需人工确认：{e}"}
    shell = str(shell or "cmd").strip().lower()
    if shell not in ("cmd", "powershell"):
        return {"ok": False, "error": f"未知 shell：{shell}（可选 cmd/powershell）"}
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = RUN_TIMEOUT_DEFAULT
    timeout = max(5, min(timeout, RUN_TIMEOUT_MAX))

    # v1.0.623：软件自装的 node/git 是便携版，从来没进系统 PATH（组件页一直写
    # 「只在本软件内可用」，但对命令行任务其实是假的）。先把它顶进本进程 PATH，
    # 后面 Popen 继承的就是可用环境——否则 agent 一句 `node x.js` 报"找不到命令"，
    # 只能满盘搜目录（用户实际就是这么绕了一圈才发现是软件装的组件）。不写注册表。
    try:
        from core import portable as _pb
        _pb.apply_component_path()
    except Exception as _pce:  # noqa: BLE001
        log_event(f"[pc_run] 便携运行时 PATH 注入失败（不拦命令执行）：{_pce}")

    try:
        if shell == "powershell":
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-Command", command]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=CREATE_NO_WINDOW)
        else:
            p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"启动命令失败：{e}"}

    timed_out = False
    try:
        out_b, err_b = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            # v1.0.361 审查修复（O6）：taskkill /F /T 杀整棵进程树（shell=True 下
            # cmd/powershell 派生的实际工作进程才是真任务，原 p.kill() 只杀壳）
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=10,
                           creationflags=0x08000000 if os.name == "nt" else 0)
            out_b, err_b = p.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
                out_b, err_b = p.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                out_b, err_b = b"", b""

    def _dec(b):
        for enc in ("gbk", "utf-8"):
            try:
                return b.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return b.decode("utf-8", errors="replace")

    stdout = _dec(out_b or b"")
    stderr = _dec(err_b or b"")
    cut = lambda s: s[:RUN_OUTPUT_LIMIT] + (f"\n...[已截断：共 {len(s)} 字符]" if len(s) > RUN_OUTPUT_LIMIT else "")
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        "timed_out": timed_out,
        "shell": shell,
        "stdout": cut(stdout),
        "stderr": cut(stderr),
    }


# ============================================================
# 进程管理
# ============================================================
def _tasklist_rows():
    """tasklist /FO CSV → [{name, pid, mem_mb}]，失败返回 None。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            creationflags=CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001
        return None
    text = (r.stdout or b"").decode("gbk", errors="replace")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        fields = re.findall(r'"([^"]*)"', line)
        if len(fields) < 5:
            continue
        name, pid, mem_raw = fields[0], fields[1], fields[3]
        try:
            mem_mb = int(mem_raw.replace(",", "")) / 1024.0
        except ValueError:
            mem_mb = 0.0
        rows.append({"name": name, "pid": int(pid), "mem_mb": round(mem_mb, 1)})
    return rows


def _apps_start(target, cfg=None):
    """pc_apps 的 start 分支：程序短名或路径。

    v1.0.660（审查修复 M1 + 拆分）：从 pc_apps 抽出——os.startfile 是绕过
    check_command 的第二条执行通道，可执行与脚本类文件一律拒（仅白名单短名放行）。
    单独成函数是为了把 pc_apps 压回 100 行棘轮线以内（巨型函数是历次静默失效的
    温床：判据算出来没进判定、异常吞掉，都发生在没人敢读整个函数的地方）。
    """
    resolved = _APP_SHORTNAMES.get(target.lower()) or _APP_SHORTNAMES.get(target)
    try:
        if target.lower().startswith(("http://", "https://", "mailto:")):
            os.startfile(target)  # noqa: S606
            return {"ok": True, "opened": target, "note": "已用默认浏览器打开"}
        # 白名单短名（记事本/浏览器/计算器…）之外的**可执行文件**一律拒。
        if not resolved:
            _ext = os.path.splitext(target)[1].lower()
            if _ext in _EXEC_EXT_DENY:
                return {"ok": False, "error": f"已拦截：start 不接受可执行文件（{_ext}）——"
                                             "这是绕过危险命令黑名单的执行通道。"
                                             "请改用 pc_run 执行（会经过命令检查与审批）。"}
        launch = resolved or target
        if os.path.isabs(launch) and os.path.exists(launch):
            os.startfile(launch)  # noqa: S606
            return {"ok": True, "started": launch}
        os.startfile(launch)  # noqa: S606  交给 Shell 解析（PATH/注册）
        return {"ok": True, "started": launch, "note": "已交 Shell 启动（短名按 PATH 解析）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"启动失败：{e}"}


def _apps_open(target, cfg=None):
    """pc_apps 的 open 分支：URL 或文件绝对路径（文档/图片/目录放行，可执行文件拒）。"""
    _ext = os.path.splitext(target)[1].lower()
    if _ext in _EXEC_EXT_DENY:
        return {"ok": False, "error": f"已拦截：open 不接受可执行文件（{_ext}）——"
                                 "这是绕过危险命令黑名单的执行通道。"
                                 "请改用 pc_run 执行（会经过命令检查与审批）。"}
    try:
        os.startfile(target)  # noqa: S606
        return {"ok": True, "opened": target, "note": "已用系统默认程序打开"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"打开失败：{e}"}


def _apps_kill(target, cfg=None):
    """pc_apps 的 kill 分支：结束进程（不可逆）。

    v1.0.660（审查修复 M1/M10 + 拆分）：企微进程族一律拒；关键系统进程
    （资源管理器/杀软/索引服务）非「完全访问」档转人工审批。
    """
    # v1.0.646（用户："软件修复的时候不要影响企业微信"）：企业微信进程族一律不许模型杀。
    # 绑定流程自己会按用户确认重启它，模型这条路只会把用户的工作现场关掉——
    # 历史上它就干过（v1.0.425 记录：流水线卡住时模型自行 curl 本 API 重启企微）。
    # v1.0.660（M10）：判定统一走 security_ops 的**共享实现**（进程名 + PID→进程名
    # 解析）——此前本文件一套、security_ops 一套，两处口径必然漂移，且命令通道
    # 只按文本正则认进程名、认不出纯数字 PID。
    from security_ops import (audit, args_hash, consume,
                              check_wecom_kill_target, check_proc_kill_target,
                              permission_level as _perm_level)
    _hit, _pname = check_wecom_kill_target(target, resolve_pid=False)
    if _hit:
        audit("pc_process:kill", target, False, "企业微信进程拒绝强杀")
        return {"ok": False, "error": "拒绝：企业微信（WXWork/WXWorkWeb）由本软件的绑定流程按用户确认管理，"
                             "不允许通过命令结束。需要重启它请到 设置 → 企业微信 里操作。"}
    # v1.0.660（M1）：关键系统进程（资源管理器/杀软/索引服务）——杀它等于当场
    # 弄坏用户现场且不可撤销，非「完全访问」档一律转人工审批（复用既有审批管道）。
    # 口径与上一条一致：数字 PID 不反查进程名（只按名字判），见 security_ops
    # check_proc_kill_target 的说明。
    _crit, _cname = check_proc_kill_target(target, resolve_pid=False)
    if _crit:
        if _perm_level(cfg) != "full":
            _h = args_hash("pc_apps:kill", target)
            if not consume(_h):
                audit("pc_process:kill", target, False,
                      f"关键系统进程（{_cname}）结束操作需人工确认")
                return {"__approval_needed__": True, "kind": "command",
                        "command": f"结束进程 {_cname}（关键系统进程）",
                        "path": "", "hash": _h,
                        "reason": f"结束关键系统进程 {_cname} 会破坏用户现场（桌面/防护/索引）"
                                  "且不可撤销，需人工确认"}
            audit("pc_process:kill", target, True, "用户已批准结束关键系统进程")
    try:
        if target.isdigit():
            args = ["taskkill", "/F", "/PID", target]
        else:
            args = ["taskkill", "/F", "/IM", target]
            if not target.lower().endswith(".exe"):
                args[3] = target + ".exe"
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=15, creationflags=CREATE_NO_WINDOW)
        text = (r.stdout or b"").decode("gbk", errors="replace").strip()
        return {"ok": r.returncode == 0, "exit_code": r.returncode,
                "output": text[:500],
                "note": "已强制结束（不可逆，进程未保存数据会丢失）" if r.returncode == 0 else ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"kill 失败：{e}"}


def pc_apps(op, name=None, timeout=None, cfg=None):
    """进程管理：list（Top N 按内存）/ start（程序短名或路径）/ open（URL/文件）/ kill（不可逆）。

    v1.0.660（审查修复 M1 + 拆分）：kill 命中**关键系统进程**（资源管理器/杀软/索引服务）时，
    非「完全访问」档转人工审批（复用既有审批管道：__approval_needed__ + 一次性令牌
    + 原样重试）；start/open 拒绝可执行与脚本类文件（os.startfile 是绕过命令
    黑名单的第二条执行通道）。
    v1.0.660（拆分）：三个写操作分支各自独立成 _apps_start/_apps_open/_apps_kill，
    本函数只做 op 分派——巨型函数（>100 行）是本项目历次静默失效的温床，
    判据算出来没进判定、异常吞掉、权限口径手抄多份，都发生在没人敢读整个函数的地方。"""
    if not IS_WIN:
        return _not_win()
    op = str(op or "").strip().lower()
    if op not in ("list", "start", "open", "kill"):
        return {"ok": False, "error": f"未知 op：{op}（可选 list/start/open/kill）"}

    if op == "list":
        rows = _tasklist_rows()
        if rows is None:
            return {"ok": False, "error": "tasklist 执行失败"}
        rows.sort(key=lambda x: x["mem_mb"], reverse=True)
        return {"ok": True, "total": len(rows), "top": rows[:APPS_TOP]}

    if op == "start":
        target = str(name or "").strip()
        if not target:
            return {"ok": False, "error": "start 需要 name 参数（程序短名/路径/URL）"}
        return _apps_start(target, cfg)

    if op == "open":
        target = str(name or "").strip()
        if not target:
            return {"ok": False, "error": "open 需要 name 参数（URL 或文件绝对路径）"}
        return _apps_open(target, cfg)

    if op == "kill":
        target = str(name or "").strip()
        if not target:
            return {"ok": False, "error": "kill 需要 name 参数（进程名如 chrome.exe 或 PID）"}
        return _apps_kill(target, cfg)


# ============================================================
# 剪贴板（ctypes，UTF-16 文本）
# ============================================================
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_CLIP_RETRY = 10          # OpenClipboard 重试次数（火绒等安全软件常短暂占用剪贴板）
_CLIP_RETRY_GAP = 0.15    # 秒

# ctypes 64 位句柄必须显式声明 restype（默认 int 会截断 32 位 → 访问违例）
# v1.0.592 修复（P0）：缓存变量原来与函数同名 `_clip_dlls`——同文件内
#   `_clip_dlls = None` 紧跟 `def _clip_dlls():`，def 把同名模块属性重绑成函数本身，
#   于是函数内 `if _clip_dlls is None` 恒为 False，restype 声明一次都不执行、
#   `_clip_dlls = (u, k)` 那次赋值也永不发生；函数最后 `return _clip_dlls` 返回的是
#   【函数对象自己】，调用方 `u, _ = _clip_dlls()` 直接 TypeError：
#   「cannot unpack non-iterable function object」——pc_clipboard 的读/写全路径自 v1.0.4xx
#   起永久失效（被 except 兜成"剪贴板操作失败"，测试因直接 monkeypatch _clip_get/_clip_set 而从没碰到）。
#   缓存改名 _CLIP_DLLS（大写=模块级可变缓存，与本文件其它缓存命名一致），彻底消除自覆盖。
_CLIP_DLLS = None


def _clip_dlls():
    global _CLIP_DLLS
    if _CLIP_DLLS is None:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HANDLE
        k.GlobalLock.argtypes = [wintypes.HANDLE]
        k.GlobalLock.restype = ctypes.c_void_p
        _CLIP_DLLS = (u, k)
    return _CLIP_DLLS


def _clip_open():
    """带重试的 OpenClipboard；全败抛 RuntimeError。"""
    import time
    u, _ = _clip_dlls()
    for i in range(_CLIP_RETRY):
        if u.OpenClipboard(None):
            return
        time.sleep(_CLIP_RETRY_GAP)
    raise RuntimeError("OpenClipboard 失败（剪贴板被其它程序占用，重试 %d 次未成功）" % _CLIP_RETRY)


def _clip_get():
    u, k = _clip_dlls()
    _clip_open()
    try:
        if not u.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return None
        h = u.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return None
        ptr = k.GlobalLock(h)
        if not ptr:
            raise RuntimeError("GlobalLock 失败")
        try:
            return ctypes.wstring_at(ptr)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _clip_set(text):
    u, k = _clip_dlls()
    data = (text or "") + "\0"
    size = len(data) * 2
    hmem = k.GlobalAlloc(_GMEM_MOVEABLE, size)
    if not hmem:
        raise RuntimeError("GlobalAlloc 失败")
    try:
        ptr = k.GlobalLock(hmem)
        if not ptr:
            raise RuntimeError("GlobalLock 失败")
        ctypes.memmove(ptr, data.encode("utf-16-le"), size)
        k.GlobalUnlock(hmem)
        _clip_open()
        try:
            u.EmptyClipboard()
            if not u.SetClipboardData(_CF_UNICODETEXT, hmem):
                # 失败时句柄所有权未转移，需释放；置 None 防 except 里二次释放
                k.GlobalFree(hmem)
                hmem = None
                raise RuntimeError("SetClipboardData 失败")
            # 成功：句柄归剪贴板所有，不得再释放
            hmem = None
        finally:
            u.CloseClipboard()
    except Exception:
        if hmem:
            try:
                k.GlobalFree(hmem)
            except Exception:  # noqa: BLE001
                pass
        raise


def pc_clipboard(op, text=None):
    """剪贴板：get 读取 / set 写入（纯文本 UTF-16）。"""
    if not IS_WIN:
        return _not_win()
    op = str(op or "").strip().lower()
    try:
        if op == "get":
            v = _clip_get()
            if v is None:
                return {"ok": True, "text": "", "note": "剪贴板无文本内容（可能是图片等其它类型）"}
            return {"ok": True, "text": v[:FILE_READ_LIMIT],
                    "truncated": len(v) > FILE_READ_LIMIT}
        if op == "set":
            if text is None:
                return {"ok": False, "error": "set 需要 text 参数"}
            _clip_set(str(text))
            return {"ok": True, "chars": len(str(text))}
        return {"ok": False, "error": f"未知 op：{op}（可选 get/set）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"剪贴板操作失败：{e}"}


# ============================================================
# 系统状态
# ============================================================
def pc_system_info():
    """内存/磁盘/本机 IP/运行时长/Top 进程。"""
    if not IS_WIN:
        return _not_win()
    # v1.0.373 管理员模式：agent 感知当前权限级别（管理员下系统级命令才可能成功）
    try:
        _is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        _is_admin = False
    info = {
        "ok": True,
        "hostname": platform.node(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "is_admin": _is_admin,
    }
    # 内存
    try:
        class _MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = _MEMSTAT()
        st.dwLength = ctypes.sizeof(_MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        info["ram_total_gb"] = round(st.ullTotalPhys / 1024 ** 3, 1)
        info["ram_available_gb"] = round(st.ullAvailPhys / 1024 ** 3, 1)
        info["ram_load_pct"] = st.dwMemoryLoad
    except Exception as e:  # noqa: BLE001
        info["ram_error"] = str(e)
    # 磁盘
    disks = []
    try:
        drives = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if drives & (1 << i):
                root = f"{chr(65 + i)}:\\"
                try:
                    du = shutil.disk_usage(root)
                    disks.append({"drive": root,
                                  "total_gb": round(du.total / 1024 ** 3, 1),
                                  "free_gb": round(du.free / 1024 ** 3, 1),
                                  "used_pct": round((du.total - du.free) * 100.0 / du.total, 1)})
                except OSError:
                    continue
        info["disks"] = disks
    except Exception as e:  # noqa: BLE001
        info["disk_error"] = str(e)
    # IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        info["local_ip"] = s.getsockname()[0]
        s.close()
    except Exception:  # noqa: BLE001
        try:
            info["local_ip"] = socket.gethostbyname(socket.gethostname())
        except Exception:  # noqa: BLE001
            info["local_ip"] = None
    # 运行时长
    try:
        tick = ctypes.windll.kernel32.GetTickCount64()
        info["uptime_hours"] = round(tick / 3600000.0, 1)
    except Exception:  # noqa: BLE001
        pass
    # Top 进程
    rows = _tasklist_rows()
    if rows:
        rows.sort(key=lambda x: x["mem_mb"], reverse=True)
        info["top_processes"] = rows[:10]
    return info
