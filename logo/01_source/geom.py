#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
geom.py —— 矢量几何工具库
=========================
Logo 设计的底层数学工具：贝塞尔采样、变宽描边（毛笔笔画）、路径序列化。

  bez3()   三次贝塞尔采样成点列
  bez2()   二���贝塞尔采样
  arc()    圆弧采样
  poly()   点列 -> SVG path 字符串（M/L/Z）
  circle() 圆形 path
  ribbon() 中心线 + 宽度序列 -> 闭合轮廓（毛笔笔画的实现方式）
  cr_dense() Catmull-Rom 平滑加密
"""
import math


def bez3(p0, p1, p2, p3, n=28):
    """三次贝塞尔采样成点列"""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((
            u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def bez2(p0, p1, p2, n=20):
    """二次贝塞尔采样"""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def arc(cx, cy, r, a0, a1, n=30):
    """圆弧采样（角度制）"""
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n),
             cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def poly(pts):
    """点列 -> SVG path 字符串"""
    d = [f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"]
    d += [f"L {p[0]:.1f},{p[1]:.1f}" for p in pts[1:]]
    d.append("Z")
    return " ".join(d)


def circle(cx, cy, r, n=44):
    """圆形 path"""
    return poly([(cx + r * math.cos(i * 2 * math.pi / n),
                  cy + r * math.sin(i * 2 * math.pi / n)) for i in range(n)])


def cr_dense(pts, n=26):
    """Catmull-Rom 平滑加密（开放曲线）"""
    if len(pts) < 3:
        return list(pts)
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(len(P) - 3):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        for t in range(n):
            s = t / n
            s2, s3 = s * s, s * s * s
            out.append((
                0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * s +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * s2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * s3),
                0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * s +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * s2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * s3)))
    out.append(tuple(pts[-1]))
    return out


def ribbon(center, widths, jag=0.0, jag_freq=20, jag_phase=0.0, jag_side=1):
    """中心线 + 宽度序列 -> 闭合轮廓 path（毛笔笔画）

    widths : 宽度列表，按弧长在中心线上均分插值
    jag    : 锯齿振幅（模拟飞白毛边），>0 时启用
    jag_side: +1 右侧生锯齿，-1 左侧
    """
    pts = cr_dense(center)
    n = len(pts)
    m = len(widths)

    def w_at(t):
        f = t * (m - 1)
        i = int(f)
        if i >= m - 1:
            return widths[-1]
        u = f - i
        return widths[i] + (widths[i + 1] - widths[i]) * u

    left, right = [], []
    for i, (x, y) in enumerate(pts):
        t = i / max(1, n - 1)
        if i == 0:
            tx, ty = pts[1][0] - x, pts[1][1] - y
        elif i == n - 1:
            tx, ty = x - pts[-2][0], y - pts[-2][1]
        else:
            tx, ty = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L, tx / L
        w = max(0.0, w_at(t)) / 2.0
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))

    if jag > 0:
        tgt = right if jag_side > 0 else left
        t2 = []
        for i, (x, y) in enumerate(tgt):
            if i == 0:
                tx, ty = tgt[1][0] - x, tgt[1][1] - y
            elif i == len(tgt) - 1:
                tx, ty = x - tgt[-2][0], y - tgt[-2][1]
            else:
                tx, ty = tgt[i + 1][0] - tgt[i - 1][0], tgt[i + 1][1] - tgt[i - 1][1]
            L = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / L, tx / L
            s = math.sin(i / max(1, len(tgt) - 1) * math.pi * 2 * jag_freq + jag_phase)
            r = (math.sin(i * 2.399) * 0.5 + 0.5)
            k = jag * (0.3 + 0.7 * r) * (0.5 + 0.5 * s)
            t2.append((x + nx * k, y + ny * k))
        right, left = (t2, left) if jag_side > 0 else (right, t2)

    d = [f"M {left[0][0]:.1f},{left[0][1]:.1f}"]
    d += [f"L {p[0]:.1f},{p[1]:.1f}" for p in left[1:]]
    d += [f"L {p[0]:.1f},{p[1]:.1f}" for p in reversed(right)]
    d.append("Z")
    return " ".join(d)
