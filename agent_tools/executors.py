"""agent_tools.executors — 工具执行器（常量 / _run_* / _TOOL_HANDLERS / run_tool / _cdp_port）。"""

# v1.0.324 启动提速 + v1.0.364 M8 收敛：requests 懒加载代理实现收敛到 core_utils
from core.logging import _lazy_requests as requests
import ipaddress
import os
import re
import socket
import threading
from urllib.parse import urljoin, urlsplit

import bridge as wb

# v1.0.663（审查 P0-4）：agent_tools **包 __init__** 禁止 services 依赖
# （tests/unit/test_import_layering.py 双锁）；本执行器层直引子模块 rule_check 合法。
from services import rule_check

from core.config_store import _load_experiences, _save_experiences, _exp_rec
from core.config_store import cdp_port as _cfg_cdp_port   # v1.0.588：端口取值口径统一

from core.config import (_pick_int, _pick_flex, _mask_args)
from core.files import _parse_file_content
from core.logging import (log_event, _timestamp, READ_TRUNCATE_BYTES)
from core.paths import (ws_rel_path, ws_sub, _ALLOWED_SUBS)
from core.workspace_io import (append_ledger, _safe_ws_read_path, dnd_allowed)
from core import paths  # noqa: F401（路径函数属性查找，可被测试 patch）
from core.workspace_io import save_workspace_file, archive_progress_round, _progress_items  # v1.0.562 修复：v1.0.555 core_utils 拆分后此函数在 workspace_io，原 save_workspace_file 断链 → agent save_file 全部报错（v1.0.561 发货版实测）
from core.branding import USER_AGENT   # v1.0.705：对外 UA 取品牌常量（不再留老名字面量）

from llm_client import judge_model, _llm_complete
from core.files import find_col_in_header  # v1.0.510（P2-13）：find_col 收敛

from . import os_ops
from . import dsh_bridge  # v1.0.298 PC 接管通道（deepseek-harness：dsh_check/dsh_prepare/dsh_run）
from . import office_gen  # v1.0.313 办公生成三件套（create_excel/create_docx/create_pptx）
from .registry import TOOLS

# ---------- 工具层常量（随模块迁移） ----------
DEFAULT_SCROLL_AMOUNT = 800    # scroll_wecom_window 默认滚动像素
DEFAULT_MSG_TYPE = "text"      # send_wecom_message 默认消息类型
DOWNLOAD_TIMEOUT = 60          # download_file 下载超时(秒)

# 工具名 -> 定义 的索引。_validate_tool_args 在**每次工具调用前**都要取一次定义，
# 原实现是每次遍历 TOOLS（条数以 len(TOOLS) 为准，勿在注释里写死 × 嵌套 .get），百轮任务即数千次线性扫描；
# 长任务里它还在并行读工具的每个 worker 线程上各跑一遍。v1.0.588 改为懒建索引，
# 并保留对 TOOLS 被替换/增删（测试 monkeypatch）的自愈：身份或长度变了就重建。
_TOOL_INDEX = {}
_TOOL_INDEX_SRC = None


def _tool_by_name(name):
    global _TOOL_INDEX, _TOOL_INDEX_SRC
    if _TOOL_INDEX_SRC is not TOOLS or len(_TOOL_INDEX) != len(TOOLS):
        _TOOL_INDEX = {t.get("function", {}).get("name"): t for t in TOOLS}
        _TOOL_INDEX_SRC = TOOLS
    return _TOOL_INDEX.get(name)


def _validate_tool_args(name, args):
    """工具调用前轻量参数 schema 校验（对标 Hermes tool-use 可靠性）：
    必填缺失 / 枚举越界 / 类型错误 提前拦截，返回结构化错误（__error__）让模型
    修正参数后重新调用同一工具——避免无效执行浪费轮次，也避免漏参调用在页面
    上产生副作用（如 click 缺 target、scroll 传非法 direction）。

    校验规则从 TOOLS 注册表的 parameters（properties + required + enum + type）派生：
    - required 缺失（或 None）→ 提示缺哪些 + 合法选项/类型
    - 枚举值不匹配 → 列出合法值
    - 类型不符（integer/array/boolean）→ 提示正确类型；数字接受字符串形式（模型常见输出）
    返回 None 表示校验通过；返回 dict 表示校验失败（作为工具结果回灌给模型）。
    """
    t = _tool_by_name(name)
    if not t:
        return None
    fn = t.get("function", {}) or {}
    params = fn.get("parameters", {}) or {}
    props = params.get("properties") or {}
    req = params.get("required") or []
    args = args or {}
    missing = [k for k in req if k not in args or args.get(k) is None]
    if missing:
        hints = []
        for k in missing:
            p = props.get(k) or {}
            if isinstance(p, dict) and p.get("enum"):
                hints.append(f"{k}(可选: {'/'.join(str(x) for x in p['enum'])})")
            elif isinstance(p, dict) and p.get("type"):
                hints.append(f"{k}({p['type']})")
            else:
                hints.append(k)
        return {"ok": False, "error":
                f"工具 {name} 缺少必填参数：{'、'.join(hints)}。请先补齐参数再重新调用本工具，不要换用无关工具。"}
    for k, v in (args or {}).items():
        p = props.get(k) or {}
        if not isinstance(p, dict):
            continue
        if p.get("enum") is not None and v not in p["enum"]:
            return {"ok": False, "error": f"工具 {name} 参数 {k}={v!r} 不合法，可选值：{'/'.join(str(x) for x in p['enum'])}"}
        pt = p.get("type")
        if pt == "integer":
            # 宽松：int 或数字字符串都放行（模型常输出字符串数字）
            if isinstance(v, bool) or not (
                isinstance(v, int)
                or (isinstance(v, str) and str(v).lstrip("+-").isdigit())
            ):
                return {"ok": False, "error": f"工具 {name} 参数 {k} 应为整数，实际是 {type(v).__name__}={v!r}"}
        elif pt == "array":
            if not isinstance(v, list):
                return {"ok": False, "error": f"工具 {name} 参数 {k} 应为数组，实际是 {type(v).__name__}"}
        elif pt == "boolean":
            if not isinstance(v, bool):
                return {"ok": False, "error": f"工具 {name} 参数 {k} 应为布尔值，实际是 {type(v).__name__}={v!r}"}
    return None


def _finish_tool():
    return _tool_by_name("finish")

def _run_create_skill(args, cfg):
    name = re.sub(r'[^A-Za-z0-9_\-]', "_", str(args.get("name", "skill")))[:40] or "skill"
    md = args.get("content", "")
    desc = args.get("description", "")
    header = f"---\nname: {name}\ndescription: {desc}\n---\n\n" if desc else ""
    return save_workspace_file("技能", f"{name}.md", header + md)


def _run_save_file(args, cfg):
    """save_file：写文件到产物库，落盘成功后自动登记产物台账（P4-5）。"""
    out = save_workspace_file(args.get("subdir", "产出"),
                              args.get("filename", "untitled.txt"),
                              args.get("content", ""))
    if isinstance(out, dict) and out.get("ok"):
        append_ledger(out.get("sub", "产出"), out.get("filename", ""), source="save_file",
                      note=(args.get("note") or "")[:40])
    return out


def _run_save_workspace_file(args, cfg):
    """save_workspace_file（v1.0.230）：产物库写入，支持追加模式。

    为什么单独开一个工具：答题记录/进度流水是「逐条累积」的内容，
    用 save_file（覆盖写）要每次重写全量——20 题就是 20 次全量输出，
    费 token 且中途出错会丢历史。追加模式一条条写即可。
    """
    out = save_workspace_file(args.get("subdir") or "产出",
                              args.get("filename", "untitled.txt"),
                              args.get("content", ""),
                              append=str(args.get("mode", "append") or "append").lower() == "append")
    if isinstance(out, dict) and out.get("ok"):
        append_ledger(out.get("sub", "产出"), out.get("filename", ""),
                      source="save_workspace_file", note=(args.get("note") or "")[:40])
    return out


def _run_close_page(args, cfg):
    """close_wecom_page（v1.0.203）：关闭指定窗口/标签页（pick 缺省关当前跟随窗口）。
    批量审核流程：审完一项 → append_progress 保存 → close_wecom_page 关详情页 →
    回列表页 → 点下一项。防详情页标签堆积（列表链接 target=_blank 每点一次开一个）。"""
    out = wb.close_page(args.get("pick"), _cdp_port(cfg))
    return out


def _run_append_progress(args, cfg):
    """append_progress（v1.0.198）：把一条「单项处理结果」追加到进度文件
    「产物库/产出/<key>_进度.md」，返回已累计条目数。

    批量任务（逐项审核/逐张处理）的上下文隔离核心：每处理完一项就立即落盘，
    最后汇总时 read_workspace_file 读进度文件合并输出总结——中间结果不丢、
    单轮上下文不用背负全部历史（配合 loop 的自动上下文精简，每项处理完
    只留指令+进度+最近状态）。"""
    key = str(args.get("key", "")).strip() or "任务"
    content = str(args.get("content", "")).strip()
    if not content:
        return {"ok": False, "error": "缺少必填参数：content（本条处理结果的记录内容）"}
    # 文件名做安全化（防路径穿越）
    safe = "".join(c for c in key if c not in '\\/:*?"<>|').strip() or "任务"
    fname = f"{safe}_进度.md"
    # 每条记录用 --- 分隔（计数依赖），格式：---\n<content>\n\n
    out = save_workspace_file("产出", fname, f"---\n{content}\n\n", append=True)
    if not (isinstance(out, dict) and out.get("ok")):
        return out or {"ok": False, "error": "追加进度文件失败"}
    # 统计累计条目数（进度文件现有内容按 --- 分隔计数）
    import os
    
    try:
        full = os.path.join(paths.workspace_root(), "产出", fname)
        n = 0
        if os.path.exists(full):
            # v1.0.361 审查修复（O3）：with 关闭句柄（原裸 open 依赖 GC）
            with open(full, encoding="utf-8") as f:
                txt = f.read()
            # v1.0.650（F5）：计数口径与归档路径共用 _progress_items，别再各数各的
            n = _progress_items(txt)
        return {"ok": True, "filename": fname, "items": n,
                "result": f"已记录第 {n} 条，累计 {n} 项。继续处理下一项。"}
    except Exception:  # noqa: BLE001
        return {"ok": True, "filename": fname, "result": "已记录（计数失败不影响）"}


def _run_new_progress_round(args, cfg):
    """new_progress_round（v1.0.650，F5）：把本轮进度台账改名归档，同一 key 另起一轮。

    进度文件是纯追加、从不轮转的，而快捷入口的提示词写着"已记过的直接跳过"——
    上一轮的记录会喂错这一轮（新轮一开头就被提醒"这些都做过了"）。归档由用户显式发起：
    说"开新一轮/重新跑一轮"或直接点产物库里那一行的「↻ 开新一轮」。
    """
    key = str(args.get("key", "")).strip()
    if not key:
        return {"ok": False, "error": "缺少必填参数：key（要开新一轮的任务标识，如 月报审核 / 答题记录）"}
    safe = "".join(c for c in key if c not in '\\/:*?"<>|').strip() or "任务"
    out = archive_progress_round("产出", f"{safe}_进度.md")
    if not (isinstance(out, dict) and out.get("ok")):
        return out or {"ok": False, "error": "开新一轮失败"}
    return {"ok": True, "result": (
        f"已把上一轮 {out['items']} 条记录归档为「{out['sub']}/{out['archived']}」，"
        f"本轮从空的 {out['cleared']} 开始——之前记过的条目本轮不再算“已处理”，可以重新跑。"),
        "archived": out["archived"], "items": out["items"]}


def _run_save_experience(args, cfg):
    """save_experience：把一条「问题+解决方法」沉淀到经验库（experiences.json）。
    供 agent 在答题/审批/流程分析等场景发现新规则或踩坑经验时主动调用，让后续同类任务
    直接复用（Claw 的 memory 思想）。模块字段用于经验库 UI 分类筛选。
    失败不阻塞主流程，错误以结构化 __error__ 返回。"""
    module = str(args.get("module", "")).strip()
    problem = str(args.get("problem", "")).strip()
    solution = str(args.get("solution", "")).strip()
    if not problem or not solution:
        return {"ok": False, "error": "缺少必填参数：problem 和 solution 都要写（即使简单也要写），"
                              "否则经验库内容无价值。"}
    items = _load_experiences()
    rec = _exp_rec({"module": module, "problem": problem, "solution": solution})
    items.append(rec)
    ok, err = _save_experiences(items)
    if not ok:
        return {"ok": False, "error": f"经验保存失败：{err}"}
    return {"ok": True, "id": rec["id"], "module": rec["module"],
            "summary": f"已沉淀：[{rec['module']}] {problem[:40]}{'...' if len(problem) > 40 else ''}"}


# ============================================================
# v1.0.660（审查修复 H5）：SSRF 防护（fetch_webpage / download_file 共用）
# ============================================================
# 事故形态：这两个工具原本只校验 http/https 前缀，于是模型可以让软件去请求
#   · 169.254.169.254（云厂商元数据服务）→ 拿到实例 IAM 临时凭据
#   · 10.x / 172.16-31.x / 192.168.x（内网 OA、路由器、NAS 管理页）
#   · 127.0.0.1:8787（本软件自己的 HTTP API —— 带着本机令牌调自己 = 提权）
#   · 127.0.0.1:9222（CDP 调试口 —— 可读页面 DOM 与 cookie）
# 且 requests 默认跟随 30x：给一个公网 URL 让它 302 到内网即可绕过"只看初始 URL"。
#
# 判定口径：
#   ① 协议必须 http/https；
#   ② 主机名解析后**每一个**解析出的 IP 都必须是公网地址（只要有一个落在
#      保留/私网段就整条拒）——DNS 轮询/多 A 记录场景下这是必须的；
#   ③ 硬拒本机关键端口（8787 本软件 API / 9222 CDP）与 127.0.0.1/本机名的一切端口；
#   ④ 重定向逐跳重判，最多 5 跳。
# 无法解析主机名时**按不安全处理**（fail-closed）：宁可少抓一个站，不给探测面开洞。
DOWNLOAD_MAX_BYTES = 200 * 1024 * 1024     # 下载体积上限 200MB（防磁盘 DoS）
# v1.0.663：fetch_webpage 响应体上限 16MB。与 download_file 的 200MB 是**对称口径**：
# 一个落磁盘（文件），一个读进内存再解析正文，容忍度差一个数量级；但都必须有上限——
# 否则公网 URL 返回超大流就能把进程内存打爆（旧实现直接 r.text 全量读入，无一上限）。
FETCH_MAX_BYTES = 16 * 1024 * 1024         # 网页正文体积上限 16MB
HTTP_MAX_REDIRECTS = 5                       # 重定向最大跳数
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "ip6-localhost",
                "ip6-loopback", "[::1]"}
_BLOCKED_PORTS = {8787, 9222}                # 本软件 HTTP API / CDP 调试口


def _ip_is_public(ip):
    """单个 IP 是否是公网可路由地址（IPv4 / IPv6 通用，含 IPv4-mapped IPv6）。

    v1.0.660（H5）：用标准库 ipaddress 判定，不手写位运算——手写最容易漏
    掉 IPv4-mapped IPv6（::ffff:127.0.0.1）这类"看着像公网其实是回环"的形态。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # IPv4-mapped / 6to4 / Teredo 隧道地址：内层地址才是真实目标
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_loopback or addr.is_link_local or addr.is_private \
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return False
    # CGNAT（100.64.0.0/10）：运营商级"私网"，公网不可达
    if addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10"):
        return False
    # 共享地址段 169.254.0.0/16（含 169.254.169.254 云元数据）已被 is_link_local 覆盖，
    # 但 IPv6 侧的 fe80::/10（link-local）也已被 is_link_local 覆盖——这里再兜一层
    # 显式网段判定，防止标准库版本差异导致漏判。
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                "169.254.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
                "::1/128", "fe80::/10", "fc00::/7"):
        try:
            if addr in ipaddress.ip_network(net):
                return False
        except ValueError:  # noqa: BLE001
            continue
    return True


def _is_safe_public_url(url):
    """SSRF 判定：返回 (是否安全, 原因)。"""
    try:
        parts = urlsplit(str(url or "").strip())
    except Exception:  # noqa: BLE001
        return False, "URL 无法解析"
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"仅支持 http/https（当前 {scheme or '无'}）"
    host = (parts.hostname or "").strip().lower()
    if not host:
        return False, "URL 缺少主机名"
    try:
        port = parts.port
    except ValueError:
        return False, "URL 端口非法"
    # ③ 本机 / 本机名的一切端口 + 关键端口硬拒
    if host in _LOCAL_HOSTS or host in ("localhost.localdomain",):
        return False, "目标指向本机（回环地址）"
    try:
        import platform as _pf
        if host and (host == _pf.node().lower() or host == (socket.gethostname() or "").lower()):
            return False, "目标指向本机主机名"
    except Exception:  # noqa: BLE001
        pass
    if port in _BLOCKED_PORTS:
        return False, f"目标端口 {port} 是本机关键端口（应用 API / CDP 调试口）"
    # ② 解析主机名 → 每一个 IP 都必须是公网地址
    try:
        infos = socket.getaddrinfo(host, port or (443 if scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except Exception as e:  # noqa: BLE001
        return False, f"域名解析失败（按不安全处理）：{e}"
    if not infos:
        return False, "域名解析无结果"
    for info in infos:
        ip = info[4][0]
        if not _ip_is_public(ip):
            return False, f"目标解析到内网/保留地址 {ip}"
    return True, ""


def _http_get_public(url, timeout, stream=False, headers=None):
    """带 SSRF 校验的 GET：逐跳重定向自检，最多 HTTP_MAX_REDIRECTS 跳。

    v1.0.660（H5）：requests 默认 allow_redirects=True，攻击者可用
    "公网 URL → 302 → http://127.0.0.1:8787/" 绕过只看初始 URL 的检查。
    这里关闭自动跳转、自己手写循环，**每一跳的目标都重新做一次 SSRF 判定**。
    返回 (response, "") 或 (None, 错误说明)。"""
    cur = str(url or "").strip()
    for _hop in range(HTTP_MAX_REDIRECTS + 1):
        ok, why = _is_safe_public_url(cur)
        if not ok:
            return None, f"已拦截不安全的目标（SSRF 防护）：{why}"
        try:
            r = requests.get(cur, timeout=timeout, headers=headers or {},
                             allow_redirects=False, stream=stream)
        except Exception as e:  # noqa: BLE001
            return None, f"请求失败：{e}"
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location") or ""
            if not loc:
                return None, f"HTTP {r.status_code} 但未返回 Location"
            try:
                nxt = urljoin(cur, loc)
            except Exception:  # noqa: BLE001
                return None, "重定向地址无法解析"
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
            cur = nxt
            continue
        return r, ""
    return None, f"重定向超过 {HTTP_MAX_REDIRECTS} 跳（疑似跳转绕过）"


def _run_download_file(args, cfg):
    """升级版 download_file：校验 http/https、SSRF 防护、超时、非 200 报错、按 Content-Disposition 命名。

    v1.0.660（审查修复 H5）：加 **SSRF 防护** + **响应体大小上限**。
      · 旧实现只校验 http/https 前缀 → 模型可让软件去请求
        169.254.169.254（云元数据）、内网 OA、127.0.0.1:8787（本软件自己的 API）、
        127.0.0.1:9222（CDP 调试口）——内网探测 + 凭据外泄。
      · 重定向：requests 默认跟随 30x，攻击者给一个公网 URL → 302 到
        http://127.0.0.1:8787/ 就绕过了"只看初始 URL"的检查。故这里
        allow_redirects=False 手写跳转循环，**每一跳**都重新做一次 SSRF 判定，
        最多 5 跳。
      · 体积上限：防止模型下载超大文件把磁盘写满（DoS）。"""
    url = (args.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "缺少 url"}
    ok, why = _is_safe_public_url(url)
    if not ok:
        return {"ok": False, "error": f"已拦截不安全的下载地址（SSRF 防护）：{why}。{url}"}
    sub = args.get("subdir") or "下载"
    filename = args.get("filename")
    try:
        r, err = _http_get_public(url, timeout=DOWNLOAD_TIMEOUT, stream=True,
                                  headers={"User-Agent": USER_AGENT})
        if err:
            return {"ok": False, "error": f"下载失败：{err}"}
        if r.status_code != 200:
            return {"ok": False, "error": f"下载失败：HTTP {r.status_code}"}
        if not filename:
            # v1.0.616（审查 P2）：原来这里自己抄了一遍裸正则取名，与
            # bridge/download.py::_name_from_cd 两套口径——EKP/Landray 给的
            # `filename*=UTF-8''%E8%AE…` 走这条路径会落成不可读的 %xx 文件名
            # （下载完还得自己猜是哪个文件）。统一走那个解码器（RFC5987 + latin-1 修复
            # + basename/非法字符消毒都在里面）。
            from bridge.download import _name_from_cd
            filename = _name_from_cd(r.headers.get("Content-Disposition", ""))
        if not filename:
            fn = url.rstrip("/").split("?")[0].split("#")[0].split("/")[-1]
            filename = fn if fn and "." in fn else f"download_{_timestamp()}.bin"
        path = ws_rel_path(sub, filename)
        # v1.0.660（H5）：Content-Length 先看一眼（能直接拒绝就不落盘），
        # 落盘过程中再按实际字节数兜底（服务器可能不返回或谎报长度）。
        _declared = r.headers.get("Content-Length")
        try:
            if _declared and int(_declared) > DOWNLOAD_MAX_BYTES:
                return {"ok": False, "error": f"文件过大（{int(_declared)} 字节 > "
                                          f"{DOWNLOAD_MAX_BYTES // 1048576}MB 上限），已中止下载"}
        except (TypeError, ValueError):
            pass  # silent-ok: Content-Length 非法（非数字）时按"未声明长度"处理，落盘时按实际字节兜底
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    total += len(chunk)
                    if total > DOWNLOAD_MAX_BYTES:
                        f.close()
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        return {"ok": False, "error": f"文件超过 {DOWNLOAD_MAX_BYTES // 1048576}MB 上限，"
                                                  "已中止下载并清理临时文件"}
                    f.write(chunk)
        return {"ok": True, "path": path, "filename": os.path.basename(path), "bytes": os.path.getsize(path)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败：{e}"}


def _run_read_workspace_file(args, cfg):
    """读取产物库内某文件，自动按扩展名解析（文本/pdf/docx/xlsx/pptx），限制在产物库范围内。

    v1.0.294：新增可选 max_chars——读取前 N 字符的「头部概览」（digest）。默认不传=原行为
    （上限 READ_TRUNCATE_BYTES=50KB）。用途：超大素材（多份视频 OCR 叠加可达 80KB+）先读
    头部概览定位相关段、保持上下文精简避免大上下文空转，确需某段全文再不带 max_chars 单独读。
    """
    try:
        # v1.0.369：_safe_ws_read_path = 产物库内正常收敛 + 拖拽白名单只读放行
        # （用户拖入聊天框的本地文件登记白名单后，agent 可直接读原路径分析，无需先上传复制）
        p = _safe_ws_read_path(args.get("path", ""))
        if not p:
            return {"ok": False, "error": "路径不合法或超出产物库范围（拖入的本地路径需先拖进聊天框登记）"}
        if not os.path.isfile(p):
            return {"ok": False, "error": f"文件不存在：{args.get('path')}"}
        # v1.0.294：可选 max_chars（头部概览/有界读取），非法值忽略回退默认
        max_chars = None
        _mc = args.get("max_chars")
        if _mc is not None:
            try:
                max_chars = int(_mc)
            except Exception:  # noqa: BLE001
                max_chars = None
            if max_chars is not None and max_chars <= 0:
                max_chars = None
        size = os.path.getsize(p)
        kind, content, note = _parse_file_content(p, size)
        if kind is None:
            return {"ok": False, "error": f"解析失败：{note}", "path": p, "size": size,
                    "filename": os.path.basename(p)}
        if content:
            # 防止超大内容撑爆上下文：有 max_chars 时以更小者为准
            _orig_len = len(content)
            _limit = max_chars if (max_chars and max_chars < READ_TRUNCATE_BYTES) else READ_TRUNCATE_BYTES
            if _orig_len > _limit:
                content = content[:_limit]
                if max_chars:
                    note += (f"（max_chars 头部概览：已读前 {_limit} 字符，原文共 {_orig_len} 字符；"
                             f"确需某段全文可去掉 max_chars 重读或按题干关键词定位）")
                else:
                    note += f"（已截断至 {READ_TRUNCATE_BYTES} 字节，原文共 {_orig_len} 字符）"
            return {"ok": True, "path": p, "size": size, "kind": kind,
                    "filename": os.path.basename(p), "note": note,
                    "truncated": _orig_len > _limit,
                    "content": content}
        return {"ok": True, "path": p, "size": size, "kind": kind,
                "filename": os.path.basename(p),
                "content": f"（{os.path.basename(p)} 无可用文本内容）{note}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读取失败：{e}"}


def _run_analyze_monthly_attachment(args, cfg):
    """analyze_monthly_attachment（v1.0.204）：读设备清单 Excel，脚本统计「在用 台式/笔记本」数。
    模型只需 download 附件 → 一次调用本工具拿统计数字 → run_rule_check 判定，
    不再让 LLM 逐行读 Excel 做统计（省 1-2 次 LLM 轮次，且不会数错）。

    自动识别表头（设备状态/在用状态/状态、设备类型/类型/类别 等近义列），
    统计 状态含「在用」且 类型含「台式/笔记本」的条目数。"""
    path = str(args.get("path", "")).strip()
    try:
        p = _safe_ws_read_path(path)   # v1.0.369：支持拖拽白名单路径（只读）
        if not p:
            return {"ok": False, "error": "路径不合法或超出产物库范围（拖入的本地路径需先拖进聊天框登记）"}
        if not os.path.isfile(p):
            return {"ok": False, "error": f"文件不存在：{path}"}
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {"ok": True, "total_rows": 0, "pcs_in_use": None,
                    "error_hint": "Excel 为空表", "filename": os.path.basename(p)}
        # v1.0.221：表头可能不在首行（前几行是"表格填写人员/截止日期"信息）——
        # v1.0.654 A7：扫描块收敛到 core.files.scan_table_header（原与 batch_flow 逐字两份）
        from core.files import scan_table_header
        header, header_row_idx = scan_table_header(rows)

        # v1.0.510（P2-13）：find_col 收敛到 core.files.find_col_in_header（原与
        # batch_flow 各持一份相同实现）
        status_col = find_col_in_header(header, [["设备状态"], ["在用状态"], ["状态"]])
        type_col = find_col_in_header(header, [["设备类型"], ["类型"], ["类别"]])
        if status_col is None or type_col is None:
            return {"ok": True, "total_rows": len(rows) - header_row_idx - 1, "pcs_in_use": None,
                    "header": header[:20], "filename": os.path.basename(p),
                    "error_hint": "未识别到状态/类型列，表头：" + " | ".join(header[:20])}
        count = 0
        for r in rows[header_row_idx + 1:]:
            st = str(r[status_col] or "").strip()
            tp = str(r[type_col] or "").strip()
            if "在用" in st:
                if "台式" in tp or "笔记本" in tp or "电脑" in tp:
                    count += 1
        return {"ok": True, "total_rows": len(rows) - header_row_idx - 1, "pcs_in_use": count,
                "header": header[:20], "filename": os.path.basename(p),
                "result": f"在用设备数（台式+笔记本）={count}，共 {len(rows)-header_row_idx-1} 行数据"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"分析 Excel 失败：{e}"}


def _run_audit_flow(args, cfg):
    """run_audit_flow（v1.0.210）：脚本流水线执行器——整条确定性流程由 Python 脚本
    毫秒级跑完（找列表→解析链接→逐个点开→读字段→下载附件→统计→判定→记录→关页），
    零 LLM 消耗；只在「智能判断」场景（字段未命中/Excel 列名无法识别/附件异常）
    产出疑问项 issues，随结果返回由 LLM 处理。flow=monthly-report 为月报审核流水线。
    v1.0.523-6：默认后台执行——22 张 5-9 分钟不再独占对话（agent 立即拿到"已开始"
    并告知用户，完成后托盘提醒 + 报告落盘，进度条在工作台实时可见）；
    background=false 显式传参时保留同步模式（调试/单测用）。"""
    flow = str(args.get("flow", "")).strip() or "monthly-report"
    if flow not in ("monthly-report", "finance"):
        return {"ok": False, "error": f"未知流水线：{flow}（当前支持 monthly-report / finance）"}
    from services import batch_flow
    port = _cdp_port(cfg)
    background = args.get("background", True)
    _mode = str(args.get("mode", "all")).strip() or "all"
    if flow == "finance":
        # v1.0.650（F6）：财务单据流水线**只读**——判定脚本早已存在（rule_check.check_finance），
        # 这里补的是"逐张点开-读字段-关页"这三步也脱离模型轮次。它没有 auto_approve 参数，
        # 也就没有任何一条代码路径会去点「通过/同意/提交」。
        if args.get("auto_approve"):
            return {"ok": False, "error": "财务流水线只读，不执行任何审批点击：auto_approve 不适用。"
                                 "要真实提交审批请由人在目标应用页面自己点（或明确改用月报流水线的确认路径）。"}
        if not background:
            return batch_flow.audit_finance_bills(port=port, mode=_mode)
        try:
            from bridge.windows import current_pane_id as _pane_id
            _pane = _pane_id()
        except Exception:  # noqa: BLE001
            _pane = None
        r = batch_flow.start_background_audit(port=port, mode=_mode, pane=_pane, flow="finance")
        if r.get("ok"):
            return {"ok": True, "background": True,
                    "result": "财务单据核查已在后台开始执行（脚本逐张读字段+判定，零 LLM 消耗）。"
                              "期间可继续对话；完成后结果自动写回本对话并弹系统通知，报告落在产物库/产出。"
                              "不要轮询等待、也不要重复调用本工具。"}
        return r
    if not background:
        return batch_flow.audit_monthly_reports(
            port=port,
            mode=str(args.get("mode", "all")).strip() or "all",
            auto_approve=bool(args.get("auto_approve", False)))
    try:
        from bridge.windows import current_pane_id as _pane_id
        _pane = _pane_id()
    except Exception:  # noqa: BLE001
        _pane = None
    r = batch_flow.start_background_audit(port=port,
                                          mode=str(args.get("mode", "all")).strip() or "all",
                                          auto_approve=bool(args.get("auto_approve", False)),
                                          pane=_pane)
    if r.get("ok"):
        return {"ok": True, "background": True,
                "result": "月报批审已在后台开始执行（约 3-6 分钟）。期间可继续对话；"
                          "完成后结果会自动写回本对话（并弹系统通知），报告自动写入产物库；"
                          "工作台顶部的进度格可实时查看已处理数量。"
                          "不要轮询等待、也不要重复调用本工具。"}
    return r


def _run_list_workspace_files(args, cfg):
    """列出产物库某子目录（或全部子目录，含知识库嵌套子目录）的文件。

    subdir 支持扁平子目录（对话记录/下载/产出/技能/上传）与知识库嵌套子目录
    （知识库/课件、知识库/题库、知识库/制度，正/反斜杠均可）。不传则返回全量树。
    v1.0.369：path=绝对目录（须在拖拽白名单内，即用户拖入聊天框的文件夹）→
    直接列出该外部目录内容，支撑「拖个文件夹让 agent 分析」场景。
    """
    try:
        root = paths.workspace_root()
        # v1.0.369：拖拽白名单目录优先（用户拖入的外部文件夹）
        d_arg = str(args.get("path") or "").strip()
        if d_arg:
            if not dnd_allowed(d_arg):
                return {"ok": False, "error": "目录不在拖拽白名单（仅支持用户拖入聊天框的文件夹）；"
                                     "分析产物库请改用 subdir 参数"}
            d = os.path.realpath(d_arg)
            if not os.path.isdir(d):
                return {"ok": False, "error": f"目录不存在：{d_arg}"}
            files = []
            for f in sorted(os.listdir(d)):
                fp = os.path.join(d, f)
                # v1.0.373（P3-12）：listdir→getsize 之间文件可能被删/改名，逐项容错
                try:
                    files.append({"name": f, "size": os.path.getsize(fp) if os.path.isfile(fp) else 0,
                                  "is_dir": os.path.isdir(fp)})
                except OSError:
                    continue
            return {"ok": True, "dir": d, "files": files,
                    "note": "拖入文件夹清单；读其中文件用 read_workspace_file(path=完整路径)"}
        sub = str(args.get("subdir") or "").replace("\\", "/").strip()
        if sub:
            if sub not in _ALLOWED_SUBS:
                return {"ok": False, "error": f"未知子目录：{sub}；可选：{', '.join(_ALLOWED_SUBS)}"}
            d = os.path.join(root, sub)
            files = [{"name": f, "size": os.path.getsize(os.path.join(d, f)),
                      "is_dir": os.path.isdir(os.path.join(d, f))} for f in sorted(os.listdir(d))]
            return {"ok": True, "subdir": sub, "dir": d, "files": files}
        out = []
        for s in _ALLOWED_SUBS:
            d = os.path.join(root, s)
            try:
                files = sorted(os.listdir(d))
            except Exception as e:  # noqa: BLE001
                log_event(f"[workspace] 读取子目录 {s} 失败：{e}")
                files = []
            out.append({"sub": s, "dir": d,
                        "files": [{"name": f, "size": os.path.getsize(os.path.join(d, f))} for f in files]})
        return {"ok": True, "root": root, "tree": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"列举失败：{e}"}


# 工具名 -> 执行函数(args, cfg) 的注册表，替代过长的 if/elif 链。
# 新增工具只需在此登记一项，dispatch_tool / build_agent_tools 体系无需改动。
def _cdp_port(cfg):
    """从配置取 CDP 端口；未配置/非法时回退默认 9222，保证绑定与实际操作使用同一端口。
    v1.0.588：实现收敛到 core.config_store.cdp_port（全项目唯一口径），本函数保留为
    既有调用面（agent_tools/__init__ re-export、routes、services 共 10+ 处在用）。"""
    return _cfg_cdp_port(cfg)


def _bing_rss_search(query, max_results, timeout=10):
    """Bing RSS 搜索（主后端）：`bing.com/search?q=…&format=rss` 返回干净 XML，
    无需 ddgs、无需 bs4，stdlib（urllib + ElementTree）即可解析。
    本机实测（2026-09-04）：duckduckgo.com 被 GFW 墙，bing.com:443 直连可达且
    RSS 稳定返回 <item><title>/<link>/<description>。返回 [{title,url,snippet}]，
    连接失败/无结果抛异常或返回空列表，由调用方决定兜底。
    """
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&format=rss"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    data = urllib.request.urlopen(req, timeout=timeout).read()
    root = ET.fromstring(data)
    results = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if not link:
            continue
        results.append({"title": title, "url": link, "snippet": desc})
        if len(results) >= max_results:
            break
    return results


def _ddgs_search(query, max_results):
    """DuckDuckGo 搜索（海外兜底后端）：依赖 ddgs 库，国内通常被墙不可用，
    仅在 Bing 主后端失败时尝试。返回 [{title,url,snippet}]。"""
    from ddgs import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": (r.get("title") or "").strip(),
                "url": r.get("href") or r.get("url") or "",
                "snippet": (r.get("body") or r.get("snippet") or "").strip(),
            })
    return results


def _run_web_search(args, cfg):
    """联网搜索：按关键词返回 标题/链接/摘要 列表。

    双后端：①Bing RSS（主，国内可达、stdlib 解析）→ ②DuckDuckGo/ddgs（海外兜底）。
    主后端连接失败/超时自动切兜底；两者都无结果给 __info__，都失败给 __error__。
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "缺少 query 参数（搜索关键词）。"}
    try:
        max_results = max(1, min(int(args.get("max_results") or 5), 10))
    except (TypeError, ValueError):
        max_results = 5

    errors = []
    # ① 主后端：Bing RSS
    try:
        results = _bing_rss_search(query, max_results)
        if results:
            return {"query": query, "count": len(results), "results": results, "source": "bing"}
        errors.append("bing: 无结果")
    except Exception as e:  # noqa: BLE001
        errors.append(f"bing: {type(e).__name__} {str(e)[:120]}")
    # ② 兜底：DuckDuckGo（ddgs 缺失则跳过）
    try:
        results = _ddgs_search(query, max_results)
        if results:
            return {"query": query, "count": len(results), "results": results, "source": "duckduckgo"}
        errors.append("ddgs: 无结果")
    except ImportError:
        errors.append("ddgs: 库未安装（仅海外兜底，可忽略）")
    except Exception as e:  # noqa: BLE001
        errors.append(f"ddgs: {type(e).__name__} {str(e)[:120]}")

    if all("无结果" in x for x in errors):
        return {"__info__": f"未搜索到与「{query}」相关的结果，请尝试更换关键词或减少 max_results。"}
    return {"ok": False, "error": f"搜索失败（{'；'.join(errors)}）。请稍后重试或更换关键词。"}


def _run_fetch_webpage(args, cfg):
    """抓取指定 URL 网页正文：自动处理编码、剥离脚本样式、截断到 max_chars（默认 8000）。

    依赖 requests（backend 顶层已导入）+ beautifulsoup4；bs4 懒加载，缺库时返回友好提示。
    非 200 返回错误。

    v1.0.660（审查修复 H5）：加 **SSRF 防护**（与 download_file 共用
    _is_safe_public_url / _http_get_public）——旧实现只校验 http/https 前缀，
    模型可让软件去读云元数据（169.254.169.254）、内网 OA、本机 API/CDP 端口，
    且 30x 跳转默认跟随（公网 URL 302 到内网即可绕过）。

    v1.0.663：**响应体 16MB 硬上限**（FETCH_MAX_BYTES，与 download_file 的
    DOWNLOAD_MAX_BYTES=200MB 对称口径：落盘文件 vs 读内存解析正文）。改流式
    iter_content 边读边累计，越限立即 r.close() 并返回「页面过大已中止」；不再
    r.text 全量读入（公网 URL 返回超大流会直接打爆进程内存）。20s 超时与
    max_chars 截断语义不变。"""
    url = (args.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "缺少 url 参数（要抓取的网页地址）。"}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "仅支持 http/https 链接。"}
    ok, why = _is_safe_public_url(url)
    if not ok:
        return {"ok": False, "error": f"已拦截不安全的抓取地址（SSRF 防护）：{why}。{url}"}
    try:
        max_chars = max(500, min(int(args.get("max_chars") or 8000), 50000))
    except (TypeError, ValueError):
        max_chars = 8000
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False, "error": "缺少依赖 beautifulsoup4：请先执行 pip install beautifulsoup4 安装后重启应用。"}
    try:
        # v1.0.663：流式读取 + 16MB 硬上限。旧实现 r.text 全量读入，公网 URL 返回
        # 超大 body 即内存 DoS；现在边读边累计，越限立刻关闭响应、不把剩余流读进内存。
        r, err = _http_get_public(url, timeout=20, stream=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"})
        if err:
            return {"ok": False, "error": f"抓取失败：{err}"}
        if r.status_code != 200:
            # stream=True 下非 200 也要显式关连接，否则套接字留到 GC 才回收
            try:
                r.close()
            except Exception as _ce:  # noqa: BLE001
                log_event(f"[fetch] 关闭响应失败：{_ce}")
            return {"ok": False, "error": f"请求失败，HTTP {r.status_code}：{url}"}
        try:
            # ① Content-Length 能直接拒绝就不读流（服务器可能不返回或谎报，读流时再兜底）
            _declared = r.headers.get("Content-Length")
            try:
                if _declared and int(_declared) > FETCH_MAX_BYTES:
                    return {"ok": False,
                            "error": f"页面过大（声明 {int(_declared)} 字节 > "
                                     f"{FETCH_MAX_BYTES // 1048576}MB 上限），已中止抓取：{url}"}
            except (TypeError, ValueError):
                _declared = None   # 非数字按「未声明长度」处理，由实际字节数兜底
            # ② 流式累计，越限即中止（不读完剩余流）
            buf = bytearray()
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                if len(buf) + len(chunk) > FETCH_MAX_BYTES:
                    return {"ok": False,
                            "error": f"页面过大（超过 {FETCH_MAX_BYTES // 1048576}MB 上限），"
                                     f"已中止抓取：{url}"}
                buf.extend(chunk)
        finally:
            try:
                r.close()
            except Exception as _ce:  # noqa: BLE001
                log_event(f"[fetch] 关闭响应失败：{_ce}")
        raw = bytes(buf)
        # 编码：优先响应头；缺省/被 requests 兜成 latin-1 时嗅探 meta charset，最后 utf-8。
        # 不能再用 apparent_encoding——它会读完整 content，等于绕过上面的上限。
        _enc = (getattr(r, "encoding", "") or "").strip()
        if not _enc or _enc.lower() in ("iso-8859-1", "ascii"):
            _m = re.search(rb"charset=?[\"']?([A-Za-z0-9_\-]+)", raw[:4096], re.I)
            _enc = _m.group(1).decode("ascii", "ignore") if _m else "utf-8"
        try:
            html = raw.decode(_enc, "replace")
        except LookupError:
            html = raw.decode("utf-8", "replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else ""
        text = soup.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        total = len(text)
        if total > max_chars:
            text = text[:max_chars] + f"\n...[已截断：原文 {total} 字符，仅显示前 {max_chars} 字符]"
        if not text:
            return {"__info__": "页面无可见正文（可能是纯 JS 渲染页面，请换其他链接）。"}
        return {"url": url, "title": title, "char_count": len(text), "content": text}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"抓取超时（20 秒）：{url}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"抓取失败：{e}"}

# ask_judge 结果缓存（v1.0.236）：agent 重试/试错时同一道判断题（同模型+同问题+同上下文）
# 不再重复烧大模型——答题「试一试排除」每轮会反复确认锁定结论，实测大模型（27b）单次 10s+。
_ASK_JUDGE_CACHE = {}          # key=(model_id, question, context) -> advice
_ASK_JUDGE_CACHE_MAX = 128     # 简易 LRU 上限
_ASK_JUDGE_CACHE_LOCK = threading.Lock()   # v1.0.361 审查修复（O2）：多 pane 并发无锁共享
# v1.0.555（P1-7）：ask_judge 工具的墙钟上限。对齐 _judge_escalate 的 deadline=300
# 但更紧——judge 是 think 已关的短问答，正常 5~20s；挂起时 120s 即放弃回退小模型
# 自行判断，不再同步阻塞工具分发 15 分钟。
_ASK_JUDGE_DEADLINE = 120


def _ask_judge_cache_get(key):
    with _ASK_JUDGE_CACHE_LOCK:
        v = _ASK_JUDGE_CACHE.get(key)
        if v is not None:                       # 命中即移到末尾（LRU）
            _ASK_JUDGE_CACHE.pop(key, None)
            _ASK_JUDGE_CACHE[key] = v
    return v


def _ask_judge_cache_put(key, val):
    with _ASK_JUDGE_CACHE_LOCK:
        _ASK_JUDGE_CACHE[key] = val
        while len(_ASK_JUDGE_CACHE) > _ASK_JUDGE_CACHE_MAX:   # 淘汰最旧
            _ASK_JUDGE_CACHE.pop(next(iter(_ASK_JUDGE_CACHE)))


def _run_ask_user(args, cfg):
    """ask_user（v1.0.373 用户需求）：任务中需要用户抉择/提供信息时弹窗询问。
    返回 __ask_user__ 标记 → agent_loop 挂起本轮并推 SSE ask_user 事件 →
    前端弹选择卡（点选项或自由输入）→ /chat/continue 以【用户决定】resume_note 恢复。
    与安全审批（__approval_needed__）同一条挂起/恢复管道，但无需令牌、不重试工具。"""
    q = str(args.get("question") or "").strip()
    if not q:
        return {"ok": False, "error": "缺少 question——要问用户的问题不能为空"}
    opts = args.get("options") or []
    if not isinstance(opts, list):
        opts = []
    opts = [str(o).strip()[:60] for o in opts if str(o).strip()][:6]
    return {"__ask_user__": True,
            "question": q[:300],
            "options": opts,
            "allow_text": bool(args.get("allow_text", True))}


def _run_ask_judge(args, cfg):
    """双模型分工：小模型把判断/决策交给大模型（judge_model）。同问命中缓存直接返回。"""
    jm = judge_model(cfg)
    if not jm:
        return {"ok": False, "error": "未配置判断模型（在「模型管理」设置「判断模型」）。请基于上下文自行判断，或改回单模型。"}
    q = (args or {}).get("question") or ""
    ctx = (args or {}).get("context") or ""
    if not q:
        return {"ok": False, "error": "ask_judge 缺少 question 参数"}
    jname = jm.get("name") or jm.get("model") or ""
    ck = (jname, q.strip(), ctx.strip())
    cached = _ask_judge_cache_get(ck)
    if cached is not None:
        return {"ok": True, "judge": jname, "advice": cached, "cached": True}
    sys_p = ("你是判断大模型，负责做明确、可执行的最终判断。agent（小模型）会把需要判断的问题与上下文交给你。"
             "请直接给出结论（如『选 B』『通过』『不通过，原因：…』），不要复述问题、不要写冗长分析。"
             "信息不足时明确指出缺什么。用中文，简洁。")
    msgs = [{"role": "system", "content": sys_p},
            {"role": "user", "content": (q + ("\n\n上下文：\n" + ctx if ctx else ""))[:8000]}]
    # v1.0.555（P1-7 修复）：原调用无 deadline 无 poll_abort——judge 挂起时单次
    # ask_judge 最坏 3 次流尝试 ×(240~300s 熔断) ≈ 12~15 分钟同步阻塞在工具分发里，
    # 且用户点停止无效（_llm_complete 的 deadline/poll_abort 机制本身完备，只是这里没用）。
    # judge 是 think 已关的短问答（正常 5~20s），120s 足够宽松。
    from agent_tools.dispatch import pane_abort_hook as _pane_abort_hook
    ans = _llm_complete(jm, msgs, disable_thinking=True,
                        poll_abort=_pane_abort_hook(), deadline=_ASK_JUDGE_DEADLINE)
    if not ans:
        return {"ok": False, "error": "判断模型无响应（服务未启动或超时），请自行判断"}
    _ask_judge_cache_put(ck, ans)
    return {"ok": True, "judge": jname, "advice": ans}


_TOOL_HANDLERS = {
    "capture_wecom_window":     lambda a, c: wb.capture(_pick_flex(a, "pick"), _cdp_port(c)),
    "list_wecom_windows":       lambda a, c: wb.window_list_summary(_cdp_port(c)),
    "send_wecom_webhook":       lambda a, c: wb.send_webhook(
        (c.get("wecom") or {}).get("webhook", ""), a.get("content", ""), a.get("msgtype", DEFAULT_MSG_TYPE)),
    "list_interactive_elements": lambda a, c: wb.get_interactive_elements(_pick_flex(a, "pick"), _cdp_port(c)),
    "click_wecom_element":      lambda a, c: wb.click_element(_pick_flex(a, "pick"), a.get("target"), _cdp_port(c), a.get("element_meta")),
    "type_wecom_text":          lambda a, c: wb.type_text(_pick_flex(a, "pick"), a.get("target"), a.get("text", ""), _cdp_port(c)),
    "read_wecom_page":          lambda a, c: wb.read_page(_pick_flex(a, "pick"), _cdp_port(c)),
    "read_wecom_fields":        lambda a, c: wb.read_fields(_pick_flex(a, "pick"), a.get("fields", []), _cdp_port(c)),
    "run_rule_check":           lambda a, c: rule_check.run_rule(str(a.get("module", "")), a.get("data") or {}),
    "run_audit_flow":           _run_audit_flow,
    "select_wecom_option":      lambda a, c: wb.select_option(_pick_flex(a, "pick"), a.get("target"), a.get("value", ""), _cdp_port(c)),
    "scroll_wecom_window":      lambda a, c: wb.scroll_window(_pick_flex(a, "pick"), a.get("direction", "down"),
                                                              a.get("amount", DEFAULT_SCROLL_AMOUNT), _cdp_port(c)),
    # v1.0.731（审查 P2-7）：默认值改用同一常量，不再承诺做不到的 25s
    # （wait_page_load 内部按 WAIT_PAGE_LOAD_MAX_S=8 封顶，registry 描述也是 8）。
    "wait_wecom_page_load":     lambda a, c: wb.wait_page_load(_pick_flex(a, "pick"),
                                                              _pick_int(a, "timeout") or wb.WAIT_PAGE_LOAD_MAX_S,
                                                              _cdp_port(c)),
    "list_wecom_links":         lambda a, c: wb.list_links(_pick_flex(a, "pick"), _cdp_port(c)),
    "upload_wecom_file":        lambda a, c: wb.upload_file(_pick_flex(a, "pick"), a.get("target"), a.get("filepath", ""), _cdp_port(c)),
    "type_wecom_document":      lambda a, c: wb.type_document(_pick_flex(a, "pick"), a.get("text", ""), _cdp_port(c)),
    "save_file":                _run_save_file,
    "save_workspace_file":      _run_save_workspace_file,
    "append_progress":          _run_append_progress,
    "new_progress_round":       _run_new_progress_round,
    "close_wecom_page":         _run_close_page,
    "save_experience":          _run_save_experience,
    "download_file":            _run_download_file,
    "download_wecom_attachment": lambda a, c: wb.download_attachment(
        _pick_flex(a, "pick"), a.get("target"), ws_sub("下载"), _cdp_port(c),
        timeout=_pick_int(a, "timeout") or 90),
    "read_workspace_file":      _run_read_workspace_file,
    "analyze_monthly_attachment": _run_analyze_monthly_attachment,
    "list_workspace_files":     _run_list_workspace_files,
    "create_skill":             _run_create_skill,
    "web_search":               _run_web_search,
    "fetch_webpage":            _run_fetch_webpage,
    "finish":                   lambda a, c: {"__finish__": True, "final_answer": a.get("final_answer", "任务已完成。")},
    "ask_user":                 _run_ask_user,
    "ask_judge":                _run_ask_judge,
    # —— PC 操控（v1.0.297）：OS 级工具，实现见 agent_tools/os_ops.py ——
    "pc_screenshot":            lambda a, c: os_ops.pc_screenshot(a.get("window")),
    "pc_files":                 lambda a, c: os_ops.pc_files(a.get("op"), a.get("path"),
                                                            a.get("target"), a.get("content"), c),
    "pc_run":                   lambda a, c: os_ops.pc_run(a.get("command"),
                                                          a.get("shell", "cmd"), a.get("timeout"), c),
    # v1.0.755（评审 P0-2）：参数数组通道，shell=False 零展开，与 pc_run 共用
    # security_ops 的 check_command / check_paths / 审批 / 审计（见 os_ops.pc_run_argv）。
    "pc_run_argv":              lambda a, c: os_ops.pc_run_argv(a.get("argv"),
                                                               a.get("timeout"), c),
    # v1.0.660（M1）：pc_apps 需要 cfg 才能判权限档（杀关键进程在非「完全访问」档
    # 要转人工审批），故把配置透传下去。
    "pc_apps":                  lambda a, c: os_ops.pc_apps(a.get("op"), a.get("name"), cfg=c),
    "pc_clipboard":             lambda a, c: os_ops.pc_clipboard(a.get("op"), a.get("text")),
    "pc_system_info":           lambda a, c: os_ops.pc_system_info(),
    # —— PC 接管通道（v1.0.298）：deepseek-harness headless 委托，实现见 agent_tools/dsh_bridge.py ——
    "dsh_status":               lambda a, c: dsh_bridge.dsh_check(c),
    "dsh_prepare":              lambda a, c: dsh_bridge.dsh_prepare(c),
    "pc_takeover":              lambda a, c: dsh_bridge.dsh_run(c, a.get("task"), a.get("cwd"), a.get("timeout")),
    # —— 办公文件生成三件套（v1.0.313）：Excel/Word/PPT 可验收交付物，实现见 agent_tools/office_gen.py ——
    "create_excel":             lambda a, c: office_gen.create_excel(a, c),
    "create_docx":              lambda a, c: office_gen.create_docx(a, c),
    "create_pptx":              lambda a, c: office_gen.create_pptx(a, c),
    # —— 企微主窗口发消息（v1.0.468 ④）：OS 级搜索+发送，实现见 bridge/win_send.py ——
    "send_wecom_message":       lambda a, c: _send_wecom_message_safe(a),
    # v1.0.470（P1-10）：wecom_run_js 加危险 API 拦截——带登录态的 OA 页面里任意 JS
    # （fetch 外传数据/读 cookie/改表单）完全绕过审批管道，现按 pc_run 同款 fail-closed
    "wecom_run_js":             lambda a, c: _run_wecom_js_safe(a, c),
}

# v1.0.470（P0-2）加载期交叉校验：registry.TOOLS 的 schema 名与执行器 key 必须一一对应。
# 此前 send_wecom_message 在 TOOLS 定义两份、_TOOL_HANDLERS 字面量写两遍（后者静默覆盖
# 前者），schema 与执行器错位导致首轮调用必失败。dict 字面量重复 key 不报错，只能在这里拦。
_tool_schema_names = [t.get("function", {}).get("name") for t in TOOLS if t.get("function")]
_dup_names = sorted({n for n in _tool_schema_names if _tool_schema_names.count(n) > 1})
if _dup_names:
    raise RuntimeError(f"registry.TOOLS 存在重复工具名（后者覆盖前者）：{_dup_names}")
# 这两个工具在 agent/loop.py 有专门分支（挂起-恢复弹窗交互），不走 _TOOL_HANDLERS
_LOOP_SPECIAL_TOOLS = {"request_skill", "import_skill_source"}
_missing_handlers = sorted(set(_tool_schema_names) - set(_TOOL_HANDLERS.keys()) - _LOOP_SPECIAL_TOOLS)
if _missing_handlers:
    raise RuntimeError(f"registry.TOOLS 里有 schema 但 _TOOL_HANDLERS 缺执行器：{_missing_handlers}")


def _run_wecom_js_safe(a, c):
    """v1.0.470（P1-10）：wecom_run_js 危险 API 拦截包装。
    带登录态的 OA 页面里执行任意 JS 可 fetch 外传数据/读 cookie/篡改表单——原实现
    完全绕过审批管道。命中危险模式转 __approval_needed__ 人工审批（pc_run 同款
    fail-closed 管道）；安全表达式（视频拖进度/读播放状态/查元素存在）直接放行。
    v1.0.555（P0-5 修复）：原 12 条黑名单对图灵完备的 JS 本质失效——字符串拼接
    （"fe"+"tch("）、window["Fun"+"ction"]、document.location=、parent.postMessage、
    document.write、navigator.clipboard.readText、atob+拼接链均不在列，漏一个模式
    就是一条无审批的带登录态数据外传通道（页面内容可经提示注入诱导模型调用）。
    现补全三类缺口：
      ① 外传/持久化通道：postMessage/clipboard/atob/btoa/import()/Worker/SRPDOC；
      ② 导航与跨帧：location 赋值/assign/replace、history.pushState/replaceState、
         opener/parent/top./frames 跨帧访问；
      ③ 混淆启发式：字符串字面量拼接（"fe"+"tch("）、全局对象括号取值
         （window["Fun"+"ction"]）、字符串参数定时器（setTimeout("..."））。
    未采用"纯白名单只放行窄只读模式"的原因：跑课引擎文档化纪律依赖页面本地
    DOM/媒体操作（静音三件套 mega 表达式含 setInterval/defineProperty/play/seek，
    及 history.back() 退出考试），纯白名单会把整个自动化跑课流程全部推去人工
    审批，功能实质下线。黑名单+启发式在保住合法用例的同时关闭已证实的外传通道。

    v1.0.660（审查修复 H4）：**进审批闸** + 危险表与审批文案同源。
      ① 闸门（security_ops.gate_write_tool）已把 wecom_run_js 列入 _ALWAYS_ASK
         （凡 expression 非空即询问）——auto 档也不再无声执行。本执行器不再
         自己另起一套"第二张弹窗"，而是先查闸门放行凭据（js_approval_state）：
         闸门已放行 → 直接执行；未放行且本函数要拦 → 用**同一枚 hash** 发审批
         请求，用户批一次即可两道关同过（否则会出现两张弹窗 + 令牌无人消费的
         无限弹窗）。
      ② 危险模式表收敛到 security_ops.js_danger_scan（与审批文案 tool_action_label
         同源），两处各写一套正则必然漂移，提示与实际拦截原因会对不上。
      ③ 敏感 API（网络/存储/消息）命中时，审批理由里点名"表达式包含网络/存储/
         消息接口"，让用户知道自己批的到底是什么。"""
    expr = str(a.get("expression") or "")
    try:
        from security_ops import (js_danger_scan, js_sensitive_api_scan,
                                  js_approval_state, js_approval_hash, audit)
    except Exception as e:  # noqa: BLE001（安全模块不可用 → fail-closed：不执行 JS）
        try:
            from security_ops import audit
            audit("wecom_run_js", expr[:200], False, f"安全模块不可用：{e}")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"页面 JS 安全检查不可用（{e}），已拒绝执行。请稍后重试。"}
    hits = js_danger_scan(expr)
    sens = js_sensitive_api_scan(expr)
    # v1.0.755（评审 P1-5）：DOM 级硬闸同样覆盖页面 JS 路径——表达式里出现流程
    # 状态词（含同义改写，如「予以通过」）时，即使没命中危险/敏感 API 也要走审批。
    # 判定模块不可用时按未命中处理，gate_write_tool 的 _ALWAYS_ASK 仍在前面兜着。
    try:
        from core.approval_dom import dom_approval_hit as _dom_hit
        dom_hit = _dom_hit(expr)
    except Exception:  # noqa: BLE001  silent-ok: 判定模块不可用时退回原有两道检查
        dom_hit = None
    # 闸门已就本次调用放行（凭据在 security_ops）→ 直接执行，不再重复询问。
    approved, _why = js_approval_state("wecom_run_js", a)
    # v1.0.660（H4）：命中**敏感 API**（网络/存储/cookie/消息/动态执行）同样强制审批。
    # 此前只有"危险 API 全表"会拦，而 sendMessage（企微消息接口）、裸 cookie 读写
    # 不在危险表里——它们恰恰是最要命的两项（能把页面数据发出去/把登录态读出来）。
    if not approved and (hits or sens or dom_hit):
        # v1.0.660（H4）：后台会话（定时任务/跑课）的降级**不在这里另起一套**——
        # gate_write_tool 的 background 分支已在工具分发前就拒了（wecom_run_js 已进
        # _ALWAYS_ASK），执行器根本轮不到。此处只管"要不要问"，与闸门共用同一枚 hash。
        if hits:
            _why_audit = "命中危险 API：" + "、".join(hits[:3])
        elif dom_hit:
            _why_audit = "命中流程状态词：" + dom_hit
        else:
            _why_audit = "命中敏感 API"
        audit("wecom_run_js", expr[:200], False, _why_audit)
        if sens:
            _tip = "表达式包含网络/存储/消息接口（" + "、".join(sens[:3]) + "）"
        elif dom_hit:
            _tip = "表达式含流程状态词「%s」（可能为自动点击审批按钮）" % dom_hit
        else:
            _tip = "命中危险接口（" + "、".join(hits[:3]) + "）"
        return {"__approval_needed__": True, "kind": "js", "command": expr[:300],
                # 与闸门同一枚 hash：用户批一次，闸门与执行器两道关同时放行
                "hash": js_approval_hash("wecom_run_js", a),
                "reason": f"页面 JS {_tip}——需人工确认。若为误报，可改用等价的只读表达式。"}
    # v1.0.660（H4）：把"已放行"这件事传给执行点（bridge/act.py 的 run_js 有
    # 一道兜底闸门：敏感 API 表达式只允许经审批后的调用放行）。
    return wb.run_js(_pick_flex(a, "pick"), expr, _cdp_port(c), approved=True)


def _send_wecom_message_safe(a):
    """v1.0.468：包装 win_send——target/text 校验 + 延迟导入（OS 级模块仅在
    真正调用时加载，避免影响主链路导入）。"""
    if not a.get("target") or not a.get("text"):
        return {"ok": False, "error": "send_wecom_message 需要 target（联系人/群名称）与 text（消息内容）"}
    try:
        from bridge.win_send import send_wecom_message
        return send_wecom_message(a.get("target"), a.get("text"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"发送异常：{type(e).__name__}: {str(e)[:100]}"}


def run_tool(name, args, cfg):
    """按注册表路由到对应工具执行函数。未知工具返回结构化错误。

    统一兜底：handler 返回值必须是 dict，否则包成 {"ok": True, "result": <原值>}。
    （list_wecom_windows 等工具原生返回 list，若直接上抛，agent_loop 对 out 调用
    .get() 会抛 AttributeError 导致 SSE 断流、前端报 network error。）

    Hermes 风格：执行前先做参数 schema 轻量校验（必填/枚举/类型），失败直接返回
    结构化错误不执行——让模型修正参数后重试，避免无效工具调用空耗轮次。
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"unknown tool {name}"}
    verr = _validate_tool_args(name, args)
    if verr:
        log_event(f"[tool-validate] {name} 参数校验失败：{_mask_args(args)}")
        return verr
    out = handler(args or {}, cfg)
    if isinstance(out, dict):
        return out
    return {"ok": True, "result": out}
