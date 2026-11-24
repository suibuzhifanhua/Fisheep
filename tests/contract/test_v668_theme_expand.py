# -*- coding: utf-8 -*-
"""test_v668_theme_expand — v1.0.668 调色盘「全面扩面」回归锁。

用户："主题色调色盘全面优化一下目前覆盖范围不多"。667 版引擎只染 --brand 族 + --bg，
「强调/选中/当前位/链接/信息提示」走的 --accent 族（style.css 69 处消费）完全不跟。
668 起选中的强调色同时驱动 brand + accent + info 三族。

本锁钉住「扩面契约」：applyTheme 注入的 CSS 必须**同时**覆盖 --accent（强调族）
与 --brand（品牌族）——只染其一并退化为 667 的窄覆盖。派生值（ink/fg/soft 的
HSL 具体档位）不锁死，那是可维护的视觉参数；契约锁的是"两族都跟"的行为边界。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _js11():
    with io_open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "js", "11-workspace-kb-study.js"), encoding="utf-8") as f:
        return f.read()


io_open = open


def test_theme_engine_drives_accent_family():
    js = _js11()
    i = js.find("function applyTheme")
    assert i >= 0, "applyTheme 引擎不见了"
    # applyTheme 到下一个 function 之间的实现体
    end = js.find("\nfunction ", i + 1)
    body = js[i:end if end > 0 else i + 4000]
    # 668 契约：注入文本里必须出现 --accent（强调族），与 --brand（品牌族）并列
    assert "--accent:" in body, "调色盘引擎不再驱动 --accent 强调族——覆盖退化回 667 的窄覆盖"
    assert "--brand:" in body, "调色盘引擎不再驱动 --brand 品牌族"
    # 深色 fg 转亮分支仍在（v1.0.598 口径：深底文字必须翻亮，否则不可读）
    assert "body.dark" in body and "fgDark" in body, "深色模式强调色转亮分支丢失"


def test_theme_family_token_consumption_baseline():
    """扩面前提：accent 族在 CSS 里有真实消费（否则锁的是空契约）。
    基线 v1.0.668 实测 69 处；给个宽下限，令牌被大规模抽走时才红。"""
    import re
    with io_open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "css", "style.css"), encoding="utf-8") as f:
        css = f.read()
    n = sum(len(re.findall(r"var\(%s\)" % t, css))
            for t in ("--accent", "--accent-soft", "--accent-ink", "--accent-fg"))
    assert n >= 40, "accent 族消费骤降到 %d——扩面契约失去意义" % n
