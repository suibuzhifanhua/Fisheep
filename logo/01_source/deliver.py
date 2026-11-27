#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
矢量交付 + 多场景适配验证
=========================
输出：
  logo_ai.svg              矢量主文件（正形，透明底，evenodd 镂空鱼眼）
  logo_ai_reverse.svg      矢量反形（白图形，透明底）
  logo_ai_mono_black.svg   单色黑（带白底，印刷用）
  logo_ai_mono_white.svg   单色白（带深底，印刷用）
  logo_ai_mark.svg         方形 mark（适合头像/favicon）
  logo_ai_16.svg / favicon PNGs
  + 场景验证图（浅底/深底/彩色底/小尺寸）
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image
import logo_final as LF
from renderer import render_logo

DIR = os.path.dirname(os.path.abspath(__file__))
BODY, EYE_D = LF.all_paths()


def write_svg(name, text):
    p = os.path.join(DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def png(name, fg, bg=None, size=1280, scale=1.0, eye_hole=True):
    p = os.path.join(DIR, name)
    render_logo(BODY, EYE_D, p, size, scale, fg, bg, eye_hole)
    return p


def main():
    # ---------- 1. SVG 交付 ----------
    write_svg("logo_ai.svg", LF.svg_document(fill="#000000", bg=None, eye="hole",
                                             title="AI Logo - Fish & Ram"))
    write_svg("logo_ai_reverse.svg", LF.svg_document(fill="#FFFFFF", bg=None, eye="hole",
                                                     title="AI Logo - reversed"))
    write_svg("logo_ai_mono_black.svg", LF.svg_document(fill="#000000", bg="#FFFFFF",
                                                        eye="hole", title="AI Logo mono"))
    write_svg("logo_ai_mono_white.svg", LF.svg_document(fill="#FFFFFF", bg="#111111",
                                                        eye="hole", title="AI Logo mono reversed"))
    # 小尺寸简化版（≤32px 专用几何）
    import logo_small as LS
    write_svg("logo_ai_small.svg", LS.svg_document(fill="#000000", bg=None,
                                                    title="AI Logo small-size"))

    # ---------- 2. PNG 交付 ----------
    png("logo_ai_white.png", (0, 0, 0, 1), (1, 1, 1, 1))
    png("logo_ai_alpha.png", (0, 0, 0, 1), None)
    png("logo_ai_2x.png", (0, 0, 0, 1), (1, 1, 1, 1), scale=2.0)
    png("logo_ai_on_dark.png", (1, 1, 1, 1), (0.07, 0.08, 0.11, 1))
    png("logo_ai_on_blue.png", (1, 1, 1, 1), (0.11, 0.35, 0.72, 1))
    png("logo_ai_on_red.png", (1, 1, 1, 1), (0.76, 0.16, 0.18, 1))
    png("logo_ai_on_green.png", (0.05, 0.09, 0.06, 1), (0.30, 0.72, 0.42, 1))

    # favicon 尺寸（主版 + 小尺寸简化版）
    import logo_small as LS
    from renderer import render_logo as _rl
    B2, E2 = LS.all_paths()
    for s in (16, 32, 48, 64, 128, 180, 192, 256, 512):
        png(f"favicon_{s}.png", (0, 0, 0, 1), (1, 1, 1, 1), size=s, scale=1.0)
        _rl(B2, E2, os.path.join(DIR, f"favicon_small_{s}.png"),
            s, 1.0, (0, 0, 0, 1), (1, 1, 1, 1))

    # ---------- 3. 场景验证图 ----------
    T = 420
    cases = [
        ("logo_ai_white.png", (255, 255, 255), "Light #FFF"),
        ("logo_ai_on_dark.png", (18, 20, 28), "Dark #12141C"),
        ("logo_ai_on_blue.png", (28, 89, 184), "Brand blue"),
        ("logo_ai_on_red.png", (194, 41, 46), "Red"),
        ("logo_ai_on_green.png", (77, 184, 107), "Green"),
    ]
    cols = 3
    rows = (len(cases) + cols - 1) // cols
    sheet = Image.new("RGB", (T * cols, (T + 34) * rows), (255, 255, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    for i, (fn, bgc, lab) in enumerate(cases):
        im = Image.open(os.path.join(DIR, fn)).convert("RGB").resize((T, T), Image.LANCZOS)
        x, y = (i % cols) * T, (i // cols) * (T + 34)
        sheet.paste(im, (x, y + 30))
        d.text((x + 8, y + 8), lab, fill=(20, 20, 20))
    sheet.save(os.path.join(DIR, "check_backgrounds.png"))

    # 小尺寸阶梯（直接用最终 favicon 产物，不留临时文件）
    sizes = [512, 256, 128, 64, 48, 32, 16]
    W = sum(s + 16 for s in sizes)
    strip = Image.new("RGB", (W, 540), (255, 255, 255))
    x = 8
    for s in sizes:
        src = os.path.join(DIR, f"favicon_{s}.png")
        if not os.path.exists(src):
            continue
        im = Image.open(src).convert("RGB")
        strip.paste(im, (x, 20 + (512 - s) // 2))
        x += s + 16
    strip.save(os.path.join(DIR, "check_small_sizes.png"))

    print("=== 交付文件 ===")
    for f in sorted(os.listdir(DIR)):
        if f.startswith(("logo_ai", "favicon_")):
            fp = os.path.join(DIR, f)
            if f.endswith((".svg", ".pdf")):
                print("  ", f, os.path.getsize(fp), "bytes")
            else:
                print("  ", f, Image.open(fp).size)


if __name__ == "__main__":
    main()
