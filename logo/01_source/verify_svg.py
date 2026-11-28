#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SVG 镂空验证（源码层 + 独立渲染器双重验证）
==========================================
1) 源码层：确认鱼身那条 <path> 的 d 里确实拼进了眼圆子路径，
   且带 fill-rule="evenodd"。
2) 渲染层：用 svglib 解析 -> 报告lab PDF -> 独立 PDF 渲染器 -> PNG，
   检查鱼眼位置是否为空白（洞）。
"""
import os, re, sys

DIR = os.path.dirname(os.path.abspath(__file__))


def check_source(svg_path):
    """检查每条 path 是否以 evenodd 声明；含眼圆坐标的那条即为镂空路径。
    眼圆坐标不写死（主版 84,628 / 简化版 98,620），改为检测路径中
    是否存在两段子路径（'M' 出现 2 次）且带 evenodd。"""
    s = open(svg_path, encoding="utf-8").read()
    paths = re.findall(r'<path d="([^"]*)"([^/]*)/>', s)
    print(f"\n--- {os.path.basename(svg_path)} ---")
    ok_evenodd = False
    for i, (d, attrs) in enumerate(paths):
        has_evenodd = "evenodd" in attrs
        n_sub = d.count("M")
        note = ""
        if has_evenodd and n_sub >= 2:
            note = " <= 鱼眼并入，evenodd 镂空成立"
            ok_evenodd = True
        print(f"  path[{i}] 子路径={n_sub} evenodd={str(has_evenodd):<5} len={len(d):5d}{note}")
    print(f"  => 鱼眼 evenodd 镂空: {'成立' if ok_evenodd else '未成立'}")
    return ok_evenodd


def render_via_pdf(svg_path, png_path):
    """svglib -> PDF -> (pypdfium2/fitz) -> PNG，独立于自研渲染器"""
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPDF
    pdf = svg_path.replace(".svg", "_v.pdf")
    d = svg2rlg(svg_path)
    renderPDF.drawToFile(d, pdf)
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(pdf)
        page = doc[0]
        w = page.get_width()
        bmp = page.render(scale=1280 / w)
        img = bmp.to_pil().convert("RGB")
        img.save(png_path)
        return img
    except ImportError:
        return None


if __name__ == "__main__":
    from PIL import Image
    import numpy as np

    allok = True
    for svg in ("logo_ai.svg", "logo_ai_reverse.svg", "logo_ai_mono_black.svg",
                "logo_ai_mono_white.svg", "logo_ai_small.svg"):
        p = os.path.join(DIR, svg)
        if not os.path.exists(p):
            continue
        allok &= check_source(p)

    # 独立渲染验证：logo_ai_mono_white.svg（深底白图形）里鱼眼应是深色洞
    print("\n=== 独立解析器渲染验证（svglib -> PDF -> PNG）===")
    svg = os.path.join(DIR, "logo_ai_mono_white.svg")
    img = render_via_pdf(svg, os.path.join(DIR, "_pdf_render.png"))
    if img is None:
        print("  (无 PDF 渲染后端，跳过像素验证)")
    else:
        a = np.array(img.convert("L"))
        print(f"  渲染尺寸 {img.size}")
        # 鱼眼区域应比周围白图形暗（洞透出深底）
        eye = a[616:640, 72:96]
        body = a[590:610, 200:260]
        print(f"  鱼眼区平均亮度 {eye.mean():.1f} (应接近背景深色)")
        print(f"  鱼身区平均亮度 {body.mean():.1f} (应接近白色)")
        print(f"  => 镂空在独立解析器中{'成立' if eye.mean() < body.mean() - 40 else '不成立'}")

    print("\n总判定:", "全部 SVG 镂空结构正确" if allok else "存在问题")
