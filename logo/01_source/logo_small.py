#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
极小尺寸简化版（favicon / app icon）
=================================
≤32px 时原版的鱼眼、散锋锋丝、羊角内涡都会糊成脏块。
此版本针对 ≤32px 重绘：
  · 鱼眼放大并加粗（保证 16px 仍是一个明确的点）
  · 去掉干笔散锋的锯齿，改为 3 根清晰的短锋丝
  · 羊角内涡简化、通道加宽
  · 整体笔画略加粗（增强小尺寸对比度）
几何仍与主版同源，可辨识为同一个 logo。
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geom as D

BLACK = "#000000"

# ---- A ----
APEX_L = (272, 296)
APEX_R = (336, 258)
A_INNER = (306, 512)
A_L_OUT_B = (74, 948)
A_L_IN_B = (166, 948)
A_R_IN_B = (452, 948)
A_R_OUT_B = (548, 948)


def letter_a():
    p = []
    p += D.bez3(APEX_L, (196, 616), (122, 792), A_L_OUT_B, 26)
    p += [A_L_IN_B]
    p += D.bez3(A_L_IN_B, (204, 750), (256, 628), A_INNER, 26)
    p += D.bez3(A_INNER, (356, 628), (408, 750), A_R_IN_B, 26)
    p += [A_R_OUT_B]
    p += D.bez3(A_R_OUT_B, (500, 792), (430, 614), APEX_R, 26)
    p += [APEX_L]
    return D.poly(p)


def fish():
    """鱼身加粗，眼更大更靠前"""
    p = []
    p += D.bez3((52, 634), (100, 578), (184, 566), (276, 572), 24)
    p += D.bez3((276, 572), (356, 578), (424, 596), (476, 616), 20)
    p += D.bez3((476, 616), (496, 626), (508, 634), (516, 642), 8)
    p += D.bez3((516, 642), (560, 612), (606, 578), (642, 544), 18)
    p += D.bez3((642, 544), (660, 528), (672, 536), (660, 560), 8)
    p += D.bez3((660, 560), (640, 592), (612, 620), (576, 640), 14)
    p += D.bez3((576, 640), (566, 644), (556, 646), (546, 648), 6)
    p += D.bez3((546, 648), (582, 660), (614, 680), (636, 700), 14)
    p += D.bez3((636, 700), (652, 716), (642, 730), (622, 718), 8)
    p += D.bez3((622, 718), (580, 700), (536, 680), (496, 668), 16)
    p += D.bez3((496, 668), (466, 660), (440, 656), (416, 654), 12)
    p += D.bez3((416, 654), (350, 668), (270, 664), (196, 654), 18)
    p += D.bez3((196, 654), (140, 646), (84, 640), (52, 634), 14)
    return D.poly(p)


def fish_eye():
    return D.circle(98, 620, 17)


# ---- I ----
I_X = 1002
I_TOP = 382


def i_stem():
    center = [(I_X + 4, I_TOP + 52), (I_X + 5, 630), (I_X + 6, 810), (I_X + 6, 950)]
    return D.ribbon(center, [92, 110, 104, 100])


def ram_horn():
    """简化羊角：内涡少一圈，通道加宽（与主版同源的圆润外弧）"""
    center = [
        (I_X + 2, 398), (968, 316), (898, 250), (796, 230),
        (692, 242), (628, 322), (626, 442), (692, 518),
        (776, 552), (842, 522), (856, 450), (810, 404),
        (744, 400), (718, 442), (746, 476),
    ]
    widths = [108, 104, 96, 86, 76, 66, 56, 46, 36, 26, 16, 10, 6, 0]
    return D.ribbon(center, widths)


def nib():
    """简化散锋：3 根清晰的短锋丝"""
    P = []
    specs = [(-14, 168, 30, 7), (0, 150, 25, 6), (15, 124, 19, 5)]
    root = (I_X + 26, I_TOP + 34)
    for ang, L, w0, w1 in specs:
        a = math.radians(ang)
        ex = root[0] + L * math.cos(a)
        ey = root[1] + L * math.sin(a)
        P.append((D.ribbon([root, (root[0] + (ex - root[0]) * 0.5,
                                     root[1] + (ey - root[1]) * 0.5 - 8), (ex, ey)],
                           [w0, w0 * 0.6, w1]), BLACK))
    return P


def i_foot():
    p = []
    p += [(I_X - 66, 862), (I_X + 128, 862), (I_X + 128, 950), (I_X - 66, 950)]
    p += D.arc(I_X - 66, 906, 44, math.radians(90), math.radians(270), 26)
    return D.poly(p)


def all_paths():
    body = [letter_a(), fish(), ram_horn(), i_foot(), i_stem()]
    body += [d for d, _ in nib()]
    return body, fish_eye()


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def svg_document(fill="#000000", bg=None, title="AI Logo small"):
    body, eye_d = all_paths()
    t = _xml_escape(title)
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="1280" '
               f'viewBox="0 0 1280 1280" role="img" aria-label="{t}">')
    if bg:
        out.append(f'  <rect width="1280" height="1280" fill="{bg}"/>')
    out.append(f'  <title>{t}</title>')
    out.append(f'  <g fill="{fill}">')
    for i, d in enumerate(body):
        if i == 1:
            out.append(f'    <path d="{d} {eye_d}" fill-rule="evenodd" clip-rule="evenodd"/>')
        else:
            out.append(f'    <path d="{d}"/>')
    out.append('  </g>')
    out.append('</svg>')
    return "\n".join(out)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "logo_ai_small.svg"), "w", encoding="utf-8") as f:
        f.write(svg_document())
    print("ok")
