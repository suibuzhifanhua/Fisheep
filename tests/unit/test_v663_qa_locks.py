# -*- coding: utf-8 -*-
"""test_v663_qa_locks — v1.0.663 三项回归锁（E / F / C2）

E  fetch_webpage 响应体 16MB 上限（agent_tools/executors.py）
   · 流式 iter_content 累计，越限立即 close() 并返回「页面过大…已中止」；
   · Content-Length 已声明超限时**不读流**；非数字长度回退实际字节兜底；
   · 正常页面解析/脚本剥离/max_chars 截断语义不变；
   · 请求参数仍是 stream=True + timeout=20（原超时语义不变）。

F  tests/unit/test_dual_model.py 收集期零网络
   · 旧形态在模块 import 期调用 probe_model_reachable（打真实局域网）；
   · 本锁：把 llm_client.probe_model_reachable 替换成「一调用就抛」后导入模块
     必须成功——导入即探测的旧形态会在这里当场炸红。

C2 services/batch_flow.py::_audit_one 收尾差分清理（只读生产代码）
   · fp is None 早退、步骤 1-6 中途异常两条路径都必须执行
     _r_close_new_targets（v1.0.663 #17：清理从 try 尾部移入 finally）；
   · 基线快照失败（_base_ids=None）时跳过清理（None≠空集，防误关预存页）；
   · AST 形状锁：清理调用必须位于 finally，而不是 try 尾部（否则异常路径漏清）。

红线：本文件只读生产代码，全部用 FakeTransport / monkeypatch 在内存中验证，
不启动服务、不碰真实 CDP / 企微窗口。
"""
import ast
import inspect
import os
import subprocess
import sys
import textwrap
import types

from agent_tools import executors as EX
from services import batch_flow as bf

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DUAL_MODEL = os.path.join(ROOT, "tests", "unit", "test_dual_model.py")

MB = 1024 * 1024


# ============================================================
# E：fetch_webpage 16MB 流式上限
# ============================================================
class _FakeStreamResp:
    """requests.Response 的最小替身：可观测「读了几块 / 是否 close」。"""

    def __init__(self, chunks, status=200, headers=None, encoding="utf-8",
                 fail_on_iter=False):
        self.status_code = status
        self.headers = dict(headers or {})
        self.encoding = encoding
        self.closed = False
        self.consumed = 0
        self._chunks = list(chunks)
        self._fail_on_iter = fail_on_iter

    def iter_content(self, size=8192):
        if self._fail_on_iter:
            raise AssertionError("Content-Length 已可拒绝，不应再读取响应流")
        for c in self._chunks:
            self.consumed += 1
            yield c

    def close(self):
        self.closed = True


def _patch_public_get(monkeypatch, resp):
    """绕过 DNS/SSRF 真实解析，并把 requests.get 换成记录入参的假实现。"""
    calls = []

    def fake_get(url, **kw):
        calls.append(kw)
        return resp

    monkeypatch.setattr(EX, "requests", types.SimpleNamespace(get=fake_get))
    monkeypatch.setattr(EX, "_is_safe_public_url", lambda u: (True, ""))
    return calls


def test_fetch_webpage_stream_cap_aborts_at_16mb(monkeypatch):
    """超大流：第 17 块触发 16MB 上限，立即中止、close、不读完剩余流。"""
    one_mb = b"x" * MB
    resp = _FakeStreamResp([one_mb] * 40)   # 40MB 流，远超上限
    calls = _patch_public_get(monkeypatch, resp)

    out = EX._run_fetch_webpage({"url": "http://example.com/huge"}, {})

    assert out["ok"] is False, f"超限必须走错误契约：{out}"
    assert "页面过大" in out["error"] and "16MB" in out["error"], out["error"]
    assert "已中止抓取" in out["error"], out["error"]
    # 16 块正好满 16MB，第 17 块越限 → 只消费 17 块，剩余 23MB 不读
    assert resp.consumed == 17, f"应只读 17 块即中止，实际 {resp.consumed}"
    assert resp.closed is True, "超限返回前必须 close 响应"
    assert calls and calls[0]["stream"] is True and calls[0]["timeout"] == 20


def test_fetch_webpage_content_length_rejects_before_streaming(monkeypatch):
    """Content-Length 已声明超限 → 不读流直接拒绝（省带宽/内存）。"""
    resp = _FakeStreamResp([], headers={"Content-Length": str(20 * MB)},
                           fail_on_iter=True)
    _patch_public_get(monkeypatch, resp)

    out = EX._run_fetch_webpage({"url": "http://example.com/declared"}, {})

    assert out["ok"] is False and "页面过大" in out["error"], out
    assert "声明" in out["error"] and "16MB" in out["error"], out["error"]
    assert resp.consumed == 0, "声明长度已超限，不应读取任何流"
    assert resp.closed is True, "提前返回也要 close 响应"


def test_fetch_webpage_bad_content_length_falls_back_to_stream(monkeypatch):
    """Content-Length 非数字（服务器谎报/缺省）→ 不崩，按实际字节兜底正常解析。"""
    html = "<html><head><title>兜底</title></head><body><p>ok</p></body></html>"
    resp = _FakeStreamResp([html.encode("utf-8")],
                           headers={"Content-Length": "not-a-number"})
    _patch_public_get(monkeypatch, resp)

    out = EX._run_fetch_webpage({"url": "http://example.com/badlen"}, {})

    assert "error" not in out, out
    assert out["title"] == "兜底"
    assert "ok" in out["content"]
    assert resp.closed is True


def test_fetch_webpage_non_200_closes_stream(monkeypatch):
    """HTTP 非 200：返回错误契约，且 stream=True 的响应被显式关闭。"""
    resp = _FakeStreamResp([], status=404)
    _patch_public_get(monkeypatch, resp)

    out = EX._run_fetch_webpage({"url": "http://example.com/missing"}, {})

    assert out["ok"] is False and "HTTP 404" in out["error"], out
    assert resp.closed is True, "非 200 早退也必须关闭流式响应"


def test_fetch_webpage_normal_page_unaffected(monkeypatch):
    """正常页面：标题/正文解析、script/style 剥离、url 回填，参数语义不变。"""
    html = ("<html><head><title>测试页</title>"
            "<meta charset='utf-8'></head><body>"
            "<script>evil()</script><style>.x{display:none}</style>"
            "<nav>导航</nav><p>正文 hello 世界</p></body></html>")
    resp = _FakeStreamResp([html.encode("utf-8")])
    calls = _patch_public_get(monkeypatch, resp)

    out = EX._run_fetch_webpage({"url": "http://example.com/ok"}, {})

    assert "error" not in out, out
    assert out["url"] == "http://example.com/ok"
    assert out["title"] == "测试页"
    assert "正文 hello 世界" in out["content"]
    assert "evil" not in out["content"] and "display:none" not in out["content"]
    assert "导航" not in out["content"], "nav 标签应被剥离"
    assert resp.closed is True
    assert calls[0]["stream"] is True and calls[0]["timeout"] == 20


def test_fetch_webpage_max_chars_truncation_unchanged(monkeypatch):
    """max_chars 截断语义保持：超长正文截断并附「已截断」说明。"""
    body = "a" * 700
    html = "<html><head><title>T</title></head><body><p>%s</p></body></html>" % body
    resp = _FakeStreamResp([html.encode("utf-8")])
    _patch_public_get(monkeypatch, resp)

    out = EX._run_fetch_webpage({"url": "http://example.com/long", "max_chars": 500}, {})

    assert "error" not in out, out
    # get_text 会把 <title>T</title> 并入正文，故前 500 字符 = "T " + 498 个 a
    assert out["content"][:500] == "T " + "a" * 498, "正文应只保留前 500 字符"
    assert "a" * 499 not in out["content"], "截断后不应残留第 500 个之后的正文"
    assert "已截断" in out["content"] and "702 字符" in out["content"], out["content"][-80:]
    # max_chars 下限钳制为 500（原语义：max(500, ...)）
    assert out["char_count"] > 500


# ============================================================
# C2：batch_flow._audit_one 收尾差分清理（finally 形态）
# ============================================================
class _FakeTransport:
    """只实现 _audit_one 用到的开详情/关新页；其余触达即失败。"""

    def __init__(self, open_result=None):
        self.open_result = open_result
        self.close_calls = []

    def open_detail(self, port, list_pick, link, issues):
        return self.open_result

    def close_new_targets(self, port, base_ids):
        self.close_calls.append((port, set(base_ids)))
        return 3

    def __getattr__(self, name):
        raise AssertionError("FakeTransport 不应被触达的方法：%s" % name)


class _FakeWB:
    """bridge 的最小替身：只给 _audit_one 早退/异常路径需要的两个方法。"""

    def __init__(self, targets=None, raise_targets=False, raise_read=False):
        self._targets = targets if targets is not None else [
            {"id": "base-page-1", "type": "page"},
            {"id": "base-other", "type": "other"},
        ]
        self._raise_targets = raise_targets
        self._raise_read = raise_read

    def list_targets(self, port):
        if self._raise_targets:
            raise RuntimeError("CDP /json 抖动")
        return self._targets

    def read_fields(self, fp, fields, port=None):
        if self._raise_read:
            raise RuntimeError("读字段炸了")
        return {"fields": []}


def _orch(transport):
    return bf.AuditOrchestrator(transport=transport, extractor=bf.ReportExtractor())


def test_audit_one_cleans_up_new_targets_on_early_return(tmp_path, monkeypatch):
    """open_detail 返回 None（第 1 步早退）→ finally 仍按基线 id 清理新开页面。"""
    wb = _FakeWB()
    monkeypatch.setattr(bf, "wb", wb)
    monkeypatch.setattr(bf, "log_event", lambda *a, **k: None)
    tr = _FakeTransport(open_result=None)   # 早退路径
    rec = _orch(tr)._audit_one(9222, "fake-list",
                               {"text": "苏州营业部月报", "url": "http://fake/1"},
                               str(tmp_path), 0)

    assert rec["index"] == 0
    assert tr.close_calls == [(9222, {"base-page-1"})], \
        "早退路径必须执行差分清理（v1.0.663 #17 从 try 尾部移入 finally）"


def test_audit_one_cleans_up_new_targets_on_mid_audit_exception(tmp_path, monkeypatch):
    """步骤 3 抛异常（被 except 吞成 issue）→ finally 仍执行差分清理。"""
    wb = _FakeWB(raise_read=True)
    monkeypatch.setattr(bf, "wb", wb)
    monkeypatch.setattr(bf, "log_event", lambda *a, **k: None)
    tr = _FakeTransport(open_result="fp-detail")
    rec = _orch(tr)._audit_one(9222, "fake-list",
                               {"text": "上海营业部月报", "url": "http://fake/2"},
                               str(tmp_path), 1)

    assert any("单张处理异常" in str(x) for x in rec["issues"]), rec["issues"]
    assert tr.close_calls == [(9222, {"base-page-1"})], \
        "异常路径必须执行差分清理；清理若在 try 尾部（非 finally）这里会是空"


def test_audit_one_skips_cleanup_when_baseline_snapshot_failed(tmp_path, monkeypatch):
    """基线快照失败（_base_ids=None）→ 跳过清理（None≠空集，防误关预存页）。"""
    wb = _FakeWB(raise_targets=True)
    monkeypatch.setattr(bf, "wb", wb)
    monkeypatch.setattr(bf, "log_event", lambda *a, **k: None)
    tr = _FakeTransport(open_result=None)
    rec = _orch(tr)._audit_one(9222, "fake-list",
                               {"text": "北京营业部月报", "url": "http://fake/3"},
                               str(tmp_path), 2)

    assert tr.close_calls == [], "基线快照失败时不得用空集去关预存页"
    assert any("页面基线快照失败" in str(x) for x in rec["issues"]), rec["issues"]


def test_audit_one_cleanup_lives_in_finally_ast_shape():
    """AST 形状锁：_r_close_new_targets 必须位于包住审计主体的 try 的 finalbody。

    显式区分「finally」与「try 尾部」：移到 try 尾部后 finalbody 为空，
    本断言即红（不依赖运行期能否走到异常分支）。"""
    src = textwrap.dedent(inspect.getsource(bf.AuditOrchestrator._audit_one))
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.FunctionDef)

    found = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        body_dump = ast.dump(ast.Module(body=node.body, type_ignores=[]))
        final_dump = ast.dump(ast.Module(body=node.finalbody, type_ignores=[]))
        if "_r_open_detail" in body_dump and "_r_close_new_targets" in final_dump:
            found = node
            break

    assert found is not None, (
        "差分清理 _r_close_new_targets 不在包住 _r_open_detail 的 try.finalbody 里——"
        "早退/异常路径会漏关页面（v1.0.663 #17 回归）")


# ============================================================
# F：test_dual_model 导入/收集期零网络
# ============================================================
def test_dual_model_import_has_zero_network_side_effect():
    """把 probe_model_reachable 换成「一调用就抛」后导入模块必须成功。

    旧形态（模块级 `_O = probe_model_reachable(...)`）在这里必定 AssertionError；
    新形态（module 作用域 fixture）import 期不调用 → 通过。"""
    script = textwrap.dedent("""
        import importlib.util
        import sys

        root, target = sys.argv[1], sys.argv[2]
        sys.path.insert(0, root)
        import llm_client

        def _boom(*a, **k):
            raise AssertionError("COLLECT-TIME NETWORK: probe_model_reachable 在 import 期被调用")

        llm_client.probe_model_reachable = _boom

        spec = importlib.util.spec_from_file_location("t_dual_model_probe", target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "ollama_or_skip"), "缺少延迟探测 fixture"
        print("IMPORT_OK_ZERO_NETWORK")
    """)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    r = subprocess.run([sys.executable, "-c", script, ROOT, DUAL_MODEL],
                       capture_output=True, text=True, timeout=120, env=env)
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "导入 test_dual_model 失败（收集期存在网络副作用？）：\n" + combined
    assert "IMPORT_OK_ZERO_NETWORK" in combined
