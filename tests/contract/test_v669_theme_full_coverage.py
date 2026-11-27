# -*- coding: utf-8 -*-
"""test_v669_theme_full_coverage — v1.0.669 调色盘「全覆盖」+ 设置右侧导航 回归锁。

用户："点了变颜色以后还是只有部分生效 并且设置模块右侧导航也没有"。
668 只染了 var() 消费点；style.css 里 ~46 处品牌/强调**硬编码**
（渐变深端 #08663a、rgba(7,193,96,*) 光晕、用户气泡残留蓝）根本不走 var()。
669 全部令牌化 + 设置页补右侧分组导航。

契约锁（对旧形态会红）：
  ① 引擎必须派生 --brand-2 / --brand-rgb / --user-fg / --side-fg 四枚新令牌；
  ② style.css 消费位不得再出现裸 rgba(7,193,96, / #08663a / #2ee38a（剥注释后计数为 0）；
  ③ 语义成功绿 --ok 族不得被主题令牌取代（防过度令牌化误染成功/警告/错误）；
  ④ 设置右侧导航三件套齐：JS initSettingsSideNav + CSS #setSideNav + 调用位。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _read(*rel):
    with io.open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


def _strip_css_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_engine_emits_new_tokens():
    js = _read("js", "11-workspace-kb-study.js")
    i = js.find("function applyTheme")
    assert i >= 0
    end = js.find("\nfunction ", i + 1)
    body = js[i:end if end > 0 else i + 6000]
    for tok in ("--brand-2", "--brand-rgb", "--user-fg", "--side-fg"):
        assert tok in body, f"主题引擎不再派生 {tok}（669 全覆盖退化）"


def test_no_raw_brand_hex_at_consumption_sites():
    css = _strip_css_comments(_read("css", "style.css"))
    for bad in ("rgba(7,193,96,", "#08663a", "#2ee38a"):
        assert bad not in css, f"style.css 消费位仍有硬编码品牌色 {bad!r}（应走令牌）"


def test_semantic_ok_family_not_themed():
    """过度令牌化的反向锁：成功绿 --ok 族必须仍是独立字面量（语义色不随主题）。"""
    css = _strip_css_comments(_read("css", "style.css"))
    assert "--ok:#07c160" in css, "语义成功绿 --ok 被改动——状态色不该跟主题"
    assert "--err:#d4380d" in css, "语义错误红 --err 被改动"


def test_settings_side_nav_wired():
    js11 = _read("js", "11-workspace-kb-study.js")
    js02 = _read("js", "02-nav-quicklinks.js")
    css = _read("css", "style.css")
    assert "function initSettingsSideNav" in js11, "设置右侧导航 JS 缺失"
    assert "initSettingsSideNav()" in js02, "设置视图入口未调用右侧导航初始化"
    assert "#setSideNav" in css, "右侧导航浮层样式缺失"
