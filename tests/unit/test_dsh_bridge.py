# -*- coding: utf-8 -*-
"""v1.0.298：PC 接管通道（deepseek-harness / dsh）单元/控制流测试。

原则：
- 不真装 dsh、不真跑 dsh（沙箱装全局 npm 包慢且污染用户环境）：
  subprocess.run / subprocess.Popen / shutil.which / _node_info / _which_dsh
  全部 monkeypatch，只测命令构造、参数传递、超时/退出码分支与降级路径。
- Windows npm shim 是 .cmd → 重点验证「shim 解析出 JS 入口 → node 直跑」
  （cmd /c 的引号语义会拆碎含空格路径+任务，实证弃用，只留无空格回退）。
- 非 win32 优雅降级：IS_WIN=False 时全部返回结构化错误不崩。
运行：python tests/unit/test_dsh_bridge.py
"""
import os
import sys
import shutil
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.result import is_err, err_msg  # noqa: E402
import agent_tools.dsh_bridge as dsh_bridge
import core
from agent_tools import build_agent_tools, dispatch_tool

TMP = tempfile.mkdtemp(prefix="dsh_bridge_test_")


def _cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


CFG_BASE = {"enabled_skills": None, "skill_overrides": {}, "connectors": {}, "wecom": {}}


def _patch_dsh_bin(path):
    """把 dsh 探测结果固定为 path（或 None），入口完整性一并钉成"存在"
    （v1.0.712 起 _which_dsh 命中还要过 _entry_present——假 shim 不算已装），
    返回恢复函数。"""
    orig = dsh_bridge._which_dsh
    dsh_bridge._which_dsh = lambda: path
    orig_e = dsh_bridge._entry_present
    dsh_bridge._entry_present = lambda b: bool(path)

    def _u():
        setattr(dsh_bridge, "_which_dsh", orig)
        setattr(dsh_bridge, "_entry_present", orig_e)
    return _u


def _patch_node(ok=True, version="v22.22.2"):
    orig = dsh_bridge._node_info
    dsh_bridge._node_info = lambda: {"ok": ok, "version": version,
                                     "detail": "" if ok else f"{version} 过低"}
    return lambda: setattr(dsh_bridge, "_node_info", orig)


# ---------- 装配 ----------
def test_build_agent_tools_dsh_mounted():
    cfg = dict(CFG_BASE)
    tools, prompt = build_agent_tools(cfg)
    names = {t["function"]["name"] for t in tools}
    expect = {"dsh_status", "dsh_prepare", "pc_takeover"}
    missing = expect - names
    assert not missing, f"dsh 接管工具未全挂载：{missing}"
    assert "pc_takeover" in prompt and "dsh_status" in prompt, "pc-ops prompt 未注入接管通道"
    print("PASS build_agent_tools：3 个 dsh 工具 + 接管通道 prompt 全挂载")


# ---------- 环境探测 ----------
def test_node_info_parse():
    orig = dsh_bridge._run_captured
    # v1.0.612：_node_info 先解析运行时路径（PATH → 软件便携版），没有 node 就直接短路，
    # 所以这里要把定位结果钉住，测的才是"版本串解析"本身。
    from core import portable as _p
    orig_find = _p.find_node
    _p.find_node = lambda: r"C:\node\node.exe"
    # 直接喂不同版本串测解析
    cases = [
        ("v22.22.2\n", True),
        ("v24.1.0\n", True),
        ("v22.18.3\n", False),
        ("v20.10.0\n", False),
    ]
    for out, want in cases:
        dsh_bridge._run_captured = lambda a, timeout, env=None, _o=out: {"exit_code": 0, "stdout": _o, "stderr": ""}
        r = dsh_bridge._node_info()
        assert r["ok"] is want, f"node 版本解析错：{out!r} → {r}"
        assert r["path"] == r"C:\node\node.exe", r      # 用的是解析出来的绝对路径
    dsh_bridge._run_captured = orig
    # 异常分支
    def _boom(args, timeout, env=None):
        raise RuntimeError("node 不可用")
    dsh_bridge._run_captured = _boom
    r = dsh_bridge._node_info()
    assert r["ok"] is False and "无法执行 node" in r["detail"], r
    dsh_bridge._run_captured = orig
    _p.find_node = orig_find
    print("PASS _node_info：版本边界（22.18/22.22/24.x）+ 异常分支")


def test_which_dsh_candidates():
    orig = dsh_bridge.shutil.which
    # v1.0.612 起 _which_dsh 还会查软件自装的便携 node 目录 —— 本机真装过一份，
    # 不钉住它"全缺失"这一档就永远测不到（测试必须是假的运行环境）。
    from core import portable as _p
    orig_find, orig_exe = _p.find_node, _p.portable_exe
    _p.find_node = lambda: None
    _p.portable_exe = lambda name: None
    hits = {"dsh.cmd": "C:\\Users\\t\\AppData\\Roaming\\npm\\dsh.cmd",
            "dsh": None, "dsh.ps1": None}
    dsh_bridge.shutil.which = lambda c: hits.get(c)
    assert dsh_bridge._which_dsh() == hits["dsh.cmd"], "应优先解析 dsh.cmd"
    hits["dsh.cmd"] = None
    hits["dsh"] = "C:\\dsh.exe"
    assert dsh_bridge._which_dsh() == "C:\\dsh.exe", "cmd 缺失应回退 dsh"
    hits["dsh"] = None
    assert dsh_bridge._which_dsh() is None, "全缺失应返回 None"
    _p.find_node, _p.portable_exe = orig_find, orig_exe
    dsh_bridge.shutil.which = orig
    print("PASS _which_dsh：dsh.cmd 优先 → dsh 回退 → None")


def test_dsh_check_ready():
    u1, u2 = _patch_node(True), _patch_dsh_bin("C:\\npm\\dsh.cmd")
    try:
        r = dsh_bridge.dsh_check({"dsh": {"base_url": "http://localhost:1234/v1",
                                          "api_key": "sk-x"}})
        assert r["ok"] and r["ready"] and not r["hints"], r
        assert r["base_url"] == "http://localhost:1234/v1" and r["has_api_key"], r
    finally:
        u1(); u2()
    print("PASS dsh_check：node+dsh+凭据齐备 → ready，无 hints")


def test_dsh_check_missing_all():
    u1, u2 = _patch_node(False, "v20.0.0"), _patch_dsh_bin(None)
    # v1.0.425：隔离环境变量——dsh_check 会读 DEEPSEEK_API_KEY/BASE_URL，
    # 开发机 shell 残留这些变量时凭据 hint 消失（实测 3 hints→2）
    saved_env = {k: os.environ.pop(k, None)
                 for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")}
    try:
        r = dsh_bridge.dsh_check({})
        assert not r["ready"], r
        assert len(r["hints"]) == 3, f"应给 3 条修复提示（node/安装/凭据）：{r['hints']}"
    finally:
        u1(); u2()
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
    print("PASS dsh_check：全缺 → 3 条 hints 全给出")


# ---------- 安装 ----------
def test_prepare_already_installed():
    u1, u2 = _patch_node(True), _patch_dsh_bin("C:\\npm\\dsh.cmd")
    orig = dsh_bridge._run_captured
    dsh_bridge._run_captured = lambda a, t: (_ for _ in ()).throw(AssertionError("不应触发 npm"))
    try:
        r = dsh_bridge.dsh_prepare({})
        assert r["ok"] and r.get("already_installed"), r
    finally:
        u1(); u2(); setattr(dsh_bridge, "_run_captured", orig)
    print("PASS dsh_prepare：已安装 → 直接返回，不跑 npm")


def test_prepare_mirror_first():
    """v1.0.608：第 1 次就用 npmmirror 国内镜像（官方源常"连得上但极慢"，
    旧顺序会让一键安装先卡满一次超时才轮到镜像）。"""
    state = {"n": 0}

    def _fake_which():
        state["n"] += 1
        return None if state["n"] == 1 else r"C:\\npm\\dsh.cmd"  # 装前无、装后有

    calls = []
    orig = dsh_bridge._run_captured

    def _fake_run(args, timeout, env=None):
        calls.append(list(args))
        return {"exit_code": 0, "stdout": "added 1 package", "stderr": ""}

    u1 = _patch_node(True)
    from core import portable as _p
    orig_npm = _p.find_npm
    _p.find_npm = lambda: r"C:/node/npm.cmd"      # v1.0.612：npm 走绝对路径（便携版不在 PATH）
    orig_which = dsh_bridge._which_dsh
    dsh_bridge._which_dsh, dsh_bridge._run_captured = _fake_which, _fake_run
    orig_bf = dsh_bridge._find_offline_bundle     # v1.0.712：钉死"无离线包"，测的才是在线顺序
    dsh_bridge._find_offline_bundle = lambda: None
    try:
        r = dsh_bridge.dsh_prepare({})
        assert r["ok"] and r.get("installed") and r.get("used_mirror"), r
        assert calls == [[r"C:/node/npm.cmd", "install", "-g", dsh_bridge.DSH_SPEC,
                          "--registry", dsh_bridge.MIRROR]], calls
        assert any("npmmirror.com" in x for x in calls[0]), calls
    finally:
        u1(); _p.find_npm = orig_npm
        setattr(dsh_bridge, "_which_dsh", orig_which); setattr(dsh_bridge, "_run_captured", orig)
        setattr(dsh_bridge, "_find_offline_bundle", orig_bf)
    print("PASS dsh_prepare：第 1 次即走 npmmirror 国内镜像，命令构造正确")


def test_prepare_official_fallback():
    """镜像真失败时才回落官方源（attempt=2、used_mirror=False）。"""
    state = {"n": 0}

    def _fake_which():
        # 调用序：#1 安装前检查(无) → npm×2 → #2 安装后检查(有)
        state["n"] += 1
        return None if state["n"] <= 1 else r"C:\\npm\\dsh.cmd"

    calls = []
    orig = dsh_bridge._run_captured

    def _fake_run(args, timeout, env=None):
        calls.append(list(args))
        return ({"exit_code": 1, "stdout": "", "stderr": "ETIMEDOUT npmmirror"}
                if len(calls) == 1
                else {"exit_code": 0, "stdout": "ok", "stderr": ""})

    u1 = _patch_node(True)
    from core import portable as _p
    orig_npm = _p.find_npm
    _p.find_npm = lambda: r"C:/node/npm.cmd"
    orig_which = dsh_bridge._which_dsh
    dsh_bridge._which_dsh, dsh_bridge._run_captured = _fake_which, _fake_run
    orig_bf = dsh_bridge._find_offline_bundle
    dsh_bridge._find_offline_bundle = lambda: None
    try:
        r = dsh_bridge.dsh_prepare({})
        assert r["ok"] and r["attempt"] == 2 and not r.get("used_mirror"), r
        assert all(c[0] == r"C:/node/npm.cmd" for c in calls), calls
        assert "--registry" in calls[0] and "--registry" not in calls[1], calls
        # v1.0.714：两轮回落都锁版本（裸名 * 范围匹配不到预发布 → ETARGET）
        assert calls[0][3] == dsh_bridge.DSH_SPEC and calls[1][3] == dsh_bridge.DSH_SPEC, calls
    finally:
        u1(); _p.find_npm = orig_npm
        setattr(dsh_bridge, "_which_dsh", orig_which); setattr(dsh_bridge, "_run_captured", orig)
        setattr(dsh_bridge, "_find_offline_bundle", orig_bf)
    print("PASS dsh_prepare：镜像失败 → 官方源兜底成功")


def test_prepare_spec_pins_version():
    """v1.0.714：绝不裸名安装——registry 上 dsh 全版本都是预发布（-rc/-alpha），
    裸名 `*` 范围匹配不到任何版本 → ETARGET（上游把 latest 换成 0.2.2-rc.1 后全新安装
    必挂；本机已装的树走 already_installed 分支不重跑 npm 才一直正常）。
    锁版本必须与离线包同源（离线包就是用同一 pin 装出的树打的）。"""
    assert dsh_bridge.DSH_VERSION_PIN, "版本 pin 不能为空"
    assert dsh_bridge.DSH_SPEC == "@deepseek-ai/dsh@" + dsh_bridge.DSH_VERSION_PIN
    bundle = dsh_bridge._find_offline_bundle()
    if bundle:
        meta = dsh_bridge._read_bundle_meta(bundle)
        assert meta.get("dsh_version") == dsh_bridge.DSH_VERSION_PIN, meta
    print("PASS dsh_prepare：安装规格锁版本（与离线包同源）")


def test_prepare_node_missing():
    u1 = _patch_node(False, "v20.0.0")
    orig = dsh_bridge._run_captured
    dsh_bridge._run_captured = lambda a, t: (_ for _ in ()).throw(AssertionError("node 不达标不该跑 npm"))
    try:
        r = dsh_bridge.dsh_prepare({})
        assert not r["ok"] and "Node 环境不满足" in r.get("error", ""), r
    finally:
        u1(); setattr(dsh_bridge, "_run_captured", orig)
    print("PASS dsh_prepare：node 不达标 → 拒绝安装并说明原因")


# ---------- 一次性委托 ----------
def test_run_empty_task():
    r = dsh_bridge.dsh_run({}, "   ")
    assert is_err(r), r
    print("PASS dsh_run：空 task 直接拒绝")


def test_run_not_installed():
    u = _patch_dsh_bin(None)
    try:
        r = dsh_bridge.dsh_run({}, "把 C:\\proj 的测试跑一遍")
        assert is_err(r) and "dsh_prepare" in err_msg(r), r
    finally:
        u()
    print("PASS dsh_run：未安装 → 指引 dsh_prepare")


def test_run_bad_cwd():
    u = _patch_dsh_bin("C:\\npm\\dsh.cmd")
    try:
        r = dsh_bridge.dsh_run({}, "task", cwd="C:\\definitely_not_exist_9999")
        assert is_err(r) and "工作目录不存在" in err_msg(r), r
    finally:
        u()
    print("PASS dsh_run：cwd 不存在 → 结构化错误")


class _FakeProc:
    def __init__(self, out_b, err_b, rc):
        self._out, self._err, self._rc = out_b, err_b, rc
        self.pid = 4321
        self.returncode = rc
        self.killed = False

    def communicate(self, timeout=None):
        if isinstance(self._out, Exception):
            raise self._out
        self.returncode = self._rc
        return self._out, self._err

    def kill(self):
        self.killed = True


def test_resolve_shim_entry():
    """npm shim 解析：真实格式提取绝对 .js 入口；% 变量/不存在路径全排除。"""
    js_real = os.path.join(TMP, "real_entry.js")
    with open(js_real, "w") as f:
        f.write("console.log(1)")
    js_abs = js_real.replace(os.sep, "\\")
    shim = os.path.join(TMP, "fake.dsh.cmd")
    with open(shim, "w", encoding="utf-8") as f:
        f.write('@ECHO OFF\r\nSET "_node_module_folder=%~dp0..\\node_modules"\r\n'
                f'FOR %%F IN ("{js_abs}") DO @SET "_script=%%~sF"\r\n'
                '& "%_prog%" "%_script%" %*\r\n')
    assert dsh_bridge._resolve_shim_entry(shim) == js_real, "应解析出存在的绝对 .js 入口"
    # 只有 % 变量路径 → None
    shim2 = os.path.join(TMP, "fake2.cmd")
    with open(shim2, "w", encoding="utf-8") as f:
        f.write('@ECHO OFF\r\nFOR %%F IN ("%~dp0..\\x.js") DO @SET s=%%~sF\r\n')
    assert dsh_bridge._resolve_shim_entry(shim2) is None, "% 变量路径不应被采信"
    # .js 路径不存在 → None
    shim3 = os.path.join(TMP, "fake3.cmd")
    with open(shim3, "w", encoding="utf-8") as f:
        f.write('FOR %%F IN ("C:\\no_such_dir_xyz\\a.js") DO @SET s=%%~sF\r\n')
    assert dsh_bridge._resolve_shim_entry(shim3) is None, "不存在的入口应判 None"
    # shim 文件不存在 → None
    assert dsh_bridge._resolve_shim_entry(os.path.join(TMP, "nope.cmd")) is None
    print("PASS _resolve_shim_entry：真实格式提取 + 三类拒绝")


def test_run_success():
    # 构造真实格式的假 shim + 假 JS 入口，覆盖「shim 解析 → node 直跑」全链路
    js_entry = os.path.join(TMP, "dsh_entry.js").replace(os.sep, "\\")
    with open(js_entry, "w") as f:
        f.write("// stub entry")
    bin_path = os.path.join(TMP, "App Data", "npm", "dsh.cmd").replace(os.sep, "\\")
    os.makedirs(os.path.dirname(bin_path), exist_ok=True)
    with open(bin_path, "w", encoding="utf-8") as f:
        f.write(f'@ECHO OFF\r\nFOR %%F IN ("{js_entry}") DO @SET "_script=%%~sF"\r\n')

    cfg = dict(CFG_BASE, dsh={"base_url": "http://localhost:1234/v1", "api_key": "sk-test"})
    captured = {}
    fake = _FakeProc(b"all tests fixed, 12/12 green", b"dsh: reasoning: step1\nstep2", 0)
    orig_popen = subprocess.Popen

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured.update({k: kw.get(k) for k in ("cwd", "env", "creationflags")})
        return fake

    u1 = _patch_dsh_bin(bin_path)
    orig_root = core.paths.workspace_root
    core.paths.workspace_root = lambda: TMP
    dsh_bridge.subprocess.Popen = _fake_popen
    try:
        r = dsh_bridge.dsh_run(cfg, "把 C:\\proj 的测试跑一遍并修复失败用例")
        assert r["ok"] and r["answer"] == "all tests fixed, 12/12 green", r
        assert r["reasoning_tail"].startswith("dsh: reasoning:"), r
        # node 直跑（不经 cmd）：[node, shim 解析出的 js 入口, ...args]
        cmd = captured["cmd"]
        assert "node" in os.path.basename(cmd[0]).lower(), f"应直跑 node：{cmd}"
        assert os.path.normpath(cmd[1]) == os.path.normpath(js_entry), cmd
        assert cmd[-3:] == ["--profile", "headless", "把 C:\\proj 的测试跑一遍并修复失败用例"], cmd
        # env 注入 + 默认 cwd + 进程组
        assert captured["env"]["DEEPSEEK_BASE_URL"] == "http://localhost:1234/v1", captured["env"]
        assert captured["env"]["DEEPSEEK_API_KEY"] == "sk-test", captured["env"]
        assert captured["cwd"] == TMP, captured
        # v1.0.299：进程组（超时杀整树）+ 无窗口（不弹黑框）
        if dsh_bridge.IS_WIN:
            assert captured["creationflags"] == (0x00000200 | 0x08000000), captured
        else:
            assert captured["creationflags"] == 0, captured
        # 转写落盘 产出/dsh/
        assert r.get("transcript") and os.path.exists(r["transcript"]), r
        assert os.sep + "产出" in r["transcript"] and "dsh" in r["transcript"], r
        content = open(r["transcript"], encoding="utf-8").read()
        assert "修复失败用例" in content and "12/12 green" in content, content[:200]
    finally:
        u1()
        setattr(dsh_bridge, "subprocess", subprocess)
        dsh_bridge.subprocess.Popen = orig_popen
        setattr(dsh_bridge, "workspace_root", orig_root)
    print("PASS dsh_run：成功路径（shim 解析→node 直跑/env 注入/进程组/转写存档）")


def test_run_timeout():
    bin_path = "C:\\npm\\dsh.cmd"
    killed = []
    fake = _FakeProc(subprocess.TimeoutExpired("dsh", 60), b"", 0)
    orig_popen, orig_kill = subprocess.Popen, dsh_bridge._kill_tree

    def _fake_popen(cmd, **kw):
        return fake

    u1 = _patch_dsh_bin(bin_path)
    orig_root = dsh_bridge.workspace_root
    dsh_bridge.workspace_root = lambda: TMP
    dsh_bridge.subprocess.Popen = _fake_popen
    dsh_bridge._kill_tree = lambda p: killed.append(p.pid)
    try:
        r = dsh_bridge.dsh_run({"dsh": {}}, "长任务", timeout=61)
        assert not r["ok"] and r.get("timed_out"), r
        assert "超时" in r.get("error", ""), r
        assert killed == [4321], f"超时必须杀进程树（taskkill /T）：{killed}"
    finally:
        u1()
        setattr(dsh_bridge, "subprocess", subprocess)
        dsh_bridge.subprocess.Popen = orig_popen
        setattr(dsh_bridge, "_kill_tree", orig_kill)
        setattr(dsh_bridge, "workspace_root", orig_root)
    print("PASS dsh_run：超时 → 进程树强杀 + 结构化超时错误")


def test_run_exit1():
    bin_path = "C:\\npm\\dsh.cmd"
    fake = _FakeProc(b"", b"dsh: auth_error: invalid api key\nretry exhausted", 1)
    orig_popen = subprocess.Popen

    def _fake_popen(cmd, **kw):
        return fake

    u1 = _patch_dsh_bin(bin_path)
    orig_root = dsh_bridge.workspace_root
    dsh_bridge.workspace_root = lambda: TMP
    dsh_bridge.subprocess.Popen = _fake_popen
    try:
        r = dsh_bridge.dsh_run({"dsh": {}}, "task")
        assert not r["ok"] and r.get("exit_code") == 1, r
        assert "invalid api key" in r.get("error", ""), r
        assert r.get("hint") and "dsh_status" in r["hint"], r
    finally:
        u1()
        setattr(dsh_bridge, "subprocess", subprocess)
        dsh_bridge.subprocess.Popen = orig_popen
        setattr(dsh_bridge, "workspace_root", orig_root)
    print("PASS dsh_run：退出码 1 → 错误含 stderr 尾部 + 诊断 hint")


def test_non_win32_degrade():
    orig = dsh_bridge.IS_WIN
    dsh_bridge.IS_WIN = False
    try:
        r1 = dsh_bridge.dsh_check({})
        r2 = dsh_bridge.dsh_prepare({})
        r3 = dsh_bridge.dsh_run({}, "task")
        for r in (r1, r2, r3):
            assert is_err(r) and "仅支持 Windows" in err_msg(r), r
    finally:
        setattr(dsh_bridge, "IS_WIN", orig)
    print("PASS 非 win32：三个入口全部优雅降级")


# ---------- v1.0.706：dsh 复用软件默认模型 ----------
CFG_MODELS = {"dsh": {}, "models": [
    # 与真实配置同形：id=内部标识（m_xxxx）、name=显示名、model=发线模型名
    {"id": "m_abc123", "name": "Local 9B", "model": "local-9b",
     "enabled": True, "is_default": True,
     "base_url": "http://127.0.0.1:1234/v1", "api_key": "sk-local"},
]}


def _strip_env():
    return {k: os.environ.pop(k, None)
            for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")}


def _restore_env(saved):
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def test_resolve_creds_order():
    """v1.0.706 三级解析：显式 dsh 段 → DEEPSEEK_* env → 软件默认模型 → None。
    model_id 仅回退档非空（供 --patch 对齐 dsh 模型名）。"""
    saved = _strip_env()
    try:
        r = dsh_bridge._resolve_creds({"dsh": {"base_url": "http://x:1/v1", "api_key": "sk-a"},
                                      "models": CFG_MODELS["models"]})
        assert r == ("http://x:1/v1", "sk-a", None, "dsh"), r
        os.environ["DEEPSEEK_BASE_URL"] = "http://env:2/v1"
        r = dsh_bridge._resolve_creds({"models": CFG_MODELS["models"]})
        assert r == ("http://env:2/v1", None, None, "env"), r
        del os.environ["DEEPSEEK_BASE_URL"]
        r = dsh_bridge._resolve_creds({"models": CFG_MODELS["models"]})
        assert r == ("http://127.0.0.1:1234/v1", "sk-local", "local-9b",
                    "default_model"), r
        assert dsh_bridge._resolve_creds({}) == (None, None, None, None)
        # 完整端点风格（.../v1/chat/completions）→ 剥后缀，避免 pi-ai 层拼双路径
        base, key, mid = dsh_bridge._default_model_creds({"models": [
            {"id": "m1", "enabled": True, "is_default": True,
             "base_url": "http://h:1/v1/chat/completions", "api_key": "k"}]})
        assert (base, key, mid) == ("http://h:1/v1", "k", "m1"), (base, key, mid)
        # 发线名口径：model 字段 > name > id（与 llm_client payload 同口径）
        _, _, mid2 = dsh_bridge._default_model_creds({"models": [
            {"id": "m_xxxx", "name": "显示名", "model": "wire-name",
             "enabled": True, "is_default": True,
             "base_url": "http://h:1/v1", "api_key": "k"}]})
        assert mid2 == "wire-name", mid2
    finally:
        _restore_env(saved)
    print("PASS _resolve_creds：三级解析顺序 + model_id 仅回退档携带")


def test_check_fallback_default_model():
    """dsh_check：dsh 段留空 → 默认模型回退即 ready，带来源与模型名。"""
    u1, u2 = _patch_node(True), _patch_dsh_bin("C:\\npm\\dsh.cmd")
    saved = _strip_env()
    try:
        r = dsh_bridge.dsh_check(CFG_MODELS)
        assert r["ok"] and r["ready"], r
        assert r["model_source"] == "default_model" and r["model_id"] == "local-9b", r
        assert r["base_url"] == "http://127.0.0.1:1234/v1" and r["has_api_key"], r
        assert not r["hints"], r
    finally:
        u1(); u2(); _restore_env(saved)
    print("PASS dsh_check：默认模型回退 → ready（无需单填 dsh 凭据）")


def test_check_no_creds_hint_mentions_default_model():
    """三级全缺 → 凭据 hint 首选「设置 → 模型」配默认模型（新推荐路径）。"""
    u1, u2 = _patch_node(True), _patch_dsh_bin("C:\\npm\\dsh.cmd")
    saved = _strip_env()
    try:
        r = dsh_bridge.dsh_check({"models": []})
        assert not r["ready"] and r["model_source"] is None, r
        cred_hints = [h for h in r["hints"] if "模型凭据" in h]
        assert cred_hints and "设置 → 模型" in cred_hints[0], r["hints"]
    finally:
        u1(); u2(); _restore_env(saved)
    print("PASS dsh_check：全缺 → 凭据 hint 首选配置默认模型")


def test_write_model_patch_content_and_escape():
    """patch 覆盖层格式对齐 dsh-base：insert 行列表 + 按 id 寻址；
    模型 id 含 YAML 单引号时双写转义；同内容幂等。"""
    orig_root = core.paths.workspace_root
    core.paths.workspace_root = lambda: TMP
    try:
        p1 = dsh_bridge._write_model_patch("local-9b")
        assert p1 and os.path.isfile(p1), p1
        content = open(p1, encoding="utf-8").read()
        assert "- insert:" in content and "agent-default-model" in content, content
        assert "model: 'local-9b'" in content, content
        p2 = dsh_bridge._write_model_patch("a'b")
        content2 = open(p2, encoding="utf-8").read()
        assert "model: 'a''b'" in content2, content2
        assert dsh_bridge._write_model_patch("a'b") == p2, "同内容应幂等"
    finally:
        core.paths.workspace_root = orig_root
    print("PASS _write_model_patch：格式/转义/幂等")


def test_run_fallback_injects_patch():
    """dsh_run 回退路径：env 注入默认模型端点/凭据 + --patch 对齐模型名。"""
    saved = _strip_env()
    fake = _FakeProc(b"done", b"", 0)
    captured = {}
    orig_popen = subprocess.Popen

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return fake

    u1 = _patch_dsh_bin("C:\\npm\\dsh.cmd")   # 不存在的 .cmd → shim 解析 None → direct
    orig_root = core.paths.workspace_root
    core.paths.workspace_root = lambda: TMP
    dsh_bridge.subprocess.Popen = _fake_popen
    try:
        r = dsh_bridge.dsh_run(dict(CFG_MODELS), "task")
        assert r["ok"], r
        cmd = captured["cmd"]
        assert "--patch" in cmd, cmd
        pidx = cmd.index("--patch")
        assert cmd[pidx - 1] == "headless" and cmd[pidx + 2] == "task", cmd
        patch_file = cmd[pidx + 1]
        assert os.path.exists(patch_file), patch_file
        content = open(patch_file, encoding="utf-8").read()
        assert "agent-default-model" in content and "local-9b" in content, content
        assert captured["env"]["DEEPSEEK_BASE_URL"] == "http://127.0.0.1:1234/v1"
        assert captured["env"]["DEEPSEEK_API_KEY"] == "sk-local"
    finally:
        u1()
        dsh_bridge.subprocess.Popen = orig_popen
        core.paths.workspace_root = orig_root
        _restore_env(saved)
    print("PASS dsh_run：默认模型回退 → env 注入 + --patch 模型名对齐")


def test_run_explicit_cfg_no_patch():
    """dsh 段显式配置 → 尊重用户选择，不注入 --patch（端点/模型名如其所填）。"""
    fake = _FakeProc(b"ok", b"", 0)
    captured = {}
    orig_popen = subprocess.Popen

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return fake

    saved = _strip_env()
    u1 = _patch_dsh_bin("C:\\npm\\dsh.cmd")
    orig_root = core.paths.workspace_root
    core.paths.workspace_root = lambda: TMP
    dsh_bridge.subprocess.Popen = _fake_popen
    cfg = {"dsh": {"base_url": "http://x:1/v1", "api_key": "sk-a"},
           "models": CFG_MODELS["models"]}
    try:
        r = dsh_bridge.dsh_run(cfg, "task")
        assert r["ok"], r
        assert "--patch" not in captured["cmd"], captured["cmd"]
        assert captured["cmd"][-3:] == ["--profile", "headless", "task"], captured["cmd"]
        assert captured["env"]["DEEPSEEK_BASE_URL"] == "http://x:1/v1"
    finally:
        u1()
        dsh_bridge.subprocess.Popen = orig_popen
        core.paths.workspace_root = orig_root
        _restore_env(saved)
    print("PASS dsh_run：显式 dsh 段 → 不注 --patch，端点如其所填")


# ---------- v1.0.712 离线安装包 ----------

def _mk_fake_bundle(dirpath, dsh_ver="9.9.9-test"):
    """造一个小假离线包（meta + 入口 JS + shim），返回 zip 路径。"""
    import json as _json
    import zipfile as _zf
    p = os.path.join(dirpath, "dsh-offline.zip")
    with _zf.ZipFile(p, "w") as z:
        z.writestr("node_modules/@deepseek-ai/dsh/lib/bin.js", "console.log('dsh');")
        z.writestr("node_modules/@deepseek-ai/dsh/package.json",
                   _json.dumps({"name": "@deepseek-ai/dsh", "version": dsh_ver}))
        z.writestr("dsh.cmd", "@ECHO off\r\nSET dp0=%~dp0\r\n\"%dp0%\\node_modules"
                              "\\@deepseek-ai\\dsh\\lib\\bin.js\" %*\r\n")
        z.writestr(dsh_bridge.BUNDLE_META,
                   _json.dumps({"dsh_version": dsh_ver, "file_count": 3}))
    return p


def _new_tmp(tag):
    d = tempfile.mkdtemp(prefix=f"dsh_offline_{tag}_")
    return d


def test_install_from_bundle_extracts(tmp=None):
    """离线包解压到便携 node 根：入口 JS 就位即 ok（shim 是 %dp0% 相对定位，免改路径）。"""
    d = _new_tmp("extract")
    root = os.path.join(d, "node_root")
    os.makedirs(root)
    bundle = _mk_fake_bundle(d)
    try:
        r = dsh_bridge._install_from_bundle(bundle, dest_root=root)
        assert r["ok"] is True and r["extracted"] >= 3, r
        entry = os.path.join(root, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
        assert os.path.isfile(entry)
        assert os.path.isfile(os.path.join(root, "dsh.cmd"))
        assert os.path.isfile(os.path.join(root, "node_modules", "@deepseek-ai",
                                            "dsh", "package.json"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS _install_from_bundle：解压到 node 根，入口就位")


def test_install_from_bundle_long_paths(tmp=None):
    r"""真实树里有 176 个文件路径超 260 字符（最长 279，dsh-experimental-inspector 子树）——
    没开长路径的 Windows 上普通 stat/open 直接失败，解压端必须走 \\?\（本机实测
    279 字符路径 \\?\ 可读写，普通 API 不可）。"""
    d = _new_tmp("longpath")
    root = os.path.join(d, "root")
    os.makedirs(root)
    deep = os.path.join(root, "node_modules")
    for i in range(24):
        deep = os.path.join(deep, "a_long_dir_%02d" % i)
    leaf = os.path.join(deep, "leaf_file_content.txt")
    assert len(leaf) > 260, f"测试前提失效（路径仅 {len(leaf)} 字符）"
    bundle = os.path.join(d, "b.zip")
    import zipfile as _zf
    with _zf.ZipFile(bundle, "w") as z:
        z.writestr(os.path.relpath(leaf, root), "long-path payload")
        z.writestr("short.txt", "s")
        z.writestr("node_modules/@deepseek-ai/dsh/lib/bin.js", "x")
    try:
        r = dsh_bridge._install_from_bundle(bundle, dest_root=root)
        assert r["ok"] is True and r["extracted"] == 3, r
        with open(dsh_bridge._ext(leaf), "rb") as f:
            assert f.read() == b"long-path payload"
        # 路径穿越防御：zip 里带 .. 的条目不得落出 dest
        evil = os.path.join(d, "evil.zip")
        with _zf.ZipFile(evil, "w") as z:
            z.writestr("../escape.txt", "no")
            z.writestr("node_modules/@deepseek-ai/dsh/lib/bin.js", "x")
        r2 = dsh_bridge._install_from_bundle(evil, dest_root=root)
        assert not os.path.exists(os.path.join(d, "escape.txt"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS _install_from_bundle：>260 字符路径走 \\\\?\\ 落地 + 穿越防御")


def test_prepare_offline_bundle_first(tmp=None):
    """v1.0.712 顺序：离线包在两个 npm 源**之前**——管控网机器上在线必挂，
    旧顺序白等 2×3 分钟；有包时 npm 一次都不该跑。"""
    d = _new_tmp("bundle_first")
    root = os.path.join(d, "node_root")
    os.makedirs(root)
    bundle = _mk_fake_bundle(d)
    orig_which, orig_run = dsh_bridge._which_dsh, dsh_bridge._run_captured
    orig_bf = dsh_bridge._find_offline_bundle
    from core import portable as _p
    orig_find = _p.find_node
    dsh_bridge._find_offline_bundle = lambda: bundle
    _p.find_node = lambda: os.path.join(root, "node.exe")
    dsh_bridge._run_captured = lambda a, t, env=None: (_ for _ in ()).throw(
        AssertionError("有离线包时不该触发 npm"))
    # 环境隔离（评审 P1-2）：本机装过 dsh 组件时 _which_dsh() 会返回真实路径，
    # dsh_prepare 走 already_installed 提前返回，本用例永远测不到离线包分支。
    # 这条桩是**行为前提**而不是装饰：删掉它，在装过组件的机器上必红。
    dsh_bridge._which_dsh = lambda: None
    u1 = _patch_node(True)
    try:
        r = dsh_bridge.dsh_prepare({})
        assert r["ok"] is True and r.get("source") == "offline_bundle", r
        assert os.path.isfile(os.path.join(root, "node_modules", "@deepseek-ai",
                                           "dsh", "lib", "bin.js"))
        assert os.path.isfile(os.path.join(root, "dsh.cmd"))
    finally:
        u1()
        dsh_bridge._which_dsh, dsh_bridge._run_captured = orig_which, orig_run
        dsh_bridge._find_offline_bundle = orig_bf
        _p.find_node = orig_find
        shutil.rmtree(d, ignore_errors=True)
    print("PASS dsh_prepare：离线包优先、零 npm 调用")


def test_prepare_partial_tree_reinstalled(tmp=None):
    """上次 npm 装到一半留下 dsh.cmd 但入口 JS 缺失——旧逻辑判「已安装」放行，
    体检假就绪、一跑就崩。v1.0.712 按未安装重装（离线包覆盖残缺树）。"""
    d = _new_tmp("partial")
    root = os.path.join(d, "node_root")
    os.makedirs(root)
    with open(os.path.join(root, "dsh.cmd"), "w", encoding="utf-8") as f:
        f.write("shim 残留（入口缺失）")
    bundle = _mk_fake_bundle(d)
    orig_bf = dsh_bridge._find_offline_bundle
    orig_which = dsh_bridge._which_dsh
    dsh_bridge._find_offline_bundle = lambda: bundle
    from core import portable as _p
    orig_find = _p.find_node
    _p.find_node = lambda: os.path.join(root, "node.exe")
    # 同上：本用例要验证「残缺树按未安装重装」，前提是 _which_dsh 不返回本机
    # 那份已装好的 dsh。删掉这行，装过组件的机器上会直接 already_installed 假绿。
    dsh_bridge._which_dsh = lambda: None
    u1 = _patch_node(True)
    try:
        r = dsh_bridge.dsh_prepare({})
        assert r["ok"] is True and r.get("source") == "offline_bundle", r
        entry = os.path.join(root, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
        assert os.path.isfile(entry), "残缺树应被离线包补全"
    finally:
        u1()
        dsh_bridge._find_offline_bundle = orig_bf
        dsh_bridge._which_dsh = orig_which
        _p.find_node = orig_find
        shutil.rmtree(d, ignore_errors=True)
    print("PASS dsh_prepare：残缺树（shim 在、入口缺）→ 按未安装重装")


def test_prepare_failure_surfaces_stderr_and_offline_guidance(tmp=None):
    """无离线包 + 两源全挂：error 必须带 npm 输出尾部（前端只回显 error，
    用户以前只看到一句通用话没法定位），hint 必须给离线自救路径。"""
    d = _new_tmp("failhint")
    orig_run = dsh_bridge._run_captured

    def _boom(args, timeout, env=None):
        return {"exit_code": 1, "stdout": "",
                "stderr": "npm ERR! code ECONNREFUSED\nnpm ERR! network request "
                          "to https://registry.npmmirror.com failed"}
    dsh_bridge._run_captured = _boom
    orig_bf = dsh_bridge._find_offline_bundle
    dsh_bridge._find_offline_bundle = lambda: None
    u1 = _patch_node(True)
    from core import portable as _p
    orig_npm = _p.find_npm
    _p.find_npm = lambda: r"C:/node/npm.cmd"
    orig_which = dsh_bridge._which_dsh
    dsh_bridge._which_dsh = lambda: None
    try:
        r = dsh_bridge.dsh_prepare({})
        assert r["ok"] is False, r
        assert "ECONNREFUSED" in r["error"], f"error 应带 npm 输出尾部：{r['error']}"
        assert "产物库/组件/node" in r["hint"], r["hint"]
    finally:
        dsh_bridge._run_captured = orig_run
        dsh_bridge._find_offline_bundle = orig_bf
        _p.find_npm = orig_npm
        dsh_bridge._which_dsh = orig_which
        u1()
        shutil.rmtree(d, ignore_errors=True)
    print("PASS dsh_prepare：失败时 error 带 stderr 尾部 + hint 给离线自救")


def test_make_offline_bundle_and_ensure(tmp=None):
    """打包/校验闭环（dist 布局口径）：dev 模式下运行期探测指向源码根/系统 node，
    build 必须钉死 dist 侧——_dist_node_root 解析 dist/组件/node/current/<ver>；
    版本一致→跳过（zip 数百 MB，构建不每次重建）；漂移→重建。"""
    import json as _json
    d = _new_tmp("mkbundle")
    ws = os.path.join(d, "ws")
    os.makedirs(ws)
    # dev 布局：workspace 父目录=源码根(d)，dist 在 d/dist 下
    root = os.path.join(d, "dist", "产物库", "组件", "node",
                        "current", "node-v22.20.0-win-x64")
    pj_dir = os.path.join(root, "node_modules", "@deepseek-ai", "dsh", "lib")
    os.makedirs(pj_dir)
    with open(os.path.join(root, "node.exe"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(root, "dsh.cmd"), "w", encoding="utf-8") as f:
        f.write("shim")
    with open(os.path.join(pj_dir, "bin.js"), "w", encoding="utf-8") as f:
        f.write("x")
    with open(os.path.join(root, "node_modules", "@deepseek-ai", "dsh",
                           "package.json"), "w", encoding="utf-8") as f:
        f.write(_json.dumps({"name": "@deepseek-ai/dsh", "version": "9.9.9-test"}))
    import core.paths as cp
    orig_ws = cp.workspace_root
    cp.workspace_root = lambda: ws
    u1 = _patch_node(True, "v22.20.0")
    try:
        assert dsh_bridge._dist_node_root() == root, dsh_bridge._dist_node_root()
        bundle = os.path.join(d, "dist", "产物库", "组件", "node", "dsh-offline.zip")
        made = dsh_bridge._make_offline_bundle(root, out=bundle)
        assert made and os.path.basename(made[0]) == "dsh-offline.zip", made
        meta = dsh_bridge._read_bundle_meta(bundle)
        assert meta and meta["dsh_version"] == "9.9.9-test" and meta["file_count"] >= 3, meta
        assert dsh_bridge.ensure_offline_bundle() == "", "版本一致应跳过重建"
        m1 = os.path.getmtime(bundle)
        assert os.path.getmtime(bundle) == m1, "跳过时不得重写 zip"
        # 版本漂移 → 重建
        with open(os.path.join(root, "node_modules", "@deepseek-ai", "dsh",
                              "package.json"), "w", encoding="utf-8") as f:
            f.write(_json.dumps({"name": "@deepseek-ai/dsh", "version": "10.0.0-test"}))
        note = dsh_bridge.ensure_offline_bundle()
        assert "重建" in note, note
        meta2 = dsh_bridge._read_bundle_meta(bundle)
        assert meta2["dsh_version"] == "10.0.0-test", meta2
    finally:
        u1()
        cp.workspace_root = orig_ws
        shutil.rmtree(d, ignore_errors=True)
    print("PASS _dist_node_root/_make_offline_bundle/ensure_offline_bundle：dist 布局、meta、一致跳过、漂移重建")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    _cleanup()
    print(f"\n{'=' * 50}\n{len(fns) - failed}/{len(fns)} 通过"
          + (" —— 有失败用例！" if failed else " —— 全绿 ✓"))
    sys.exit(1 if failed else 0)
