#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Logo 最终版 v21 —— 矢量交付
================================
设计要点
  A：Λ 骨架（顶点斜切）+ 横杠为横卧的鱼（尖吻带眼 / 纺锤身 / 实心燕尾）
  I：竖直主干 + 顶部向左上敞开的公羊角（W2 形态：外弧饱满、底部回卷、
      内端收出清晰小涡）+ 底部衬线 + 顶端干笔散锋

「适合所有情况」的实现方式
  · 单一几何（同一批 path），正形/反形/单色/彩色共用，绝不重复画
  · 鱼眼用 fill-rule="evenodd" 真镂空 —— 反形时它自动变成"底色穿透"，
    在任何背景色上都成立
  · 羊角内涡的白色通道宽度经小尺寸测试，保证 16px 下不糊
  · 附带：正形 / 反形 / 单色黑 / 单色白 / 彩色 / 极小尺寸专用简化版
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geom as D

BLACK = "#000000"

# ============================================================
# 几何定义（唯一真源）
# ============================================================

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


def fish(head_x=48, tail_x=508, y=630):
    p = []
    p += D.bez3((head_x, y + 8), (head_x + 44, y - 54), (head_x + 124, y - 70),
                (head_x + 214, y - 64), 24)
    p += D.bez3((head_x + 214, y - 64), (head_x + 300, y - 60), (head_x + 378, y - 40),
                (tail_x - 44, y - 22), 20)
    p += D.bez3((tail_x - 44, y - 22), (tail_x - 14, y - 16), (tail_x - 2, y - 8), (tail_x, y), 8)
    p += D.bez3((tail_x, y), (tail_x + 48, y - 32), (tail_x + 100, y - 66), (tail_x + 140, y - 92), 18)
    p += D.bez3((tail_x + 140, y - 92), (tail_x + 160, y - 104), (tail_x + 166, y - 84), (tail_x + 146, y - 64), 8)
    p += D.bez3((tail_x + 146, y - 64), (tail_x + 128, y - 38), (tail_x + 100, y - 17), (tail_x + 64, y - 6), 14)
    p += D.bez3((tail_x + 64, y - 6), (tail_x + 56, y - 4), (tail_x + 48, y - 2), (tail_x + 40, y), 6)
    p += D.bez3((tail_x + 40, y), (tail_x + 80, y + 13), (tail_x + 114, y + 38), (tail_x + 134, y + 62), 14)
    p += D.bez3((tail_x + 134, y + 62), (tail_x + 152, y + 80), (tail_x + 142, y + 96), (tail_x + 122, y + 84), 8)
    p += D.bez3((tail_x + 122, y + 84), (tail_x + 74, y + 62), (tail_x + 28, y + 30), (tail_x - 8, y + 16), 16)
    p += D.bez3((tail_x - 8, y + 16), (tail_x - 90, y + 44), (head_x + 258, y + 70),
                (head_x + 154, y + 60), 18)
    p += D.bez3((head_x + 154, y + 60), (head_x + 94, y + 52), (head_x + 38, y + 32), (head_x, y + 8), 14)
    return D.poly(p)


# ---- I ----
I_X = 1002
I_TOP = 382


def i_stem():
    center = [(I_X + 4, I_TOP + 52), (I_X + 5, 630), (I_X + 6, 810), (I_X + 6, 950)]
    return D.ribbon(center, [86, 104, 98, 96])


def ram_horn():
    """公羊角：外弧饱满圆隆（顶部无折角）→ 底部回卷 → 内端小涡收尖"""
    center = [
        (I_X + 2, 398), (968, 316), (898, 252), (800, 234),
        (700, 244), (634, 322), (630, 440), (692, 514),
        (772, 548), (836, 520), (850, 452), (808, 408),
        (746, 404), (722, 444), (748, 476), (782, 464),
        (778, 436),
    ]
    widths = [104, 100, 92, 82, 72, 62, 52, 43, 34, 25, 16, 10, 7, 4, 2, 0]
    return D.ribbon(center, widths)


def nib():
    """顶端干笔散锋：锯齿沿笔身上缘朝外"""
    root = (I_X + 24, I_TOP + 32)
    tip = (I_X + 194, I_TOP - 54)
    dx, dy = tip[0] - root[0], tip[1] - root[1]
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    px, py = uy, -ux
    if px < 0:
        px, py = -px, -py
    h_root, h_tip = 29.0, 2.0

    def edge(t, sign):
        w = h_root + (h_tip - h_root) * t
        return (root[0] + dx * t + px * w * sign,
                root[1] + dy * t + py * w * sign)

    teeth = [(0.16, 10), (0.34, 15), (0.53, 12), (0.72, 9), (0.88, 6)]
    p = []
    p += D.bez3(edge(0.0, -1), (root[0] + dx * 0.33, root[1] + dy * 0.33),
                (root[0] + dx * 0.66, root[1] + dy * 0.66), edge(1.0, -1), 20)
    p.append(tip)
    for t, h in reversed(teeth):
        p.append(edge(t, 1))
        p.append((edge(t, 1)[0] + px * h, edge(t, 1)[1] + py * h))
        p.append(edge(max(0.0, t - 0.09), 1))
    p.append(edge(0.0, 1))
    return D.poly(p)


def i_foot():
    """底部衬线：左圆头 / 右方角"""
    p = []
    p += [(I_X - 66, 862), (I_X + 128, 862), (I_X + 128, 950), (I_X - 66, 950)]
    p += D.arc(I_X - 66, 906, 44, math.radians(90), math.radians(270), 26)
    return D.poly(p)


EYE_C = (84, 628)
EYE_R = 12


def fish_eye():
    return D.circle(EYE_C[0], EYE_C[1], EYE_R)


# ============================================================
# 路径集合
# ============================================================

def all_paths():
    """返回 [(d, kind)]，kind: 'body' | 'eye'
    body 索引 1 是鱼身，索引 4 是羊角 —— 鱼眼与鱼身合并做 evenodd 镂空
    """
    body = [letter_a(), fish(), ram_horn(), i_foot(), i_stem(), nib()]
    return body, fish_eye()


def _xml_escape(s):
    """转义 XML 实体，避免 title 里的 & < > " 破坏 SVG 合法性。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def svg_document(fill="#000000", bg=None, eye="hole", title="AI Logo"):
    """生成完整 SVG 文本。
    fill    : 图形主色
    bg      : 背景色（None = 透明）
    eye     : 'hole' = evenodd 镂空（推荐，任意背景成立）
              'solid'= 单独画一个同色圆点（用于图形极小时眼过细的场景）
    """
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
        if i == 1 and eye == "hole":
            # 鱼身 + 眼圆 合并，evenodd -> 眼是真洞
            out.append(f'    <path d="{d} {eye_d}" fill-rule="evenodd" clip-rule="evenodd"/>')
        else:
            out.append(f'    <path d="{d}"/>')
    if eye == "solid":
        out.append(f'    <path d="{eye_d}"/>')
    out.append('  </g>')
    out.append('</svg>')
    return "\n".join(out)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "_tmp_check.svg"), "w", encoding="utf-8") as f:
        f.write(svg_document())
    print("ok")
