# -*- coding: utf-8 -*-
"""tests/unit/test_v737_visual_polish.py — v1.0.737 界面视觉审查 10 项全修回归锁

用户「全做」（2026-11-21）：把 `界面视觉审查报告_v1.0.736.md` 的 5 项 P1 +
5 项 P2 全部落地。本文件锁住每处改动的代码契约，防止后续改版回退：

  ① 发送钮二次覆盖  ② 视图尺度  ③ 文本符号图标  ④ 对话页空态/控件
  ⑤ 左列容器  ⑥ 组件页  ⑦ 统一 EmptyState  ⑧ 状态栏  ⑨ 主色分工  ⑩ 散值
视觉部分另由 1914×1024 真浏览器渲染复核（浅色 + 深色）。
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


def _css():
    return _read("css", "style.css")


def _css_nc():
    return re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)


def _html():
    return _read("index.html")


# ───────────────────── ① 发送钮：删二次覆盖、回归主色、统一尺寸 ─────────────────────
def test_send_button_single_definition_and_accent():
    raw, nc = _css(), _css_nc()
    # 只在「去注释后的规则文本」里查旧色值：历史注释保留了旧硬编码色的说明字面，
    # 那是文档不是生效样式（v1.0.598 附近就有 #3b82f6 的沿革记录）。
    assert "#1a1c21" not in nc, "近黑二次覆盖的字面色值又回来了"
    assert "#3b82f6" not in nc and "#2563eb" not in nc, "旧硬编码蓝渐变未清理"
    blocks = re.findall(r"\.composer-send\s*\{[^}]*\}", nc)
    assert len(blocks) == 1, "发送钮主定义应只有一处（后段二次覆盖已删）: %r" % blocks
    one = blocks[0]
    assert "linear-gradient(135deg,var(--accent),var(--accent-ink))" in one
    assert "width:var(--control-h)" in one and "height:var(--control-h)" in one
    assert ".composer-send.stopping { background:var(--err)" in nc
    # 深色块不得再覆写发送钮背景
    assert "body.dark .composer-send" not in nc


# ───────────────────── ② 视图尺度：全局 --content-max 居中 ─────────────────────
def test_all_views_share_content_max():
    nc = _css_nc()
    assert "--content-max:1440px" in nc
    m = re.search(r"\.view\s*\{[^}]*\}", nc)
    assert m, ".view 规则缺失"
    assert "max-width:var(--content-max)" in m.group(0)
    assert "margin-left:auto" in m.group(0) and "margin-right:auto" in m.group(0)
    assert "width:100%" in m.group(0)
    # 不回退 v1.0.735：设置页仍是 1200px 长表单阅读上限
    assert "max-width:1200px" in nc
    assert "#view-settings { width:100%; max-width:1200px" in nc


# ───────────────────── ③ 文本符号图标 → 统一 SVG ─────────────────────
def test_text_glyph_icons_replaced():
    html = _html()
    for bad in (">＋</button>", ">✕</button>", ">✖</button>",
                "▶ 运行</button>", "⚙️ 固化为技能</button>",
                "＋ 添加</button>", "＋ 新建技能</button>", "＋ 新增经验</button>"):
        assert bad not in html, "控件里仍有文本符号图标: %r" % bad
    assert html.count('data-ic="xmark"') == 10   # 8 弹窗关闭 + 清空搜索 + 关闭大图
    assert html.count('data-ic="plus"') >= 5
    # 帮助正文 / kbd 里的按键文案按报告口径保留
    assert "<kbd>＋</kbd>" in html and "<kbd>▶ 重放</kbd>" in html
    for name in ("01-core.js", "02-nav-quicklinks.js", "05-connectors-status.js",
                 "10-chat.js", "11-workspace-kb-study.js", "03-experiences.js"):
        # 去掉整行 // 注释：历史说明里保留了旧写法的字面（如 02 顶部注释），
        # 那是文档不是控件，不能算「仍有文本符号图标」。
        js = re.sub(r"^\s*//.*$", "", _read("js", name).replace("\r\n", "\n"), flags=re.M)
        assert 'textContent="＋' not in js, name
        assert 'textContent="⚙️' not in js, name
        assert 'textContent="▶' not in js, name
    # 纯图标按钮用结构选择器自动居中，无需给每个按钮加 class
    assert "button > .ic:only-child, button > [data-ic]:only-child .ic" in _css_nc()


# ───────────────────── ④ 对话页空态 + 输入条控件同高 ─────────────────────
def test_chat_empty_state_and_control_height():
    html = _html()
    seg = html.split('id="view-chat"', 1)[1].split("</section>", 1)[0]
    assert 'class="empty"' in seg and 'class="em-ic"' in seg
    assert 'class="em-t"' in seg and 'class="em-sub"' in seg
    assert "Fisheep · 随时待命" in seg
    nc = _css_nc()
    assert "--control-h:34px" in nc
    assert "min-height:var(--control-h)" in nc          # 文字下拉
    assert nc.count("var(--control-h)") >= 5            # 图标钮/发送钮/下拉共用同一标尺
    assert "margin-bottom:6px;" in nc                  # 输入区与工具栏呼吸


# ───────────────────── ⑤ 工作台左列统一容器 ─────────────────────
def test_work_left_pane_blocks_unified():
    html = _html()
    assert html.count("pane-block") == 4, "实时预览 + 3 个 .sec 都应有 pane-block"
    assert 'class="shotbox pane-block"' in html
    assert html.count('class="sec pane-block"') == 3
    nc = _css_nc()
    assert ".pane-block { background:var(--panel2); border:1px solid var(--border);" in nc
    assert ".livecard .scroll > .pane-block { margin:0; flex:0 0 auto; }" in nc
    assert "display:flex; flex-direction:column; gap:10px;" in nc


# ───────────────────── ⑥ 组件页两行结构 + 空态引导 ─────────────────────
def test_components_page_structure():
    html = _html()
    assert 'class="comp-empty" id="compEmpty"' in html
    assert "等待组件体检结果" in html
    nc = _css_nc()
    assert "grid-template-columns:minmax(0,1fr) auto auto" in nc   # comp-rem 两行
    assert ".comp-rem-note { color:var(--muted); font-size:var(--fs-xs); grid-column:1 / -1; grid-row:2; }" in nc
    assert "min-height:360px" in nc
    js = _read("js", "11-workspace-kb-study.js")
    assert "function compEmptyHtml(title, desc)" in js
    assert 'if(!box.innerHTML.trim()) box.innerHTML=compEmptyHtml(' in js
    assert 'box.innerHTML=compEmptyHtml("体检失败"' in js


# ───────────────────── ⑦ 统一 EmptyState ─────────────────────
def test_unified_empty_state():
    core = _read("js", "01-core.js")
    assert "function showEmpty(el, text, opt)" in core
    assert "const o = opt || {};" in core
    assert 'icAny(o.ic || "box")' in core
    assert 'class="empty empty-state"' in core
    assert "📭" not in core and "🧩" not in core
    assert 'o.title' in core and "o.action" in core
    nc = _css_nc()
    assert "min-height:220px" in nc
    assert "justify-content:center" in nc
    assert ".empty .em-ic .ic, .empty .em-ic svg.ic" in nc
    # 各调用点去掉彩色 emoji 前缀、改传 opt
    for name, old in (("04-skills.js", "🧩 暂无技能"),
                      ("08-templates.js", "📋 暂无模板"),
                      ("11-workspace-kb-study.js", "📂 产物库为空"),
                      ("05-connectors-status.js", "⚠️ 加载失败")):
        assert old not in _read("js", name), name
    chat = _read("js", "10-chat.js")
    assert "showEmpty(pv," in chat and 'style="padding:40px 16px"' not in chat
    # 只锁空态区：10-chat.js 别的UI（模式选择弹窗）按报告口径仍保留文案 emoji
    empty_seg = chat[chat.find("showEmpty(pv,"):chat.find("showEmpty(pv,") + 900]
    assert "💬" not in empty_seg and "🤖" not in empty_seg


# ───────────────────── ⑧ 状态栏靠右 + 标签宽度令牌 ─────────────────────
def test_status_bar_alignment():
    nc = _css_nc()
    assert "--label-w:72px" in nc
    assert ".work-status #wecomKv2 { margin-right:auto; }" in nc
    assert "width:var(--label-w)" in nc
    assert nc.count("width:var(--label-w)") >= 2
    assert "min-width:64px" not in nc and "width:96px" not in nc


# ───────────────────── ⑨ 主色分工 ─────────────────────
def test_semantic_color_division():
    raw, nc = _css(), _css_nc()
    assert "--ok:#07c160" in raw, "语义成功绿是历史事实，不得拆成近似色"
    assert "主色分工定稿" in raw
    assert ".elem.t-btn { border-left:3px solid var(--ok); }" in nc
    assert "border-left:3px solid var(--brand); } .elem.t-btn" not in nc
    # 发送钮回归 --accent；主按钮仍走 --brand 渐变（分工不靠拆值）
    assert "background:linear-gradient(135deg,var(--brand-ink),var(--brand-2))" in nc


# ───────────────────── ⑩ 散值令牌化 ─────────────────────
def test_scatter_values_tokenized():
    nc = _css_nc()
    for tok in ("--r-hair:2px", "--r-xs:4px", "--bubble-r:16px 16px 6px 16px",
                "--fs-3xl:42px", "--fs-display:36px"):
        assert tok in nc, tok
    assert not re.search(r"font-size:\s*\d+px", nc), "仍有硬编码 px 字号"
    assert not re.search(r"border-radius:\s*\d+px", nc), "仍有硬编码 px 圆角"
    assert "border-radius:var(--bubble-r)" in nc
    assert "font-size:var(--fs-3xl)" in nc and "font-size:var(--fs-display)" in nc
    assert "font-size:var(--fs-2xs)" in nc and "font-size:var(--fs-lg)" in nc


# ───────────────────── 打包门禁 ─────────────────────
def test_frontend_structure_gate_still_green():
    from core.frontend_check import run_all
    errors = run_all(ROOT)
    assert not errors, errors


def test_v735_settings_layout_not_regressed():
    """② 收敛尺度不得回退 v1.0.735 的设置页占比成果。"""
    css = _css()
    assert "#view-settings { max-width:860px; }" not in css
    assert "@media (max-width:1039px)" in css
    assert "#view-settings.set-nav-on { padding-right:170px; }" in css
