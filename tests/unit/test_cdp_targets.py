# -*- coding: utf-8 -*-
"""tests/unit/test_cdp_targets.py — 多目标 CDP 绑定（v1.0.723）
①detect_browsers / normalize_target / cdp_target_kind（五态）
②pick_cdp_port_for：本目标已占口复用 / 别家避让 / 整段不可用
③ensure_browser_binding：新启（Popen 带 CDP flag）/ needs_kill 不杀 /
  allow_kill 只杀当场枚举 PID / 退不干净放弃 / 端口漂移拒绑
④bind_custom_cdp：只探测接管（up/down 两态）
⑤config_store.cdp_port 的 custom 路由 + make_bind_merger 新键
⑥/api/wecom/bind 分派（custom 缺端口 400 / target 透传）+ browser_detect 端点
  + /api/wecom/open（v1.0.725「打开」按钮：恒 allow_kill=False；启动必须带专用 profile）
⑦bind_status 目标感知字段（target/target_label/target_short）
全部 mock 网络与进程探测，无真实浏览器依赖。pytest 与直跑双兼容（手动补丁）。
"""
import io
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import core  # noqa: E402
from core import config_store  # noqa: E402
import core.logging as clog  # noqa: E402

WS = tempfile.mkdtemp(prefix="cdp_tgt_")
core.paths.workspace_root = lambda: WS
config_store.config_path = lambda: os.path.join(WS, "config.json")

import bridge.app_targets as at  # noqa: E402
import bridge as wb  # noqa: E402


class _Resp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _Patches:
    """手动补丁栈（与 test_wecom_bind 同款，直跑兼容）。"""
    def __init__(self):
        self._stack = []

    def set(self, obj, name, fn):
        self._stack.append((obj, name, getattr(obj, name)))
        setattr(obj, name, fn)

    def undo(self):
        for obj, name, old in reversed(self._stack):
            setattr(obj, name, old)
        self._stack.clear()


FAKE_EXE = os.path.join(WS, "msedge.exe")
io.open(FAKE_EXE, "w", encoding="utf-8").close()
FAKE_CHROME = os.path.join(WS, "chrome.exe")
io.open(FAKE_CHROME, "w", encoding="utf-8").close()


def _reset_cfg(port=9222, **extra):
    cfg = {"schema_version": 2, "models": [],
           "wecom": {"cdp_port": port, "exe_path": FAKE_EXE,
                     "auto_launch": True, "auto_cdp": True, **extra}}
    io.open(config_store.config_path(), "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False))
    return cfg


def _ua_map(p, by_port):
    """按端口返回 /json/version 的 UA 文本；不在表里的端口=连不上。key 用字符串端口。"""
    by_port = {str(k): v for k, v in by_port.items()}
    def fake_get(url, timeout=None):
        port = url.split("//", 1)[1].split("/")[0].split(":")[1]   # 保持字符串对齐 key
        if port not in by_port:
            raise OSError("connection refused")
        return _Resp(by_port[port])
    p.set(clog, "_lazy_requests", type("NS", (), {"get": staticmethod(fake_get)}))


# ---------- 1. 目标定义与品牌识别 ----------
def test_normalize_target_unknown_falls_back_to_wecom():
    assert at.normalize_target("EDGE") == "edge"
    assert at.normalize_target("  chrome ") == "chrome"
    assert at.normalize_target("custom") == "custom"
    assert at.normalize_target("bogus") == "wecom", "未知目标必须回退企微（向后兼容）"
    assert at.normalize_target(None) == "wecom"
    assert at.normalize_target("") == "wecom"


def test_detect_browsers_registry_fallback():
    """候选路径为空时走注册表兜底（注册表桩只认得 edge），chrome 探不到=None。"""
    p = _Patches()
    try:
        old = {k: v["exe_candidates"] for k, v in at.BROWSERS.items()}
        for k in at.BROWSERS:
            at.BROWSERS[k]["exe_candidates"] = []
        p.set(at, "_find_browser_from_registry",
              lambda spec: FAKE_EXE if spec["exe_name"] == "msedge.exe" else None)
        out = at.detect_browsers()
        assert out == {"edge": FAKE_EXE, "chrome": None}, out
        for k, v in old.items():
            at.BROWSERS[k]["exe_candidates"] = v
    finally:
        p.undo()


def test_cdp_target_kind_five_states():
    """五态：wecom / edge（UA 同时含 Chrome 字样，判序必须 edg 先于 chrome）/
    chrome / unknown（活但认不出）/ None（没活）。"""
    p = _Patches()
    try:
        _ua_map(p, {
            "9222": '{"Browser":"Chrome/131.0.0.0","User-Agent":"Mozilla/5.0 (WeCom) wxwork/4.1"}',
            "9223": '{"Browser":"Edg/131.0.0.0","User-Agent":"Mozilla/5.0 Edg/131.0.0.0 Chrome/131"}',
            "9224": '{"Browser":"Chrome/131.0.0.0","User-Agent":"Mozilla/5.0 Chrome/131"}',
            "9225": '{"browser":"MysteryProxy/1.0"}',
        })
        assert at.cdp_target_kind(9222) == "wecom"
        assert at.cdp_target_kind(9223) == "edge", "Edge UA 同时含 Chrome——判序必须 edg 先"
        assert at.cdp_target_kind(9224) == "chrome"
        assert at.cdp_target_kind(9225) == "unknown", "活但认不出 ≠ 没活（P1-12 同款纪律）"
        assert at.cdp_target_kind(9999) is None, "连不上必须 None，不许断言被别家占用"
    finally:
        p.undo()


# ---------- 2. 端口择优 ----------
def test_pick_cdp_port_reuses_target_own_port():
    p = _Patches()
    try:
        _ua_map(p, {"9222": '{"Browser":"Edg/131.0.0.0"}'})
        port, why = at.pick_cdp_port_for("edge", 9222)
        assert port == 9222, (port, why)
        assert "复用" in why
    finally:
        p.undo()


def test_pick_cdp_port_avoids_foreign_and_takes_next_free():
    p = _Patches()
    try:
        # 9222 是企微的 CDP（对 edge 而言是别家）；9223 没活、真空闲
        _ua_map(p, {"9222": '{"User-Agent":"wxwork/4.1"}'})
        p.set(at, "port_bindable", lambda port: port != 9222)
        port, why = at.pick_cdp_port_for("edge", 9222)
        assert port == 9223, (port, why)
    finally:
        p.undo()


def test_pick_cdp_port_all_foreign_gives_up():
    p = _Patches()
    try:
        p.set(at, "cdp_target_kind", lambda port=9222: "wecom")   # 全段都是别家
        p.set(at, "port_bindable", lambda port: False)
        port, why = at.pick_cdp_port_for("edge", 9222, span=3)
        assert port is None and "占用" in why, (port, why)
    finally:
        p.undo()


# ---------- 3. 浏览器幂等绑定 ----------
def _base_patches(p, *, up=False, ws=False, kind="edge", pids=(), popen=None,
                  exe=None, auto=True):
    """ensure_browser_binding 的标准桩位（与真实模块边界一致，一个不多一个不少）。"""
    p.set(at, "detect_browsers", lambda: {"edge": exe or FAKE_EXE, "chrome": None})
    p.set(at, "cdp_up", lambda port: up)
    p.set(at, "_cdp_ws_ok", lambda port: ws)
    p.set(at, "cdp_target_kind", lambda port=9222: kind)
    p.set(at, "port_bindable", lambda port: True)
    p.set(at, "find_browser_pids", lambda t: list(pids))
    p.set(at, "list_targets", lambda port: [])
    p.set(at, "window_list_summary", lambda port: ["w1"])
    p.set(at, "windows_signature", lambda tg: "sig")
    p.set(at, "_logw", lambda msg: None)
    p.set(at.time, "sleep", lambda s: None)   # 等待循环不真睡（at.time 即全局 time 模块，undo 时还原）
    # v1.0.725 / v1.0.738：专用 profile 目录桩到测试临时区（真实函数在 产物库/cdp_profiles/）
    p.set(at, "browser_profile_dir", lambda t: os.path.join(WS, "产物库", "cdp_profiles", t))
    # 默认桩：全部运行 PID 都算调试 session（= 旧行为），个别用例单独改
    p.set(at, "_debug_session_pids", lambda target, pids: list(pids))
    launched = {"n": 0, "args": None}

    def fake_popen(argv, creationflags=0):
        launched["n"] += 1
        launched["args"] = argv
    p.set(at.subprocess, "Popen", fake_popen)
    return launched


def test_ensure_browser_binding_launches_with_cdp_flags():
    """冷启动：没进程 + auto_launch → 带 --remote-debugging-port + origin 白名单拉起。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    try:
        state = {"up": False}
        launched = _base_patches(p, up=False, ws=False)
        p.set(at, "cdp_up", lambda port: state["up"])
        p.set(at, "_cdp_ws_ok", lambda port: state["up"])
        p.set(at, "find_browser_pids", lambda t: [555] if state["up"] else [])
        p.set(at.subprocess, "Popen",
             lambda argv, creationflags=0: state.__setitem__("up", True)
             or launched.__setitem__("n", launched["n"] + 1)
             or launched.__setitem__("args", argv))
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=False, allow_kill=True)
        assert st["bound"] is True, st
        assert st["target"] == "edge" and st["target_label"] == "Microsoft Edge"
        args = " ".join(launched["args"])
        assert "--remote-debugging-port=9222" in args, args
        assert "remote-allow-origins" in args, "CDP origin 白名单 flag 丢了（v1.0.646 收紧）"
        # v1.0.725：必须带非默认 --user-data-dir——136+ 默认 profile 的 CDP 端口被静默
        # 关闭（本机 Edge 157 实跑根因），带 flag 启默认 profile 端口永远起不来
        assert "--user-data-dir=" in args, f"没带专用 profile 目录（默认 profile 的 CDP 端口 136+ 不生效）：{args}"
        assert "cdp_profiles" in args, args
        assert launched["n"] == 1
    finally:
        p.undo()


def test_ensure_browser_binding_running_without_cdp_needs_kill():
    """红线移植：运行中未带调试端口 + 未确认 → needs_kill，一个进程都不动。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    killed = {"cmds": []}
    try:
        _base_patches(p, up=False, pids=(333,))
        p.set(at, "_run_hidden", lambda args, **kw: killed["cmds"].append(" ".join(args)))
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=True, allow_kill=False)
        assert st["bound"] is False and st.get("needs_kill") is True, st
        assert killed["cmds"] == [], f"未确认就杀了进程：{killed['cmds']}"
    finally:
        p.undo()


def test_ensure_browser_binding_allow_kill_only_enumerated_pids():
    """确认后只杀当场枚举到的 PID（含子进程树语义），绝不按镜像名 /im 全杀。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    killed = {"cmds": []}
    launched = {"n": 0}
    try:
        state = {"phase": "pre"}
        p.set(at, "cdp_up", lambda port: state["phase"] == "post")
        p.set(at, "_cdp_ws_ok", lambda port: state["phase"] == "post")
        p.set(at, "find_browser_pids",
             lambda t: [333] if state["phase"] == "pre" else ([555] if state["phase"] == "post" else []))
        p.set(at, "_debug_session_pids", lambda target, pids: list(pids))   # 旧语义：全算调试 session
        p.set(at, "_run_hidden",
             lambda args, **kw: killed["cmds"].append(" ".join(args)) or state.__setitem__("phase", "clean"))
        p.set(at.subprocess, "Popen",
             lambda argv, creationflags=0: state.__setitem__("phase", "post")
             or launched.__setitem__("n", launched["n"] + 1))
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "cdp_target_kind", lambda port=9222: "edge")
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: ["w1"])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=True, allow_kill=True)
        cmds = " | ".join(killed["cmds"])
        assert st["bound"] is True, st
        assert "/pid 333" in cmds, f"要杀当场枚举的 PID：{cmds}"
        assert "/t" in cmds, f"要连子进程树一起收（只杀主进程会残留占锁，新实例被单实例提示挡掉）：{cmds}"
        assert "/im" not in cmds, f"又按镜像名全杀了（浏览器进程树误伤面更大）：{cmds}"
        assert launched["n"] == 1
    finally:
        p.undo()


def test_ensure_browser_binding_gives_up_when_kill_incomplete():
    """退不干净 → 放弃重启：不 Popen、不升级 /im，把「该你手动退」说清。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    killed = {"cmds": []}
    launched = {"n": 0}
    try:
        p.set(at, "cdp_up", lambda port: False)
        p.set(at, "_cdp_ws_ok", lambda port: False)
        p.set(at, "cdp_target_kind", lambda port=9222: None)
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "find_browser_pids", lambda t: [222])     # 永远赖着不退
        p.set(at, "_debug_session_pids", lambda target, pids: list(pids))   # 旧语义：全算调试 session
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "_run_hidden", lambda args, **kw: killed["cmds"].append(" ".join(args)))
        p.set(at.subprocess, "Popen", lambda argv, creationflags=0:
              launched.__setitem__("n", launched["n"] + 1))
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: [])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=True, allow_kill=True)
        cmds = " | ".join(killed["cmds"])
        assert st["bound"] is False, st
        assert "没有" in st["reason"] and "强杀" in st["reason"], st["reason"]
        assert launched["n"] == 0, "没退干净就 Popen 新实例，等于给用户开两个"
        assert "/im" not in cmds, cmds
    finally:
        p.undo()


def test_debug_session_pids_classifies_by_command_line():
    """v1.0.725：按 WMI 命令行把运行 PID 分成调试 session / 用户日常浏览器。
    专用 profile 路径（cdp_profiles/<target>，反斜杠或正斜杠）或
    --remote-debugging-port → 调试 session（选中）；裸命令行 → 不选。
    WMI 查不到（异常）→ None（调用方走保守旧路径）；无进程（null）→ []；
    单进程时 ConvertTo-Json 返回 dict 而非 list，要归一成单元素列表。"""
    p = _Patches()
    try:
        def _run_fake(stdout=None, exc=None):
            def fake_run(cmd, **kw):
                if exc:
                    raise exc
                return types.SimpleNamespace(stdout=stdout, returncode=0)
            return fake_run

        payload = json.dumps([
            {"ProcessId": 111,
             "CommandLine": r"C:\edge\msedge.exe --user-data-dir=C:\app\cdp_profiles\edge"},
            {"ProcessId": 222, "CommandLine": r"C:\edge\msedge.exe"},
            {"ProcessId": 333,
             "CommandLine": r"C:\edge\msedge.exe --remote-debugging-port=9223"}])
        p.set(at.subprocess, "run", _run_fake(stdout=payload))
        assert at._debug_session_pids("edge", [111, 222, 333]) == [111, 333], \
            "111（专用 profile）/333（调试 flag）要选中，222（用户日常）不能选"
        # 单进程 → dict；正斜杠形式的 profile 路径也算
        p.set(at.subprocess, "run", _run_fake(stdout=json.dumps(
            {"ProcessId": 444,
             "CommandLine": r"C:\edge\msedge.exe --user-data-dir=C:/app/cdp_profiles/edge"})))
        assert at._debug_session_pids("edge", [444]) == [444]
        # WMI 异常 → None（调用方确认+全杀保守路径）
        p.set(at.subprocess, "run", _run_fake(exc=OSError("wmi unavailable")))
        assert at._debug_session_pids("edge", [111]) is None
        # 无进程（ConvertTo-Json 输出 null）→ []
        p.set(at.subprocess, "run", _run_fake(stdout="null"))
        assert at._debug_session_pids("edge", []) == []
    finally:
        p.undo()


def test_ensure_browser_binding_kills_only_debug_session():
    """v1.0.725：调试实例 111 与用户日常浏览器 222 共存时，确认后**只杀**调试
    session（/pid 111），222 一动不动；等待循环只等被杀批次（wait_for=111）
    消失——日常浏览器还在跑不算「没退干净」。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    killed = {"cmds": []}
    launched = {"n": 0}
    try:
        state = {"phase": "pre"}
        p.set(at, "cdp_up", lambda port: state["phase"] == "post")
        p.set(at, "_cdp_ws_ok", lambda port: state["phase"] == "post")
        p.set(at, "find_browser_pids",
              lambda t: ([111, 222] if state["phase"] == "pre"
                         else ([555] if state["phase"] == "post" else [222])))
        p.set(at, "_debug_session_pids", lambda target, pids: [111])   # 只有 111 是调试 session
        p.set(at, "_run_hidden",
              lambda args, **kw: killed["cmds"].append(" ".join(args))
              or state.__setitem__("phase", "clean"))
        p.set(at.subprocess, "Popen",
              lambda argv, creationflags=0: state.__setitem__("phase", "post")
              or launched.__setitem__("n", launched["n"] + 1))
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "cdp_target_kind", lambda port=9222: "edge")
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: ["w1"])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=True, allow_kill=True)
        cmds = " | ".join(killed["cmds"])
        assert st["bound"] is True, st
        assert "/pid 111" in cmds, f"要杀调试 session 的 PID：{cmds}"
        assert "/pid 222" not in cmds, f"杀了用户日常浏览器（只该动调试 session）：{cmds}"
        assert "/t" in cmds and "/im" not in cmds, cmds
        assert launched["n"] == 1
    finally:
        p.undo()


def test_ensure_browser_binding_daily_browser_not_touched():
    """v1.0.725：运行中的全是用户日常浏览器（session=[]）→ 零杀命令、**不需要
    用户确认**（allow_kill=False 也直接成），专用 profile 另起独立调试实例绑定。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    killed = {"cmds": []}
    launched = {"n": 0}
    try:
        state = {"phase": "pre"}
        p.set(at, "cdp_up", lambda port: state["phase"] == "post")
        p.set(at, "_cdp_ws_ok", lambda port: state["phase"] == "post")
        p.set(at, "find_browser_pids",
              lambda t: [333] if state["phase"] == "pre" else [555, 333])
        p.set(at, "_debug_session_pids", lambda target, pids: [])   # 全是日常浏览器
        p.set(at, "_run_hidden",
              lambda args, **kw: killed["cmds"].append(" ".join(args)))
        p.set(at.subprocess, "Popen",
              lambda argv, creationflags=0: state.__setitem__("phase", "post")
              or launched.__setitem__("n", launched["n"] + 1)
              or launched.__setitem__("args", argv))
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "cdp_target_kind", lambda port=9222: "edge")
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: ["w1"])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=True, allow_kill=False)
        assert st["bound"] is True, st
        assert st.get("needs_kill") is not True, "日常浏览器共存不该要求确认杀进程"
        assert killed["cmds"] == [], f"杀了用户日常浏览器：{killed['cmds']}"
        args = " ".join(launched.get("args") or [])
        assert launched["n"] == 1, "没另起独立调试实例"
        assert "--user-data-dir=" in args and "cdp_profiles" in args, \
            f"调试实例没带专用 profile（会占用户日常 profile 的单实例锁）：{args}"
    finally:
        p.undo()


def test_ensure_browser_binding_startup_never_launches():
    """v1.0.727（用户：「启动软件直接弹出浏览器这个设定取消，要让用户自己去打开，
    不然很多用户感觉像是广告就关闭了」）：启动自动绑定（allow_launch=False）冷启动
    时不 Popen——浏览器窗口不许在软件启动时凭空弹出，回「不自动拉起」+ 出路指引。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    launched = {"n": 0}
    try:
        p.set(at, "cdp_up", lambda port: False)
        p.set(at, "_cdp_ws_ok", lambda port: False)
        p.set(at, "find_browser_pids", lambda t: [])
        p.set(at.subprocess, "Popen",
              lambda argv, creationflags=0: launched.__setitem__("n", launched["n"] + 1))
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "cdp_target_kind", lambda port=9222: "edge")
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: [])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=False, allow_kill=False, allow_launch=False)
        assert st["bound"] is False, st
        assert "不自动拉起" in st["reason"], st["reason"]
        assert "CDP 绑定" in st["reason"], "出路要指向「CDP 绑定」按钮"
        assert launched["n"] == 0, f"启动时 Popen 了浏览器（凭空弹窗=被当广告关掉）：{st}"
        assert st.get("needs_kill") is not True
    finally:
        p.undo()


def test_ensure_browser_binding_startup_daily_browser_no_launch():
    """v1.0.727：日常浏览器在跑（session=[]）时启动绑定也**不**另起调试实例——
    零杀命令、零 Popen，只回「不自动拉起」的未绑定态（用户日常浏览器一个不碰）。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    killed = {"cmds": []}
    launched = {"n": 0}
    try:
        p.set(at, "cdp_up", lambda port: False)
        p.set(at, "_cdp_ws_ok", lambda port: False)
        p.set(at, "find_browser_pids", lambda t: [333])
        p.set(at, "_debug_session_pids", lambda target, pids: [])   # 全是日常浏览器
        p.set(at, "_run_hidden", lambda args, **kw: killed["cmds"].append(" ".join(args)))
        p.set(at.subprocess, "Popen",
              lambda argv, creationflags=0: launched.__setitem__("n", launched["n"] + 1))
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "cdp_target_kind", lambda port=9222: "edge")
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: [])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=False, allow_kill=False, allow_launch=False)
        assert st["bound"] is False, st
        assert "不自动拉起" in st["reason"] and "日常浏览器" in st["reason"], st["reason"]
        assert killed["cmds"] == [] and launched["n"] == 0, \
            f"动了用户日常浏览器/起了调试实例：killed={killed['cmds']} popen={launched['n']}"
    finally:
        p.undo()


def test_ensure_browser_binding_exepath_cache_not_cross_target():
    """v1.0.728（实跑日志定位）：换绑目标不得复用上一目标的 exe 缓存——原守卫
    拿「已覆盖后」的 cdp_target 比较恒真，17:21 实锤绑 chrome 起的是 msedge.exe
    （端口特征 edge 被拒绑「请换一个空闲端口重试」，用户无解）。"""
    _reset_cfg(9222, cdp_target="edge")     # 上一轮绑的是 edge，exe_path 缓存为 edge 路径
    p = _Patches()
    launched = {"n": 0, "args": None}
    try:
        state = {"up": False}
        p.set(at, "cdp_up", lambda port: state["up"])
        p.set(at, "_cdp_ws_ok", lambda port: state["up"])
        p.set(at, "find_browser_pids", lambda t: [555] if state["up"] else [])
        p.set(at, "detect_browsers",
              lambda: {"edge": FAKE_EXE, "chrome": FAKE_CHROME})
        p.set(at.subprocess, "Popen",
              lambda argv, creationflags=0: state.__setitem__("up", True)
              or launched.__setitem__("n", launched["n"] + 1)
              or launched.__setitem__("args", argv))
        p.set(at, "port_bindable", lambda port: True)
        p.set(at, "cdp_target_kind", lambda port=9222: "chrome")
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: ["w1"])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        p.set(at.time, "sleep", lambda s: None)
        st = at.ensure_browser_binding(config_store.load_config(), "chrome",
                                       force=False, allow_kill=True)
        assert st["bound"] is True, st
        assert launched["n"] == 1
        assert (launched["args"] or [None])[0] == FAKE_CHROME, \
            f"复用了上一目标 edge 的 exe 起 chrome（跨目标缓存污染）：{launched['args']}"
        # 落盘由调用方走 make_bind_merger，这里断言返回体里的 exe_path（合并器原样透传）
        assert st["exe_path"] == FAKE_CHROME, f"返回体 exe_path 没换成 chrome：{st}"
    finally:
        p.undo()


def test_ensure_browser_binding_foreign_drift_refuses():
    """择优与连口之间端口状态漂移（别家刚把调试口开上来）→ 不绑错家、不动进程。"""
    _reset_cfg(9222, cdp_target="edge")
    p = _Patches()
    launched = {"n": 0}
    try:
        p.set(at, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        p.set(at, "port_bindable", lambda port: True)
        state = {"phase": "pick"}

        def up_fake(port):
            if port == 9223:
                state["phase"] = "connect"   # 择优结束、开始连口
            return port == 9223

        def kind_seq(port):
            # pick 阶段：9222 是企微口（避让）→ 9223 没活可用；
            # connect 阶段：9223 上出现的却是 chrome（漂移）
            if state["phase"] == "pick":
                return "wecom" if port == 9222 else None
            return "chrome" if port == 9223 else None
        p.set(at, "cdp_target_kind", kind_seq)
        p.set(at, "cdp_up", up_fake)
        p.set(at, "_cdp_ws_ok", lambda port: True)
        p.set(at, "find_browser_pids", lambda t: [])
        p.set(at.subprocess, "Popen",
             lambda argv, creationflags=0: launched.__setitem__("n", launched["n"] + 1))
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: [])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        st = at.ensure_browser_binding(config_store.load_config(), "edge",
                                       force=True, allow_kill=True)
        assert st["bound"] is False, st
        assert "不是" in st["reason"] and "重试" in st["reason"], st["reason"]
        assert st["port"] == 9223, "避让已落到 9223"
        assert launched["n"] == 0, "漂移端口上不许拉起任何进程"
    finally:
        p.undo()


def test_ensure_browser_binding_unknown_target():
    st = at.ensure_browser_binding({}, "wecom")
    assert st["ok"] is False and st["bound"] is False
    assert "未知" in st["reason"]


# ---------- 4. 自定义 CDP 软件（接管） ----------
def test_bind_custom_cdp_attached_and_down():
    _reset_cfg(9222, cdp_target="custom", custom_cdp_port=9333)
    p = _Patches()
    try:
        p.set(at, "cdp_up", lambda port: True)
        p.set(at, "_cdp_ws_ok", lambda port: True)
        p.set(at, "list_targets", lambda port: [])
        p.set(at, "window_list_summary", lambda port: ["a", "b"])
        p.set(at, "windows_signature", lambda tg: "sig")
        p.set(at, "_logw", lambda msg: None)
        cfg = config_store.load_config()
        st = at.bind_custom_cdp(cfg, 9333)
        assert st["bound"] is True and st["target"] == "custom", st
        assert "attached" in st["reason"] and st["windows"] == 2, st
        assert cfg["wecom"]["cdp_target"] == "custom"
        assert cfg["wecom"]["custom_cdp_port"] == 9333
        assert cfg["wecom"]["cdp_port"] == 9333
        # 端口没活：只探测说不成，不装死
        p.set(at, "cdp_up", lambda port: False)
        st3 = at.bind_custom_cdp(config_store.load_config(), 9999)
        assert st3["bound"] is False
        assert "--remote-debugging-port" in st3["reason"], st3["reason"]
    finally:
        p.undo()


def test_bind_custom_cdp_non_numeric_port():
    st = at.bind_custom_cdp({}, "abc")
    assert st["bound"] is False and "数字" in st["reason"], st


# ---------- 5. 配置口径 ----------
def test_config_cdp_port_custom_routing():
    from core.config_store import cdp_port
    base = {"wecom": {"cdp_port": 9222}}
    assert cdp_port(base) == 9222, "默认（wecom 目标）口径不动"
    custom = {"wecom": {"cdp_port": 9222, "cdp_target": "custom", "custom_cdp_port": 9333}}
    assert cdp_port(custom) == 9333, "custom 目标必须以 custom_cdp_port 为准"
    broken = {"wecom": {"cdp_port": 9444, "cdp_target": "custom", "custom_cdp_port": 0}}
    assert cdp_port(broken) == 9444, "custom 端口非法时落回常规 cdp_port 口径"
    edge = {"wecom": {"cdp_port": 9555, "cdp_target": "edge", "custom_cdp_port": 9333}}
    assert cdp_port(edge) == 9555, "浏览器目标仍走 cdp_port（避让写回的那个）"


def test_make_bind_merger_merges_new_target_keys():
    from core.config_store import make_bind_merger, update_config
    snap = {"wecom": {"cdp_port": 9333, "cdp_target": "chrome",
                     "custom_cdp_port": 9333, "bound_pid": 555, "bound_sig": "s",
                     "auto_launch": False}}
    update_config(make_bind_merger(snap))
    got = json.load(io.open(config_store.config_path(), encoding="utf-8"))["wecom"]
    assert got["cdp_target"] == "chrome", got
    assert got["custom_cdp_port"] == 9333, got
    assert got["cdp_port"] == 9333 and got["bound_pid"] == 555, got


# ---------- 6. 路由分派 ----------
def test_api_wecom_bind_dispatch_by_target():
    import backend
    client = backend.app.test_client()
    p = _Patches()
    calls = {"edge": 0, "custom": 0, "wecom": 0}

    def fake_edge_bind(cfg, target, force=False, allow_kill=False, max_wait_s=15.0):
        calls["edge"] += 1
        cfg.setdefault("wecom", {})["cdp_target"] = target   # 真实现就地写 delta，桩要同形
        return {"bound": True, "changed": True, "windows": 2, "reason": "launched",
                "target": target, "target_label": "Microsoft Edge"}

    def fake_custom_bind(cfg, port, max_wait_s=0.0):
        calls["custom"] += 1
        w = cfg.setdefault("wecom", {})
        w["cdp_target"] = "custom"; w["custom_cdp_port"] = port; w["cdp_port"] = port
        return {"bound": True, "changed": True, "windows": 1,
                "reason": "attached to existing CDP endpoint",
                "target": "custom", "target_label": "自定义 CDP 软件", "port": port}

    try:
        import bridge.windows as win
        _reset_cfg(9222)
        p.set(win, "_agent_busy", lambda: False)   # confirm_kill 守卫桩：测试环境视为空闲
        p.set(wb, "ensure_browser_binding", fake_edge_bind)
        p.set(wb, "bind_custom_cdp", fake_custom_bind)
        # custom 缺端口 → 400
        r = client.post("/api/wecom/bind", json={"target": "custom"})
        assert r.status_code == 400, r.status_code
        assert calls["custom"] == 0, "守卫之后还在跑接管，等于没拦"
        # custom 带端口 → 200 + 接管路径
        r = client.post("/api/wecom/bind", json={"target": "custom", "custom_port": 9333})
        assert r.status_code == 200 and r.get_json()["bound"] is True, r.get_json()
        assert calls["custom"] == 1
        # edge → 浏览器路径（force/allow_kill 语义透传）
        r = client.post("/api/wecom/bind", json={"target": "edge", "confirm_kill": True})
        assert r.status_code == 200 and r.get_json()["target"] == "edge", r.get_json()
        assert calls["edge"] == 1
        got = json.load(io.open(config_store.config_path(), encoding="utf-8"))["wecom"]
        assert got["cdp_target"] == "edge", f"按键合并要落盘最后一次绑定的目标：{got}"
        assert got["cdp_port"] == 9333, got   # 最后一次**写端口**的是 custom 接管（9333）
    finally:
        p.undo()


def test_api_wecom_open_endpoint_never_kills():
    """v1.0.725「打开」按钮端点：浏览器目标恒 allow_kill=False/force=False（绝不杀/重启）；
    custom 目标是接管模式——不拉起，只回当前连通状态。"""
    import backend
    client = backend.app.test_client()
    p = _Patches()
    calls = {}
    try:
        def fake_browser(cfg, target, force=False, allow_kill=False, max_wait_s=15.0):
            calls["force"] = force
            calls["allow_kill"] = allow_kill
            return {"bound": True, "changed": False, "windows": 2, "reason": "already up",
                    "target": "edge", "target_label": "Microsoft Edge", "exe_path": FAKE_EXE,
                    "port": 9222, "pid": 1, "auto_launch": True}
        _reset_cfg(9222, cdp_target="edge")
        p.set(at, "ensure_browser_binding", fake_browser)
        r = client.post("/api/wecom/open", json={})
        assert r.status_code == 200, r.status_code
        d = r.get_json()
        assert d["bound"] is True and d["target"] == "edge", d
        assert calls["allow_kill"] is False, "「打开」绝不允许杀/重启（allow_kill 必须 False）"
        assert calls["force"] is False, "「打开」不能触发重启路径（force 必须 False）"
        # custom 目标：不拉起，只探测 + 说明接管语义
        _reset_cfg(9333, cdp_target="custom", custom_cdp_port=9333)
        p.set(at, "cdp_up", lambda port: False)
        r2 = client.post("/api/wecom/open", json={})
        d2 = r2.get_json()
        assert d2["bound"] is False and "接管" in d2["reason"], d2
    finally:
        p.undo()


def test_browser_detect_endpoint():
    import backend
    client = backend.app.test_client()
    p = _Patches()
    try:
        p.set(wb, "detect_browsers", lambda: {"edge": FAKE_EXE, "chrome": None})
        _reset_cfg(9222)
        d = client.get("/api/wecom/browser_detect").get_json()
        assert d["browsers"] == {"edge": FAKE_EXE, "chrome": None}, d
        assert d["target"] == "wecom" and d["target_label"] == "企业微信", d
        _reset_cfg(9222, cdp_target="edge")
        d2 = client.get("/api/wecom/browser_detect").get_json()
        assert d2["target"] == "edge" and d2["target_label"] == "Microsoft Edge", d2
    finally:
        p.undo()


# ---------- 7. bind_status 目标感知 ----------
def test_bind_status_target_fields():
    import routes.wecom as rw
    p = _Patches()
    try:
        p.set(wb, "find_browser_pids", lambda t: [])
        p.set(wb, "cdp_up", lambda port: False)
        p.set(wb, "_cdp_ws_ok", lambda port: False)
        p.set(wb, "find_wecom_pids", lambda: [])
        rw._bs_cache_invalidate()
        d = rw.bind_status({"wecom": {"cdp_target": "chrome", "cdp_port": 9222}})
        assert d["target"] == "chrome" and d["target_label"] == "Google Chrome", d
        assert d["target_short"] == "Chrome", d
        assert d["bound"] is False and d["needs_kill"] is False, d
        rw._bs_cache_invalidate()
        d2 = rw.bind_status({"wecom": {"cdp_target": "custom", "custom_cdp_port": 9333}})
        assert d2["port"] == 9333 and d2["target"] == "custom", d2
        assert d2["target_label"] == "自定义 CDP 软件", d2
        rw._bs_cache_invalidate()
        d3 = rw.bind_status({"wecom": {}})
        assert d3["target"] == "wecom" and d3["target_short"] == "企业微信", d3
    finally:
        p.undo()


# ---------- 8. 前端契约锁（防 UI 回归） ----------
def test_frontend_contracts():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    html = io.open(os.path.join(root, "index.html"), encoding="utf-8").read()
    assert "选择绑定目标（企业微信 / 浏览器 / CDP 软件）" in html, "设置页按钮没换成选择绑定目标"
    assert 'set-group">CDP 绑定' in html, "设置分组没改成 CDP 绑定"
    js = io.open(os.path.join(root, "js", "05-connectors-status.js"), encoding="utf-8").read()
    assert "openBindPicker" in js and "runBindTarget" in js
    assert '"/api/wecom/browser_detect"' in js, "弹窗数据源端点没接"
    assert 'w.target_label' in js and 'w.target_short' in js, "refreshStatus 没做目标感知"
    assert 'doBind(btnArg)' in js or "function doBind(btnArg)" in js, "doBind 签名不能变（两处调用点）"
    # v1.0.725：顶栏按钮恒定「CDP 绑定」+ 绑定成功后「打开」按钮（前端接线 /api/wecom/open）
    # + 选择弹窗当前绑定目标亮色高亮
    assert ">CDP 绑定<" in html, "顶栏按钮文案没统一成「CDP 绑定」（用户要求不再叫「绑定 Edge」）"
    assert 'id="btnOpenTarget"' in html, "绑定成功后的「打开」按钮缺失"
    assert "openTargetApp" in js and '"/api/wecom/open"' in js, "「打开」按钮没接后端端点"
    assert "当前绑定" in js, "弹窗里当前绑定目标没高亮标注"
    # v1.0.727（用户：「属于cdp的浏览器没有高亮」）：高亮要一眼可见——3px 实线边框
    # + 实心绿底白字「✓ 当前绑定」chip（浅绿 chip 在白底卡片上不够跳）
    assert "3px solid var(--ok)" in js, "当前目标边框没加粗到 3px"
    assert "✓ 当前绑定" in js, "「当前绑定」chip 没改实心绿标"


if __name__ == "__main__":
    test_normalize_target_unknown_falls_back_to_wecom()
    test_detect_browsers_registry_fallback()
    test_cdp_target_kind_five_states()
    test_pick_cdp_port_reuses_target_own_port()
    test_pick_cdp_port_avoids_foreign_and_takes_next_free()
    test_pick_cdp_port_all_foreign_gives_up()
    test_ensure_browser_binding_launches_with_cdp_flags()
    test_ensure_browser_binding_running_without_cdp_needs_kill()
    test_ensure_browser_binding_allow_kill_only_enumerated_pids()
    test_ensure_browser_binding_gives_up_when_kill_incomplete()
    test_debug_session_pids_classifies_by_command_line()
    test_ensure_browser_binding_kills_only_debug_session()
    test_ensure_browser_binding_daily_browser_not_touched()
    test_ensure_browser_binding_startup_never_launches()
    test_ensure_browser_binding_startup_daily_browser_no_launch()
    test_ensure_browser_binding_exepath_cache_not_cross_target()
    test_ensure_browser_binding_foreign_drift_refuses()
    test_ensure_browser_binding_unknown_target()
    test_bind_custom_cdp_attached_and_down()
    test_bind_custom_cdp_non_numeric_port()
    test_config_cdp_port_custom_routing()
    test_make_bind_merger_merges_new_target_keys()
    test_api_wecom_bind_dispatch_by_target()
    test_api_wecom_open_endpoint_never_kills()
    test_browser_detect_endpoint()
    test_bind_status_target_fields()
    test_frontend_contracts()
    print("\n== 全部通过：多目标 CDP 绑定 ==")
