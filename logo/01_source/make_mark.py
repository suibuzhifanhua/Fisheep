#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mark 的 PNG（圆角底 + 自动内缩放居中，保证图形完整不裁切）"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cairo
import numpy as np
from PIL import Image
import logo_final as LF
from renderer import build_path

DIR = os.path.dirname(os.path.abspath(__file__))
B1, E1 = LF.all_paths()
S = 1280
R = 180


def rounded_rect(cr, x, y, w, h, r):
    cr.new_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def content_bbox():
    tmp = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    c = cairo.Context(tmp)
    for d in B1:
        c.new_path()
        build_path(c, [d])
        c.set_source_rgba(0, 0, 0, 1)
        c.fill()
    a = np.frombuffer(tmp.get_data(), dtype=np.uint8).reshape(S, S, 4)
    ys, xs = np.where(a[:, :, 3] > 8)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


bx0, by0, bx1, by1 = content_bbox()
bw, bh = bx1 - bx0, by1 - by0
margin = 0.09
avail = S * (1 - margin * 2)
k = min(avail / bw, avail / bh)
off_x = (S - bw * k) / 2 - bx0 * k
off_y = (S - bh * k) / 2 - by0 * k
print(f"mark bbox=({bx0},{by0})-({bx1},{by1}) scale={k:.4f} off=({off_x:.1f},{off_y:.1f})")


def place(ctx):
    ctx.save()
    rounded_rect(ctx, 0, 0, S, S, R)
    ctx.clip()
    ctx.translate(off_x, off_y)
    ctx.scale(k, k)
    for i, d in enumerate(B1):
        ctx.new_path()
        if i == 1:
            build_path(ctx, [d, E1]); ctx.set_fill_rule(cairo.FillRule.EVEN_ODD)
        else:
            build_path(ctx, [d]); ctx.set_fill_rule(cairo.FillRule.WINDING)
        ctx.set_source_rgba(1, 1, 1, 1)
        ctx.fill()
    ctx.restore()


surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
cr = cairo.Context(surf)
rounded_rect(cr, 0, 0, S, S, R)
cr.set_source_rgba(0.067, 0.067, 0.067, 1)
cr.fill()
place(cr)
surf.write_to_png(os.path.join(DIR, "logo_ai_mark.png"))

for s in (32, 64, 128, 256, 512):
    surf2 = cairo.ImageSurface(cairo.FORMAT_ARGB32, s, s)
    c2 = cairo.Context(surf2)
    c2.scale(s / S, s / S)
    rounded_rect(c2, 0, 0, S, S, R)
    c2.set_source_rgba(0.067, 0.067, 0.067, 1); c2.fill()
    place(c2)
    surf2.write_to_png(os.path.join(DIR, f"mark_{s}.png"))

print("ok")
