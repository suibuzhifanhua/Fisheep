# -*- coding: utf-8 -*-
"""tests/test_v688_settings_freeze.py — v1.0.688「点设置必卡死」的根因回归锁。

事故现场
--------
用户报：进软件后点「设置」模块就卡死（v1.0.685~687 连续三个版本的自愈重载/
进程级重置都只治标未治本，v1.0.686 甚至误判为「与具体视图无关的 GPU 合成器问题」）。

实测根因（v1.0.688 定位并定案）
-------------------------------
v1.0.669 给设置页加的右侧分组导航里有这么两行：

    const place=()=>{ ...; view.classList.add("set-nav-on"); };      // 写 class
    new MutationObserver(place).observe(view,{attributes:true, attributeFilter:["class","style"]});

浏览器规范：DOMTokenList.add/remove/toggle **每次调用都重写 class 属性**——
哪怕 token 集合根本没变，也照样算一次属性变更。于是：

    MutationObserver 回调 place() → place() 写 class → 属性变更
      → 观察器再回调 place() → place() 再写 class → ……

**微任务死循环**：观察器回调是微任务，事件循环永远轮不到别的任务 → 渲染器主线程
100% CPU，页面渲染正常但 JS 全停（心跳信标断、CDP Runtime.evaluate 超时），
与后端健康与否完全无关。

修法：class 写入改成幂等（只有目标态 ≠ 现状才写），观察器最多多回调一次就收敛。

本文件锁的是**行为**不是文案
--------------------------
按 AGENTS.md「改动要配行为断言，不要 grep 源码文案」：不 grep 那句 classList 源码，
而是用 tests/fixture/settings_side_nav_harness.js 复刻「会触发这一循环」的
DOM 语义（classList 每次调用都通知 + 观察器按微任务投递），把 js/11 里**真实的**
initSettingsSideNav 抽出来喂进去，数观察器回调次数：

  · 修好的实现 → 回调次数收敛（个位数）→ verdict=ok
  · 自激的实现 → 回调次数爆表 → verdict=loop

并且**同一台测试台必须先证明自己有牙齿**：--buggy 模式喂的是 v1.0.669 的自激实现，
必须被判成 loop。如果哪天测试台退化成「什么都不报、恒绿」，这条控制用例会先红，
从而暴露测试台本身失效——避免本项目历史上那种「回归测试只 grep 文案、恒绿」的坑。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "fixture", "settings_side_nav_harness.js")
TARGET_JS = os.path.join(ROOT, "js", "11-workspace-kb-study.js")


def _node():
    """找 node：优先 PATH，其次项目自带的便携 node（组件/node/current/...）。"""
    from shutil import which
    p = which("node")
    if p:
        return p
    import glob
    for pat in (r"C:\组件\node\current\node-v*\node.exe",
                os.path.join(ROOT, "组件", "node", "current", "node-v*", "node.exe")):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def _run(buggy=False, calibrate=False):
    node = _node()
    if not node:
        pytest_skip("本机找不到 node（可装便携 node 或加进 PATH）")
    cmd = [node, HARNESS, TARGET_JS]
    if calibrate:
        cmd.append("--calibrate")
    elif buggy:
        cmd.append("--buggy")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, cwd=ROOT)
    out = (p.stdout or "").strip().splitlines()
    if not out:
        raise AssertionError("测试台没有输出 JSON：stderr=%s" % (p.stderr or "")[:500])
    try:
        return json.loads(out[-1])
    except Exception as e:
        raise AssertionError("测试台输出不是 JSON：%r（%s）" % (out[-1][:300], e))


def pytest_skip(reason):
    import pytest
    pytest.skip(reason)


# ---------------------------------------------------------------- 用例

def test_settings_side_nav_has_no_observer_self_loop():
    """真实实现：观察器回调次数必须收敛（不再自激死循环）。"""
    r = _run(buggy=False)
    assert r.get("verdict") == "ok", (
        "设置页右侧分组导航出现观察器自激死循环（点「设置」即卡死）："
        "verdict=%s observerCalls=%s detail=%s"
        % (r.get("verdict"), r.get("observerCalls"), r.get("detail"))
    )
    assert r.get("observerCalls", 999) <= 20, (
        "观察器回调次数没收敛（%s 次）——class 写入不是幂等的，"
        "MutationObserver 会自己唤醒自己" % r.get("observerCalls")
    )


def test_harness_semantics_match_chromium():
    """**测试台自身的标定**：垫片里的 classList 语义必须与 Chromium 实测一致。

    这条是整个行为锁的地基：测试台把「什么算一次属性变更」学错了，
    上面两条用例会一起变成假绿（测台测不出循环，却报 ok）。
    Chromium 实测值（Edge headless，2026-11-16）：
      · 重复 add 已存在的 class    → 30 次（**无条件**重写 class 属性）
      · 重复 toggle(token, true)   → 1 次（状态没变就不写不通知）
      · 重复 remove 不存在的 class → 30 次（**无条件**通知）
      · v1.0.669 的 place() 语义    → 30 次（死循环）
      · 无条件 toggle 的 place()   → 1 次（toggle 幂等）
    """
    r = _run(calibrate=True)
    expected = {
        "repeatedAdd": 30,
        "repeatedToggleTrue": 1,
        "repeatedRemoveAbsent": 30,
        "originalPlaceSemantics": 30,
        "unconditionalTogglePlace": 1,
    }
    assert r == expected, (
        "测试台的 classList 语义与 Chromium 实测不符 → 行为锁失真（会假绿）：\n"
        "  实测 %s\n  期望 %s" % (json.dumps(r, ensure_ascii=False),
                                json.dumps(expected, ensure_ascii=False))
    )


def test_harness_can_detect_the_original_bug():
    """**控制用例**：测试台必须能抓到 v1.0.689 之前的自激实现。

    没有这条，测试台哪天退化成「恒绿」也无人察觉——本项目吃过这种亏
    （回归测试只 grep 源码文案、不验行为，锁子空转十几个版本）。
    """
    r = _run(buggy=True)
    assert r.get("verdict") == "loop", (
        "测试台失灵：喂自激实现（v1.0.669 原版）都没判成 loop → "
        "这条行为锁根本抓不住回归。verdict=%s observerCalls=%s"
        % (r.get("verdict"), r.get("observerCalls"))
    )
    assert r.get("observerCalls", 0) > 100, (
        "测试台失灵：自激实现的观察器回调次数只有 %s（应爆表）"
        % r.get("observerCalls")
    )


def test_settings_class_write_is_idempotent():
    """源码层的行为护栏：class 的增删必须先比对现状（幂等）。

    这条是**行为契约的静态镜像**——真正的行为锁是上面两条；这条只防
    「有人把幂等判断删掉、却又改了别处让它看起来还在跑」。
    """
    import io
    src = io.open(TARGET_JS, encoding="utf-8").read()
    i = src.find("function initSettingsSideNav(")
    assert i > 0, "js/11-workspace-kb-study.js 里找不到 initSettingsSideNav"
    j = src.find("\nfunction ", i + 10)
    body = src[i:j if j > 0 else len(src)]
    # 断言：class 的写入走的是带现状比对的辅助函数，而不是裸 classList.add/remove
    assert "_setNavOn" in body, (
        "initSettingsSideNav 里没有幂等写 class 的辅助函数 _setNavOn —— "
        "MutationObserver 会自己唤醒自己（点设置即卡死）"
    )
    assert "view.classList.add(" not in body and "view.classList.remove(" not in body, (
        "initSettingsSideNav 里仍有裸的 classList.add/remove —— "
        "DOMTokenList 每次调用都重写 class 属性，观察器会自激"
    )


if __name__ == "__main__":
    sys.exit(0)
