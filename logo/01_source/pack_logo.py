#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pack_logo.py —— 一键把 AI 鱼&羊 Logo 打包成「可调用 + 可编辑」的桌面资产包
=========================================================================
从几何真源（logo_final / logo_small）重新生成全部 SVG + 各尺寸 PNG（干净版，无版权戳），
拷贝源文件 / 参考图 / 原始备份，统一命名，产出到：
    C:\\Users\\<user>\\Desktop\\Fisheep_AI_Logo

运行：  python pack_logo.py        （需 Python3.12 + pycairo + Pillow + numpy）
复现：  改任何参数后重跑即可整体重建，桌面包会被重建（先删后建）。
"""
import os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logo_final as LF
import logo_small as LS
from PIL import Image
from renderer import render_logo
import brand_kit as BK

WS  = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.expanduser("~"), "Desktop", "Fisheep_AI_Logo")

FOLDERS = ["01_source", "02_svg", "03_png_main", "04_png_web",
           "05_png_mark", "06_reference", "07_originals"]
WEB_SIZES  = [512, 256, 192, 180, 128, 64, 48, 32, 16]
MARK_SIZES = [512, 256, 192, 180, 128, 64, 32, 16]

# 10 种颜色类型 -> 03 主图文件名（1024）
MAIN10 = [
    (0, "logo_primary_black_alpha_1024.png"),
    (1, "logo_primary_black_white_1024.png"),
    (2, "logo_reverse_white_alpha_1024.png"),
    (3, "logo_reverse_white_dark_1024.png"),
    (4, "logo_reverse_white_blue_1024.png"),
    (5, "logo_reverse_white_red_1024.png"),
    (6, "logo_reverse_green_green_1024.png"),
    (7, "logo_mono_black_white_1024.png"),
    (8, "logo_mono_white_dark_1024.png"),
    (9, "logo_mark_dark_1024.png"),
]


def fresh():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    for f in FOLDERS:
        os.makedirs(os.path.join(OUT, f))
    os.makedirs(os.path.join(OUT, "07_originals", "py"))
    os.makedirs(os.path.join(OUT, "07_originals", "images"))


# ---------- 02 SVG（矢量，首选）----------
def gen_svg():
    d = os.path.join(OUT, "02_svg")
    specs = [
        ("logo_primary_black_alpha.svg", LF.svg_document("#000000", None, "hole", "AI Logo - Fish & Ram")),
        ("logo_reverse_white_alpha.svg", LF.svg_document("#FFFFFF", None, "hole", "AI Logo reversed")),
        ("logo_mono_black_white.svg",    LF.svg_document("#000000", "#FFFFFF", "hole", "AI Logo mono black")),
        ("logo_mono_white_dark.svg",     LF.svg_document("#FFFFFF", "#111111", "hole", "AI Logo mono white")),
        ("logo_small_alpha.svg",         LS.svg_document("#000000", None, "AI Logo small")),
    ]
    for name, doc in specs:
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(doc)
    return len(specs)


# ---------- 03 主图（1024，10 颜色类型，RGB 展示母版）----------
def gen_main():
    d = os.path.join(OUT, "03_png_main")
    n = 0
    for idx, fname in MAIN10:
        variant = BK.VARIANTS[idx]
        tile, _ = BK.render_tile(variant, 1024, with_stamp=False)
        tile.convert("RGB").save(os.path.join(d, fname))
        n += 1
    return n


# ---------- 04 Web 常用尺寸（正形·黑·透明底，真透明 RGBA）----------
def gen_web():
    d = os.path.join(OUT, "04_png_web")
    B, E = LF.all_paths()
    n = 0
    for s in WEB_SIZES:
        tmp = os.path.join(BK.KIT, "_web_tmp.png")
        render_logo(B, E, tmp, s, 2.0, (0, 0, 0, 1), None)
        im = Image.open(tmp).convert("RGBA").resize((s, s), Image.LANCZOS)
        im.save(os.path.join(d, "logo_primary_black_%d.png" % s))
        os.remove(tmp)
        n += 1
    return n


# ---------- 05 mark（圆角方形，头像/favicon 首选，透明四角 RGBA）----------
def gen_mark():
    d = os.path.join(OUT, "05_png_mark")
    n = 0
    for s in MARK_SIZES:
        tmp = os.path.join(BK.KIT, "_mark_tmp_pack.png")
        BK.render_mark(tmp, s, scale=2.0)          # 2s 高分辨渲染
        im = Image.open(tmp).convert("RGBA").resize((s, s), Image.LANCZOS)  # 降回目标尺寸
        im.save(os.path.join(d, "logo_mark_%d.png" % s))
        os.remove(tmp)
        n += 1
    return n


# ---------- 01 源文件（可编辑）----------
def copy_source():
    d = os.path.join(OUT, "01_source")
    pys = ["geom.py", "logo_final.py", "logo_small.py", "renderer.py", "make_mark.py",
           "brand_kit.py", "deliver.py", "deliver2.py", "overview.py", "verify_svg.py",
           "compare_horn.py", "compare_before_after.py", "pack_logo.py"]
    n = 0
    for f in pys:
        src = os.path.join(WS, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(d, f)); n += 1
    with open(os.path.join(d, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("# AI 鱼&羊 Logo 源文件依赖（Python 3.12）\n"
                "pycairo\nPillow\nnumpy\n")
    return n


# ---------- 06 参考/验收图 ----------
def copy_reference():
    d = os.path.join(OUT, "06_reference")
    refs = ["brand_overview.png", "check_backgrounds.png",
            "check_grayscale.png", "check_small_sizes.png", "compare_horn.png"]
    n = 0
    for f in refs:
        src = os.path.join(WS, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(d, f)); n += 1
    return n


# ---------- 07 原始素材备份（未改目录结构，保留原始文件名）----------
def copy_originals():
    pyd = os.path.join(OUT, "07_originals", "py")
    imgd = os.path.join(OUT, "07_originals", "images")
    n = 0
    for f in os.listdir(WS):
        p = os.path.join(WS, f)
        if os.path.isfile(p) and f.lower().endswith(".py"):
            shutil.copy2(p, os.path.join(pyd, f)); n += 1
        elif os.path.isfile(p) and f.lower().endswith((".svg", ".png")):
            shutil.copy2(p, os.path.join(imgd, f)); n += 1
    bk = os.path.join(WS, "brand_kit")
    if os.path.isdir(bk):
        bkd = os.path.join(imgd, "brand_kit")
        os.makedirs(bkd, exist_ok=True)
        for f in os.listdir(bk):
            p = os.path.join(bk, f)
            if os.path.isfile(p) and f.lower().endswith((".png", ".svg", ".md")):
                shutil.copy2(p, os.path.join(bkd, f)); n += 1
        bsvg = os.path.join(bk, "svg")
        if os.path.isdir(bsvg):
            os.makedirs(os.path.join(bkd, "svg"), exist_ok=True)
            for f in os.listdir(bsvg):
                if f.endswith(".svg"):
                    shutil.copy2(os.path.join(bsvg, f), os.path.join(bkd, "svg", f)); n += 1
    return n


def copy_readme():
    src = os.path.join(WS, "README_logo.md")
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(OUT, "README.md"))


def clean_temps():
    for t in ("_mark_tmp.png", "_tile_tmp.png", "_web_tmp.png",
              "_mark1024.png", "_c05.png", "_ctop.png", "_cfoot.png"):
        for base in (BK.KIT, WS):
            p = os.path.join(base, t)
            if os.path.exists(p):
                os.remove(p)


def main():
    fresh()
    stats = {"02_svg": gen_svg(), "03_png_main": gen_main(),
             "04_png_web": gen_web(), "05_png_mark": gen_mark(),
             "01_source": copy_source(), "06_reference": copy_reference(),
             "07_originals": copy_originals()}
    copy_readme()
    clean_temps()
    print("PACK ->", OUT)
    for k, v in stats.items():
        print("  %-14s %d files" % (k, v))


if __name__ == "__main__":
    main()
