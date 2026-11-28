# -*- coding: utf-8 -*-
"""services.event_notify — 企微群事件推送（v1.0.783，12 款 Agent 技术借鉴 F2）
=================================================================
用户不在电脑前时，任务的关键状态（阻塞需处理 / 长任务完成 / 定时任务失败 /
预算将尽）以前只有软件内 Toast 与断点——人不在就干等。这里把四类事件经
已配置的群机器人 webhook 推一条 text 消息到企微群。

纪律（与项目各处一致）：
  · 开关 feature_flags.event_notify（默认关——往群里发消息是外部副作用，
    未经用户勾选不推）+ 已配置 wecom.webhook 两者同时满足才推；
  · 全程静默：开关关 / 无 webhook / 推送失败 → 不做任何事，**永不抛异常**，
    绝不阻塞调用方（agent 主循环 / 调度 worker 都不该被一条群消息卡住）；
  · 非阻塞：推送走 daemon 线程（webhook 是同步 HTTP，最长 10s 超时，
    不能挂在 SSE 流里）；
  · 同 kind 冷却 120s：防 ping-pong（如 blocked 移交自限 3 次、预算预警反复
    进入判定窗）把群刷屏。
"""
import threading
import time

_COOLDOWN_SEC = 120.0
_last = {}
_last_lock = threading.Lock()


def notify_event(kind, text):
    """推一条事件到企微群。kind ∈ blocked / done / sched / budget / approval / audit（冷却键）。
    v1.0.788（P0-2）新增：approval（安全中心出现待审批）/ audit（月报审计终态有红/待定）。
    静默：不满足条件或任何异常都静默返回，调用方不需要 try。"""
    try:
        from core.config_store import load_config
        cfg = load_config() or {}
        if not (cfg.get("feature_flags") or {}).get("event_notify"):
            return
        url = (cfg.get("wecom") or {}).get("webhook") or ""
        if not url:
            return
        msg = str(text or "").strip()
        if not msg:
            return
        now = time.time()
        with _last_lock:
            if now - _last.get(kind, 0.0) < _COOLDOWN_SEC:
                return
            _last[kind] = now
        t = threading.Thread(target=_push, args=(url, msg[:500]),
                             daemon=True, name="event-notify")
        t.start()
    except Exception:  # noqa: BLE001
        pass   # silent-ok: 推送是增值旁路，任何异常都不该反噬主流程


def _push(url, msg):
    try:
        from bridge import send_webhook
        send_webhook(url, "【Fisheep】" + msg, "text")
    except Exception:  # noqa: BLE001
        pass   # silent-ok: 群消息发不出去只少一条通知，不能影响任务本身
