# -*- coding: utf-8 -*-
"""v1.0.685（11-16「点模块卡死」取证）回归锁。

12:10/12:12 实例卡死但无崩溃、无日志线索——684 的信标只在内存，进程一死
证据就没了。本版把信标落盘 + 启动时读回留日志：下次卡死后重启，日志直接
给出「死在哪个视图、信标停了多久」。本文件锁住：
  1. 信标带视图落盘（js_beacon.json 原子写）
  2. 启动读回上次信标并留 [webview] 日志
  3. /api/ping 携带 js_last_view
"""
from conftest import _src


def test_beacon_persists_view():
    s = _src("backend.py")
    assert "_jsbeacon_persist" in s
    assert "js_beacon.json" in s
    assert "os.replace(" in s, "落盘必须原子写（tmp+replace），半截文件比没有更坏"


def test_boot_reads_previous_beacon():
    """上次实例怎么死的，本次启动就要在第一行日志里接住。"""
    s = _src("backend.py")
    assert "_read_js_beacon_file" in s
    assert "上次 JS 信标" in s, "启动读回上次信标必须留 [webview] 日志"


def test_ping_carries_last_view():
    s = _src("backend.py")
    assert "js_last_view" in s


def test_persist_failure_cannot_slow_beacon():
    """落盘失败只告警一次——5s 一次的信标不得被磁盘错误拖成日志洪水。"""
    s = _src("backend.py")
    assert "_persist_warned" in s
