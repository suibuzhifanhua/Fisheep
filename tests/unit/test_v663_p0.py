# -*- coding: utf-8 -*-
"""test_v663_p0 — v1.0.663 五维度审查 4 条 P0 的回归锁。

每条锁都能「会红」：对着修复前的旧形态会失败（不是恒绿样板）。
  P0-1  llm_client._loopback_proxies_kw 返回单个 proxies= 关键字（旧版裸 http/https
        关键字 → requests 拒绝，loopback 全链路 TypeError）；
  P0-2  security_ops._strip_extended_prefix 剥扩展长路径前缀，写删/读两通道都拦
        \\?\\ / \\.\\ / \\?\\UNC\\ 前缀（旧版前缀一加即绕过保护）；
  P0-3  wecom_bridge.tucker._agent_busy 探测异常 fail-closed（旧版 except:pass
        静默回落宽限 → 恒「空闲」，任务在飞仍会收纳/误判可杀企微）。

反斜杠一律用 chr(92) 构造，避免 raw/普通字符串在多层工具间被重复转义搞歪。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_B = chr(92)          # 单反斜杠
# Windows 扩展长路径前缀（真实字符：\\ ? \）
_EXT = _B + _B + "?" + _B          # \\?\
_DEV = _B + _B + "." + _B          # \\.\\
_UNC = _B + _B + "?" + _B + "UNC" + _B   # \\?\UNC\


# ============================================================ P0-1 loopback 代理
def test_loopback_kw_shape_is_single_proxies_key():
    """旧形态锁：_loopback_proxies_kw 必须返回单个 proxies= 覆盖字典。
    旧版 _loopback_proxies 返回顶层 {"http":None,"https":None}——`**` 解包后
    塞给 requests 的是裸 http/https 关键字（Session.request 不接受）→ 本锁旧版必红。"""
    import llm_client
    kw = llm_client._loopback_proxies_kw("http://127.0.0.1:11434/v1/models")
    assert set(kw.keys()) == {"proxies"}, f"loopback 必须只出单键 proxies=，实际 {kw.keys()}"
    assert kw["proxies"] == {"http": None, "https": None}, f"覆盖字典内容错 {kw['proxies']}"
    # 非 loopback：解包 {} = 调用形态零变化
    assert llm_client._loopback_proxies_kw("https://api.openai.com/v1/models") == {}


def test_loopback_call_is_accepted_by_requests_not_typeerror():
    """真行为锁：对 loopback 死端口发请求，异常必须是连接类而不是 TypeError。
    旧版把裸 http= 关键字解包进去 → TypeError('unexpected keyword argument http')，
    本锁旧版必红。"""
    import llm_client
    requests = __import__("requests")
    kw = llm_client._loopback_proxies_kw("http://127.0.0.1:1/v1/models")
    try:
        requests.get("http://127.0.0.1:1/v1/models", timeout=(0.3, 0.8), **kw)
    except TypeError as e:
        raise AssertionError(f"loopback 请求抛 TypeError={e}——proxies= 形态没生效")
    except Exception:
        pass  # 连接拒绝/超时 = 预期（死端口），说明关键字被 requests 接受


def test_loopback_kw_wired_to_all_call_sites():
    """源码锁：调用点必须用 _loopback_proxies_kw（不是旧名 _loopback_proxies）。"""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "llm_client.py"), encoding="utf-8").read()
    assert "_loopback_proxies_kw(" in src
    # 旧名调用点不得残留：`_loopback_proxies(` 前面不跟 _kw 后缀
    stale = re.findall(r"(?<!_kw)\b_loopback_proxies\(", src)
    assert stale == [], f"仍有旧名调用点：{stale}"


# ============================================================ P0-2 扩展长路径前缀
def test_strip_extended_prefix_forms():
    """纯函数锁：三种前缀各归一到可比对形态；无前缀路径幂等原样。"""
    from security_ops import _strip_extended_prefix as sp
    p = "C:" + _B + "Windows" + _B + "System32" + _B + "drivers" + _B + "etc" + _B + "hosts"
    assert sp(_EXT + p) == p, "扩展长路径前缀没剥掉"
    assert sp(_DEV + p) == p, "设备前缀 \\\\.\\ 没剥掉"
    unc_in = _UNC + "localhost" + _B + "c$" + _B + "win.ini"
    assert sp(unc_in) == _B + _B + "localhost" + _B + "c$" + _B + "win.ini", "UNC 形态归一错"
    plain = "C:" + _B + "Users" + _B + "f" + _B + "x.txt"
    assert sp(plain) == plain, "无前缀路径必须原样（幂等）"


def test_check_paths_blocks_extended_prefix():
    """会红锁：写/删目标带 \\?\\ 前缀时必须与无前缀同样被拦。
    旧版 check_paths 对扩展前缀归一化后对不上保护前缀 → 放行（本锁旧版必红）。"""
    import security_ops
    tmp = tempfile.mkdtemp(prefix="wbc_p02_")
    cfg = {"security": {"protected_paths": [tmp]}}
    target = os.path.join(tmp, "victim.txt").replace("/", _B)
    ok_plain, _ = security_ops.check_paths([target], cfg)
    assert ok_plain is False, "无前缀受保护路径没被拦（基线错）"
    prefixed = _EXT + target
    ok_pref, hit = security_ops.check_paths([prefixed], cfg)
    assert ok_pref is False, f"扩展前缀绕过了保护（P0-2 未修）——hit={hit!r}"


def test_read_denied_wires_to_same_stripper():
    """会红锁：读侧拒读判定必须走 security_ops 的**同一**剥离函数（单源）。
    旧版 _read_denied_project_asset 直接 str(p).lower() 不剥前缀 →
    扩展前缀版的 config.json 与精确路径对不上，读侧漏拦。"""
    import inspect
    import security_ops
    from agent_tools import os_ops
    src = inspect.getsource(os_ops._read_denied_project_asset)
    assert "_strip_extended_prefix" in src, "读侧没接扩展前缀剥离（P0-2 读通道未修）"
    # 前缀版剥完必须与无前缀版等价（判定口径一致）
    base = "C:" + _B + "x" + _B + "config.json"
    assert security_ops._strip_extended_prefix(_EXT + base).lower() == base.lower()


# ============================================================ P0-3 tucker fail-closed
def test_agent_busy_fail_closed_on_probe_exception():
    """会红锁：任务状态探测**抛异常**时 _agent_busy 必须判忙（True，fail-closed）。
    旧版 except:pass 静默吞异常后回落 30s 宽限——宽限窗外恒 False（空闲），
    于是任务在飞但探测炸了仍会收纳窗口/被误判可杀企微（触碰红线）。"""
    import agent.session as sess
    import wecom_bridge.windows as win
    import wecom_bridge.tucker as tucker
    win._last_agent_active = -1e9  # 宽限窗外：结果只由探测分支决定

    def _boom():
        raise RuntimeError("探测炸了")

    orig = sess.agent_task_running
    sess.agent_task_running = _boom
    try:
        assert tucker._agent_busy() is True, "探测异常必须 fail-closed 判忙（宁可不收纳/不杀企微）"
    finally:
        sess.agent_task_running = orig


def test_agent_busy_real_idle_still_false():
    """口径不回退：探测正常且确无任务、宽限窗外 → 仍是空闲（False）。
    锁 fail-closed 只吃**异常**分支，不得把真空闲也判忙（否则永不收纳/绑定永拒）。"""
    import agent.session as sess
    import wecom_bridge.windows as win
    import wecom_bridge.tucker as tucker
    win._last_agent_active = -1e9
    assert sess.agent_task_running() is False  # 前置：此刻确无任务在飞
    assert tucker._agent_busy() is False, "真空闲被误判忙——fail-closed 吃掉了正常空闲路径"
