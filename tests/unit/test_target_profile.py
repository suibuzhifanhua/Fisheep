# -*- coding: utf-8 -*-
"""tests/unit/test_target_profile.py — TargetProfile 的**快照锁 + 行为锁**（方向 1）。

批次1/2 —— 内置 wecom profile 的值必须与散落企微硬编码**逐字一致**（冻结事实）；
批次3   —— 业务代码（cdp_is_wecom / cdp_target_kind / wecom_proc / sys_prompts）改读
           profile，旧「源码含硬编码」漂移锁**反转**为「源码不得含硬编码、必须读 profile」
           （改回硬编码必红），并补行为+覆盖锁喂给 buildlib 增量覆盖率门槛。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import target_profile as TP  # noqa: E402


# ---------- ① schema 校验 ----------

def test_builtin_wecom_profile_is_valid():
    TP.validate_profile(TP.WECOM_PROFILE)
    assert TP.get_profile("wecom") is TP.WECOM_PROFILE
    assert TP.get_profile("WECOM") is TP.WECOM_PROFILE, "target id 应大小写不敏感"


def test_get_profile_unknown_raises():
    with pytest.raises(TP.ProfileError):
        TP.get_profile("not_registered")


def test_invalid_profile_rejected():
    def _mutate(key_path, value):
        p = json.loads(json.dumps(TP.WECOM_PROFILE))
        node = p
        *rest, last = key_path
        for k in rest:
            node = node[k]
        node[last] = value
        return p
    # 逐条踩 validate_profile 的拒绝分支
    with pytest.raises(TP.ProfileError):
        TP.validate_profile(["not", "a", "dict"])
    for key_path, value in [
        (("schema_version",), 999),
        (("id",), "   "),
        (("label",), ""),
        (("kind",), "unknown_kind"),
        (("process", "exe_names"), []),
        (("process", "exe_names"), ["ok.exe", ""]),
        (("process", "launch_args_template"), "no-placeholder"),
        (("cdp", "default_port"), 0),
        (("cdp", "port_scan_span"), -1),
        (("ua_fingerprint", "positive"), []),
        (("window", "title_keywords"), "not-a-list"),
        (("tools",), "not-a-list"),
    ]:
        with pytest.raises(TP.ProfileError):
            TP.validate_profile(_mutate(key_path, value))
    missing = json.loads(json.dumps(TP.WECOM_PROFILE))
    del missing["ua_fingerprint"]
    with pytest.raises(TP.ProfileError):
        TP.validate_profile(missing)


def test_expanded_exe_candidates():
    """%LOCALAPPDATA% 等占位展开成绝对路径，空值过滤。"""
    prof = json.loads(json.dumps(TP.WECOM_PROFILE))
    prof["process"]["exe_candidates"].append("")
    prof["process"]["exe_candidates"].append("%LOCALAPPDATA%\\WXWork\\WXWork.exe")
    out = TP.expanded_exe_candidates(prof)
    assert all("LOCALAPPDATA" not in c for c in out), "占位变量必须展开"
    assert out[-1].endswith(os.path.join("WXWork", "WXWork.exe"))
    assert "" not in out


def test_load_from_dict_round_trip():
    p = TP.load_profile_from_dict(TP.WECOM_PROFILE)
    assert p == TP.WECOM_PROFILE


def test_load_from_json_round_trip(tmp_path):
    fp = tmp_path / "oa.json"
    fp.write_text(json.dumps(TP.WECOM_PROFILE, ensure_ascii=False), encoding="utf-8")
    assert TP.load_profile_from_json(str(fp)) == TP.WECOM_PROFILE
    with pytest.raises(TP.ProfileError):
        TP.load_profile_from_json(str(tmp_path / "missing.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(TP.ProfileError):
        TP.load_profile_from_json(str(bad))


# ---------- ② 快照锁：内置值 == 当前散落硬编码 ----------

def test_snapshot_cdp_port_and_span():
    from wecom_bridge import cdp_client as CC
    from wecom_bridge import app_targets as AT
    assert TP.WECOM_PROFILE["cdp"]["default_port"] == CC.DEFAULT_PORT
    assert TP.WECOM_PROFILE["cdp"]["port_scan_span"] == AT.CDP_PORT_SCAN_SPAN


def test_snapshot_target_labels():
    from wecom_bridge import app_targets as AT
    assert TP.WECOM_PROFILE["label"] == AT.TARGET_LABELS["wecom"]
    assert TP.WECOM_PROFILE["short_label"] == AT.TARGET_SHORT["wecom"]


def test_snapshot_ua_fingerprint_matches_source():
    """UA 指纹唯一事实源 = profile；cdp_is_wecom / cdp_target_kind 必须读 profile。

    v1.0.762（方向1 批次3）反转：旧锁断言两处源码「仍含 5 个特征串」（硬编码时代）；
    改读 profile 后，源码**不得再含**硬编码 UA 判定，且必须调
    target_profile.ua_matches + get_profile("wecom")。改回硬编码 → 本锁必红。"""
    assert TP.WECOM_PROFILE["ua_fingerprint"]["positive"] == ["wxwork", "wecom"]
    assert TP.WECOM_PROFILE["ua_fingerprint"]["negative_chromium"] == ["chrome", "edg", "headless"]

    def _body(relpath, marker, span=1600):
        src = io.open(os.path.join(ROOT, relpath), encoding="utf-8").read()
        assert marker in src, "%s 缺少 %s" % (relpath, marker)
        return src.split(marker, 1)[1][:span]

    # cdp_is_wecom：整函数 profile 化（只判"是不是本目标"），不得含任何硬编码 UA 判定
    cc_body = _body("bridge/cdp_client.py", "def cdp_is_wecom")
    assert "ua_matches" in cc_body and "get_profile" in cc_body and '"wecom"' in cc_body, \
        "cdp_is_wecom 未改读 target_profile.ua_matches/get_profile(\"wecom\")（勿回退硬编码）"
    for w in ('"wxwork" in', '"wecom" in', '"chrome" in', '"edg" in', '"headless" in'):
        assert w not in cc_body, "cdp_is_wecom 出现硬编码 UA 判定 %r（应全读 profile）" % w
    # cdp_target_kind：仅"是不是本目标" profile 化；edge/chrome 分支属 BROWSERS 边界保留
    at_body = _body("bridge/app_targets.py", "def cdp_target_kind")
    assert "ua_matches" in at_body and "get_profile" in at_body and '"wecom"' in at_body, \
        "cdp_target_kind 未改读 target_profile.ua_matches/get_profile(\"wecom\")（勿回退硬编码）"
    for w in ('"wxwork" in', '"wecom" in'):
        assert w not in at_body, "cdp_target_kind 硬编码 wecom 判定 %r（应读 profile）" % w


def test_snapshot_tool_subset_matches_registry():
    """profile 的工具子集 == registry 里名字含 'wecom' 的全部工具（dispatch 门控口径）。"""
    from agent_tools import registry as RG
    actual = {t["function"]["name"] for t in RG.TOOLS if "wecom" in t["function"]["name"]}
    assert set(TP.WECOM_PROFILE["tools"]) == actual, \
        "profile.tools 与 registry 里 *wecom* 工具漂移：差集=%s" % (
            set(TP.WECOM_PROFILE["tools"]) ^ actual)


def test_snapshot_process_exe_names():
    """process.exe_names 唯一事实源 = profile；wecom_proc 进程枚举必须读 profile。

    v1.0.762（方向1 批次3）反转：wecom_proc 不再硬编码 "wxwork.exe"/"wxworkweb.exe"、
    候选路径或注册表关键词元组，改读 profile.process.*。源码出现这些字面量 = 回退硬编码 = 必红。"""
    assert TP.WECOM_PROFILE["process"]["exe_names"] == ["wxwork.exe", "wxworkweb.exe"]
    assert TP.WECOM_PROFILE["process"]["main_exe_name"] == "wxwork.exe"
    wp_src = io.open(os.path.join(ROOT, "bridge", "wecom_proc.py"),
                     encoding="utf-8").read()
    # wecom_proc 必须读 profile
    assert "get_profile" in wp_src, "wecom_proc 未改读 target_profile（应读 profile，勿回退硬编码）"
    # 不得再硬编码进程名 / 候选路径 / 注册表关键词元组
    for lit in ('"wxwork.exe"', '"wxworkweb.exe"',
                "C:\\Program Files (x86)\\WXWork\\WXWork.exe",
                '("企业微信", "WeCom", "WXWork")'):
        assert lit not in wp_src, "wecom_proc 出现硬编码 %r（应读 profile）" % lit


# ---------- ③ 行为一致：ua_matches 与 cdp_is_wecom 三态对齐 ----------

@pytest.mark.parametrize("blob,expected", [
    ({"Browser": "WXWork/4.1"}, "target"),
    ({"userAgent": "wxwork electron"}, "target"),
    ({"Browser": "Chrome/120 Safari"}, "chromium"),
    ({"Browser": "Edg/119"}, "chromium"),
    ({"note": "some opaque gateway 200"}, "unknown"),
    ("", None),
    (None, None),
])
def test_ua_matches_tri_state(blob, expected):
    assert TP.ua_matches(TP.WECOM_PROFILE, blob) == expected


def test_ua_matches_precedence_positive_before_negative():
    """positive 优先于 negative（与 cdp_is_wecom「先判企微再判 Chromium」同序）。

    同时含企微与 Chromium 特征时判 target——保证口径与 cdp_is_wecom 逐字一致，
    配合 test_snapshot_ua_fingerprint_matches_source（断言 profile 与源码同用这 5 串）
    即锁死「换 profile 后 UA 判定零变化」。"""
    assert TP.ua_matches(TP.WECOM_PROFILE, "wxwork ... chrome/120") == "target"
    assert TP.ua_matches(TP.WECOM_PROFILE, "wecom electron headless") == "target"


# ---------- ④ 批次3 行为+覆盖锁：业务代码真的读 profile（改回硬编码必红） ----------
# 方向1 批次3：把散落企微常量替换成读 profile。这里锁两件事——
#   a) 行为等价：profile 值逐字流向业务函数（改回硬编码 / 漏读 profile 必红）；
#   b) 覆盖：detect_wecom_exe / _find_wecom_pids_tasklist / _relaunch_with_cdp 的新增行
#      被执行（喂给 buildlib 增量覆盖率门槛，避免"抽成读 profile 却从不测"）。

def test_detect_wecom_exe_candidate_from_profile(tmp_path, monkeypatch):
    """候选路径读 profile.process.exe_candidates（expanded_exe_candidates）。"""
    import wecom_bridge.wecom_proc as wp
    exe = tmp_path / "CandWX" / "CandWX.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    proc = TP.WECOM_PROFILE["process"]
    monkeypatch.setitem(proc, "exe_candidates", [str(exe)])
    assert wp.detect_wecom_exe() == str(exe)


def test_detect_wecom_exe_registry_fallback_reads_profile(tmp_path, monkeypatch):
    """注册表回退读 profile：registry_keywords 命中 → InstallLocation 下按
    registry_install_subdirs × main_exe_name 逐个探。改回硬编码关键词/exe/子目录必红。"""
    import wecom_bridge.wecom_proc as wp
    import winreg
    exe = tmp_path / "RegWX" / "SentinelWX.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    proc = TP.WECOM_PROFILE["process"]
    monkeypatch.setitem(proc, "exe_candidates", [str(tmp_path / "nope" / "x.exe")])  # 不存在→走注册表
    monkeypatch.setitem(proc, "main_exe_name", "SentinelWX.exe")
    monkeypatch.setitem(proc, "registry_keywords", ["SENTINEL_APP"])
    monkeypatch.setitem(proc, "registry_install_subdirs", ["", "RegWX"])
    # 伪造注册表卸载项：DisplayName 含 SENTINEL_APP，InstallLocation = tmp_path
    monkeypatch.setattr(winreg, "OpenKey",
                        lambda a, b: "UNINSTALL_KEY" if "Uninstall" in b else "SUBKEY")
    monkeypatch.setattr(winreg, "QueryInfoKey", lambda key: (1,))
    monkeypatch.setattr(winreg, "EnumKey", lambda key, i: "sub0")

    def _qve(sk, name):
        if name == "DisplayName":
            return ("SENTINEL_APP 1.0", 1)
        if name == "InstallLocation":
            return (str(tmp_path), 1)
        raise OSError("no value")
    monkeypatch.setattr(winreg, "QueryValueEx", _qve)
    assert wp.detect_wecom_exe() == str(exe)


def test_find_wecom_pids_tasklist_reads_profile(monkeypatch):
    """tasklist 回退读 profile.process.main_exe_name（过滤参数 + PID 比较）。"""
    import wecom_bridge.wecom_proc as wp
    proc = TP.WECOM_PROFILE["process"]
    monkeypatch.setitem(proc, "main_exe_name", "fakewx.exe")
    captured = {}

    class _R:
        stdout = ('"fakewx.exe","777","Console","1","1,234 K"\n'
                  '"Other.exe","888","Console","1","999 K"\n')

    def _run(args, **kw):
        captured["args"] = args
        return _R()
    monkeypatch.setattr(wp, "_run_hidden", _run)
    assert wp._find_wecom_pids_tasklist() == [777]
    assert "imagename eq fakewx.exe" in " ".join(captured["args"]), captured["args"]


def test_relaunch_launch_args_from_profile(monkeypatch):
    """_relaunch_with_cdp 启动参数读 profile.process.launch_args_template（sentinel 必红）。"""
    import wecom_bridge.wecom_proc as wp
    import tempfile
    ws = tempfile.mkdtemp(prefix="reln_")
    exe = os.path.join(ws, "SentinelWX.exe")
    io.open(exe, "w", encoding="utf-8").close()
    proc = TP.WECOM_PROFILE["process"]
    monkeypatch.setitem(proc, "launch_args_template", "--sentinel-cdp-flag={port}")
    captured = {}

    def _popen(argv, creationflags=0):
        captured["argv"] = argv
    monkeypatch.setattr(wp.subprocess, "Popen", _popen)
    monkeypatch.setattr(wp, "find_wecom_all_pids", lambda: set())   # 无进程可杀→直接 Popen
    monkeypatch.setattr(wp, "_minimize_wecom_when_ready", lambda *a, **k: None)
    ok, why = wp._relaunch_with_cdp(exe, 9331)
    assert ok is True, why
    assert captured["argv"][0] == exe
    assert "--sentinel-cdp-flag=9331" in " ".join(captured["argv"]), captured["argv"]


# ---------- ⑤ 批次4：第二目标应用（外部 profile 最小链路，只加 profile 不改业务代码） ----------
# 方向1 批次4：证明「新增目标应用不改 Python 业务代码，只加一个 profile」。
# 第二目标应用 = 同为 Electron 内核的**内部 OA**（非企微、非 edge/chrome 浏览器）。
# CDP 传输层（list_targets / read_page / click_element）本就与具体应用无关——第二应用只需：
#   ① 一份**外部** profile（`load_profile_from_json` 载入）提供 端口 / UA / label / 工具；
#   ② 复用既有的 custom-CDP 绑定（app-agnostic）+ app-agnostic 读页 / 点击。
# 全程零业务代码改动——第二应用的唯一事实源就是这份 profile。
_SECOND_APP_PROFILE = {
    "schema_version": 1,
    "id": "oa_demo",
    "label": "内部OA系统",
    "short_label": "内部OA",
    "kind": "electron",
    "process": {
        "exe_names": ["oademo.exe", "oademo_web.exe"],
        "main_exe_name": "oademo.exe",
        "exe_candidates": ["%LOCALAPPDATA%\\OaDemo\\OaDemo.exe"],
        "registry_keywords": ["内部OA", "OaDemo"],
        "registry_install_subdirs": ["", "OaDemo"],
        "launch_args_template": "--remote-debugging-port={port}",
    },
    "cdp": {"default_port": 9331, "port_scan_span": 8},
    "ua_fingerprint": {
        "positive": ["oademo", "oademo-internal-shell"],
        "negative_chromium": ["chrome", "edg", "headless"],
    },
    "window": {"title_keywords": ["内部OA", "OaDemo"]},
    "tools": ["read_oa_page", "list_oa_windows", "click_oa_element"],
}


def _load_second_app(tmp_path):
    """把第二应用 profile 落成 JSON 文件并走 load_profile_from_json（真实外部载入路径）。"""
    fp = tmp_path / "oa_demo.json"
    fp.write_text(json.dumps(_SECOND_APP_PROFILE, ensure_ascii=False), encoding="utf-8")
    return TP.load_profile_from_json(str(fp))


def test_second_app_profile_loads_from_json(tmp_path, monkeypatch):
    """外部 profile 经 load_profile_from_json 载入并登记后 get_profile 可用（大小写不敏感）。"""
    prof = _load_second_app(tmp_path)
    assert prof["id"] == "oa_demo" and prof["cdp"]["default_port"] == 9331
    monkeypatch.setitem(TP.BUILTIN_PROFILES, "oa_demo", prof)
    assert TP.get_profile("oa_demo") is prof
    assert TP.get_profile("OA_DEMO") is prof, "target id 应大小写不敏感"


def test_second_app_ua_identity_is_profile_driven(tmp_path):
    """UA 身份判定按 profile 走：第二应用的 UA 由它自己的 profile 认；企微不会误认它。

    这是批次3 的回报——UA 指纹已抽成 profile，第二应用无需改 cdp_is_wecom/cdp_target_kind。"""
    prof = _load_second_app(tmp_path)
    # 第二应用自己的 UA → target
    assert TP.ua_matches(prof, "Mozilla/5.0 oademo-internal-shell/1.0 (OaDemo)") == "target"
    assert TP.ua_matches(prof, "oademo/2.3") == "target"
    # 企微 UA ≠ 第二应用
    assert TP.ua_matches(prof, "wxwork/4.1 wecom") != "target"
    # 纯 Chromium → chromium
    assert TP.ua_matches(prof, "Mozilla/5.0 Chrome/120") == "chromium"
    # 反向：企微 profile 不应把第二应用误判成企微（否则绑定会绑错家）
    assert TP.ua_matches(TP.get_profile("wecom"), "oademo-internal-shell/1.0") != "target"


def test_second_app_minimal_chain_bind_read_click(tmp_path, monkeypatch):
    """最小链路 绑定→读页→点击，全部只靠 profile（端口/UA/label）+ app-agnostic 传输层。

    换目标应用不改任何 Python 业务代码——第二应用的唯一事实源是这份外部 profile。
    链路跑在 profile 的 default_port（9331，与企微 9222 不同）上，点击落在第二应用的 WS 上。"""
    import bridge.app_targets as AT
    import bridge.read as RD
    import core.approval_dom as AD
    prof = _load_second_app(tmp_path)
    monkeypatch.setitem(TP.BUILTIN_PROFILES, "oa_demo", prof)
    port = prof["cdp"]["default_port"]                     # 9331：链路跑在 profile 的端口
    assert port != TP.get_profile("wecom")["cdp"]["default_port"], "第二应用端口应区别于企微"
    ws_url = "ws://127.0.0.1:%d/devtools/page/DEMO-PAGE" % port
    page = {"id": "DEMO-PAGE", "type": "page",
            "url": "http://10.0.0.7:8080/oa/leave-apply",
            "title": "内部OA-请假申请", "webSocketDebuggerUrl": ws_url}

    # —— 绑定：接管第二应用已带调试端口的 CDP（custom 通道，app-agnostic，绝不碰进程）——
    cfg = {"wecom": {}}
    monkeypatch.setattr(AT, "cdp_up", lambda p=port: True)
    monkeypatch.setattr(AT, "_cdp_ws_ok", lambda p=port: True)
    monkeypatch.setattr(AT, "list_targets", lambda p=port: [page])
    monkeypatch.setattr(AT, "window_list_summary", lambda p=port: [page["title"]])
    monkeypatch.setattr(AT, "windows_signature", lambda t: "sig-oa")
    st = AT.bind_custom_cdp(cfg, port)
    assert st["bound"] is True, st
    assert cfg["wecom"]["custom_cdp_port"] == port

    # —— 读页：read_page（mock CDP 数据边界：取页 / DOM 就绪 / 正文稳定 / 读数据）——
    monkeypatch.setattr(RD, "_require_page",
                        lambda pick, p, with_windows=False: (None, ws_url, page))
    monkeypatch.setattr(RD, "_wait_dom_ready", lambda *a, **k: None)
    monkeypatch.setattr(RD, "_wait_body_stable", lambda *a, **k: None)
    monkeypatch.setattr(RD, "_read_page_data",
                        lambda ws, pg: {"title": page["title"], "text": "请假申请单",
                                        "fields": [{"label": "事由", "value": "", "required": True}]})
    r = RD.read_page("内部OA", port)
    assert r.get("url") == page["url"], r
    assert any(f.get("label") == "事由" for f in r.get("fields", [])), r

    # —— 点击：click_element（mock CDP evaluate + 后置等待；断言点击落在第二应用的 WS 上）——
    eval_calls = []
    monkeypatch.setattr(AD, "guard_click", lambda *a, **k: None)       # 审批闸放行（中性动作）
    monkeypatch.setattr(RD, "_cdp_eval", lambda ws, expr, **k: "")       # 状态指纹
    monkeypatch.setattr(RD, "_cdp_eval_retry",
                        lambda ws, expr, **k: (eval_calls.append(ws) or json.dumps(
                            {"ok": True, "method": "text-fallback", "tag": "button",
                             "x": 10, "y": 10, "text": "提交"})))
    monkeypatch.setattr(RD, "_json_ok_result",
                        lambda raw: json.loads(raw) if isinstance(raw, str) else raw)
    monkeypatch.setattr(RD, "_post_click_wait", lambda *a, **k: (None, None, True))  # JS 已生效
    monkeypatch.setattr(RD, "list_targets", lambda p=port, **k: [page])
    monkeypatch.setattr(RD, "new_window_baseline", lambda *a, **k: {})
    c = RD.click_element("内部OA", "提交", port)
    assert isinstance(c, dict) and c.get("ok") is True, c
    assert eval_calls and eval_calls[0] == ws_url, "点击必须落在第二应用的 WS 上"


def test_second_app_defined_only_in_profile_not_business_code():
    """验收锁：新增第二目标应用不改任何业务代码——它的标识只存在于 profile（本测试），
    绝不出现在 bridge/agent/agent_tools/routes/services/core 等业务模块里。
    若有人为第二应用写了硬编码业务分支，本锁必红（「只加 profile，不改业务代码」）。"""
    import glob
    sentinels = ("oademo", "oa_demo")
    hits = []
    for d in ("bridge", "agent", "agent_tools", "routes", "services", "core"):
        dpath = os.path.join(ROOT, d)
        if not os.path.isdir(dpath):
            continue
        for py in glob.glob(os.path.join(dpath, "*.py")):
            if os.path.basename(py).startswith("test_"):
                continue
            src = io.open(py, encoding="utf-8", errors="ignore").read()
            for s in sentinels:
                if s in src:
                    hits.append("%s → %r" % (os.path.relpath(py, ROOT), s))
    assert not hits, "第二应用标识泄漏进业务代码（应只存在于 profile）：%s" % hits
