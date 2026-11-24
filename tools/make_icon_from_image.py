# -*- coding: utf-8 -*-
"""make_icon_from_image — 由一张插画生成 app.ico / tray.ico / favicon.png。

v1.0.681（用户给了新图标：黑白线条插画，羊角角色）。

为什么不用原来的 make_app_icon.py：那个是**画字**（PIL 逐档渲染「灵」字形），
新图是**现成插画**，需要的是"裁切 + 缩放 + 锐化"，两件事的管线完全不同。

关键设计——**分档裁切**（本脚本的核心）：
插画缩到 16px 会糊成一团黑团。所有图标工具（Windows/macOS）都按尺寸做不同裁切：
大尺寸给完整构图，小尺寸**紧裁主体**（只留最有识别度的部分）。

本图主体识别特征 = **白色羊角 + 黑发**（脸在 16px 下认不出，羊角能认出）。
所以：
  · 16/20/24 档：只取"羊角 + 额头"（横向窄、纵向更浅），主体占满 → 羊角清晰
  · 32/48 档：取"羊角 + 脸"
  · 64/128/256 档：取完整构图（羊角 + 脸 + 上身）

缩放用 LANCZOS（插画是连续调，降采样不应用 NEAREST），
小尺寸额外做一次 UnsharpMask —— 16px 下线条发虚，锐化能把羊角边缘拉回来。

白底处理：源图是近白底（254,255,250）+ 极淡的圆形边界。保留白底，
但把**圆形边界加重**成一道细灰环 —— 否则浅色任务栏上图标"融"进背景、
看不出边界（原来 app.ico 是近黑底，与白底插画完全相反的策略）。

托盘：白底黑线在**深色**任务栏上清晰；浅色任务栏上 Windows 会自己处理。
托盘只需要 16~48 五档，不做紧裁（托盘通常显示 16/24，居中看更清楚）。

复跑：python tools/make_icon_from_image.py [源图.jpg]（缺省 tools/icon_src.jpg）
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# v1.0.722 入库去个性化：源图路径不再写死某台机器的微信 temp 目录，走命令行参数
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "tools", "icon_src.jpg")

APP_OUT = os.path.join(BASE, "app.ico")
TRAY_OUT = os.path.join(BASE, "tray.ico")
FAV_OUT = os.path.join(BASE, "favicon.png")

# 源图 1280x1280 的裁切框（左, 上, 右, 下）——按 10x10 网格实测构图定标：
#   羊角横向占 1.5~8.2 格；脸心在第 6 格横 / 第 5 格纵附近；肩线在第 7 格纵。
# 大尺寸给完整构图，小尺寸往上收（羊角优先）。
CROPS = {
    # size: (left, top, right, bottom)
    16: (268, 72, 1012, 816),      # 羊角 + 额头，主体占满
    20: (250, 62, 1030, 842),
    24: (228, 48, 1052, 872),
    32: (195, 55, 1085, 945),      # 羊角 + 脸
    48: (175, 45, 1105, 975),
    64: (160, 40, 1120, 1000),
    128: (150, 35, 1130, 1015),
    256: (140, 30, 1140, 1030),
}
TRAY_CROPS = {16: CROPS[16], 20: CROPS[20], 24: CROPS[24], 32: CROPS[32], 48: CROPS[48]}

BG = (254, 255, 250)      # 源图底色（近白微暖）
# 圆形边界细环：白底插画在**浅色任务栏**上会整块融进背景（实测：只描一道浅灰环
# 完全看不出边界，图标像"消失"了）。这里用中深灰 + 随尺寸加粗，
# 保证 16px（4x 画布上 4px ≈ 成品 1px）到 256px 都有清晰轮廓。
RING = (118, 123, 133)


def _stretch(im, lo=18, hi=242):
    """对比度拉伸：把黑发压到接近纯黑、把白羊角提到接近纯白。

    为什么必须做：插画是**连续调**（黑发里有灰、白角里有浅灰），
    缩到 16px 后中间调糊成一团灰，白角和黑发的边界消失 → 图标变成一坨。
    拉伸后只剩"极黑"与"极白"两个值，白角成为黑底上的清晰镂空。
    """
    return im.point(lambda v: max(0, min(255, int((v - lo) * 255.0 / (hi - lo)))))


def _load():
    if not os.path.isfile(SRC):
        raise SystemExit("[icon] 找不到源图：%s" % SRC)
    return Image.open(SRC).convert("RGB")


def _circular(im):
    """把裁切块放进正圆（画布外裁成透明），并描一道细灰环。

    不裁圆的话，插画里的水草/气泡会伸出方形边界，小尺寸下变成杂乱噪点。
    """
    n = min(im.size)
    side = im.crop((0, 0, n, n)) if im.size[0] == im.size[1] else None
    if side is None:
        # 非正方形先补白到正方形（居中）
        c = Image.new("RGB", (n, n), BG)
        c.paste(im, ((n - im.size[0]) // 2, (n - im.size[1]) // 2))
        side = c
    mask = Image.new("L", (n * 4, n * 4), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((2, 2, n * 4 - 3, n * 4 - 3), fill=255)
    mask = mask.resize((n, n), Image.LANCZOS)
    out = Image.new("RGBA", (n, n), BG + (0,))
    out.paste(side.convert("RGBA"), (0, 0), mask)
    # 描边环
    ring = Image.new("RGBA", (n * 4, n * 4), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ring)
    dr.ellipse((2, 2, n * 4 - 3, n * 4 - 3), outline=RING + (235,), width=max(4, n // 8))
    ring = ring.resize((n, n), Image.LANCZOS)
    out.alpha_composite(ring)
    return out


def _render(src, size, crop, sharpen=True):
    """按目标分辨率单独裁切 + 缩放（不复用同一张母版缩下来——那是插画变糊的主因）。"""
    region = src.crop(crop)
    im = _circular(region)
    im = im.resize((size, size), Image.LANCZOS)
    if sharpen and size <= 48:
        # 小尺寸锐化：线条发虚时把边缘拉回来（半径随尺寸缩，1px 级别刚好）
        im = im.filter(ImageFilter.UnsharpMask(radius=0.8, percent=135, threshold=2))
    return im


def _render_boosted(src, size, crop):
    """小尺寸专用：先对比度拉伸再缩放（白角镂空在 16px 下才立得住）。"""
    region = _stretch(src.crop(crop))
    im = _circular(region)
    im = im.resize((size, size), Image.LANCZOS)
    return im.filter(ImageFilter.UnsharpMask(radius=0.7, percent=150, threshold=1))


def build():
    src = _load()
    frames = []
    for size in sorted(CROPS):
        if size <= 24:
            frames.append(_render_boosted(src, size, CROPS[size]))
        else:
            frames.append(_render(src, size, CROPS[size]))
    frames[-1].save(APP_OUT, format="ICO",
                     sizes=[(s, s) for s in sorted(CROPS)],
                     append_images=frames[:-1])
    print("[icon] app.ico ← %d 档 %s" % (len(frames), sorted(CROPS)))

    tf = []
    for size in sorted(TRAY_CROPS):
        if size <= 24:
            tf.append(_render_boosted(src, size, TRAY_CROPS[size]))
        else:
            tf.append(_render(src, size, TRAY_CROPS[size]))
    tf[-1].save(TRAY_OUT, format="ICO",
                sizes=[(s, s) for s in sorted(TRAY_CROPS)],
                append_images=tf[:-1])
    print("[icon] tray.ico ← %d 档 %s" % (len(tf), sorted(TRAY_CROPS)))

    fav = _render(src, 256, CROPS[256], sharpen=False)
    fav.save(FAV_OUT, format="PNG")
    print("[icon] favicon.png ← 256x256")


if __name__ == "__main__":
    build()
    # 自检：把各档拼一张，肉眼确认小尺寸可读
    _src = _load()
    tiles = [(_render_boosted(_src, s, CROPS[s]) if s <= 24 else _render(_src, s, CROPS[s]))
             for s in (16, 20, 24, 32, 48, 64, 128, 256)]
    W = sum(t.size[0] for t in tiles) + 7 * 10
    H = max(t.size[1] for t in tiles)
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    x = 0
    for t in tiles:
        canvas.paste(t, (x, H - t.size[1]), t)
        x += t.size[0] + 10
    _p = os.path.join(BASE, "build", "_icon_preview.png")
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    canvas.save(_p)
    print("[icon] 预览 → %s" % _p)
