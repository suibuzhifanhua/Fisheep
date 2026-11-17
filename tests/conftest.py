# tests/conftest.py
import io
import os
import sys

_sys_path_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sys_path_root not in sys.path:
    sys.path.insert(0, _sys_path_root)
# v1.0.646：测试之间不得互相 import（test_v623_mute_sync 曾 `from test_v555_batch3
# import _js_verdict`，把两个不相关版本的测试文件焊死在一起——归并按域做时必踩）。
# 跨文件复用的判定函数一律放这里，测试文件只 `from conftest import js_verdict`。
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def js_verdict(expr):
    """跑 _run_wecom_js_safe 的安全判定（不执行 JS——wb.run_js 打桩）。
    返回 "approval"（转人工审批）或 "pass"（直接放行）。

    共享原因：wecom_run_js 防火墙的判定被「JS 通道安全」与「跑课静音表达式过审」
    两类用例同时依赖；放在生产者文件里会让测试文件之间产生 import 依赖。"""
    from agent_tools import executors as EX
    import wecom_bridge as wb

    orig = wb.run_js
    wb.run_js = lambda *a, **k: {"ok": True}   # 放行分支的打桩
    try:
        out = EX._run_wecom_js_safe({"expression": expr}, {"wecom": {"cdp_port": 9222}})
    finally:
        wb.run_js = orig
    if isinstance(out, dict) and out.get("__approval_needed__"):
        return "approval"
    return "pass"

# v1.0.526（QA 改进项1）：sys.modules 别名机制已删除——它把生产环境的裸名 import
# 断裂掩盖了多版本（v1.0.526 P1：memory_ops 裸名导致打包版记忆面板全 500，测试却全绿）。
# 现全部测试与生产统一使用包路径（services.* / desktop.* / core.*），已 grep 验证
# tests/ 与生产代码均无裸名残留。若未来引入别名需求，须同时保证生产可解析。

# v1.0.405（H1）：API 鉴权豁免——test_client 不走 / 路由拿不到 cookie，
# 测试环境统一关闭鉴权闸门（backend._AUTH_DISABLED）。鉴权本身有专项测试
# tests/test_api_auth.py（临时重新开启验证 401/403/放行三态）。
# v1.0.660（审查修复 B22）：原来这里是 `except Exception: pass`——backend 导入
# 失败（依赖缺失/语法错/循环 import）时，豁免没生效也不报错，测试照样跑，
# 只是所有需要鉴权豁免的用例莫名其妙地 401/500。根因被完全吞掉，
# 排查时先怀疑业务逻辑，绕一圈才发现是 conftest 自己没跑成。
# 改为显式报错：import backend 是整个测试集的前提，前提不成立就该当场停。
try:
    import backend as _backend
    _backend._AUTH_DISABLED = True
except Exception as _e:  # noqa: BLE001
    raise RuntimeError(
        "tests/conftest.py 导入 backend 失败——测试环境无法关闭鉴权闸门，"
        "后续用例会以 401/500 的假象失败。先修好这个导入错误再跑测试。"
    ) from _e

# v1.0.401：测试环境屏蔽管理员自启动的任务查询（schtasks 在沙箱黑名单会
# 终止整个 pytest 进程树；生产环境不受影响）。_admin_task_exists 无专测。
import pytest  # noqa: E402

# ---------- v1.0.619（测试卫生）：跑批临时目录不再留垃圾 ----------
# 全仓 50 处 tempfile.mkdtemp 里只有 6 处配套 rmtree——实测在 %TEMP% 面积累过
# 3390 个 wec_* 目录（几百 MB），而 tests/ 源码树里也长期躺着 _comp_tmp/_dl_test*
# 这类固定名残留（被误当源码读、还污染打包扫描）。这里在会话级统一回收：
# 包一层 mkdtemp 记下本次跑批创建的目录，会话结束整批删掉（跨用例仍然可见，
# 所以不影响"先写后读"那类同会话依赖）。
import shutil  # noqa: E402
import tempfile  # noqa: E402

_REAL_MKDTEMP = tempfile.mkdtemp
_SESSION_DIRS = []


def _tracked_mkdtemp(*args, **kwargs):
    d = _REAL_MKDTEMP(*args, **kwargs)
    _SESSION_DIRS.append(d)
    return d


_TESTS_SCRATCH = ("_comp_tmp", "_dl_test", "_dl_test2", "_dl_test3", "_dl_out")


@pytest.fixture(scope="session", autouse=True)
def _reclaim_temp_dirs():
    tempfile.mkdtemp = _tracked_mkdtemp
    try:
        yield
    finally:
        tempfile.mkdtemp = _REAL_MKDTEMP
        for d in _SESSION_DIRS:
            shutil.rmtree(d, ignore_errors=True)
        _SESSION_DIRS.clear()
        for name in _TESTS_SCRATCH:
            shutil.rmtree(os.path.join(_sys_path_root, "tests", name), ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_schtasks_query(monkeypatch):
    # v1.0.402 拆分后定义处是 routes.b_admin——patch 定义处（壳与调用方同时覆盖）
    try:
        import routes.b_admin as _ba
        monkeypatch.setattr(_ba, "_admin_task_exists", lambda: False)
        # v1.0.661：_task_armed（v1.0.542 自举任务查询）是另一条真实 schtasks /query 路径——
        # /api/admin/status 的 armed 字段（v1.0.655 B6 并入）无 frozen 守卫、恒调它，
        # 与 _admin_task_exists 同沙箱黑名单风险，一并屏蔽，否则整轮 pytest 被杀进程树。
        monkeypatch.setattr(_ba, "_task_armed", lambda: False)
    except Exception:
        pass
    try:
        import routes.management as _mg
        monkeypatch.setattr(_mg, "_admin_task_exists", lambda: False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _fast_cdp_refused():
    """v1.0.677（测试提速）：让 CDP 探活里的"连不上"这条路径**瞬时**返回。

    为什么能提速：wecom_bridge.cdp_up 探的是 127.0.0.1:9222。测试机上没有企微
    在跑 → 每次探活都是"连接被拒"→ 原实现重试 3 次（每次 1s 超时 + 0.4s sleep），
    **单次约 4 秒**。而 agent/context.build_chat_messages 每轮对话都问一次，
    于是大量用例的时间全花在"等一个必然失败的探活"上。

    实测（cProfile）：两个用例的 28.9 秒里有 **25.7 秒**在 cdp_up 的 18 个 HTTP GET 上。
    全量套件的最慢 22 个用例耗时呈 8.4~9.0s / 13~18s 的**固定簇**——全是这个。

    为什么**不直接桩掉 wecom_bridge.cdp_up**：那样会把 cdp_up 的**真实逻辑**一起换掉，
    而 tests/test_wecom_bind.py 正是靠"假 HTTP 服务器 + 真实 cdp_up"来测端口避让
    （404 占位者识别、活端口优先等）——整个换掉会让那些用例失真（已实测打挂 3 条）。

    这里只换**最底层的一环**：cdp_client 里的 requests.get 碰到 CDP 的
    /json/version 就立即抛 ConnectionError。于是 cdp_up 自己的判定逻辑
    （状态码校验 / JSON 校验 / 重试口径）原样跑，只是"端口没人监听"这个
    必然失败的分支不用再等 1 秒超时 × 3 次。

    顺序：autouse fixture 先设 → 用例体内的 monkeypatch（test_wecom_bind 的假 HTTP
    服务器）后设 → 覆盖本 fixture → 那些用例仍走自己的假服务器，行为不变。
    """
    import requests as _rq
    from wecom_bridge import cdp_client as _cc

    _real = _cc.requests

    class _FastGet:
        """只拦 CDP /json/version；其余方法原样转发给真 requests。"""

        def __getattr__(self, name):
            return getattr(_real, name)

        def get(self, url, *a, **k):
            if "/json/version" in str(url):
                raise _rq.exceptions.ConnectionError("connection refused (test fast path)")
            return _real.get(url, *a, **k)

    _cc.requests = _FastGet()
    try:
        yield
    finally:
        _cc.requests = _real


# ---------- v1.0.648 归并残留收敛（A12）：跨文件逐字重复的 helper ----------
# 按 v1.0.646 约定收进来：生产模块一律函数内延迟 import（本模块不依赖生产代码），
# 测试文件以 `from conftest import _src`（或 as 别名）取用。行为与被删的本地拷贝
# 逐字一致；行为不同的本地 helper（如各文件自带的 _mk/_patch/_cleanup）未动。

def _src(*rel):
    """读仓库根下相对路径的文本文件。
    收敛自 6 份同型拷贝（_src/_read/rd 各文件名不变，见下方别名）。"""
    return io.open(os.path.join(_sys_path_root, *rel), encoding="utf-8").read()


_read = _src   # test_agent_runtime / test_silent_fail_gate / test_build_guard 的原有调用名
rd = _src      # test_misc_fixes / test_wecom_windows 的原有调用名


def _done(text="", calls=None):
    """agent 事件流假件：done 事件（原 test_agent_scenarios / test_audit_batch 两份逐字相同）。"""
    return ("done", {"text": text, "tool_calls": calls or [], "usage": None})


def _call(name, args, cid="1"):
    """agent 事件流假件：tool_call 项（同上两份逐字相同）。"""
    return {"id": cid, "name": name, "args": args}


def _patch_config_path(tmp_path):
    """config_store.config_path 改道到临时目录，返回恢复函数。
    （原 test_config_dsh / test_custom_skills 两份逐字相同。）"""
    import core.config_store as config_store
    orig = config_store.config_path
    config_store.config_path = lambda: tmp_path
    return orig


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """隔离 config 的 Flask test_client（yield (client, cfg_path)）。
    原 test_build_guard / test_conversation_settings / test_silent_fail_gate 三份逐字相同。"""
    import core.config_store as cs
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cs, "config_path", lambda: str(cfg))
    cs._CFG_CACHE["cfg"] = None
    cs._CFG_CACHE["mtime"] = None
    import backend as backend_mod
    backend_mod.app.config["TESTING"] = True
    with backend_mod.app.test_client() as c:
        yield c, cfg
    cs._CFG_CACHE["cfg"] = None
    cs._CFG_CACHE["mtime"] = None
