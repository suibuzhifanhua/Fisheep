#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
overview.py —— 交付总览图
=========================
仅依赖本版产物，不引用任何旧版中间文件。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
import logo_final as LF
import logo_small as LS
from renderer import render_logo

DIR = os.path.dirname(os.path.abspath(__file__))
B1, E1 = LF.all_paths()
B2, E2 = LS.all_paths()


def main():
    W, H = 1400, 900
    sheet = Image.new("RGB", (W, H), (252, 252, 254))
    d = ImageDraw.Draw(sheet)

    # ---- 主图 / 反形 / mark ----
    main = Image.open(os.path.join(DIR, "logo_ai_white.png")).convert("RGB").resize((380, 380), Image.LANCZOS)
    sheet.paste(main, (40, 60))
    d.text((40, 38), "PRIMARY   logo_ai.svg  /  logo_ai_white.png", fill=(20, 20, 20))

    rev = Image.open(os.path.join(DIR, "logo_ai_reverse.png")).convert("RGB").resize((380, 380), Image.LANCZOS)
    sheet.paste(rev, (440, 60))
    d.text((440, 38), "REVERSE   logo_ai_reverse.svg", fill=(20, 20, 20))

    mk = Image.open(os.path.join(DIR, "logo_ai_mark.png")).convert("RGBA").resize((380, 380), Image.LANCZOS)
    sheet.paste(mk, (840, 60), mk)
    d.text((840, 38), "MARK      logo_ai_mark.svg", fill=(20, 20, 20))

    # ---- 尺寸阶梯（主版 + 简化版对照）----
    d.text((40, 470), "SIZES  512 / 256 / 128 / 64 / 48 / 32 / 16    (top: full    bottom: small-size variant)",
           fill=(20, 20, 20))
    x = 40
    ROW_H = 128
    for s in (512, 256, 128, 64, 48, 32, 16):
        full_p = os.path.join(DIR, f"favicon_{s}.png")
        small_p = os.path.join(DIR, f"favicon_small_{s}.png")
        if not (os.path.exists(full_p) and os.path.exists(small_p)):
            continue
        show = min(s, ROW_H)
        full = Image.open(full_p).convert("RGB").resize((show, show), Image.LANCZOS)
        small = Image.open(small_p).convert("RGB").resize((show, show), Image.LANCZOS)
        sheet.paste(full, (x, 496))
        sheet.paste(small, (x, 496 + show + 4))
        d.text((x, 496 + show * 2 + 10), f"{s}", fill=(110, 110, 118))
        x += show + 18

    # ---- 多背景 ----
    d.text((40, 790), "BACKGROUNDS", fill=(20, 20, 20))
    x = 40
    for fn in ("logo_ai_white.png", "logo_ai_reverse.png", "logo_ai_on_blue.png",
               "logo_ai_on_red.png", "logo_ai_on_green.png"):
        p = os.path.join(DIR, fn)
        if not os.path.exists(p):
            continue
        im = Image.open(p).convert("RGB").resize((118, 118), Image.LANCZOS)
        sheet.paste(im, (x, 810))
        d.rectangle([x, 810, x + 118, 928], outline=(215, 215, 220))
        x += 130

    sheet.save(os.path.join(DIR, "delivery_overview.png"))
    print("ok", sheet.size)


if __name__ == "__main__":
    main()
