#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
deliver2.py —— 场景验证图
=========================
交付产物本体由 deliver.py（SVG + PNG + favicon）、make_mark.py（方形 mark）、
compare_*.py（对比图）、overview.py（总览）产出。
本脚本只负责生成「多场景适配」验证图。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
from renderer import render_logo
import logo_final as LF

DIR = os.path.dirname(os.path.abspath(__file__))
B1, E1 = LF.all_paths()


def main():
    # 反形 PNG（深底白图形）
    render_logo(B1, E1, os.path.join(DIR, "logo_ai_reverse.png"), 1280, 1.0,
                (1, 1, 1, 1), (0.067, 0.078, 0.11, 1))

    T = 400
    cases = [
        ("logo_ai_white.png", "正形 / 浅底"),
        ("logo_ai_reverse.png", "反形 / 深底"),
        ("logo_ai_on_blue.png", "反形 / 品牌蓝"),
        ("logo_ai_on_red.png", "反形 / 红底"),
        ("logo_ai_on_green.png", "正形 / 绿底"),
        ("logo_ai_mark.png", "方形 mark"),
    ]
    cols = 3
    rows = (len(cases) + cols - 1) // cols
    sheet = Image.new("RGB", (T * cols, (T + 32) * rows), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, (fn, lab) in enumerate(cases):
        p = os.path.join(DIR, fn)
        if not os.path.exists(p):
            continue
        im = Image.open(p).convert("RGB").resize((T, T), Image.LANCZOS)
        x, y = (i % cols) * T, (i // cols) * (T + 32)
        sheet.paste(im, (x, y + 28))
        d.rectangle([x, y + 28, x + T, y + 28 + T], outline=(200, 200, 205))
        d.text((x + 8, y + 8), lab, fill=(20, 20, 20))
    sheet.save(os.path.join(DIR, "check_backgrounds.png"))

    # 灰阶阶梯（单色印刷检验）
    import numpy as np
    gray = Image.open(os.path.join(DIR, "logo_ai_white.png")).convert("L")
    steps = [0, 64, 128, 192, 255]
    gw = 240
    gstrip = Image.new("RGB", (gw * len(steps), gw + 26), (255, 255, 255))
    dg = ImageDraw.Draw(gstrip)
    base = np.array(gray).astype(np.float32)
    for i, v in enumerate(steps):
        arr = base * (v / 255.0)
        tmp = Image.fromarray(arr.astype(np.uint8)).resize((gw, gw), Image.LANCZOS)
        gstrip.paste(tmp, (i * gw, 26))
        dg.text((i * gw + 8, 8), f"{v}% ink", fill=(30, 30, 30))
    gstrip.save(os.path.join(DIR, "check_grayscale.png"))

    print("ok: check_backgrounds.png / check_grayscale.png")


if __name__ == "__main__":
    main()
