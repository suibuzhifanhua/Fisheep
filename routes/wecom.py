"""
routes/wecom — 企微绑定/窗口/抓取/互动元素/截图/发消息 + bind_status
====================================================================
从 backend.py 逐字迁入（T05）。R7 修复：补 save_workspace_file import（原缺失致截图落盘静默失效）。
"""
import os
import time
import bridge as wb
from flask import Blueprint, request, jsonify

from core.logging import log_event
from core import paths  # noqa: F401（路径函数属性查找，可被测试 patch）
from core.workspace_io import save_workspace_file  # v1.0.562 修复：拆分后函数在 workspace_io，原 paths. 断链
from core.config_store import load_config, update_config, _save_config_safe  # noqa: F401
# v1.0.660（B7）：只读配置快照（只读路径用，省掉每次深拷贝整份 config）
from core.config import load_config_view, config_copy
from agent_tools import _cdp_port

# 绑定状态缓存：v1.0.510 起定义在 core/wecom_state.py（backend 与本蓝图共用同一
# dict 对象；原"backend 反向 import routes.wecom._BIND_CACHE"的入口层私有符号依赖已消除）
from core.wecom_state import _BIND_CACHE  # noqa: F401（re-export 供存量 patch/引用兼容）

bp = Blueprint("wecom", __name__, url_prefix="/api")

@bp.route("/wecom/bind", methods=["POST"])
def api_wecom_bind():
    # 强杀/重启绑定目标属破坏性动作：后端强制要求 confirm_kill=true 才放行（P0-2）
    data = request.get_json(force=True, silent=True) or {}
    confirm_kill = bool(data.get("confirm_kill", False))
    # v1.0.723：多目标 CDP 绑定（wecom/edge/chrome/custom），未知目标归一为 wecom
    # （向后兼容旧前端——不带 target 的旧请求逐字走原企微路径）。
    target = wb.normalize_target(data.get("target") or "wecom")
    custom_port = data.get("custom_port")
    if target == "custom":
        try:
            custom_port = int(custom_port)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "自定义 CDP 软件缺少端口（custom_port 必须是数字）"}), 400
    label = wb.TARGET_LABELS[target]
    # v1.0.413（观测点）：绑定流程全程留痕——2026-09-30 17:07 实测点击绑定后软件
    # 进程消失且零日志（无崩溃记录、无 taskkill 证据），入口无日志导致完全无法回溯。
    log_event(f"[bind] 收到绑定请求：target={target} confirm_kill={confirm_kill} pid={os.getpid()}")
    # v1.0.425（2026-10-01 23:34 实测）：审计流水线运行期间拒绝杀目标型绑定——
    # 模型曾在附件下载卡住时自行 curl 本 API 重启企微，打断整条流水线。
    if confirm_kill:
        try:
            from services import batch_flow as _bf
            if _bf._AUDIT_RUNNING.is_set():
                log_event(f"[bind] 拒绝：审计流水线运行中，禁止重启{label}")
                return jsonify({"ok": False,
                                "error": f"月报审计流水线正在运行，禁止重启{label}。请等待流水线结束（或先停止审计任务）再绑定。"}), 409
        except Exception:  # noqa: BLE001
            pass
    # v1.0.646（用户："软件修复的时候不要影响企业微信"）：把上面那条"审计中拒杀"
    # 扩成**任何 agent 任务在跑都拒**——历史事故就是模型在任务中途自己 curl 这个接口
    # 重启企微（v1.0.425 记录）。confirm_kill 只该是"人坐在屏幕前点的那一下"，
    # 任务跑着的时候它一定不是人点的（人在看着界面操作时不会有并发 agent 任务）。
    if confirm_kill:
        try:
            from bridge.windows import _agent_busy
            if _agent_busy():
                log_event(f"[bind] 拒绝：agent 任务正在运行，禁止重启{label}")
                return jsonify({"ok": False,
                                "error": f"有任务正在运行，期间不会重启{label}（你的{label}窗口可能正被任务使用）。"
                                         "请等任务结束或先点「停止」，再到 设置 → CDP 绑定 里绑定。"}), 409
        except Exception as e:  # noqa: BLE001
            # fail-closed：判据取不到就当"在跑"，宁可不杀也不能误杀（这条是红线，方向唯一）
            log_event(f"[bind] agent 忙判定异常，按运行中处理（拒绝重启{label}）：{e}")
            return jsonify({"ok": False,
                            "error": f"暂时无法确认是否有任务在运行，为保护{label}已拒绝本次重启。请稍后再试。"}), 409
    # v1.0.555（P0-10）：绑定函数就地改 cfg['wecom']（exe_path/bound_pid/
    # bound_sig/bound_started_ts/cdp_port/…），原先 load→绑定→save——绑定
    # 流程内含拉进程+CDP 等待可达数十秒，期间用户的其它配置保存会被这个陈旧快照
    # 整体回滚。改为：事务内 fresh cfg 上重跑绑定函数的落盘 delta。
    # （绑定函数本身不写盘，只在 cfg 上记 delta，故可安全重入。）
    # v1.0.660（B7）：绑定函数会**写** cfg['wecom'] → 这里必须用可写副本
    # （config_copy），不能直接用只读快照，否则写绑定字段会抛 TypeError。
    cfg = config_copy(load_config_view())
    if target == "wecom":
        st = wb.ensure_binding(cfg, force=True, allow_kill=confirm_kill)
    elif target in wb.BROWSERS:
        # v1.0.723：浏览器目标（edge/chrome）——同款红线（needs_kill 闸门/不按名全杀），
        # 差别只在进程名与 exe 探测（见 bridge.app_targets）。
        st = wb.ensure_browser_binding(cfg, target, force=True, allow_kill=confirm_kill)
    else:  # custom：接管已带调试端口启动的任意 CDP 软件（只探测不动进程）
        st = wb.bind_custom_cdp(cfg, custom_port)
    log_event(f"[bind] 绑定流程返回：target={target} bound={st.get('bound')} "
              f"reason={str(st.get('reason'))[:60]}")
    # v1.0.652（P1-17）+ v1.0.654 A9：按键合并进 fresh cfg（绝不整段覆盖 wecom 段），
    # 合并器单源在 core.config_store.make_bind_merger（与 backend 启动绑定同源）。
    from core.config_store import make_bind_merger
    ok = update_config(make_bind_merger(cfg))
    if not ok:
        return jsonify({"ok": False, "error": "保存失败：配置写入异常（详见日志）"}), 500
    _BIND_CACHE.clear()
    _bs_cache_invalidate()   # v1.0.364（H6）：绑定状态变化立即失效缓存
    return jsonify(st)


@bp.route("/wecom/open", methods=["POST"])
def api_wecom_open():
    """v1.0.725：绑定成功后的「打开」按钮——拉起已绑定目标（在运行则不动它）。
    绝不杀/重启（allow_kill=False 由 open_bound_target 内锁死）；目标在运行但未带
    CDP 端口时原样返回（needs_kill 提示），重启只走 /wecom/bind 的确认链。"""
    log_event(f"[open] 收到打开目标请求 pid={os.getpid()}")
    cfg = config_copy(load_config_view())
    try:
        st = wb.open_bound_target(cfg)
    except Exception as e:  # noqa: BLE001
        log_event(f"[open] 打开目标异常：{e}")
        return jsonify({"ok": False, "error": f"打开目标异常：{e}"}), 500
    log_event(f"[open] 打开目标结果：target={st.get('target')} bound={st.get('bound')} "
              f"reason={str(st.get('reason'))[:60]}")
    from core.config_store import make_bind_merger
    ok = update_config(make_bind_merger(cfg))
    if not ok:
        return jsonify({"ok": False, "error": "保存失败：配置写入异常（详见日志）"}), 500
    _BIND_CACHE.clear()
    _bs_cache_invalidate()   # v1.0.364（H6）：绑定状态变化立即失效缓存
    return jsonify(st)


@bp.route("/wecom/browser_detect")
def api_browser_detect():
    """v1.0.723：绑定目标选择弹窗的数据源——本机装了哪些可绑浏览器 + 当前目标。
    只读探测（常见路径 + 注册表卸载项），不做任何进程/端口操作。"""
    cfg = load_config_view()
    w = (cfg.get("wecom") or {}) if isinstance(cfg, dict) else {}
    target = wb.normalize_target(w.get("cdp_target") or "wecom")
    return jsonify({
        "browsers": wb.detect_browsers(),
        "target": target,
        "target_label": wb.TARGET_LABELS[target],
        "targets": wb.TARGET_LABELS,
        "target_short": wb.TARGET_SHORT,
    })


@bp.route("/wecom/windows")
def api_windows():
    return jsonify(wb.window_list_summary(_cdp_port(load_config_view())))


@bp.route("/wecom/close", methods=["POST"])
def api_wecom_close():
    # v1.0.272：工作台窗口列表手动关闭单个窗口。复用 agent 工具验证过的
    # wb.close_page（CDP /json/close/<targetId>）。pick 必填——缺省 close_page
    # 会关 agent 的「当前跟随窗口」，手动入口绝不能误伤 agent 会话。
    data = request.get_json(force=True, silent=True) or {}
    pick = data.get("pick")
    if pick is None:
        return jsonify({"ok": False, "error": "未指定窗口序号（pick）"}), 400
    try:
        pick = int(pick)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pick 必须是窗口序号（整数）"}), 400
    result = wb.close_page(pick, _cdp_port(load_config_view()))
    if result.get("ok"):
        log_event(f"[wecom] 工作台手动关闭窗口 {result.get('closed_index')}"
                  f"（{(result.get('title') or '')[:40]}）")
    return jsonify(result)


@bp.route("/wecom/capture")
def api_capture():
    pick = request.args.get("pick", type=int)
    return jsonify(wb.capture(pick, _cdp_port(load_config_view())))


@bp.route("/wecom/interactive")
def api_interactive():
    pick = request.args.get("pick", type=int)
    return jsonify(wb.get_interactive_elements(pick, _cdp_port(load_config_view())))


@bp.route("/wecom/screenshot")
def api_screenshot():
    pick = request.args.get("pick", type=int)
    # v1.0.225：预览截图默认【不再落盘】——前端实时预览每 5s 轮询一次，
    # 旧版每次调用都存 PNG，实测堆积 11176 张 / 2.3GB（agent 操作是 DOM/CDP 级，
    # 不依赖截图；截图只是给人看的预览）。需要保存时显式传 save=1。
    # v1.0.274：默认【不再前置窗口】——5s 轮询每轮都 Page.bringToFront 是
    # 「企微窗口反复弹到最前盖住用户工作」的主因；仅在 save=1（手动存图）
    # 或 front=1（显式要求）时前置强制新帧。
    front = request.args.get("front") == "1" or request.args.get("save") == "1"
    result = wb.capture_screenshot(pick, _cdp_port(load_config_view()), front=front)
    if request.args.get("save") == "1":
        data = result.get("data")
        if data:
            import time as _tm, base64 as _b64
            fname = "screenshot_" + _tm.strftime("%Y%m%d_%H%M%S") + ".png"
            try: save_workspace_file("下载", fname, _b64.b64decode(data))
            except Exception:  # noqa: BLE001
                pass
    return jsonify(result)


@bp.route("/wecom/window_diag")
def api_window_diag():
    """v1.0.274 诊断（只读）：CDP 页面列表 vs OS 可见窗口标题列表。
    用于验证「新学习窗口自动最小化」是否生效；若 companion.log 出现
    「未找到 OS 窗口」，用本端点比对两侧标题修正 _title_match 规则。
    v1.0.282：三个阶段（cdp/os/proc）各自 5s 超时守卫——v1.0.279 的
    GetUserObjectInformationW 在 exe 内崩 libffi 拖死全进程的教训：任何单阶段
    挂起都不允许拖死端点（超时返回部分数据 + stage_status 标注）。"""
    port = _cdp_port(load_config_view())

    cdp_error = None

    def _cdp_stage():
        nonlocal cdp_error
        tg = wb.list_targets(port)
        # v1.0.730（审查 P2-D）：list_targets 失败会返回 {"ok": False, "error":...}，
        # 原实现 `if isinstance(tg, list) else []` 把 CDP 故障伪装成「0 个页面」，
        # 诊断端点反而掩盖根因（违背 6.17 的防误导精神）。保持 cdp_pages 为 list
        # （前端 js/05-connectors-status.js 按数组消费），错误另放 cdp_error 字段。
        if not isinstance(tg, list):
            cdp_error = (str((tg or {}).get("error") or "CDP 目标枚举失败")
                         if isinstance(tg, dict) else "CDP 目标枚举失败")
            return []
        return [{"title": (t.get("title") or "")[:60], "url": (t.get("url") or "")[:90]}
                for t in tg if isinstance(t, dict) and t.get("type") == "page"]

    st_cdp, cdp_pages = wb._stage_guard(_cdp_stage, 5.0, "cdp")
    st_os, os_windows = wb._stage_guard(wb.list_os_window_titles, 5.0, "os")
    st_proc, process = wb._stage_guard(wb.process_session_info, 5.0, "proc")
    resp = {"cdp_pages": cdp_pages, "os_windows": os_windows, "process": process}
    if cdp_error:
        resp["cdp_error"] = cdp_error
    if "ok" not in (st_cdp, st_os, st_proc):
        resp["stage_status"] = {"cdp": st_cdp, "os": st_os, "proc": st_proc}
    return jsonify(resp)


@bp.route("/wecom/send", methods=["POST"])
def api_send():
    data = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    hook = (cfg.get("wecom", {}) or {}).get("webhook", "")
    return jsonify(wb.send_webhook(hook, data.get("content", ""), data.get("msgtype", "text")))

# ---------- 绑定状态（不重启） ----------
# v1.0.364 审查修复（H6）：前端每 8s 轮询 /api/status → bind_status 每次做进程快照
# + 3 次 CDP HTTP + 1 次 WS 握手（~1.08 万次/天）。结果缓存 3s——状态感知延迟可忽略，
# CDP churn 大幅下降；绑定/解绑等写操作直接失效缓存。
_BS_CACHE = {"ts": 0.0, "data": None}
_BS_TTL = 3.0
_BS_STALE_MAX = 30.0        # v1.0.540：陈旧值最长可用期（超此才同步阻塞刷新）
_BS_LOCK = __import__("threading").Lock()
_BS_REFRESHING = False


def _bs_cache_invalidate():
    with _BS_LOCK:
        _BS_CACHE["ts"] = 0.0


def _bs_refresh_async(cfg):
    """后台刷新绑定状态（v1.0.540 stale-while-revalidate）——企微 CDP 探测最坏 4s，
    /api/status 高频调用绝不同步等它（实测 status 响应 4.2s→瞬时）。"""
    global _BS_REFRESHING
    with _BS_LOCK:
        if _BS_REFRESHING:
            return
        _BS_REFRESHING = True

    def _job():
        global _BS_REFRESHING
        try:
            bind_status(cfg, force_sync=True)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _BS_LOCK:
                _BS_REFRESHING = False
    __import__("threading").Thread(target=_job, name="bs-refresh", daemon=True).start()


def bind_status(cfg, force_sync=False):
    now = time.time()
    with _BS_LOCK:
        cached = (_BS_CACHE["data"] is not None and now - _BS_CACHE["ts"] < _BS_STALE_MAX)
        if cached:
            d = dict(_BS_CACHE["data"])
            age = now - _BS_CACHE["ts"]
    if cached:
        # 3s~30s：返回陈旧值 + 后台刷新（force_sync=True 的调用方【绑定页】仍同步等待新鲜值）
        if age > _BS_TTL and force_sync:
            pass   # 落到下方同步探测
        else:
            if age > _BS_TTL:
                _bs_refresh_async(cfg)
            d["cached"] = True
            return d
    w = cfg.get("wecom", {}) or {}
    port = _cdp_port(cfg)   # v1.0.588：统一口径（原 w.get("cdp_port", 9222) 在值为空串时会把空值透下去）
    # v1.0.660（审查修复 A10）：exe_path 探测结果写进深拷贝是 dead store——前端零引用，
    # 已连同响应字段一并删除（顺带省掉每次 8s 轮询都可能触发的注册表探测）。
    # v1.0.723：目标感知——wecom 分支保持原口径（tests 锁契约），浏览器目标换成
    # 同名镜像枚举，custom 目标进程不归本软件管（pids 恒空）。
    target = wb.normalize_target(w.get("cdp_target") or "wecom")
    if target == "wecom":
        pids = wb.find_wecom_pids()
    elif target in wb.BROWSERS:
        pids = wb.find_browser_pids(target)
    else:
        pids = []
    up = wb.cdp_up(port)
    if target == "custom":
        up = up and wb._cdp_ws_ok(port)   # 接管判据与绑定一致：HTTP + WS 双确认
    # v1.0.734 ①「有接管黄框但显示未绑定」：此前界面只信 CDP HTTP 探活，
    # 而黄框只凭「命令行带调试端口的主进程」定位，两套判据各说各话。实测本机
    # 就出现过 9223 上 Edge 进程在、但 /json/version 连接超时 → 框亮着、界面
    # 写「未绑定」，用户无法判断到底接管没有。
    # 现在直接读高亮守护线程的 owner_hwnd（纯内存、零 WMI / 零 Win32），
    # 与「框亮着」共用同一份事实源，并区分三态：
    #   bound=True            → 已绑定（CDP 端点可达，可操作页面）
    #   bound=False+attached  → 已接管·连接中（进程在、CDP 面暂不可达）
    #   bound=False+!attached → 未绑定
    # bound 仍严格等于 up，**绝不**因为框亮就把不可达端口说成已绑定，
    # 否则 agent 会拿着一个连不上的端点去做页面操作。
    try:
        owner_attached = bool(wb.cdp_owner_attached())
    except Exception as e:  # noqa: BLE001 - 门面不可用（老包/非 Win32）时保守降级
        log_event(f"[warn] 高亮接管证据读取失败（按未接管处理）：{e}")
        owner_attached = False
    attached = bool(up or owner_attached)
    prev = w.get("bound_pid")
    alive = prev in pids if prev else False
    sig = wb.windows_signature(wb.list_targets(port)) if up else None
    if target == "custom":
        # custom 不持有进程：alive 无意义，changed 只看端点+窗口签名
        changed = not (up and sig == w.get("bound_sig"))
    else:
        changed = not (up and alive and sig == w.get("bound_sig"))
    d = {
        "bound": up, "changed": changed, "port": port,
        # v1.0.734 ①：三态状态识别。前端据此把「未绑定」升级为
        # 「已接管 · 连接中」，让界面和黄框不再互相矛盾。
        "attached": attached,
        "bound_detail": ("bound" if up else
                         ("attaching" if attached else "unbound")),
        # v1.0.652（审计 P1-7）：补 cdp_port 别名——前端设置页按 config 段字段名
        # w.cdp_port 读取，而本响应旧只有 port，设置页端口恒显 9222 回退值，
        # 且 refreshStatus 每 8s 把输入框刷回 9222 后任何一次保存都会反向覆盖
        # pick_cdp_port 自动避让写回配置的端口。双写过渡，前端可逐步收敛到单名。
        "cdp_port": port,
        "pid": prev if alive else (pids[0] if pids else None),
        "auto_launch": w.get("auto_launch", True),
        "auto_cdp": w.get("auto_cdp", True),
        "silent_mode": bool(w.get("silent_mode", True)),   # v1.0.523：静默模式开关回传前端
        # v1.0.732：CDP 接管窗口高亮边框（设置页开关读这里，8s 状态轮询顺带回显）
        "cdp_highlight": bool(w.get("cdp_highlight", True)),
        "cdp_highlight_color": w.get("cdp_highlight_color") or "#FF9500",
        "cdp_highlight_width": w.get("cdp_highlight_width", 8),
        "needs_kill": bool(pids) and not up,  # 有目标进程但 CDP 未起：需重启才能绑定
        "windows": len(wb.window_list_summary(port)) if up else 0,
        # v1.0.723：当前绑定目标回传前端（顶栏/侧栏/按钮/横幅统一取这里，不再手抄"企微"）
        "target": target,
        "target_label": wb.TARGET_LABELS[target],
        "target_short": wb.TARGET_SHORT[target],
    }
    # v1.0.652（审计 P2-7）：缓存写入进锁——原读侧持锁写侧裸写，与 _bs_refresh_async
    # 并发时可能产生 ts/data 不配对的缓存（陈旧 data 配新鲜 ts，被当新鲜值用满 30s）。
    with _BS_LOCK:
        _BS_CACHE["ts"] = now
        _BS_CACHE["data"] = d
    return d