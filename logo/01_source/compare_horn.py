#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compare_horn.py —— 羊角优化前后对比
==================================
左侧的"优化前"形态由旧版控制点在**当前代码内重建**（不依赖任何旧版文件），
右侧直接取本版产物。
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
import geom as D
import logo_final as LF

DIR = os.path.dirname(os.path.abspath(__file__))
I_X = LF.I_X

# 优化前的羊角（v20 形态：外弧偏平、内涡浅）
OLD = ([
    (I_X + 2, 396), (956, 330), (872, 292), (780, 302),
    (704, 328), (668, 392), (680, 462), (726, 506),
    (778, 514), (810, 478), (798, 442), (762, 434),
], [100, 96, 88, 78, 68, 58, 48, 38, 28, 19, 11, 0])


def build_old():
    P = [
        (LF.letter_a(), "#000000"),
        (LF.fish(), "#000000"),
        (D.ribbon(*OLD), "#000000"),
        (LF.i_foot(), "#000000"),
        (LF.i_stem(), "#000000"),
        (LF.nib(), "#000000"),
    ]
    return P + [(D.circle(LF.EYE_C[0], LF.EYE_C[1], LF.EYE_R), "#FFFFFF")]


def main():
    import cairo
    from renderer import build_path
    S = 1280
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    cr = cairo.Context(surf)
    cr.set_source_rgba(1, 1, 1, 1); cr.paint()
    for i, d in enumerate(build_old()):
        cr.new_path()
        build_path(cr, [d])
        cr.set_source_rgba(0, 0, 0, 1)
        cr.fill()
    old_png = os.path.join(DIR, "_old_horn.png")
    surf.write_to_png(old_png)

    old = Image.open(old_png).convert("RGB").crop((600, 210, 1260, 710)).resize((620, 468), Image.LANCZOS)
    new = Image.open(os.path.join(DIR, "logo_ai_white.png")).convert("RGB") \
        .crop((600, 210, 1260, 710)).resize((620, 468), Image.LANCZOS)

    hc = Image.new("RGB", (1252, 512), (255, 255, 255))
    d = ImageDraw.Draw(hc)
    hc.paste(old, (6, 36)); hc.paste(new, (632, 36))
    d.text((10, 12), "BEFORE   outer arc flatter, inner vortex shallow", fill=(125, 125, 130))
    d.text((636, 12), "AFTER   rounded outer arc, clear spiral vortex", fill=(20, 20, 20))
    hc.save(os.path.join(DIR, "compare_horn.png"))
    os.remove(old_png)
    print("ok")


if __name__ == "__main__":
    main()
