#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
正负形渲染器 v2（修正视口）
==========================
两个 bug 都已修：
  1. evenodd 镂空：鱼身与眼圆必须作为**同一条 path 的两个子路径**一次提交，
     之前用 new_path() 清空了鱼身 -> 鱼身消失。
  2. 视口：画布尺寸 != 1280 时必须按比例缩放坐标，否则图形被裁在左上角。
     现在统一用 cr.scale(size/1280 * scale) 处理。
"""
import os, math
import cairo

DIR = os.path.dirname(os.path.abspath(__file__))
VIEW = 1280.0


def tokenize(d):
    toks = []
    i = 0
    while i < len(d):
        c = d[i]
        if c in "MLCZ":
            toks.append(c); i += 1
        elif c in "0123456789.-":
            j = i
            while j < len(d) and d[j] in "0123456789.-":
                j += 1
            toks.append(float(d[i:j])); i = j
        else:
            i += 1
    seq, cur = [], []
    for t in toks:
        if isinstance(t, str):
            seq.append((t, cur)); cur = []
        else:
            cur.append(t)
    if cur:
        seq.append((None, cur))
    return seq


def build_path(cr, subpaths):
    for d in subpaths:
        for cmd, a in tokenize(d):
            if cmd == "M" and len(a) >= 2:
                cr.move_to(a[0], a[1])
            elif cmd == "L":
                for i in range(0, len(a) - 1, 2):
                    cr.line_to(a[i], a[i + 1])
            elif cmd == "Z":
                cr.close_path()


def render_logo(body_paths, eye_d, out_png, size=1280, scale=1.0,
                fg=(0, 0, 0, 1), bg=None, eye_hole=True):
    """size = 输出逻辑边长（正方形），scale = 超采样倍率"""
    px = int(round(size * scale))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    cr = cairo.Context(surf)
    k = size / VIEW
    cr.scale(k * scale, k * scale)
    if bg:
        cr.set_source_rgba(*bg)
        cr.paint()
    for i, d in enumerate(body_paths):
        cr.new_path()
        if i == 1 and eye_hole:
            build_path(cr, [d, eye_d])
            cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
        else:
            build_path(cr, [d])
            cr.set_fill_rule(cairo.FillRule.WINDING)
        cr.set_source_rgba(*fg)
        cr.fill()
    surf.write_to_png(out_png)
    return out_png


def render_crop(body_paths, eye_d, out_png, box, pad=0.0, size=512,
                fg=(0, 0, 0, 1), bg=None, eye_hole=True):
    """按 bbox 裁切渲染（box = (x0,y0,x1,y1) in 1280 坐标系）"""
    x0, y0, x1, y1 = box
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
    w, h = x1 - x0, y1 - y0
    px = int(round(size * scale_of(w, h)))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    cr = cairo.Context(surf)
    cr.translate(-x0, -y0)
    cr.scale(w / w, h / h)
    cr.scale(px / w, px / h)
    if bg:
        cr.set_source_rgba(*bg)
        cr.paint()
    for i, d in enumerate(body_paths):
        cr.new_path()
        if i == 1 and eye_hole:
            build_path(cr, [d, eye_d])
            cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
        else:
            build_path(cr, [d])
            cr.set_fill_rule(cairo.FillRule.WINDING)
        cr.set_source_rgba(*fg)
        cr.fill()
    surf.write_to_png(out_png)
    return out_png


def scale_of(w, h):
    return 1.0
