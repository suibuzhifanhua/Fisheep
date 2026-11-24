# -*- coding: utf-8 -*-
"""tests/unit/test_v735_settings_layout.py — v1.0.735 设置页排版回归锁

用户截图反馈（2026-11-20）：
  ① 设置界面占比太小、不随窗口自适应——1914px 窗口下内容只有 860px 靠左一条，
     右侧上千像素空白，分组导航孤零零飘在内容右缘外；
  ② 开关以及描述不对齐、整体风格不一致——4 个开关挤在 flex-wrap 的 .row 里，
     标签长短不一导致换行后开关基线参差；颜色选择器被拉成 100% 白条、
     粗细滑块是浏览器默认蓝 thumb，与整页黑白体系打架。

本文件锁住 HTML 结构、CSS 规则与 JS 窄窗行为，防止后续改动把布局打回
860px 窄栏或把控件改回默认样式。视觉部分另由 1914×1024 / 1000×800
两档真浏览器渲染校验（不改本文件，属一次性目检）。
"""
import io
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def html():
    return _read("index.html")


@pytest.fixture(scope="module")
def css():
    return _read("css/style.css")


@pytest.fixture(scope="module")
def navjs():
    return _read("js/11-workspace-kb-study.js")


@pytest.fixture(scope="module")
def statusjs():
    return _read("js/05-connectors-status.js")


# ---------------------------------------------------------------- ① 占比 / 自适应
def test_settings_view_is_fluid_centered_and_not_860(css):
    """860px 硬上限必须消失；改成 width:100% + 1200px 阅读上限 + 水平居中。"""
    assert "#view-settings { max-width:860px; }" not in css
    assert "max-width:1200px" in css
    assert "margin-left:auto" in css and "margin-right:auto" in css
    assert "box-sizing:border-box" in css
    # v1.0.677 那条写反的老规则（窄窗才解除上限，且被后面的 860px 覆盖）不得复活
    assert "#view-settings { max-width:none; width:100%; }" not in css
    assert "@media (max-width:1440px)" not in css


def test_narrow_window_hides_side_nav_and_reclaims_padding(css, navjs):
    """窄窗隐藏右侧 fixed 导航 + 撤回 170px 让位，JS 与 CSS 同阈值 1040。"""
    assert "@media (max-width:1039px)" in css
    assert "#setSideNav { display:none !important; }" in css
    assert "#view-settings.set-nav-on { padding-right:14px; }" in css
    assert "window.innerWidth < 1040" in navjs
    assert "_setNavOn(false)" in navjs
    # 宽窗仍保留精确避让，导航贴 view 右缘内侧
    assert "#view-settings.set-nav-on { padding-right:170px; }" in css


# ---------------------------------------------------------------- ② 开关对齐
def test_four_toggles_are_equal_grid_cards(html, css):
    """2×2 等宽开关卡：开关左、主标题 + 弱化说明右，四项同高同基线。"""
    assert 'class="set-toggle-grid"' in html
    assert html.count('class="field st-item"') == 4
    assert html.count('class="st-txt"') == 4
    for title in ("自动启动绑定目标", "自动注入 CDP", "静默后台执行",
                  "接管窗口显示高亮边框"):
        assert "<b>%s</b>" % title in html
    # 旧结构：外层 .field 里再套一个 <label>，文字是裸文本流 → 换行必错位
    assert '<label class="field inl-202"><label>' not in html
    assert '<label class="field inl-203"><label>' not in html
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert ".set-toggle-grid > .st-item {" in css
    assert "min-height:62px" in css
    # checkbox 的 margin/padding 必须显式覆盖 #view-settings 的 ID 级规则
    assert "#view-settings .set-toggle-grid > .st-item > input" in css


# ---------------------------------------------------------------- ③ 控件风格一致
def test_color_and_range_controls_match_global_style(html, css):
    """色块复用主题调色盘 64×32；滑块去白框、thumb 走 --ink 黑白体系。"""
    assert 'class="set-ctl-grid"' in html
    assert 'class="set-ctl-line"' in html
    assert 'class="set-color"' in html and 'class="set-range"' in html
    assert 'class="set-ctl-val" id="cdpHlColorNote"' in html
    assert "<span id=\"cdpHlWidthNote\">8</span> px" in html

    color_rule = re.search(r"#view-settings \.set-color \{[^}]*\}", css)
    assert color_rule and "width:64px" in color_rule.group(0)
    assert color_rule and "height:32px" in color_rule.group(0)
    # 与「界面偏好 · 主题调色盘」的既有色块同尺寸
    assert ".inl-178 { width:64px; height:32px" in css

    range_rule = re.search(r"#view-settings \.set-range \{[^}]*\}", css)
    assert range_rule and "border:none" in range_rule.group(0)
    assert range_rule and "background:transparent" in range_rule.group(0)
    assert range_rule and "accent-color:var(--ink)" in range_rule.group(0)
    thumb_rule = re.search(
        r"#view-settings \.set-range::-webkit-slider-thumb \{[^}]*\}", css)
    assert thumb_rule and "background:var(--ink)" in thumb_rule.group(0)
    assert ".set-ctl-line { display:flex; align-items:center; gap:10px; height:32px; }" in css


def test_all_cdp_ids_and_handlers_preserved(html, statusjs):
    """重排不得丢任何 id / onchange / 脏标记：后端保存链路按 id 读取。"""
    for needle in ('id="opt_auto_launch"', 'id="opt_auto_cdp"',
                   'id="opt_silent_mode"', 'id="opt_cdp_highlight"',
                   'id="opt_cdp_highlight_color"', 'id="opt_cdp_highlight_width"',
                   'id="cdpHlColorNote"', 'id="cdpHlWidthNote"'):
        assert needle in html, needle
    assert html.count("_cdpHlDirty=true;saveWecomOpt()") == 2
    assert "cdpHlWidthPreview(this.value)" in html
    for needle in ('$("opt_cdp_highlight_color")', '$("cdpHlColorNote")',
                   '$("cdpHlWidthNote")', "payload.cdp_highlight_color",
                   "payload.cdp_highlight_width"):
        assert needle in statusjs, needle


# ---------------------------------------------------------------- ④ 统一与响应式
def test_block_notes_unified_without_touching_inline_status(css, html):
    """块级说明统一底色/圆角/行高；.row 里的行内状态不被撑成方块。"""
    assert "#view-settings .card > .note," in css
    assert "#view-settings .card > .field > .note {" in css
    assert "display:block; padding:8px 10px; border-radius:var(--r-sm)" in css
    # 行内状态仍用裸 <span class="note">，结构未变
    assert '<span class="note" id="cdpPortNote">' in html
    assert '<span class="set-ctl-val" id="cdpHlColorNote">' in html


def test_grids_collapse_to_single_column_on_narrow(css):
    assert "@media (max-width:760px)" in css
    assert ".set-toggle-grid, .set-ctl-grid { grid-template-columns:1fr; }" in css


def test_dead_inl_rules_removed(html, css):
    """旧 .field{flex:1} 三条规则已无引用，删掉不留静态噪声。"""
    for name in ("inl-202", "inl-203", "inl-204"):
        assert name not in html
        assert (".%s {" % name) not in css


# ---------------------------------------------------------------- ⑤ 打包门禁
def test_frontend_structure_gate_still_green():
    """改完 HTML/CSS/JS 后，打包前前端结构门禁必须仍全绿。"""
    from core.frontend_check import run_all
    errors = run_all(ROOT)
    assert not errors, errors
