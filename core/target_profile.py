# -*- coding: utf-8 -*-
"""core.target_profile — 目标应用适配器 `TargetProfile` 的 schema 与加载器。

方向 1（docs/roadmap.md 阶段 B，★最高优先）的第一批：**只定义 schema + 内置企微
profile + 加载/校验**，纯新增、不改任何现有代码路径（零行为变化，企微 100% 不退化）。

为什么内置 profile 用 Python 字面量而不是独立 .json 数据文件：PyInstaller 单文件
打包只自动收源码，独立 .json 需在 spec 里额外声明 datas，漏声明时冻结版运行期读不到
profile 会直接坏。内置 wecom 用字面量随代码一起打包，最稳；`load_profile_from_json`
保留「从外部 JSON 载入」能力，供第四批接第二个目标应用（内部 OA / 其它 Electron 客户端）。

内置值必须与当前散落的企微硬编码**逐字一致**（见 tests/unit/test_target_profile.py
的「快照锁」）——本批次先冻结事实，后续批次再逐处把硬编码替换成「读 profile」。
"""
import json
import os
import re

SCHEMA_VERSION = 1

# profile 必须包含的顶层键（缺任何一个都视为非法 profile）。
_REQUIRED_TOP_KEYS = ("schema_version", "id", "label", "kind",
                      "process", "cdp", "ua_fingerprint", "window", "tools")
# kind 目前支持的值：electron（企微这类 Electron 内核客户端）/ browser（Edge/Chrome）/ custom。
_ALLOWED_KINDS = ("electron", "browser", "custom")


class ProfileError(ValueError):
    """profile 非法（缺键 / 类型错 / 值不在允许集）——统一抛这个，调用方好区分。"""


def _require(cond, msg):
    if not cond:
        raise ProfileError(msg)


def validate_profile(p):
    """校验一份 profile dict 的结构与取值，通过则原样返回。

    只做**结构/取值域**校验（不联网、不碰文件系统）：
      · 顶层必需键齐全；schema_version == 1；kind 在允许集内；
      · process.exe_names / launch_args_template / cdp.default_port /
        ua_fingerprint.positive / window.title_keywords / tools 形态正确。
    非法时抛 ProfileError（带具体哪个键的问题）。
    """
    if not isinstance(p, dict):
        raise ProfileError("profile 必须是 dict")
    for k in _REQUIRED_TOP_KEYS:
        _require(k in p, "缺顶层键：%s" % k)
    _require(p.get("schema_version") == SCHEMA_VERSION,
             "schema_version 必须是 %d（收到 %r）" % (SCHEMA_VERSION, p.get("schema_version")))
    _require(isinstance(p.get("id"), str) and p["id"].strip(), "id 必须是非空字符串")
    _require(isinstance(p.get("label"), str) and p["label"].strip(), "label 必须是非空字符串")
    _require(p.get("kind") in _ALLOWED_KINDS,
             "kind 必须在 %s（收到 %r）" % (_ALLOWED_KINDS, p.get("kind")))

    proc = p.get("process")
    _require(isinstance(proc, dict), "process 必须是 dict")
    exe_names = proc.get("exe_names")
    _require(isinstance(exe_names, list) and exe_names and
             all(isinstance(x, str) and x for x in exe_names),
             "process.exe_names 必须是非空字符串列表")
    _require(isinstance(proc.get("launch_args_template"), str) and
             "{port}" in proc["launch_args_template"],
             "process.launch_args_template 必须是含 {port} 占位的字符串")

    cdp = p.get("cdp")
    _require(isinstance(cdp, dict), "cdp 必须是 dict")
    _require(isinstance(cdp.get("default_port"), int) and cdp["default_port"] > 0,
             "cdp.default_port 必须是正整数")
    _require(isinstance(cdp.get("port_scan_span"), int) and cdp["port_scan_span"] >= 0,
             "cdp.port_scan_span 必须是非负整数")

    ua = p.get("ua_fingerprint")
    _require(isinstance(ua, dict), "ua_fingerprint 必须是 dict")
    for k in ("positive", "negative_chromium"):
        vals = ua.get(k)
        _require(isinstance(vals, list) and all(isinstance(x, str) and x for x in vals),
                 "ua_fingerprint.%s 必须是字符串列表" % k)
    _require(ua.get("positive"), "ua_fingerprint.positive 不能为空（否则无法识别目标）")

    win = p.get("window")
    _require(isinstance(win, dict), "window 必须是 dict")
    tk = win.get("title_keywords")
    _require(isinstance(tk, list) and all(isinstance(x, str) and x for x in tk),
             "window.title_keywords 必须是字符串列表")

    tools = p.get("tools")
    _require(isinstance(tools, list) and all(isinstance(t, str) and t for t in tools),
             "tools 必须是字符串列表")
    return p


def load_profile_from_dict(p):
    """从 dict 载入并校验一份 profile（返回同一 dict，便于原地使用）。"""
    return validate_profile(dict(p))


def load_profile_from_json(path):
    """从 JSON 文件载入并校验一份 profile（供外部目标应用，如内部 OA）。"""
    _require(isinstance(path, str) and os.path.isfile(path),
             "profile 文件不存在：%r" % path)
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as e:
            raise ProfileError("profile JSON 解析失败：%s" % e)
    return load_profile_from_dict(data)


# ---------- 内置 profile：企微（值与当前散落硬编码逐字一致，快照锁见测试） ----------

# 与 bridge/wecom_proc.py 的 exe 探测候选一致（%LOCALAPPDATA% 保留原始形态，
# 由 expanded_exe_candidates() 在用时展开，避免打包期把本机路径写死进 profile）。
WECOM_PROFILE = {
    "schema_version": SCHEMA_VERSION,
    "id": "wecom",
    "label": "企业微信",          # == app_targets.TARGET_LABELS["wecom"]
    "short_label": "企业微信",     # == app_targets.TARGET_SHORT["wecom"]
    "kind": "electron",
    "process": {
        "exe_names": ["wxwork.exe", "wxworkweb.exe"],   # == wecom_proc 的 tasklist/winapi 枚举名
        "main_exe_name": "wxwork.exe",
        "exe_candidates": [
            r"C:\Program Files (x86)\WXWork\WXWork.exe",
            r"C:\Program Files\WXWork\WXWork.exe",
            r"%LOCALAPPDATA%\WXWork\WXWork.exe",
        ],
        "registry_keywords": ["企业微信", "WeCom", "WXWork"],  # 注册表卸载项 DisplayName 匹配
        # 注册表 InstallLocation 下主 exe 的安装子目录（"" = 直接在 InstallLocation 根下）。
        # detect_wecom_exe 的注册表回退按此逐个拼 <InstallLocation>/<subdir>/<main_exe>。
        "registry_install_subdirs": ["", "WXWork"],
        "launch_args_template": "--remote-debugging-port={port}",
    },
    "cdp": {
        "default_port": 9222,          # == cdp_client.DEFAULT_PORT
        "port_scan_span": 8,           # == app_targets.CDP_PORT_SCAN_SPAN
    },
    # == cdp_client.cdp_is_wecom / app_targets.cdp_target_kind 的 UA 特征判定：
    #    positive 命中 = 明确是企微；negative_chromium 命中 = 明确是 Chromium 系（非企微）。
    "ua_fingerprint": {
        "positive": ["wxwork", "wecom"],
        "negative_chromium": ["chrome", "edg", "headless"],
    },
    "window": {
        "title_keywords": ["企业微信", "WeCom", "WXWork"],
    },
    "selector_strategy": "wecom",      # 当前选择器/元素扫描口径（后续批次参数化）
    "download": {
        "channel": "cdp",              # 企微走 CDP/JS 真实下载通道
    },
    # 可用工具子集：名字含 "wecom" 的注入工具（dispatch._wecom_tools_allowed 的口径）。
    "tools": [
        "capture_wecom_window", "list_wecom_windows", "send_wecom_webhook",
        "click_wecom_element", "type_wecom_text", "read_wecom_page",
        "read_wecom_fields", "select_wecom_option", "scroll_wecom_window",
        "wait_wecom_page_load", "wecom_run_js", "list_wecom_links",
        "upload_wecom_file", "type_wecom_document", "close_wecom_page",
        "download_wecom_attachment",
        # OS 级发消息（registry 末尾，与 send_wecom_webhook 同名族的独立工具）
        "send_wecom_message",
    ],
}

# 内置 profile 注册表（第四批接第二个目标应用时在此加一条，或走 load_profile_from_json）。
BUILTIN_PROFILES = {"wecom": WECOM_PROFILE}


def get_profile(target="wecom"):
    """按目标 id 取内置 profile（未登记的目标抛 ProfileError）。

    第四批接第二个应用时：要么在 BUILTIN_PROFILES 加一条内置，要么先
    `BUILTIN_PROFILES[id] = load_profile_from_json(path)` 再取。"""
    tid = str(target or "wecom").strip().lower()
    prof = BUILTIN_PROFILES.get(tid)
    _require(prof is not None, "未登记的目标 profile：%r（可用：%s）"
             % (tid, ", ".join(sorted(BUILTIN_PROFILES))))
    return prof


def ua_matches(profile, blob):
    """用 profile 的 UA 指纹判定一段 /json/version 响应体是谁。

    返回 "target"（命中 positive）/ "chromium"（命中 negative_chromium）/
    "unknown"（有内容但特征不明）/ None（空响应）。
    口径与 cdp_is_wecom / cdp_target_kind 一致——第四批把那两处换成读本函数，
    判定结果保持逐字一致（企微零变化）。"""
    s = str(blob or "").lower()
    if not s.strip():
        return None
    for w in profile.get("ua_fingerprint", {}).get("positive", []):
        if w.lower() in s:
            return "target"
    for w in profile.get("ua_fingerprint", {}).get("negative_chromium", []):
        if w.lower() in s:
            return "chromium"
    return "unknown"


def expanded_exe_candidates(profile):
    """把 profile 里 %LOCALAPPDATA% 等环境变量展开成绝对路径（打包期不写死本机路径）。"""
    out = []
    for c in profile.get("process", {}).get("exe_candidates", []):
        if c:
            out.append(os.path.expandvars(str(c)))
    return out
