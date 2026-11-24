# -*- coding: utf-8 -*-
"""v1.0.682（2026-11-16 08:30 事故加固）回归锁。

08:30 事故：进程活着但服务线程全停——各模块 fetch 悬挂（"点不开"）、
关闭确认轮询断链（15s 后才弹出 Windows 默认色的原生兜底框）、日志彻底无声。
本文件锁住四项加固：
  1. /api/ping 最轻探针（200 + ok，不碰配置锁）
  2. waitress 线程池 16→32（SSE 长连接占线程的余量）
  3. config 锁 5s 超时降级（持锁者卡死不得拖死全服务）
  4. 关闭降级 15s→8s + 前端看门狗 / 启动心跳在源码中
"""
from conftest import _src


def test_api_ping(app_client):
    """看门狗探针端点：最轻响应，不碰任何锁。"""
    c, _ = app_client
    r = c.get("/api/ping")
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is True
    assert isinstance(d.get("ts"), (int, float))


def test_waitress_thread_headroom():
    s = _src("backend.py")
    assert "threads=32" in s, "waitress 线程池须留 SSE 余量（08:30：16 线程被占满 → 界面整体假死）"


def test_boot_heartbeat_present():
    """启动心跳：08:30 那次'日志彻底无声'导致无法定位死亡时刻——心跳必须留下时间轴。"""
    s = _src("backend.py")
    assert "_boot_heartbeat" in s
    assert "api/ping" in s
    # v1.0.683：自检必须带鉴权 cookie——/api/* 有 token 闸门（v1.0.405 H1），
    # 不带 cookie 的自检永远 401，心跳全变「服务可能停摆」误报（10:02 实例实锤）
    assert "_hb_tok = _api_token()" in s
    assert "__wc_token=" in s


def test_config_lock_timeout_degrade(monkeypatch, tmp_path):
    """08:30 根因形态：某线程在锁内卡住（DPAPI/磁盘 IO）→ 所有配置读排队。
    加固：等锁 5s 超时 → 降级读缓存（或默认配置），服务保持响应、绝不挂死。"""
    import core.config_store as cs

    p = str(tmp_path / "c.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{}")
    monkeypatch.setattr(cs, "config_path", lambda: p)
    sentinel = {"wecom": {"cdp_port": 9999}, "schema_version": 2}
    cs._CFG_CACHE.update({"path": p, "stamp": (1, 1), "cfg": sentinel})

    class _StuckLock:
        """模拟「另一线程持锁卡死」：任何等待都失败（真实 RLock 同语义：acquire 超时返 False）。"""
        def acquire(self, timeout=None):
            return False
        def release(self):
            pass

    monkeypatch.setattr(cs, "_config_lock", _StuckLock())
    # 1) 有缓存：返回缓存深拷贝（不挂死、调用方改动不污染缓存）
    cfg = cs.load_config()
    assert cfg["wecom"]["cdp_port"] == 9999
    cfg["wecom"]["cdp_port"] = 1
    assert cs._CFG_CACHE["cfg"]["wecom"]["cdp_port"] == 9999
    # 2) 无缓存：返回默认配置（wecom.cdp_port 兜底 9222）
    cs._CFG_CACHE.update({"path": None, "stamp": None, "cfg": None})
    cfg2 = cs.load_config()
    assert cfg2["wecom"]["cdp_port"] == 9222
    assert isinstance(cfg2, dict) and "models" in cfg2


def test_close_fallback_shortened():
    from desktop import close_guard as cg
    assert cg._WEB_ASK_FALLBACK_S <= 8.5, "08:30「按 X 很久才弹窗」：降级时限须缩短到 8s"


def test_js_watchdog_present():
    s = _src("js", "01-core.js")
    assert "__hbTick" in s and "/api/ping" in s, "前端看门狗缺失：后端停摆时页面须自愈（toast + 重载）"
