# -*- coding: utf-8 -*-
"""v1.0.756（评审 P1-4 字面补做）行为锁：run_id 细粒度埋点 + 任务回放页。

闭环 docs/deferred.md D7。改回坏形状必红：
  · 把 token / 耗时 / 判定依据从 record_sse 里删掉；
  · summarize 不再给 failed / tokens / tool_calls / first_error；
  · 删掉 /runs 独立页、把它改成依赖 18 个前端 js 的面板；
  · 把入口改回依赖全局函数（ES Module 迁移后会全废）；
  · 把 D7 改回「后端地基已落地」。
验收（报告原文）：任取一次失败任务，用 run_id 一次查询即可拿到完整调用链
与耗时分布，无需翻多份日志 —— 由 test_failed_run_is_queryable_in_one_call 复现。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import run_trace as RT          # noqa: E402
from core import run_replay_page as RP    # noqa: E402
from core import frontend_check as FC     # noqa: E402

RID = "20261123-120000-deadbeef"


def _src(rel):
    with io.open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


class TestFineGrainedRecord:
    def test_record_sse_keeps_tokens_timing_and_verdict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RT, "runs_dir", lambda: str(tmp_path))
        item = ('data: {"type":"tool","tool":"pc_run_argv","round":2,'
                '"tokens":30,"prompt_tokens":20,"completion_tokens":10,'
                '"duration_ms":42,"verdict":"通过","model":"m1"}')
        assert RT.record_sse(item, run_id=RID, elapsed_ms=137) is True
        ev = RT.read_run(RID)
        assert len(ev) == 1
        row = ev[0]
        assert row["tokens"] == 30 and row["prompt_tokens"] == 20
        assert row["duration_ms"] == 42 and row["elapsed_ms"] == 137
        assert row["verdict"] == "通过" and row["model"] == "m1"
        assert row["tool"] == "pc_run_argv"

    def test_record_sse_ignores_non_data_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RT, "runs_dir", lambda: str(tmp_path))
        assert RT.record_sse(": keepalive", run_id=RID) is False
        assert RT.record_sse("data: not-json", run_id=RID) is False
        assert RT.read_run(RID) == []

    def test_summarize_keeps_old_keys_and_adds_new_ones(self):
        s = RT.summarize([{"ts": 1.0, "type": "tool", "tool": "x", "tokens": 12},
                          {"ts": 3.5, "type": "error", "text": "boom"}])
        for k in ("events", "duration_s", "types", "errors", "started_at", "ended_at"):
            assert k in s, "旧键不能丢：%s" % k
        assert s["events"] == 2 and s["duration_s"] == 2.5
        assert s["failed"] is True and s["tokens"] == 12 and s["tool_calls"] == 1
        assert s["first_error"]["text"] == "boom"
        assert s["slowest_gap_s"] == 2.5

    def test_run_end_ok_false_counts_as_error(self):
        s = RT.summarize([{"ts": 1, "event": "run_start"},
                          {"ts": 2, "event": "run_end", "ok": False}])
        assert s["failed"] is True and s["errors"] >= 1

    def test_empty_summary_is_explicit(self):
        s = RT.summarize([])
        assert s["failed"] is False and s["tokens"] == 0
        assert s["first_error"] is None and s["slowest_gap_s"] == 0.0


class TestAcceptanceOneCall:
    def test_failed_run_is_queryable_in_one_call(self, tmp_path, monkeypatch):
        """报告验收：一次 read_run + summarize 拿到完整调用链与耗时分布。"""
        monkeypatch.setattr(RT, "runs_dir", lambda: str(tmp_path))
        RT.record("run_start", run_id=RID, pane_id="p1", started_at=1000.0)
        RT.record_sse('data: {"type":"tool","tool":"pc_run_argv","round":1,"tokens":30}',
                      run_id=RID, elapsed_ms=120)
        RT.record_sse('data: {"type":"tool","tool":"read_page","round":2,"tokens":18}',
                      run_id=RID, elapsed_ms=340)
        RT.record_sse('data: {"type":"error","text":"boom","verdict":"不通过"}',
                      run_id=RID, elapsed_ms=500)
        RT.record("run_end", run_id=RID, duration_ms=520, events=3, ok=False)

        events = RT.read_run(RID)
        s = RT.summarize(events)
        assert s["events"] == 5 and s["failed"] is True
        assert s["tokens"] == 48 and s["tool_calls"] == 2
        assert s["first_error"]["text"] == "boom"
        assert [e.get("tool") for e in events if e.get("tool")] == ["pc_run_argv", "read_page"]
        assert any(e.get("elapsed_ms") == 340 for e in events)
        assert any(e.get("duration_ms") == 520 for e in events)
        assert s["types"]["run_start"] == 1 and s["types"]["run_end"] == 1

    def test_list_runs_with_status_marks_failed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RT, "runs_dir", lambda: str(tmp_path))
        RT.record("run_start", run_id=RID)
        RT.record_sse('data: {"type":"error","text":"boom"}', run_id=RID)
        rows = RT.list_runs_with_status(10)
        assert len(rows) == 1
        assert rows[0]["run_id"] == RID
        assert rows[0]["failed"] is True and rows[0]["errors"] >= 1
        assert rows[0]["events"] == 2


class TestStandaloneReplayPage:
    def test_page_is_self_contained_and_has_no_frontend_js(self):
        html = RP.render()
        assert "任务轨迹回放" in html
        assert "<style>" in html and "<script>" in html
        assert "/api/runs?limit" in html and "/api/runs/" in html
        assert "run_id=" in html, "必须支持 ?run_id=xxx 直达"
        assert "textContent" in html, "全部用 textContent 渲染，不拼 innerHTML"
        assert "<script src=" not in html
        assert "js/" not in html, "不得引用 18 个前端 js（ES Module 迁移不波及）"

    def test_runs_route_serves_the_page(self):
        import backend as B
        with B.app.test_client() as c:
            r = c.get("/runs")
            assert r.status_code == 200
            body = r.get_data(as_text=True)
            assert "任务轨迹回放" in body
            assert "<script src=" not in body
            assert r.headers.get("Cache-Control") == "no-store"

    def test_backend_wires_the_page(self):
        text = _src("backend.py")
        assert '@app.route("/runs")' in text
        assert "_rr.render()" in text
        assert "run_replay_page" in text

    def test_index_entry_does_not_depend_on_global_functions(self):
        text = _src("index.html")
        assert "location.href='/runs'" in text
        assert "openRunReplay()" not in text, \
            "入口不得依赖全局函数（ES Module 迁移后内联 onclick 会全废）"
        assert FC.run_all(ROOT) == [], "新增导航不得破坏 div/id/脚本顺序契约"


class TestWiringAndDocs:
    def test_sse_stream_has_fine_grained_timing(self):
        text = _src("agent/sse_stream.py")
        for k in ("run_start", "started_at", "elapsed_ms", "duration_ms",
                  "events=_n_events", "ok=not _failed", "run_end"):
            assert k in text, "缺埋点字段：%s" % k

    def test_api_runs_returns_status(self):
        assert '"status": _rt.list_runs_with_status' in _src("routes/b_admin.py")

    def test_d7_is_closed(self):
        text = _src("docs/deferred.md")
        d7 = text.split("## D7")[1].split("## D8")[0]
        assert "**已闭环**" in d7
        assert "前端呈现待拓展方向 3" not in d7
        assert "core/run_replay_page.py" in d7
        assert text.count("| 复核版本 |") == 10, "D1–D10 复核版本行不能增减"

    def test_replay_page_module_is_not_empty(self):
        text = _src("core/run_replay_page.py")
        assert len(text.splitlines()) > 100, "回放页不能是空壳"
        assert "def render()" in text
