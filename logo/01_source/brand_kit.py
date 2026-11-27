#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
brand_kit.py —— 品牌图标套件（整理全部颜色类型 + 版权戳）
================================================================
从唯一几何真源（logo_final / logo_small）重新渲染，不依赖任何历史 PNG。

产出
  brand_kit/                     —— 10 种颜色类型的独立 PNG（1024px，右下角 © 戳）
  brand_kit/svg/                 —— 5 个内嵌「© 2026 Fisheep.L」文本的可缩放 SVG
  brand_kit/清单.md              —— 颜色类型清单表
  brand_overview.png             —— 品牌总览大图（网格 + 标题 + 版权区）

版权戳
  文字    © 2026 Fisheep.L
  位置    每张图右下角，轻量半透明，自适应底色对比（深底浅戳 / 浅底深戳）
  透明底   展示 tile 先铺一层中性浅底再盖戳，保证任意场景下戳都清晰
"""
import contextlib, os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cairo
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import logo_final as LF
import logo_small as LS
from renderer import render_logo, build_path

DIR = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(DIR, "brand_kit")
KV  = os.path.join(KIT, "svg")
os.makedirs(KV, exist_ok=True)

BODY, EYE_D = LF.all_paths()
COPYRIGHT = "© 2026 Fisheep.L"
STAMP = False   # 版权戳开关：True=每图右下角©+SVG文本层；False=干净图标（当前）

# 中性浅底（透明源变体展示用，铺在图标下让 © 戳可见）
NEUTRAL = (0.960, 0.960, 0.964, 1.0)   # #F5F5F6

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
def load_font(size, bold=False):
    # 首选微软雅黑：同时含中文、拉丁字母与 © 符号，避免豆腐块
    cands = (
        [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
         r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"]
        if not bold else
        [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
         r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
    )
    for p in cands:
        if os.path.exists(p):
            # 加载失败 → 试下一个候选字体（v1.0.785：原 except:pass 改为
            # contextlib.suppress——主仓裸 except-pass 棘轮口径，语义不变）
            with contextlib.suppress(Exception):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def lum(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def stamp_color(bg_rgb):
    """深底用浅戳，浅底用深戳；半透明保持轻量。"""
    if lum(bg_rgb) < 128:
        return (255, 255, 255, 150)
    return (50, 53, 64, 165)


# ---------------------------------------------------------------------------
# 变体定义  (ascii 名, 中文标签, 图形色 rgba, 底色 rgba|None, 是否简化几何)
# ---------------------------------------------------------------------------
VARIANTS = [
    ("01_primary_black_alpha", "正形 · 黑 · 透明底", (0, 0, 0, 1),      None,                       False),
    ("02_primary_black_white", "正形 · 黑 · 白底",   (0, 0, 0, 1),      (1, 1, 1, 1),               False),
    ("03_reverse_white_alpha", "反形 · 白 · 透明底", (1, 1, 1, 1),      None,                       False),
    ("04_reverse_white_dark",  "反形 · 白 · 深底",   (1, 1, 1, 1),      (0.07, 0.08, 0.11, 1),     False),
    ("05_reverse_white_blue",  "反形 · 白 · 品牌蓝", (1, 1, 1, 1),      (0.11, 0.35, 0.72, 1),     False),
    ("06_reverse_white_red",   "反形 · 白 · 红底",   (1, 1, 1, 1),      (0.76, 0.16, 0.18, 1),     False),
    ("07_deep_green",          "深绿 · 绿底",        (0.05, 0.09, 0.06, 1), (0.30, 0.72, 0.42, 1), False),
    ("08_mono_black_print",    "单色黑 · 印刷白底",  (0, 0, 0, 1),      (1, 1, 1, 1),               False),
    ("09_mono_white_print",    "单色白 · 印刷深底",  (1, 1, 1, 1),      (0.067, 0.067, 0.067, 1),  False),
    ("10_mark_rounded",        "圆角方形 · mark",    None,             None,                       "mark"),
]


# ---------------------------------------------------------------------------
# mark 圆角方形（深底 + 白图形，自动内缩放居中，复用 make_mark 的几何逻辑）
# ---------------------------------------------------------------------------
_MARK_CACHE = {}


def _mark_transform(S=1280, margin=0.10):
    """计算 mark 的 (k, off_x, off_y)，一次算好缓存。"""
    if "k" in _MARK_CACHE:
        return _MARK_CACHE
    tmp = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    c = cairo.Context(tmp)
    for d in BODY:
        c.new_path()
        build_path(c, [d])
        c.set_source_rgba(0, 0, 0, 1)
        c.fill()
    a = np.frombuffer(tmp.get_data(), dtype=np.uint8).reshape(S, S, 4)
    ys, xs = np.where(a[:, :, 3] > 8)
    bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    bw, bh = bx1 - bx0, by1 - by0
    avail = S * (1 - margin * 2)
    k = min(avail / bw, avail / bh)
    off_x = (S - bw * k) / 2 - bx0 * k
    off_y = (S - bh * k) / 2 - by0 * k
    _MARK_CACHE.update(k=k, off_x=off_x, off_y=off_y)
    return _MARK_CACHE


def render_mark(out_png, size, scale=2.0, bg=(0.067, 0.067, 0.067, 1), fg=(1, 1, 1, 1)):
    """渲染圆角方形 mark。size=逻辑边长。"""
    S = 1280
    px = int(round(size * scale))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    cr = cairo.Context(surf)
    cr.scale(px / S, px / S)
    tr = _mark_transform(S)
    _draw_mark(cr, tr, bg, fg)
    surf.write_to_png(out_png)
    return out_png


def rounded_rect(cr, x, y, w, h, r):
    cr.new_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_mark(cr, tr, bg, fg):
    S = 1280
    R = 200
    rounded_rect(cr, 0, 0, S, S, R)
    cr.set_source_rgba(*bg)
    cr.fill()
    cr.save()
    rounded_rect(cr, 0, 0, S, S, R)
    cr.clip()
    cr.translate(tr["off_x"], tr["off_y"])
    cr.scale(tr["k"], tr["k"])
    for i, d in enumerate(BODY):
        cr.new_path()
        if i == 1:
            build_path(cr, [d, EYE_D]); cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
        else:
            build_path(cr, [d]); cr.set_fill_rule(cairo.FillRule.WINDING)
        cr.set_source_rgba(*fg)
        cr.fill()
    cr.restore()


# ---------------------------------------------------------------------------
# 统一 tile 渲染：底色 + 图形 + 右下角 © 戳
# ---------------------------------------------------------------------------
def _checkerboard(px, light=(245, 245, 248), dark=(224, 224, 230), cell=None):
    """透明底的标准棋盘格提示（浅色系，保证黑/白两形都可见）。"""
    cell = cell or max(20, px // 16)
    img = Image.new("RGBA", (px, px), light + (255,))
    d = ImageDraw.Draw(img)
    for y in range(0, px, cell):
        for x in range(0, px, cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=dark + (255,))
    return img


def render_tile(variant, px, with_stamp=True):
    """返回 px×px 的 RGBA PIL 图 + 展示底色 rgb（供总览/对比）。"""
    name, _cn, fg, bg, small = variant
    if small == "mark":
        tmp = os.path.join(KIT, "_mark_tmp.png")
        render_mark(tmp, px, scale=2.0)
        mark = Image.open(tmp).convert("RGBA")
        # mark 铺在浅底上，留外边距
        m = int(px * 0.08)
        mk = mark.resize((px - 2 * m, px - 2 * m), Image.LANCZOS)
        tile = Image.new("RGBA", (px, px), tuple(int(v * 255) for v in NEUTRAL[:3]) + (255,))
        tile.paste(mk, (m, m), mk)
        display_bg = (int(NEUTRAL[0] * 255), int(NEUTRAL[1] * 255), int(NEUTRAL[2] * 255))
        if with_stamp:
            tile = _stamp(tile, px, display_bg)
        return tile, display_bg

    b, e = (LS.all_paths() if small else (BODY, EYE_D))
    tmp = os.path.join(KIT, "_tile_tmp.png")
    if bg is None:
        # 透明底：先渲染透明 logo，再合成到透明棋盘格上（黑/白两形均清晰可见）
        render_logo(b, e, tmp, px, 2.0, fg, None)
        logo_img = Image.open(tmp).convert("RGBA").resize((px, px), Image.LANCZOS)
        tile = Image.alpha_composite(_checkerboard(px), logo_img)
        display_bg = (240, 240, 244)   # 棋盘平均亮度 → 用深戳
    else:
        render_logo(b, e, tmp, px, 2.0, fg, bg)
        tile = Image.open(tmp).convert("RGBA").resize((px, px), Image.LANCZOS)
        display_bg = (int(bg[0] * 255), int(bg[1] * 255), int(bg[2] * 255))
    if with_stamp:
        tile = _stamp(tile, px, display_bg)
    return tile, display_bg


def _stamp(tile, px, display_bg_rgb):
    tile = tile.copy()
    fs = max(14, int(px * 0.036))
    font = load_font(fs)
    d = ImageDraw.Draw(tile)
    tb = d.textbbox((0, 0), COPYRIGHT, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = int(px * 0.030)
    x = tile.width - tw - pad - tb[0]
    y = tile.height - th - pad - tb[3]
    d.text((x, y), COPYRIGHT, font=font, fill=stamp_color(display_bg_rgb))
    return tile


# ---------------------------------------------------------------------------
# SVG 注入版权文本（可缩放 / 可编辑）
# ---------------------------------------------------------------------------
def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_with_copyright(svg_text, text_fill, text_size=46):
    """在 viewBox(1280) 右下角注入 © 文本。"""
    anchor = ('    <text x="1224" y="1216" font-family="Segoe UI, Arial, sans-serif" '
              f'font-size="{text_size}" font-weight="600" fill="{text_fill}" '
              'text-anchor="end" letter-spacing="1">%s</text>\n' % _esc(COPYRIGHT))
    return svg_text.replace("</svg>", anchor + "</svg>")


def build_kit_saves():
    """10 张 1024px PNG + 5 个 SVG + 清单。"""
    rows = []
    for variant in VARIANTS:
        name, cn, fg, bg, small = variant
        tile, _ = render_tile(variant, 1024, with_stamp=STAMP)
        out = os.path.join(KIT, name + ".png")
        tile.convert("RGB" if bg is not None or small == "mark" else "RGBA").save(out)
        rows.append((name, cn, variant))
        print("  png", name)

    # SVG（5 个正源：正形黑/透明、反形白/透明、单色黑/白底、单色白/深底）
    specs = [
        ("logo_ai.svg",            LF.svg_document(fill="#000000", bg=None, eye="hole", title="AI Logo"),        "#3a3d49"),
        ("logo_ai_reverse.svg",    LF.svg_document(fill="#FFFFFF", bg=None, eye="hole", title="AI Logo reversed"), "#e8e8ee"),
        ("logo_ai_mono_black.svg", LF.svg_document(fill="#000000", bg="#FFFFFF", eye="hole", title="AI mono black"), "#8a8a92"),
        ("logo_ai_mono_white.svg", LF.svg_document(fill="#FFFFFF", bg="#111111", eye="hole", title="AI mono white"), "#6f6f78"),
        ("logo_ai_small.svg",      LS.svg_document(fill="#000000", bg=None, title="AI Logo small"),  "#3a3d49"),
    ]
    for fname, doc, tf in specs:
        with open(os.path.join(KV, fname), "w", encoding="utf-8") as f:
            f.write(svg_with_copyright(doc, tf) if STAMP else doc)
        print("  svg", fname)

    # 清单
    write_manifest(rows)


def write_manifest(rows):
    L = []
    L.append("# AI · FISHEEP 品牌图标 — 颜色类型清单")
    L.append("")
    if STAMP:
        L.append("**版权戳：** %s（内嵌于每张图右下角 / SVG 文本层，可缩放可编辑）" % COPYRIGHT)
    else:
        L.append("**版权：** %s（本套件为干净图标，未内嵌戳；需要时可在总览页脚或印刷说明中标注）" % COPYRIGHT)
    L.append("")
    L.append("| # | 颜色类型 | 图形 / 底色 | 文件 |")
    L.append("|---|---------|-----------|------|")
    meta = {
        "01_primary_black_alpha": "黑图 + 透明",
        "02_primary_black_white": "黑图 + 白",
        "03_reverse_white_alpha": "白图 + 透明",
        "04_reverse_white_dark": "白图 + #12141C",
        "05_reverse_white_blue": "白图 + 品牌蓝",
        "06_reverse_white_red": "白图 + 红",
        "07_deep_green": "深绿图 + 绿",
        "08_mono_black_print": "黑图 + 白（印刷）",
        "09_mono_white_print": "白图 + #111（印刷）",
        "10_mark_rounded": "深底圆角方 + 白图（头像/favicon）",
    }
    for i, (name, cn, _v) in enumerate(rows, 1):
        L.append("| %02d | %s | %s | `brand_kit/%s.png` |" % (i, cn, meta[name], name))
    L.append("")
    L.append("## SVG（内嵌 © 文本层）")
    L.append("`brand_kit/svg/` 下有 5 个可无限缩放矢量：正形黑 / 反形白 / 单色黑 / 单色白 / 小尺寸简化。")
    L.append("")
    with open(os.path.join(KIT, "清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------------------
# 品牌总览大图
# ---------------------------------------------------------------------------
def build_overview():
    W = 1560
    M = 60
    gap = 40
    cols = 2
    tile = 640
    label_h = 74
    row_gap = 34
    rows = (len(VARIANTS) + cols - 1) // cols

    header = 150
    footer = 130
    H = M + header + 24 + rows * (tile + label_h + row_gap) + footer + M

    sheet = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(sheet)

    # ---- 标题区 ----
    f_title = load_font(66, bold=True)
    f_sub = load_font(30)
    d.text((M, M - 6), "FISHEEP.L · AI 品牌图标", font=f_title, fill=(18, 20, 26))
    d.text((M + 4, M + 74), "鱼 & 羊 · Fish & Ram  ——  全颜色类型", font=f_sub, fill=(120, 122, 132))
    d.line([M, M + header, W - M, M + header], fill=(226, 226, 232), width=2)

    # ---- 网格 ----
    x0 = (W - (cols * tile + (cols - 1) * gap)) // 2
    y = M + header + 24
    meta_bg = {
        "01_primary_black_alpha": "黑图 + 透明底",
        "02_primary_black_white": "黑图 + 白底",
        "03_reverse_white_alpha": "白图 + 透明底",
        "04_reverse_white_dark": "白图 + 深底",
        "05_reverse_white_blue": "白图 + 品牌蓝",
        "06_reverse_white_red": "白图 + 红底",
        "07_deep_green": "深绿图 + 绿底",
        "08_mono_black_print": "单色黑 + 白底（印刷）",
        "09_mono_white_print": "单色白 + 深底（印刷）",
        "10_mark_rounded": "深底圆角方 + 白图（mark）",
    }
    f_lab = load_font(30, bold=True)
    f_lab2 = load_font(22)
    for i, variant in enumerate(VARIANTS):
        name, cn, _fg, _bg, _sm = variant
        cx = x0 + (i % cols) * (tile + gap)
        cy = y + (i // cols) * (tile + label_h + row_gap)
        tile_img, _ = render_tile(variant, tile, with_stamp=STAMP)
        tile_img = tile_img.convert("RGB")
        # 卡片阴影 + 底
        d.rounded_rectangle([cx - 4, cy - 4, cx + tile + 4, cy + tile + 4], radius=18,
                            fill=(236, 236, 240))
        sheet.paste(tile_img, (cx, cy))
        d.rounded_rectangle([cx - 4, cy - 4, cx + tile + 4, cy + tile + 4], radius=18,
                            outline=(222, 222, 228), width=1)
        # 标签
        d.text((cx, cy + tile + 16), "%02d · %s" % (i + 1, cn), font=f_lab, fill=(30, 32, 40))
        d.text((cx, cy + tile + 52), "%s   ·   %s" % (meta_bg[name], name), font=f_lab2, fill=(150, 152, 162))

    # ---- 版权区 ----
    fy = H - footer
    d.line([M, fy, W - M, fy], fill=(226, 226, 232), width=2)
    # 小 mark 点缀
    mk = Image.open(os.path.join(DIR, "logo_ai_mark.png")).convert("RGBA")
    if mk.size[0] > 0:
        mk = mk.resize((72, 72), Image.LANCZOS)
        sheet.paste(mk, (M, fy + (footer - 72) // 2), mk)
    f_c1 = load_font(34, bold=True)
    f_c2 = load_font(22)
    d.text((M + 100, fy + 30), COPYRIGHT + "  ·  All Rights Reserved", font=f_c1, fill=(40, 42, 50))
    d.text((M + 100, fy + 76), "鱼（Fisheep）× 羊  双元素 AI 标识  ·  几何真源：logo_final.py  ·  可无限缩放", font=f_c2, fill=(140, 142, 152))

    out = os.path.join(DIR, "brand_overview.png")
    sheet.save(out)
    print("overview", sheet.size, "->", out)


if __name__ == "__main__":
    print("=== 生成品牌套件 ===")
    build_kit_saves()
    print("=== 生成品牌总览图 ===")
    build_overview()
    for tmp in ("_mark_tmp.png", "_tile_tmp.png"):
        p = os.path.join(KIT, tmp)
        if os.path.exists(p):
            os.remove(p)
    print("done")
