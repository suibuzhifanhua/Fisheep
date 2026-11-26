"""agent_tools.dsh_bridge — deepseek-harness（dsh）电脑接管通道（v1.0.298）。

定位：内置 pc_* 直连工具搞不定的任务（多步骤/复杂/需要看结果再调整的电脑操作），
委托给本机 deepseek-harness headless agent 自主完成。dsh 自带终端/文件/工作区
工具与自己的 sandbox+审批安全策略，跑完打印最终答案并退出（不开端口、不留进程）。

三个入口（均为 agent 工具后端）：
  dsh_check(cfg)     就绪探测：node 版本(需>=22.19 或 24+) / dsh 是否安装 / 模型凭据，
                     不满足时给可执行的修复提示
  dsh_prepare(cfg)   装 @deepseek-ai/dsh：已装→跳过；离线包（dsh-offline.zip，免网络）
                     → npmmirror 国内镜像 → 官方源（v1.0.712 顺序）
  dsh_run(cfg,task)  一次性委托：dsh --profile headless "<task>"，超时强杀进程树，
                     全程转写存档 产物库/产出/dsh/

实现要点：
  - Windows npm 全局 bin 是 .cmd shim：直接 Popen 会 WinError 193，
    需经 cmd /c 启动且对含空格参数加引号（_build_cmd 处理）
  - 长任务用 Popen+进程组（CREATE_NEW_PROCESS_GROUP），超时 taskkill /F /T 杀整树
    （dsh 内部会再派生 shell 子进程，只 kill 主进程会留孤儿）
  - stdout=最终答案 / stderr=推理流；模型端点走 dsh 官方机制：
    环境变量 DEEPSEEK_BASE_URL + DEEPSEEK_API_KEY。v1.0.706 起凭据三级解析：
    显式 dsh 段 → DEEPSEEK_* 环境变量 → 软件默认模型回退（同时 --patch 对齐
    dsh 模型名，见 _resolve_creds/_write_model_patch 头注）
  - 非 win32 全部返回结构化错误（源码托管跨平台跑测试不崩）
"""

import contextlib
import json
import os
import re
import sys
import time
import shutil
import subprocess
import zipfile

from core.logging import log_event
from core import paths  # noqa: F401（路径函数属性查找，可被测试 patch）

# ---------- 常量 ----------
IS_WIN = (sys.platform == "win32")

DSH_PKG = "@deepseek-ai/dsh"
# v1.0.714：装 dsh 必须**锁版本**。registry 上 dsh 所有版本都是预发布（-rc/-alpha），
# npm 裸名解析出的 `*` 范围匹配不到任何预发布版本 → ETARGET「No matching version
# found for @deepseek-ai/dsh@*」（2026-11-18 复现：上游把 latest 换成 0.2.2-rc.1 后，
# 全新机器无锁版本的一键安装必挂，本机已装的树走 already_installed 分支不受影响）。
# 锁的版本 == 离线包版本 == 本机生产验证版本；升级 dsh 时同步改此 pin 并重建离线包。
DSH_VERSION_PIN = "0.2.1-rc.2"
DSH_SPEC = DSH_PKG + "@" + DSH_VERSION_PIN
NODE_MIN = (22, 19, 0)                    # dsh engines: ^22.19.0 || >=24.0.0
MIRROR = "https://registry.npmmirror.com"

RUN_TIMEOUT_DEFAULT = 900                 # pc_takeover 默认超时（秒）
RUN_TIMEOUT_MIN = 60
RUN_TIMEOUT_MAX = 1800
PREPARE_TIMEOUT = 900                     # npm 全局安装上限（首次 1-10 分钟）

ANSWER_LIMIT = 20000                      # 最终答案截断
REASONING_LIMIT = 8000                    # 推理流尾部截断
DASH_DIR = os.path.join("产出", "dsh")


def _not_win():
    return {"ok": False, "error": "dsh 接管通道仅支持 Windows（当前 " + sys.platform + "）"}


def _ts():
    return time.strftime("%Y%m%d_%H%M%S")


def _dec(b):
    """node 输出默认 utf-8；兜底 gbk（Windows 控制台编码）再兜底 replace。"""
    if not b:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return b.decode("utf-8", errors="replace")


def _run_captured(args, timeout, env=None):
    """同步跑一条命令，返回 {exit_code, stdout, stderr}；不抛异常。
    v1.0.299：CREATE_NO_WINDOW——npm i -g / node --version 不再闪可见控制台黑框。
    v1.0.612：可选 env —— 便携版 node 不在系统 PATH 里，npm.cmd 内部还要再起 node，
    必须把 node 目录顶到子进程的 PATH 前面，否则 npm 自己就 WinError 2。"""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, env=env,
                           creationflags=0x08000000 if IS_WIN else 0)
        return {"exit_code": r.returncode,
                "stdout": _dec(r.stdout), "stderr": _dec(r.stderr)}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"命令超时（{timeout}s）"}
    except Exception as e:  # noqa: BLE001
        return {"exit_code": -1, "stdout": "", "stderr": f"启动失败：{e}"}


# ============================================================
# 环境探测
# ============================================================
def _node_info():
    """探测 node 版本是否满足 dsh 要求（^22.19 或 >=24）。

    v1.0.612：不再只认 PATH 里的 node —— 软件自装的便携版（`组件/node/…/node.exe`）
    同样是合法运行时。原先 `subprocess.run(["node", ...])` 在 PATH 无 node 时抛
    `[WinError 2] 系统找不到指定的文件`，一键安装就在 dsh 这一步死掉并把锅甩回用户。"""
    try:
        from core import portable as _pb
        exe = _pb.find_node()
    except Exception:  # noqa: BLE001
        exe = shutil.which("node")
    if not exe:
        return {"ok": False, "version": None, "detail": "未检测到 node（系统 PATH 与软件便携版都没有）"}
    try:
        r = _run_captured([exe, "--version"], timeout=15)
        m = re.search(r"v(\d+)\.(\d+)\.(\d+)", r["stdout"])
        if m:
            v = tuple(int(x) for x in m.groups())
            vs = "v" + ".".join(map(str, v))
            ok = v >= NODE_MIN or v[0] >= 24
            return {"ok": ok, "version": vs, "path": exe,
                    "detail": "" if ok else f"{vs} 过低，需 Node 22.19+ 或 24+"}
        return {"ok": False, "version": None, "path": exe,
                "detail": "node --version 未返回有效版本：" + r["stderr"][:100]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "version": None, "path": exe, "detail": f"无法执行 node：{e}"}


def _node_dir_on_path():
    """node 所在目录（便携版或系统版），给子进程 PATH 前置用；没有 node 返回 None。"""
    try:
        from core import portable
        n = portable.find_node()
        return os.path.dirname(n) if n else None
    except Exception:  # noqa: BLE001
        return None


def _which_dsh():
    """解析 dsh 可执行路径。Windows npm 全局 bin 是 dsh.cmd shim（PATH 里同时有
    dsh.cmd/dsh/dsh.ps1 三份），shutil.which 按 PATHEXT 顺序解析；逐个尝试更稳。

    v1.0.612：还要找**软件自装便携 node 的目录**——npm -g 装进的就是那个前缀，
    而它从不写进系统 PATH，所以只查 PATH 会「装成功却找不到 dsh」的假失败。"""
    candidates = (["dsh.cmd", "dsh", "dsh.ps1"] if IS_WIN else ["dsh"])
    for c in candidates:
        try:
            p = shutil.which(c)
        except Exception:  # noqa: BLE001
            p = None
        if p:
            return p
    try:
        from core import portable as _pb
        node = _pb.find_node()
    except Exception:  # noqa: BLE001
        node = None
    if node:
        d = os.path.dirname(node)
        for c in candidates:
            p = os.path.join(d, c)
            if os.path.isfile(p):
                return p
    return None


# ============================================================
# 离线安装包（v1.0.712）：分支/管控网络的新机器上 npm 源必不通，
# dsh 必须能零网络装好——打包时把本机已装的 dsh 全局树压成 zip 随 dist 分发，
# 新机器装不上线就解这个包（shim 是 %dp0% 相对定位，解压即用、无需改路径）。
# 注意：树内有 176 个文件路径超 260 字符（最长 279，全在 dsh-experimental-
# inspector 子树）——普通 API 在没开长路径的 Windows 上 stat 都失败，读写一律
# 走 _ext() 的 \\?\ 扩展前缀（实测本机 279 字符路径 \\?\ 可读写）。
# ============================================================
BUNDLE_REL = os.path.join("组件", "node", "dsh-offline.zip")
BUNDLE_META = "_fisheep-bundle-meta.json"
_DSH_ENTRY_REL = os.path.join("node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")


def _entry_present(bin_path):
    """shim 对应的真实 JS 入口是否存在（dsh 全局树完整性的最小判据）。
    npm shim 是 %dp0% 相对定位（bin 就在 node 根），入口固定
    <node根>/node_modules/@deepseek-ai/dsh/lib/bin.js。"""
    if not bin_path:
        return False
    e = os.path.join(os.path.dirname(bin_path), "node_modules",
                     "@deepseek-ai", "dsh", "lib", "bin.js")
    return os.path.isfile(_ext(e))


def _ext(p):
    r"""Windows 长路径：>240 字符加 \\?\ 扩展前缀（绕过 260 限制，无需注册表开关）。
    非 Windows 或短路径原样返回。"""
    if IS_WIN and len(p) > 240 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.normpath(p).replace("/", "\\")
    return p


def _makedirs_longsafe(p):
    """makedirs 的长路径版：自底向上找缺失链、自顶向下逐层 mkdir（均走 _ext）。"""
    if os.path.isdir(_ext(p)):
        return
    missing = []
    cur = p
    while not os.path.isdir(_ext(cur)):
        missing.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for d in reversed(missing):
        with contextlib.suppress(FileExistsError):
            os.mkdir(_ext(d))


def _find_offline_bundle():
    """离线包路径（build.py 构建时放进 dist 随包分发）；没有返回 None。"""
    p = os.path.join(paths.workspace_root(), BUNDLE_REL)
    return p if os.path.isfile(_ext(p)) else None


def _dist_node_root():
    """构建产物（dist）里的便携 node 版本目录。

    dev 模式跑 build.py 时 workspace_root 指向**源码根**，find_node 默认探测会命中
    系统 node（dsh 实际装在 dist 的便携 node 里）——构建必须钉死 dist 侧。
    dev：workspace 父目录=源码根，dist 在 父/dist；frozen：父目录=dist 本身。"""
    try:
        base = os.path.dirname(paths.workspace_root())
        for cur in (os.path.join(base, "dist", "产物库", "组件", "node", "current"),
                   os.path.join(base, "产物库", "组件", "node", "current")):
            if not os.path.isdir(cur):
                continue
            for name in sorted(os.listdir(cur)):
                root = os.path.join(cur, name)
                for exe in ("node.exe", "node.EXE"):
                    if os.path.isfile(os.path.join(root, exe)):
                        return root
    except Exception:  # noqa: BLE001
        return None
    return None


def _make_offline_bundle(root=None, out=None):
    """把便携 node 里已装的 dsh 全局树打成离线包，返回 (路径, meta)；条件不满足 None。
    root 缺省走便携 node 探测（运行期口径）；out 缺省走产物库（运行期口径）。
    build.py 显式传 dist 侧的 root/out。"""
    if not root:
        try:
            from core import portable as _pb
            node = _pb.find_node()
        except Exception:  # noqa: BLE001
            return None
        if not node:
            return None
        root = os.path.dirname(node)
    if not os.path.isfile(_ext(os.path.join(root, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js"))):
        return None
    # dsh 版本按**本 root** 的 package.json 读（不用运行期探测的 node 根，口径对不上）
    dsh_ver = ""
    pj = os.path.join(root, "node_modules", "@deepseek-ai", "dsh", "package.json")
    with contextlib.suppress(Exception):
        with open(_ext(pj), "r", encoding="utf-8") as f:
            dsh_ver = str(json.load(f).get("version") or "")
    try:
        node_ver = str(_node_info().get("version") or "")
    except Exception:  # noqa: BLE001
        node_ver = ""
    if not out:
        out = os.path.join(paths.workspace_root(), BUNDLE_REL)
    _makedirs_longsafe(os.path.dirname(out))
    tmp = out + ".tmp"
    n = skipped = 0
    t0 = time.time()
    try:
        with zipfile.ZipFile(_ext(tmp), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, root)
                    try:
                        z.write(_ext(full), rel)
                        n += 1
                    except Exception:  # noqa: BLE001（个别文件读不到不废整包）
                        skipped += 1
            meta = {"dsh_version": dsh_ver, "node_version": node_ver,
                     "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "file_count": n, "skipped": skipped,
                     "elapsed_s": round(time.time() - t0, 1)}
            z.writestr(BUNDLE_META, json.dumps(meta, ensure_ascii=False))
        os.replace(_ext(tmp), _ext(out))
    except Exception as e:  # noqa: BLE001
        with contextlib.suppress(OSError):
            if os.path.isfile(_ext(tmp)):
                os.remove(_ext(tmp))
        log_event(f"[dsh] 离线包打包失败：{e}")
        return None
    meta = {"dsh_version": dsh_ver, "node_version": node_ver,
             "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "file_count": n, "skipped": skipped,
             "elapsed_s": round(time.time() - t0, 1),
             "size_mb": round(os.path.getsize(_ext(out)) / 1048576, 1)}
    log_event("[dsh] 离线包已生成 " + out + "（" + str(meta["file_count"])
              + " 文件 / " + str(meta["size_mb"]) + "MB / " + str(meta["elapsed_s"]) + "s）")
    return out, meta


def _read_bundle_meta(bundle):
    """读离线包 meta；失败 None。"""
    try:
        with zipfile.ZipFile(bundle) as z:
            with z.open(BUNDLE_META) as f:
                return json.loads(f.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def ensure_offline_bundle():
    """build.py 用：确保 **dist** 带与当前 dsh 版本一致的离线包。
    dev 模式下运行期口径（workspace_root/find_node）指向源码根与系统 node，
    所以全程显式用 dist 侧路径（_dist_node_root）。版本一致跳过（zip 数百 MB，
    不每次重建）。返回说明串（空串＝已就绪未动）。"""
    root = _dist_node_root()
    if not root:
        return "跳过（dist/组件/node/current 下没有便携 node——本机跑过软件才装得上 dsh）"
    pj = os.path.join(root, "node_modules", "@deepseek-ai", "dsh", "package.json")
    live = ""
    with contextlib.suppress(Exception):
        with open(_ext(pj), "r", encoding="utf-8") as f:
            live = str(json.load(f).get("version") or "")
    if not live:
        return "跳过（dist 便携 node 未装 dsh——先在本机装好 dsh 再发版）"
    bundle = os.path.join(os.path.dirname(os.path.dirname(root)), "dsh-offline.zip")
    if os.path.isfile(_ext(bundle)):
        meta = _read_bundle_meta(bundle)
        if meta and str(meta.get("dsh_version") or "") == live:
            return ""
    made = _make_offline_bundle(root, out=bundle)
    if not made:
        return "跳过（打包条件不满足）"
    return "已重建（dsh %s，%sMB）" % (made[1].get("dsh_version"), made[1].get("size_mb"))


def _install_from_bundle(bundle, dest_root=None):
    r"""把离线包解进便携 node 根（默认当前 node 根）。长路径走 \\?\。
    返回 {ok, extracted, skipped, elapsed_s, dest, error?}。"""
    if dest_root is None:
        try:
            from core import portable as _pb
            node = _pb.find_node()
        except Exception:  # noqa: BLE001
            node = None
        if not node:
            return {"ok": False, "error": "无便携 node 根，无法解离线包"}
        dest_root = os.path.dirname(node)
    dest_norm = os.path.normpath(dest_root)
    t0 = time.time()
    extracted = skipped = 0
    with zipfile.ZipFile(bundle) as z:
        for info in z.infolist():
            name = info.filename
            if name == BUNDLE_META:
                continue
            target = os.path.normpath(os.path.join(dest_norm, name))
            if target != dest_norm and not target.startswith(dest_norm + os.sep):
                skipped += 1            # 路径穿越防御
                continue
            try:
                if info.is_dir():
                    _makedirs_longsafe(target)
                    continue
                _makedirs_longsafe(os.path.dirname(target))
                with z.open(info) as src, open(_ext(target), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted += 1
            except Exception:  # noqa: BLE001
                skipped += 1
    entry = os.path.join(dest_norm, _DSH_ENTRY_REL)
    ok = os.path.isfile(_ext(entry))
    r = {"ok": ok, "extracted": extracted, "skipped": skipped,
         "elapsed_s": round(time.time() - t0, 1), "dest": dest_root}
    if not ok:
        r["error"] = "解压完成但 dsh 入口缺失（node_modules/@deepseek-ai/dsh/lib/bin.js）"
    return r


def _dsh_cfg(cfg):
    d = (cfg or {}).get("dsh")
    return d if isinstance(d, dict) else {}


# v1.0.706（dsh 复用默认模型）：dsh 端点/凭据三级解析 + 模型名对齐。
#
# 背景：dsh 内部的默认模型写死 provider=deepseek-official / model=deepseek-flash
# （dsh-base/cordis.patch.yml 的 agent-default-model 行）。端点走 DEEPSEEK_BASE_URL
# 环境变量没问题，但**模型名仍请求 deepseek-flash**——本地 OpenAI 兼容端点
# （LM Studio / Ollama）上没有这个模型，直接复用默认模型会 404。
# 解决：回落到软件默认模型时，额外生成一个 --patch 覆盖层把 agent-default-model
# 的 model 改成默认模型的 id（dsh 的 patch 栈中 --patch 是最高层，按 id 寻址
# 整行替换 config，官方 README 明确的覆盖机制）。
MODEL_PATCH_REL = os.path.join("组件", "dsh", "fisheep-model-patch.yml")


def _default_model_creds(cfg):
    """回退源：软件「模型」设置里的默认模型 → (base_url, api_key, model_id)。
    任一字段缺失给 None；整体不可用（没有可用默认模型）三个全 None。

    base_url 归一化：软件模型配置兼容两种填法（llm_client._chat_url 的口径）——
    根地址 `.../v1`（客户端自己补 /chat/completions）或完整端点
    `.../v1/chat/completions`。dsh 的 pi-ai 层同前者语义（自己补 /chat/completions），
    所以完整端点风格要剥掉后缀再注入，否则拼出双路径 404。"""
    try:
        from llm_client import resolve_model
        m = resolve_model(cfg)
    except Exception:  # noqa: BLE001（模型解析不可用不阻断 dsh 通道）
        return None, None, None
    if not isinstance(m, dict):
        return None, None, None
    base = str(m.get("base_url") or "").strip()
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    key = str(m.get("api_key") or "").strip()
    if key.startswith("enc:"):   # 运行期正常已解密；仍加密则不可直接使用
        key = ""
    # 发线模型名口径必须和软件自己一致（llm_client 的 payload 用 model.get("model")）：
    # model 字段＝发给端点的真实模型名；name 是显示名（常与线名相同）；id 是内部
    # 标识（m_xxxx，端点不认，末位兜底）。实测：只取 id 时中继端点 404 "model does not exist"。
    mid = str(m.get("model") or m.get("name") or m.get("id") or "").strip()
    if not base and not key:
        return None, None, None
    return base or None, key or None, mid or None


def _resolve_creds(cfg):
    """v1.0.706：dsh 端点/凭据统一解析（check 与 run 同一口径）。
    优先级：显式 dsh 段 → DEEPSEEK_* 环境变量 → 软件默认模型（回退）。
    返回 (base_url, api_key, model_id, source)：
      source ∈ "dsh" / "env" / "default_model" / None（全部缺）。
    model_id 仅 default_model 时非空（供 --patch 对齐 dsh 模型名）。
    按「段」解析而非按「字段」：dsh 段填了任一字段即整体采信它——
    用户显式配置后不应再被默认模型截胡。"""
    d = _dsh_cfg(cfg)
    base = str(d.get("base_url") or "").strip()
    key = str(d.get("api_key") or "").strip()
    if base or key:
        return base or None, key or None, None, "dsh"
    ebase = str(os.environ.get("DEEPSEEK_BASE_URL") or "").strip()
    ekey = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if ebase or ekey:
        return ebase or None, ekey or None, None, "env"
    base, key, mid = _default_model_creds(cfg)
    if base or key:
        return base, key, mid, "default_model"
    return None, None, None, None


def _write_model_patch(model_id):
    """写 dsh 的 --patch 覆盖层（模型名对齐文件），返回绝对路径；失败返回 None。
    格式对齐 dsh-base 的 cordis.patch.yml：`- insert:` 行列表，按 id 寻址、
    逐行 last-write-wins、config 整体替换。内容相同不重写（幂等、并发安全）。"""
    mid = str(model_id).replace("'", "''")      # YAML 单引号内转义
    content = ("- insert:\n"
               "    - id: agent-default-model\n"
               "      name: '@deepseek-ai/dsh-agent-default-model'\n"
               "      config:\n"
               "        provider: deepseek-official\n"
               "        model: '" + mid + "'\n")
    try:
        path = os.path.join(paths.workspace_root(), MODEL_PATCH_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == content:
                    return path
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except Exception as e:  # noqa: BLE001（patch 写失败降级为不带 --patch 运行）
        log_event(f"[dsh] 模型 patch 覆盖层写入失败：{e}")
        return None


def dsh_check(cfg=None):
    """就绪探测：node / dsh 安装 / 模型凭据，返回结构化状态 + 修复提示。
    v1.0.706：凭据走 _resolve_creds 三级解析——「模型」设置里配了默认模型即可
    用，不必单填 dsh 端点/凭据；结果带 model_source / model_id 供前端展示。"""
    if not IS_WIN:
        return _not_win()
    node = _node_info()
    bin_path = _which_dsh()
    # v1.0.712：shim 在但入口 JS 缺失＝上次装到一半（假就绪），按未安装算
    if bin_path and not _entry_present(bin_path):
        bin_path = None
    base_url, api_key, model_id, source = _resolve_creds(cfg)
    bundle = _find_offline_bundle()

    hints = []
    if not node["ok"]:
        hints.append(f"Node 环境不满足：{node['detail'] or '未检测到 node'}。"
                     "需安装 Node.js 22.19+（或 24+）并在 PATH 中可用。")
    if not bin_path:
        hints.append("dsh 未安装：点「一键安装」即可（带离线包的构建秒装免网络；"
                     "无离线包时走 npm i -g " + DSH_SPEC + "，首次 1-10 分钟）。")
    if source is None:
        hints.append("未检测到模型凭据：在「设置 → 模型」配置并启用一个默认模型"
                     "（dsh 自动复用它的端点与模型名），或在「设置 → 个人信息与凭据」"
                     "填 dsh 端点与访问凭据，或设置系统环境变量 DEEPSEEK_API_KEY"
                     "（DeepSeek 官方）。缺凭据时 dsh 会请求官方默认模型，需有效官方 key。")
    ready = node["ok"] and bool(bin_path) and bool(base_url or api_key)
    return {
        "ok": ready,
        "bundle_available": bool(bundle),
        "ready": ready,
        "node": node,
        "dsh_bin": bin_path,
        "base_url": base_url or None,
        "has_api_key": bool(api_key),
        "model_source": source,
        "model_id": model_id,
        "package": DSH_PKG,
        "hints": hints,
    }


# ============================================================
# 安装
# ============================================================
def dsh_prepare(cfg=None, timeout=None):
    """装 @deepseek-ai/dsh。顺序（v1.0.712）：已装→跳过；**离线包**（dsh-offline.zip，
    随 dist 分发，零网络、秒级）→ npmmirror 国内镜像 → 官方源。

    v1.0.608 顺序调整：原来先撞 registry.npmjs.org、失败才回退 npmmirror——本机实测
    官方源常常是"连得上但极慢"，要等它把 timeout 预算耗掉才轮到国内镜像，用户看到的就是
    一键安装卡住。镜像放前面后，官方源只在镜像真失败时才试一次。

    v1.0.712：全新机器（分支/管控网络）两个在线源必不通，旧顺序让用户白等 2×3 分钟才
    失败——离线包提到最前；两个源都失败时把 npm 真实输出尾部并进 error（前端只回显
    error，用户以前只看到一句通用话、没法定位是网络还是杀软）。

    v1.0.714：npm 安装锁版本 DSH_VERSION_PIN。registry 上 dsh 全部版本都是预发布，
    裸名 `*` 范围解析不到任何版本（ETARGET）——上游 11-04 把 latest 换成 0.2.2-rc.1 后
    全新机器必挂（本机已装的树不重跑 npm 所以一直正常）。锁版本 = 离线包 = 生产验证版。"""
    if not IS_WIN:
        return _not_win()
    node = _node_info()
    if not node["ok"]:
        return {"ok": False, "error": f"Node 环境不满足，无法安装：{node['detail']}",
                "node": node}
    existing = _which_dsh()
    if existing and _entry_present(existing):
        return {"ok": True, "already_installed": True, "dsh_bin": existing,
                "version_note": "dsh 已在 PATH 中，无需安装"}
    if existing:
        # v1.0.712：上次 npm 装到一半失败会留下 dsh.cmd 但入口 JS 缺失——
        # 按「已安装」放行就成了假就绪（体检 ok、一跑就崩）。按未安装重装覆盖。
        log_event("[dsh] 检测到 dsh 入口残缺（上次安装可能装到一半），按未安装处理重装")

    # v1.0.712：离线包优先——确定性最高、零网络，管控网机器上唯一可行路径
    bundle = _find_offline_bundle()
    if bundle:
        log_event("[dsh] 尝试离线包安装（免网络）：" + bundle)
        r = _install_from_bundle(bundle)
        if r.get("ok"):
            bin_path = _which_dsh()
            return {"ok": True, "installed": True, "source": "offline_bundle",
                    "dsh_bin": bin_path, "extracted": r["extracted"],
                    "elapsed_s": r["elapsed_s"],
                    "note": "离线包安装完成（免网络），pc_takeover 可用"}
        log_event("[dsh] 离线包安装失败（" + str(r.get("error") or r)
                  + "），回落在线安装")
    timeout = int(timeout or PREPARE_TIMEOUT)
    last = None
    # v1.0.612：npm 用**绝对路径**跑（便携版不在系统 PATH 上），并给子进程补 PATH，
    # 否则 npm.cmd 自己找不到 node —— 那正是截图里那句「WinError 2 系统找不到指定的文件」。
    npm = None
    try:
        from core import portable as _pb
        npm = _pb.find_npm()
    except Exception:  # noqa: BLE001
        npm = None
    if not npm:
        npm = "npm"
    npm_env = None
    try:
        from core import portable as _pb
        npm_env = _pb.node_env()
    except Exception:  # noqa: BLE001
        pass
    # 第 1 次走 npmmirror 国内镜像，第 2 次才回落官方源（v1.0.608）
    for attempt, extra in enumerate((["--registry", MIRROR], []), start=1):
        args = [npm, "install", "-g", DSH_SPEC] + extra
        log_event(f"[dsh] 安装尝试 #{attempt}（{'npmmirror 国内镜像' if extra else 'npm 官方源'}）: {' '.join(args)}")
        r = _run_captured(args, timeout=timeout, env=npm_env)
        last = r
        if r["exit_code"] == 0:
            bin_path = _which_dsh()
            return {"ok": bool(bin_path), "installed": True, "attempt": attempt,
                    "used_mirror": attempt == 1, "dsh_bin": bin_path,
                    "stderr_tail": r["stderr"][-500:],
                    "note": "安装完成，pc_takeover 可用" if bin_path
                    else "npm 返回成功但未在 PATH 找到 dsh，可能 PATH 未刷新，重启软件再试"}
        log_event(f"[dsh] 安装尝试 #{attempt} 失败 exit={r['exit_code']}：{r['stderr'][-300:]}")
    # v1.0.712：npm 真实输出尾部并进 error——前端只回显 error，用户以前只看到一句
    # 通用话，分不清是网络封了、杀软拦了还是磁盘满。stderr_tail 原样保留供日志。
    tail = str((last or {}).get("stderr") or "").strip()[-300:]
    err = "npm 全局安装失败（npmmirror 国内镜像 + 官方源都试过）"
    if tail:
        err += "｜npm 输出尾部：" + tail.replace("\r\n", " ⏎ ")
    return {"ok": False,
            "error": err,
            "stderr_tail": str((last or {}).get("stderr") or "")[-1500:],
            "hint": ("若这台机器在管控网络（分支机/无外网），在线安装永远成不了——"
                     "① 从一台 PC 接管正常的工作机把「产物库/组件/node」整个文件夹"
                     "（约 800MB）拷到本机「产物库/组件/」下即可离线用；"
                     "② 或用带 dsh-offline.zip 离线包的构建（「运行组件」页一键安装，免网络）。"
                     "网络正常仍失败时：查火绒/360/Defender 是否拦了 node.exe 联网、"
                     "公司代理是否放行 npmmirror.com，或手动执行：npm i -g "
                     + DSH_SPEC + " --registry=" + MIRROR)}


# ============================================================
# 一次性委托（headless）
# ============================================================
def _resolve_workdir(cwd_param, cfg_dsh):
    """归一化工作目录：参数 > config dsh.cwd > 产物库根。返回 (path, err)。"""
    want = str(cwd_param or cfg_dsh.get("cwd") or "").strip()
    if not want:
        return paths.workspace_root(), None
    p = want.replace("/", "\\").strip('"').strip("'")
    if p.startswith("~"):
        p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(os.path.expanduser("~"), p)
    p = os.path.normpath(p)
    if not os.path.isdir(p):
        return None, f"工作目录不存在：{p}"
    return p, None


def _build_cmd(bin_path, rest):
    """拼最终 argv，返回 (cmd, via)。

    npm 全局 bin 是 .cmd shim。cmd 的引号语义（/c 首尾引号剥离、4 引号规则）会把
    「含空格路径 + 含空格任务」拆碎——实证两次翻车（""双重引号"" 与路径空格截断）。
    所以首选：解析 shim 拿到真正的 JS 入口，直接 [node, entry.js, ...args]，
    不经过 cmd，引号由 list2cmdline 一次处理，任务文本原样进 node argv。
    解析失败（shim 格式变化/无 node）才回退 cmd /c（仅无空格场景可用）。
    """
    if IS_WIN and bin_path.lower().endswith((".cmd", ".bat")):
        entry = _resolve_shim_entry(bin_path)
        if entry:
            node = shutil.which("node") or "node"
            return [node, entry] + rest, "node-direct"
    return [bin_path] + rest, "direct"


def _resolve_shim_entry(bin_path):
    """从 npm .cmd shim 解析真实 JS 入口绝对路径。

    npm shim 内嵌一行 `FOR %%F IN ("<绝对路径>.js") DO ...`（npm 安装时把绝对路径
    写死进 shim）。取最后一个存在的绝对 .js 路径；找不到返回 None。"""
    if not IS_WIN:
        return None
    try:
        with open(bin_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:  # noqa: BLE001
        return None
    # 引号包裹的 .js 路径，排除含 % 的批处理变量行
    for m in reversed(re.findall(r'"([A-Za-z]:[^"\n%]+?\.js)"', text)):
        if os.path.exists(m):
            return m
    return None


def _kill_tree(p):
    """杀 dsh 进程整棵树（它会派生 shell 子进程，只杀主进程留孤儿）。"""
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=15,
                           creationflags=0x08000000)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        p.kill()
    except Exception:  # noqa: BLE001
        pass


def _save_transcript(task, answer, reasoning, exit_code, cwd, elapsed, timed_out=False):
    """全程转写落盘 产物库/产出/dsh/，用户可复查 dsh 到底干了什么。
    v1.0.361（O6）：支持 timed_out 标记（超时被杀路径也落转写）。"""
    try:
        # 不走 ws_sub（白名单只认 5 个扁平子目录，"产出/dsh" 会被回退到 产出），
        # 同 pc_screenshot 模式：直接拼 workspace_root + makedirs
        d = os.path.join(paths.workspace_root(), DASH_DIR)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{_ts()}_dsh.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# dsh 接管记录\n\n"
                    f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"- 工作目录：{cwd}\n"
                    f"- 耗时：{elapsed}s\n"
                    f"- 退出码：{exit_code if exit_code is not None else '（超时被杀）'}\n"
                    + ("- 状态：⚠ 超时被强杀（可能已执行部分操作）\n" if timed_out else "")
                    + "\n"
                    f"## 任务\n{task}\n\n"
                    f"## 最终答案\n{answer or '（空）'}\n\n"
                    f"## 过程推理（stderr 全文）\n{reasoning or '（空）'}\n")
        return {"path": path}
    except Exception as e:  # noqa: BLE001
        log_event(f"[dsh] 转写存档失败：{e}")
        return {}


def dsh_run(cfg, task, cwd=None, timeout=None):
    """委托 dsh headless agent 跑一个任务，返回最终答案 + 过程记录路径。"""
    if not IS_WIN:
        return _not_win()
    task = str(task or "").strip()
    if not task:
        return {"ok": False, "error": "pc_takeover 需要非空 task：把要完成的具体任务描述写清楚"
                             "（含路径、期望结果、约束）"}
    d = _dsh_cfg(cfg)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = RUN_TIMEOUT_DEFAULT
    timeout = max(RUN_TIMEOUT_MIN, min(timeout, RUN_TIMEOUT_MAX))

    bin_path = _which_dsh()
    if not bin_path:
        with contextlib.suppress(Exception):
            # v1.0.704（组件提醒）：任务委托 PC 接管但 dsh 未安装——记需求事件，前端弹提醒
            from core.component_reminder import record_demand
            record_demand("dsh", "PC 接管（dsh）",
                           "PC 接管通道不可用——dsh 未安装，可到「运行组件」页一键安装"
                           "（约 800MB，仅一次），装好后本任务可继续委托")
        return {"ok": False, "error": "dsh 未安装，先调用 dsh_prepare（npm i -g "
                             f"{DSH_SPEC}）装好再委托任务",
                "hint": "安装首次需 1-10 分钟（视网络）"}

    workdir, err = _resolve_workdir(cwd, d)
    if err:
        return {"ok": False, "error": err}

    env = dict(os.environ)
    # v1.0.612：便携版 node 不在系统 PATH 里，而 npm 装的 dsh.cmd 内部要靠 node 起进程
    _np = _node_dir_on_path()
    if _np:
        env["PATH"] = os.pathsep.join([_np, env.get("PATH", "")])
    # v1.0.706：三级解析（显式 dsh 段 → DEEPSEEK_* env → 软件默认模型回退），
    # 与 dsh_check 同一口径——「模型」设置里配了默认模型，dsh 不填也能跑。
    base_url, api_key, fb_model, source = _resolve_creds(cfg)
    if base_url:
        env["DEEPSEEK_BASE_URL"] = base_url
    if api_key:
        env["DEEPSEEK_API_KEY"] = api_key
        # v1.0.660（审查修复 M9）：**凭据注入必须留痕**——dsh 子进程拿到的是本机
        # 的 API 密钥，且它被授权在这台机器上跑最长 30 分钟。此前只有"dsh 跑完了"
        # 一条日志，事后没人知道密钥曾经被交给了另一个进程。落一条安全审计
        # （只记"注入了"这件事，不记密钥本身——密钥不落明文是 M5 的同一口径）。
        try:
            from security_ops import audit
            audit("dsh_run:credential", "已向子进程注入凭据（DEEPSEEK_API_KEY）",
                  True, "dsh 子进程将持有本机 API 密钥运行（最长 30 分钟）")
        except Exception:  # noqa: BLE001（审计不可用不阻断委托）
            log_event("[dsh] 凭据注入审计写入失败（不阻断委托）")

    rest = ["--profile", "headless"]
    if source == "default_model" and fb_model:
        # 回退默认模型时对齐 dsh 模型名：dsh 内部默认请求 deepseek-flash，
        # 本地端点没有这个模型会 404 —— 用 --patch 覆盖层把 agent-default-model
        # 的 model 改成默认模型的 id（patch 栈最高层，官方覆盖机制）。
        patch = _write_model_patch(fb_model)
        if patch:
            rest += ["--patch", patch]
            log_event(f"[dsh] 复用软件默认模型：endpoint={base_url} model={fb_model}"
                       f"（--patch 对齐模型名 → {patch}）")
        else:
            log_event("[dsh] 复用默认模型但 patch 写入失败——dsh 将按默认名 "
                      "deepseek-flash 请求（非 DeepSeek 端点会失败）")
    rest.append(task)
    extra = str(d.get("extra_args") or "").strip()
    if extra:
        rest += extra.split()
    cmd, via = _build_cmd(bin_path, rest)
    if via == "direct" and IS_WIN and str(bin_path).lower().endswith((".cmd", ".bat")):
        log_event("[dsh] 警告：shim 入口解析失败，回退 cmd /c——含空格的任务可能被 cmd 拆碎")

    log_event(f"[dsh] 接管任务开始 via={via} cwd={workdir} timeout={timeout}s task={task[:120]!r}")
    t0 = time.time()
    # v1.0.299：CREATE_NEW_PROCESS_GROUP（超时杀整树）| CREATE_NO_WINDOW（不弹黑框）
    creationflags = (0x00000200 | 0x08000000) if IS_WIN else 0
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=workdir, env=env, creationflags=creationflags)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"启动 dsh 失败：{e}"}

    try:
        out_b, err_b = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            out_b, err_b = p.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            out_b, err_b = b"", b""
        elapsed = round(time.time() - t0, 1)
        log_event(f"[dsh] 接管任务超时被杀 elapsed={elapsed}s")
        # v1.0.361 审查修复（O6）：超时路径也落转写——dsh 可能已执行过操作（含文件
        # 改动），无留痕违背"全程转写存档可复查"的设计承诺。
        _save_transcript(task, _dec(out_b), _dec(err_b), None, workdir, elapsed, timed_out=True)
        return {"ok": False, "timed_out": True,
                "error": f"dsh 运行超时（{timeout}s），进程组已强杀。"
                         "任务可能没完成：拆小任务重试，或加大 timeout（上限 1800s）",
                "elapsed_sec": elapsed}

    elapsed = round(time.time() - t0, 1)
    answer = _dec(out_b)
    reasoning = _dec(err_b)
    rec = _save_transcript(task, answer, reasoning, p.returncode, workdir, elapsed)
    log_event(f"[dsh] 接管任务结束 exit={p.returncode} elapsed={elapsed}s "
              f"answer={len(answer)}字 reasoning={len(reasoning)}字")

    if p.returncode == 0:
        return {
            "ok": True,
            "answer": answer[:ANSWER_LIMIT] + (f"\n...[已截断：共 {len(answer)} 字符]"
                                               if len(answer) > ANSWER_LIMIT else ""),
            "reasoning_tail": reasoning[-REASONING_LIMIT:],
            "elapsed_sec": elapsed,
            "cwd": workdir,
            "transcript": rec.get("path"),
            "note": "以上 answer 是 dsh agent 的最终结论；完整过程见 transcript 文件",
        }
    return {
        "ok": False,
        "exit_code": p.returncode,
        "error": f"dsh 退出码 {p.returncode}：" + (reasoning[-800:] or "（无错误输出）"),
        "answer_tail": answer[-1000:],
        "elapsed_sec": elapsed,
        "cwd": workdir,
        "transcript": rec.get("path"),
        "hint": "常见原因：模型端点/key 未配置或不可达（先 dsh_status 查就绪）、"
                "任务需要本机缺少的环境（依赖/工具）。详见 transcript 过程记录",
    }
