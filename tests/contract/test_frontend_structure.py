# -*- coding: utf-8 -*-
"""test_frontend_structure — 前端结构域回归锁（v1.0.646 按域并入 v1.0.615 聊天滚动锁）。

背景：index.html 结构破坏连续 3 次走到用户手里（CSS 花括号错位致未绑定
单栏失效、删块残留 </div> 致导航全部失效）。本文件把 tools/check_frontend.py
的全套校验纳入 pytest——任何破坏结构的改动在回归时当场红掉。

v1.0.646 归并：原 test_v615_chat_scroll.py（11 条）并入本文件的 TestChatScrollContainer。
两者同属"不用浏览器就能判的前端结构锁"——前者守 index.html 的整体结构，后者守
聊天滚动容器这条具体链路不被改回空写。

| 原文件                        | 版本出处  | 守护对象                     | 用例数 |
|-------------------------------|-----------|------------------------------|--------|
| （本文件模块级）              | —         | index.html 结构四项校验      |   4    |
| test_v615_chat_scroll.py      | v1.0.615  | 聊天滚动容器/回最新按钮      |  11    |
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.check_frontend import (check_css_braces, check_div_balance,  # noqa: E402
                                  check_dup_ids, check_inline_style_ratchet,
                                  check_script_style_close)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(*rel):
    return io.open(os.path.join(_ROOT, *rel), encoding="utf-8").read()


def _html():
    with open(os.path.join(_ROOT, "index.html"), encoding="utf-8") as f:
        return f.read()


def test_div_balance():
    errors = []
    check_div_balance(_html(), errors)
    assert not errors, errors
    print("PASS index.html div 嵌套配对")


def test_no_dup_ids():
    errors = []
    check_dup_ids(_html(), errors)
    assert not errors, errors
    print("PASS index.html 无重复 id")


def test_script_style_closed():
    errors = []
    check_script_style_close(_html(), errors)
    assert not errors, errors
    print("PASS index.html script/style 标签闭合")


def test_css_braces_balanced():
    errors = []
    check_css_braces(_html(), errors)
    assert not errors, errors
    print("PASS index.html CSS 花括号平衡")


# ============================================================ v1.0.615 聊天滚动容器
class TestChatScrollContainer:
    """聊天跟随/「↓ 回到最新」必须盯真正的滚动容器（v1.0.615）。

    用户反馈："聊天界面往回翻了以后没有按钮到最新的答复界面"。
    复现出来的根因不是没写按钮——v1.0.595 就写了，而是它一直挂在**滚不动的元素**上：
    `.conv-view` 自 v1.0.495 起刻意不做滚动容器（css/style.css:1103，为了修「滚轮被
    overscroll-behavior:contain 吞掉」），滚动条在宿主 #opPane。于是
    `cv_x.scrollHeight - cv_x.scrollTop - cv_x.clientHeight` 恒等于 0（CDP 实测：注入 60 条
    消息后 pane=0、host=2026px），导致三件事同时坏掉：
      · 按钮显示条件永不成立 → 翻上去没有回最新入口（用户看到的症状）；
      · 跟随判定永远算成「在底部」→ v1.0.594 那套「用户上翻就别拽回去」形同虚设；
      · `pane.scrollTop = pane.scrollHeight` 全是空写 → 新消息也不会把视图带到最新。
    行为判据在 build/_test_chat_jump.py（22 项，headless Chrome 量真实渲染树）；
    本文件是**结构锁**——防止以后有人把某一行改回 `pane.scrollTop=` 就悄悄退回旧行为。
    """

    CHAT = _src("js", "10-chat.js")
    RENDER = _src("js", "09-chat-render.js")
    INIT = _src("js", "12-init.js")
    CSS = _src("css", "style.css")

    def _fn(self, name, src=None):
        src = src if src is not None else self.CHAT
        i = src.index("function " + name)
        j = src.index("\nfunction ", i + 1)
        return src[i:j]

    def test_scroller_resolver_exists_and_is_used_everywhere(self):
        assert "function _scrEl" in self.CHAT, "容器解析器被删了"
        for fn in ("__scrollBottom", "__dist", "__nearBottom", "__bindStickEl"):
            body = self._fn(fn)
            assert "_scrEl(" in body or "__dist(" in body, "%s 又回去盯 cv_* 元素了" % fn
        assert "sc.scrollTop = sc.scrollHeight" in self._fn("__scrollBottom"), "滚底没落在解析出的容器上"

    def test_no_raw_scroll_write_left_in_chat_render(self):
        """js/09 里两处 `pane.scrollTop=pane.scrollHeight` 是本次病灶之一，必须走 __scrollBottom。"""
        assert "pane.scrollTop=pane.scrollHeight" not in self.RENDER, "addMsg/addToolCard 又直接写空 scrollTop"
        assert self.RENDER.count("__scrollBottom(paneId)") >= 2, self.RENDER.count("__scrollBottom(paneId)")

    def test_jump_button_self_heals_after_innerhtml_rewrite(self):
        """clearChat / 恢复历史会整片重写容器，按钮被抹掉而绑定是一次性的——不重建就永远没入口。"""
        body = self._fn("__syncJump")
        assert "isConnected" in body and "__jumpEl(sc)" in body, body[:200]

    def test_pane_host_stray_filter_excludes_chrome_and_no_duplicate_scratch(self):
        """_paneEl 的"收拢静态内容"分支：① 按钮属界面外壳，不能被当对话内容搬进容器；
        ② key==='scratch' 时刚 new 的 v 就是那个容器，再 new 一个同 id 的会让 DOM 里出现
        两个 #cv_scratch（实测：一个装占位、一个空的，消息落在空的那个）。"""
        i = self.CHAT.index("const stray =")
        seg = self.CHAT[i:i + 700]
        assert 'jump-latest' in seg.split("\n")[1] + seg.split("\n")[2], seg[:300]
        assert 'key === "scratch"' in seg, "没有复用刚创建的 scratch 容器"
        assert 'document.getElementById("cv_scratch")' in seg

    def test_boot_binds_both_surfaces_and_emptiness_check_ignores_chrome(self):
        assert '__bindStick("setChatPane")' in self.INIT, "对话页开屏不绑定 → 该面版永远没有回最新入口"
        assert "__chatKids" in self.INIT, "判对话是否为空仍在数界面外壳（按钮一出现 clearChat 就永不触发）"
        assert '"jump-latest"' in self.INIT

    def test_switching_conversation_scrolls_after_the_pane_is_shown(self):
        """切换会话时先 _showPane 再滚底：切换前该会话还是 display:none，
        内容高度算不出来，那时的 scrollTop 赋值等于什么都没做。"""
        i = self.CHAT.index("if(pane) __scrollBottom(key, true);")
        assert "_showPane(key);" in self.CHAT[max(0, i - 400):i], "滚底又排到显示之前了"
        # 先剥行注释再断言：本项目的注释惯例是「WHY + 引用被替换掉的旧代码原文」，
        # 不剥就会拿自己写的说明当残留（v1.0.613 那轮同类误判已踩过一次）。
        code = re.sub(r"//[^\n]*", "", self.CHAT)
        assert "pane.scrollTop = pane.scrollHeight" not in code and "pane.scrollTop=pane.scrollHeight" not in code, \
            "js/10 里还留着对 cv_* 的空写"

    def test_showpane_resyncs_the_button(self):
        body = self._fn("_showPane")
        assert "__syncJump" in body, "切会话后按钮显隐不重算（长会话切短会话会挂着不动）"

    def test_pill_shape_is_sticky_and_not_full_width(self):
        m = re.search(r"\.jump-latest\s*\{([^}]*)\}", self.CSS)
        assert m, ".jump-latest 规则没了"
        body = m.group(1)
        assert "position:sticky" in body and "bottom:8px" in body
        assert "width:max-content" in body, "块级按钮 + margin auto 不限宽会拉成整条通栏"
        assert "display:block" in body, "隐藏靠 JS 写内联 display:none，CSS 必须保持 block 才可被扫描器测量"

    def test_theme_toggle_rebuilds_the_pill(self):
        """浅色态创建的 pill 在运行时切深色后不会重算 var(--muted)（实测仍是 #5a6572，
        深底上 2.79:1）；applyDarkMode 必须把它删掉交给 __syncJump 自愈重建。"""
        ws = _src("js", "11-workspace-kb-study.js")
        body = ws[ws.index("function applyDarkMode"):][:700]
        assert "__jump" in body and "remove()" in body, body[:200]
        assert "opPane" in body and "setChatPane" in body, "只清了一个面版"

    def test_contrast_scanner_covers_the_hidden_pill(self):
        """平时 display:none 的组件是扫描器盲区（v1.0.614 已为报错气泡踩过一次）。"""
        scan = _src("tools", "contrast_scan.py")
        i = scan.index("SYNTH_INJECT")
        assert "jump-latest" in scan[i:i + 1200], "「↓ 回到最新」又回到采不到的状态"

    def test_inactive_component_exemption_is_limited_to_controls(self):
        """加载态按钮用 pointer-events:none 而非 disabled 属性表达"点不动"，
        豁免只给 button/input/a——普通文字带 pointer-events 一律照常测。"""
        scan = _src("tools", "contrast_scan.py")
        i = scan.index("TAGACT")
        seg = scan[i - 200:i + 300]
        assert "button|input|a" in seg and "pointerEvents" in seg
        assert "e.tagName" in seg


# ===================== v1.0.681：HTML 文本泄漏回归锁 =====================

def test_no_text_leaks_into_body():
    """v1.0.681（用户："软件左上角有个符号 去掉"）。

    起因：`<link rel="icon" ... href="/favicon.png">/>` —— 结尾多了一个 `>`。
    浏览器把 `<link ...>` 当 void 元素解析完，剩下的 `/>` 就成了 **body 里的
    文本节点**，被当作正文渲染在页面左上角——用户看到的那个 "/>" 符号。

    为什么必须用 **DOM 解析**来判：纯文本检查（grep / 正则）完全看不出这个问题——
    源码里 `/>` 长得就像一个正常的自闭合标签结尾，只有真正解析成 DOM 才知道
    它跑到了 body 里变成了可见文本。

    判据：解析后的 body 的**直接子文本节点**必须全是空白。
    （body 里允许 <script>/<style> 这类元素内含文本，但元素之外的裸文本不行。）
    """
    from html.parser import HTMLParser

    class _BodyTextCollector(HTMLParser):
        """收集 body 直属的**非空白文本节点**。

        用 HTMLParser 而不是正则：正则分不清 `<link>` 后面那个 `/>` 到底是
        标签的一部分还是正文——而这正是本例的要害。
        """

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.in_body = False
            self.leaks = []
            self._skip_depth = None   # script/style 内部的文本不算泄漏

        def handle_starttag(self, tag, attrs):
            if tag == "body":
                self.in_body = True
                return
            if not self.in_body:
                return
            if tag in ("script", "style"):
                self._skip_depth = self.depth
            self.depth += 1

        def handle_endtag(self, tag):
            if not self.in_body:
                return
            if self._skip_depth is not None and self.depth == self._skip_depth:
                self._skip_depth = None
            self.depth = max(0, self.depth - 1)
            if tag == "body":
                self.in_body = False

        def handle_data(self, data):
            if not self.in_body or self._skip_depth is not None:
                return
            # 只在**没有元素开着**时才算泄漏——元素内部的文本是正常正文
            # （"灵"、"灵伴"…）。真正的问题是 body 直属的裸文本：
            # 标签没写对时，多出来的尾巴被浏览器当成 body 的直接子文本。
            if self.depth == 0 and data.strip():
                self.leaks.append(data.strip()[:60])

    p = _BodyTextCollector()
    p.feed(_html())
    p.close()
    assert not p.leaks, (
        "body 里出现了裸文本节点（会被渲染成可见文字）——多半是标签没写对，"
        "多出来的尾巴跑到正文里了：%r" % (p.leaks,))


def test_void_tags_are_not_double_closed():
    """void 元素（link/meta/br/img/input…）后面不能再多一个 `/>`。

    上一条锁的**根因锁**：`<link ...>` 写成 `<link ...>>` 或 `<link ...>/>` 时，
    多出来的尾巴会被浏览器当成正文。直接扫源码里 void 标签的写法，
    把这类笔误挡在源头。
    """
    html = _html()
    # `<tag ...>` 后面紧跟 `/>` —— 正确；后面跟 `>` 后又跟 `/>` —— 多余
    bad = re.findall(r"<(link|meta|br|img|input|hr|source)\b[^>]*>\s*/>", html, re.I)
    assert not bad, ("void 标签后多了一个 />：%r（浏览器会把它当正文渲染）" % (bad,))


# ===================== v1.0.702（A1）：内联样式棘轮 =====================

def test_inline_style_ratchet():
    """v1.0.702（A1）：index.html 内联 style 只降不升（基线 7）。

    324 处单次布局样式已抽成 css/style.css 的布局类（v1.0.702 inl-NNN，
    v1.0.776 收口为 u-*/容器前缀类）；基线 7 是「JS 按
    el.style.display 读初始态判定」的 display 集（core.frontend_check 的
    INLINE_STYLE_BASELINE 注释有完整白名单）。再冒出来的内联样式 → 红。
    """
    errors = []
    check_inline_style_ratchet(_html(), errors)
    assert not errors, errors
    print("PASS index.html 内联样式棘轮（≤ 基线 7）")


# ===================== v1.0.702（A2）：setInterval 可见性守卫 =====================

class TestSetIntervalHiddenGuard:
    """v1.0.702（A2）：setInterval 必须可见性感知。

    逐点核对全仓 13 处（12 处真实调用 + 1 处 prompt 文本）：
      · 7 处后端轮询/高频 DOM 渲染 → 整窗隐藏（最小化到托盘）时必须跳过
        （12-init.js:25 记录过同一问题：隐藏后仍每几秒打一次后端）；
      · 3 处 SSE 看门狗（07 基准 / 08 模板 / 10 聊天）→ **不加**：tick 是纯本地
        Date.now 比较（无请求无 DOM），阈值是绝对时间「120s 无数据」，隐藏期
        跳拍、可见后首拍即收敛，语义等价；
      · 14-mic 录音秒数 → **不加**：秒数要对应真实录音时长，跳拍会落后；
      · 12-init.js:78 已有守卫（v1.0.510）；02-nav-quicklinks 的 "setInterval"
        是快捷入口 prompt 文本，不是调用点。
    """
    CHAT = _src("js", "10-chat.js")
    KB = _src("js", "11-workspace-kb-study.js")
    DASH = _src("js", "13-dashboard.js")

    def test_typewriter_skips_when_hidden(self):
        seg = self.CHAT[self.CHAT.index("st.timer = setInterval"):][:300]
        assert "if(document.hidden) return;" in seg, "打字机隐藏时仍在 33ms 渲染"

    def test_wait_seconds_skips_when_hidden(self):
        seg = self.CHAT[self.CHAT.index("const waitTimer=setInterval"):][:400]
        assert "if(document.hidden) return;" in seg, "等待秒数隐藏时仍在刷状态条"

    def test_kb_ops_polls_skip_when_hidden(self):
        assert self.KB.count("if(!document.hidden) loadKbOps()") == 2, \
            "kbOpsPoll 两处有一处隐藏时还在打下端"

    # v1.0.768（学习模块移除）：原 test_study_poll_skips_when_hidden 的对象
    # （refreshStudy 3s 轮询）已随学习中心视图删除。

    def test_pending_approval_poll_skips_when_hidden(self):
        seg = self.KB[self.KB.index("function _startPendPoll"):][:600]
        assert "!document.hidden" in seg, "待审批 25s 轮询隐藏时没停"

    def test_audit_progress_poll_skips_when_hidden(self):
        seg = self.DASH[self.DASH.index("__auditPollTimer = setInterval"):][:400]
        assert "if(document.hidden) return;" in seg, "批审进度 5s 轮询隐藏时没停"
