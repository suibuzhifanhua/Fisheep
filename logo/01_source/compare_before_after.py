#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compare_before_after.py —— 原图 vs 最终版对照
==========================================
左侧为用户提供的原始素材，右侧为最终定稿的 AI logo。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw

DIR = os.path.dirname(os.path.abspath(__file__))
# v1.0.785 去个性化：原图路径不再写死某台机器的 clipboard 目录（镜像门禁拦个人
# 路径），走命令行参数；缺省找包内 original_ref.png（自备原图放这里即可）。
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DIR, "original_ref.png")


def main():
    orig = Image.open(SRC).convert("RGB")
    new = Image.open(os.path.join(DIR, "logo_ai_white.png")).convert("RGB")
    T = 560
    canvas = Image.new("RGB", (T * 2 + 60, T + 70), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    canvas.paste(orig.resize((T, T), Image.LANCZOS), (20, 50))
    canvas.paste(new.resize((T, T), Image.LANCZOS), (T + 40, 50))
    d.text((24, 18), "ORIGINAL  (source artwork)", fill=(30, 30, 30))
    d.text((T + 44, 18), "FINAL  (AI logo: fish + ram horn)", fill=(30, 30, 30))
    canvas.save(os.path.join(DIR, "compare_before_after.png"))
    print("ok")


if __name__ == "__main__":
    main()
