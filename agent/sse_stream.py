"""agent.sse_stream — SSE 包装器（GeneratorExit 清理 + 心跳防断流，P1-3/P2-5）。
逐字自 backend.py 迁出（T04）；_HEARTBEAT 提为模块常量（值不变）。
契约：空闲超阈值时 yield 注释行 ": keepalive\n\n"（前端忽略注释行）。

v1.0.755（评审 P1-4）：新增 run_id 旁路轨迹。普通 Thread 不继承 contextvars，
故泵线程改用 copy_context().run 启动；每条 data 事件在透传前录一条摘要到
产物库/runs/<run_id>.jsonl。录制是 fail-open 旁路，主链路行为零变化。
v1.0.755：泵函数拆到模块级 `_pump_events`——巨型函数棘轮（>100 行）只许下降，
把闭包搬出去后 `_sse_stream` 回到 ~70 行，不抬基线。
"""

import time
import queue as _q
import threading as _th
import contextvars as _ctx

from .session import _set_abort, drop_chat_state
from core import run_trace as _rt

_HEARTBEAT = 8.0          # 空闲超过 8s 发一次心跳（远小于 WebView2 ~30s 断连阈值）
# v1.0.646：背压兜底。队列满 = 主线程卡在 socket 写（客户端不读）或消费极慢，
# 此时泵线程会一直 10ms 空转重试，任务就这样"看似在跑、实际永久停摆"——
# 既占着 LLM/tool，也没有任何报错（界面只剩转圈）。超过本阈值判定客户端已失联，
# 主动中止任务（置 abort + 清断点 + 发 error 事件），把"卡死"变成"可感知的失败"。
_STALL_TIMEOUT = 30.0
_STOP, _ERR = object(), object()


def _pump_events(gen, q, stop, err_holder):
    """后台泵线程主体：把 gen 的事件塞进有界队列，背压超时判定客户端失联。

    由 `_sse_stream` 用 `copy_context().run(...)` 在新线程进入，以便继承主线程的
    run_id（普通 Thread 不继承 contextvars，工具/日志轨迹会全部串不上）。
    """
    try:
        for item in gen:
            if stop.is_set():
                return
            t0 = time.monotonic()
            while True:
                try:
                    q.put_nowait(item)
                    break
                except _q.Full:
                    if stop.is_set():
                        return
                    if time.monotonic() - t0 > _STALL_TIMEOUT:
                        # 客户端长时间不消费：不等了，把任务判死（见 _STALL_TIMEOUT 注释）
                        err_holder["exc"] = RuntimeError(
                            f"SSE 背压超时：{_STALL_TIMEOUT:.0f}s 内客户端未消费任何事件，判定已失联")
                        try:
                            q.put(_ERR, timeout=1.0)
                        except _q.Full:
                            try:
                                q.get_nowait()      # 挤掉一条最老的渲染事件也要把错误送出去
                                q.put_nowait(_ERR)
                            except Exception:  # noqa: BLE001
                                pass
                        return
                    time.sleep(0.01)
        try:
            # v1.0.652（审计 P2-7）：收尾 put 加超时——客户端早退+队列积压时
            # 无超时 put 永久阻塞泵线程（daemon 泄漏且持有 gen 引用）。
            q.put(_STOP, timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
    except GeneratorExit:
        pass  # 生成器自身被关闭，静默退出即可
    except Exception as e:  # noqa: BLE001
        err_holder["exc"] = e
        try:
            q.put_nowait(_ERR)
        except _q.Full:
            pass  # silent-ok: 队列满时 _ERR 已由主路径投递，丢弃这条不会吞掉错误


def _sse_stream(gen, pane_id=None, run_id=None):
    """包装 SSE 生成器（心跳防断流 + 断线清理 + run_id 轨迹旁路）：
    - 客户端断开（GeneratorExit）→ 置 abort 并清该 pane 断点，防后端空耗；
    - 生成异常 → 同样清理（断点状态不可信）并补发一条干净的 error 事件；
    - 正常走完（含到达轮次上限已存断点）→ 不清理，保留「继续」能力；
    - 空闲超 _HEARTBEAT 发 `: keepalive` 注释行，防 WebView2 ~30s 断流；
    - run_id 在本生成器生命周期内绑定，退出时复位，避免线程池复用串号。
    """
    _tok = _rt.bind(run_id) if run_id else None
    # v1.0.756（评审 P1-4）：细粒度埋点全部在 SSE 单收口完成，不动业务链路
    _t0 = time.monotonic()
    _n_events = 0
    _failed = False
    _rt.record("run_start", run_id=run_id, pane_id=pane_id,
               started_at=round(time.time(), 3))
    q = _q.Queue(maxsize=64)
    stop = _th.Event()
    err_holder = {"exc": None}
    _pump_ctx = _ctx.copy_context()
    _th.Thread(target=_pump_ctx.run,
               args=(_pump_events, gen, q, stop, err_holder), daemon=True).start()
    try:
        while True:
            try:
                item = q.get(timeout=_HEARTBEAT)
            except _q.Empty:
                yield ": keepalive\n\n"
                continue
            if item is _STOP:
                break
            if item is _ERR:
                if pane_id:
                    _set_abort(pane_id, True)
                    drop_chat_state(pane_id)
                # 先补发干净 error 事件，再抛出交 Flask 收尾（裸抛会让前端收不到
                # done/error，typing 残留、气泡塌成窄列且看不到报错）。
                import json as _json
                _err_text = "agent 内部异常：" + str(err_holder["exc"])
                _failed = True
                _rt.record("run_error", run_id=run_id, error=_err_text[:200],
                           elapsed_ms=int((time.monotonic() - _t0) * 1000))
                yield "data: " + _json.dumps({"type": "error", "text": _err_text}, ensure_ascii=False) + "\n\n"
                raise RuntimeError(f"agent 生成器内部异常：{err_holder['exc']}") from err_holder["exc"]
            _n_events += 1
            _rt.record_sse(item, run_id=run_id,
                           elapsed_ms=int((time.monotonic() - _t0) * 1000))
            yield item
    except GeneratorExit:
        _failed = True
        if pane_id:
            _set_abort(pane_id, True)
            drop_chat_state(pane_id)
        stop.set()
        raise
    finally:
        stop.set()
        _rt.record("run_end", run_id=run_id,
                   duration_ms=int((time.monotonic() - _t0) * 1000),
                   events=_n_events, ok=not _failed)
        _rt.unbind(_tok)
