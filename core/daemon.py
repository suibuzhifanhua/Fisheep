# -*- coding: utf-8 -*-
"""core.daemon — 守护线程骨架单源（审计 A13+B1）
=================================================================
收敛服务层六套守护循环与两处看门狗各抄一份的线程样板
（Thread+Event.wait+while+try/except 吞异常）：

- start_periodic：周期 tick 守护线程。内部 is_alive 判重（重复调用安全）、
  daemon 线程、Event.wait(interval) 挂起不占 CPU、tick_fn 异常吞掉留痕
  （log_event）、gate 返回 False 的轮次跳过 tick_fn、first_delay 控制首拍延时。
  返回线程对象，附 stop_event 供可停场景——调用方原有可停/不可停现状不变。
- spawn_deadline_watchdog：一次性看门狗守护线程，seconds 内未取消则调
  on_fire()；返回 (线程, cancel_event)，cancel_event.set() 提前取消。

行为与被收敛的原实现逐一等价（tick 间隔/判重/异常吞噬留痕/gate 跳过）。
"""
import threading
import time
import sys

from core.logging import log_event

_REG_LOCK = threading.Lock()
_THREADS = {}          # name -> 线程（is_alive 判重；死线程启动时清出登记表）

# ---------- 统一注册表（v1.0.660 功能 B6） ----------
# 现状：backend.py 启动期各自 try/except 起了 10 个后台件（调度/自修复/知识库/日报/
# 待办/企微归档/OCR 自检×2/tucker/save-dialog），逐个 try/except、逐个写日志，
# 没有统一名册 → 「现在到底哪些在跑、哪个悄悄死了」只能翻日志猜。
# 这里提供 register/start_all/status：注册名册 + 统一启动 + 带存活检查的状态。
# 保持「单个 daemon 启动失败不影响其他」的既有行为（start_all 逐个隔离异常）。
_REGISTRY = {}         # name -> {"target": callable, "thread": Thread|None, "error": str|None, "started": bool}


def register(name, target, thread_name=None, desc=""):
    """登记一个后台件（只入册，不启动）。

    name       ：名册里的名字（前端/状态接口按它展示）
    target     ：启动函数（无参可调用，自己负责起线程）
    thread_name：用于存活检查的线程名（默认=name；start_periodic 起的线程以自报名为准）
    desc       ：一句话说明
    """
    with _REG_LOCK:
        _REGISTRY[str(name)] = {"target": target, "thread": None,
                                 "error": None, "started": False,
                                 "thread_name": thread_name or str(name),
                                 "desc": str(desc or "")}
    return name


def start_all():
    """启动名册里**尚未启动**的后台件。单个失败只记 error，不影响其他（既有行为保持）。

    幂等：已成功启动过的条目会被跳过（backend 分两批启动同一名册时不会起两个线程）。
    返回 {name: {"ok": bool, "error": str}}（含本轮新启动的与已启动跳过的）。
    """
    results = {}
    with _REG_LOCK:
        items = list(_REGISTRY.items())
    for name, spec in items:
        if spec.get("started") and not spec.get("error"):
            results[name] = {"ok": True, "error": ""}
            continue
        try:
            th = spec["target"]()
            spec["thread"] = th if isinstance(th, threading.Thread) else None
            spec["started"] = True
            spec["error"] = None
            results[name] = {"ok": True, "error": ""}
        except Exception as e:  # noqa: BLE001
            spec["started"] = False
            spec["error"] = "%s: %s" % (type(e).__name__, e)
            results[name] = {"ok": False, "error": spec["error"]}
            log_event(f"[daemon] 启动失败（不影响其他后台件）{name}：{spec['error']}")
    return results


def _alive_of(spec):
    """该后台件的存活判定。

    三级兜底（不同后台件的线程归属不同）：
      ① 注册时直接返回的线程对象（自己起的线程）
      ② core.daemon.start_periodic 的名册（_THREADS）
      ③ 按线程名在 threading.enumerate() 里找（第三方模块自起的线程，如 OCR 自检）
      ④ 都查不到 → 只能按"已启动且未报错"报存活（无法证实线程在跑）
    """
    th = spec.get("thread")
    if th is not None:
        try:
            return bool(th.is_alive())
        except Exception:  # noqa: BLE001
            return False
    tname = spec.get("thread_name") or ""
    with _REG_LOCK:
        t = _THREADS.get(tname)
    if t is not None:
        try:
            return bool(t.is_alive())
        except Exception:  # noqa: BLE001
            return False
    if tname:
        try:
            for t2 in threading.enumerate():
                if t2.name == tname:
                    return bool(t2.is_alive())
        except Exception as e:  # noqa: BLE001
            # v1.0.660（构建门禁）：裸 except-pass 会让"静默降级"棘轮计数上涨——
            # 线程枚举失败是"状态未知"，必须留痕而不是静默吞掉。
            log_event(f"[daemon] 枚举线程 {tname} 失败（存活状态按 started 兜底）：{e}")
    return bool(spec.get("started")) and not spec.get("error")


def status():
    """返回名册状态：{name: {"alive": bool, "started": bool, "error": str, "desc": str}}。"""
    with _REG_LOCK:
        items = dict(_REGISTRY)
    out = {}
    for name, spec in items.items():
        out[name] = {
            "alive": _alive_of(spec),
            "started": bool(spec.get("started")),
            "error": spec.get("error") or "",
            "desc": spec.get("desc") or "",
        }
    return out


# ---------- 结构化失败计数（v1.0.660 功能 B9） ----------
# 现状：关键路径大量裸 except 降级，失败只进日志文本；self_repair 体检只能扫
# companion.log 的正则聚类，既漏（异常格式千变万化）又把"同一类错"和"无关噪声"混一起。
# 这里提供轻量计数器：按 (组件, 错误类型) 计数，供体检优先读取（读不到再回退扫日志）。
_FAIL_COUNTS = {}      # (component, kind) -> count
_FAIL_LOCK = threading.Lock()
_FAIL_TOTAL = 0


def record_failure(component, kind="error", detail=""):
    """记一次失败（结构化）。

    component：出问题的组件（模块/子系统名）
    kind     ：错误类别（异常类型名或稳定短码）
    detail   ：可选说明（只进内存、不落盘明文）
    """
    global _FAIL_TOTAL
    c, k = str(component or "?"), str(kind or "error")
    with _FAIL_LOCK:
        _FAIL_COUNTS[(c, k)] = _FAIL_COUNTS.get((c, k), 0) + 1
        _FAIL_TOTAL += 1
    try:
        log_event(f"[fail] {c}/{k}" + (("：" + str(detail)[:160]) if detail else ""))
    except Exception as e:  # noqa: BLE001
        # v1.0.660（构建门禁）：日志本身写失败（磁盘满/句柄耗尽）时不能静默吞掉——
        # 计数器已记上，这里用 stderr 兜底留痕（不能再递归调用 log_event）。
        sys.stderr.write("[fail] log_event 写入失败：%s\n" % e)


def failure_report():
    """返回结构化失败统计（供体检/状态接口用）。

    形状：{"total": int, "by_component": {c: {"total": n, "kinds": {k: n}}}}
    """
    with _FAIL_LOCK:
        items = dict(_FAIL_COUNTS)
        total = _FAIL_TOTAL
    by_comp = {}
    for (c, k), n in items.items():
        rec = by_comp.setdefault(c, {"total": 0, "kinds": {}})
        rec["total"] += n
        rec["kinds"][k] = rec["kinds"].get(k, 0) + n
    return {"total": total, "by_component": by_comp}


def start_periodic(name, tick_fn, interval_s, gate=None, first_delay=0.0):
    """启动（或复用）名为 name 的周期守护线程：先等 first_delay，此后每
    interval_s 调一次 tick_fn；gate 返回 False 的轮次跳过 tick_fn。同名线程仍在
    运行时直接返回它（重复调用安全）。tick_fn/gate 异常一律吞掉并留痕，循环不退。
    返回线程对象（附 stop_event，置位即停）。"""
    with _REG_LOCK:
        th = _THREADS.get(name)
        if th is not None and th.is_alive():
            return th
        for k in [k for k, v in _THREADS.items() if not v.is_alive()]:
            _THREADS.pop(k, None)
        stop_event = threading.Event()

        def _loop():
            if first_delay > 0 and stop_event.wait(first_delay):
                return
            while not stop_event.is_set():
                try:
                    if gate is None or gate():
                        tick_fn()
                except Exception as e:  # noqa: BLE001
                    log_event(f"[daemon:{name}] tick 异常：{type(e).__name__}: {e}")
                    # v1.0.660（B9）：结构化失败计数——后台件的静默异常是体检最该看见的一类，
                    # 只在日志里躺着的话，体检正则聚类经常把它当噪声冲掉。
                    # record_failure 内部已自保护（计数+日志都不抛），此处不再包一层
                    # try/except——多包一层只会让"静默降级"棘轮计数上涨且无任何收益。
                    record_failure("daemon:%s" % name, type(e).__name__)
                if stop_event.wait(interval_s):
                    return

        th = threading.Thread(target=_loop, name=name, daemon=True)
        th.stop_event = stop_event
        _THREADS[name] = th
        th.start()
    return th


def spawn_deadline_watchdog(seconds, on_fire, name="deadline-watchdog"):
    """一次性看门狗守护线程：seconds 秒内未取消则调 on_fire()（触发后不再动作）。
    返回 (线程, cancel_event)——cancel_event.set() 提前取消，到期不触发。"""
    cancel = threading.Event()

    def _run():
        if cancel.wait(seconds):
            return
        try:
            on_fire()
        except Exception as e:  # noqa: BLE001
            log_event(f"[daemon:{name}] 看门狗触发异常：{type(e).__name__}: {e}")

    th = threading.Thread(target=_run, name=name, daemon=True)
    th.cancel_event = cancel
    th.start()
    return th, cancel
