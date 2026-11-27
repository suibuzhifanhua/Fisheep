# -*- coding: utf-8 -*-
"""make_brand_icons — 由 logo/ 包生成 app.ico / tray.ico / favicon.png（唯一入口）。

v1.0.785（品牌升级「AI 鱼&羊」）。取代三个旧品牌脚本：make_app_icon.py /
make_tray_icon.py / make_icon_from_image.py（全为旧「灵伴」画字/插画品牌，逻辑已
废弃，历史版见 git）。

真源 = logo/ 包的矢量渲染 PNG（01_source 几何真源经 cairo 逐档渲染——包内各档
就是按目标分辨率出的，**不要**从 256 母版再缩样重画）：
  · app.ico（8 档 16/20/24/32/48/64/128/256）← mark（深底圆角方 + 白标）：
    16/32/64/128/256 用包内原生档；20/24/48 包内没有，从 512 档 LANCZOS
    （矢量形状无字体笔画，降采样不糊）。
  · tray.ico（同 8 档，同一单色语言反相：近白竖向微渐变底 + 近黑标）——沿用旧
    make_tray_icon 的 TOP(244,244,245)→BOT(220,221,223)；标取自 mark 亮度通道
    （白标=亮区），形状取 mark alpha（圆角方），深标色近黑 (24,24,27)。
  · favicon.png（256×256 RGBA）← logo/05_png_mark/logo_mark_256.png 原样。

改几何只动 logo/01_source/logo_final.py 后重跑 pack_logo.py 出图，再跑本脚本。
复跑：python tools/make_brand_icons.py （PIL 必须）
"""
import os

from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK_DIR = os.path.join(BASE, "logo", "05_png_mark")
APP_OUT = os.path.join(BASE, "app.ico")
TRAY_OUT = os.path.join(BASE, "tray.ico")
FAV_OUT = os.path.join(BASE, "favicon.png")

SIZES = (256, 128, 64, 48, 32, 24, 20, 16)   # 与 v1.0.670 起八档口径一致
_NATIVE = (16, 32, 64, 128, 180, 192, 256, 512)  # 包内 mark 原生档
TOP = (244, 244, 245)   # tray 近白底（旧 make_tray_icon 同款）
BOT = (220, 221, 223)
DARK_GLYPH = (24, 24, 27)


def _mark(size):
    """取 size 档 mark：原生档直读，缺档（20/24/48）从 512 档 LANCZOS。"""
    if size in _NATIVE:
        return Image.open(os.path.join(MARK_DIR, "logo_mark_%d.png" % size)).convert("RGBA")
    return Image.open(os.path.join(MARK_DIR, "logo_mark_512.png")).convert("RGBA").resize(
        (size, size), Image.LANCZOS)


def _gradient(size):
    """近白竖向微渐变（顶 TOP → 底 BOT），一次画 1×256 再拉伸到目标档。"""
    strip = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255.0
        strip.putpixel((0, y), tuple(int(TOP[i] + (BOT[i] - TOP[i]) * t) for i in range(3)))
    return strip.resize((size, size), Image.BILINEAR)


def _tray_tile(size):
    """tray 一档：渐变浅底（mark 圆角形状裁边）+ 近黑标（mark 亮度>128 的亮区）。"""
    mark = _mark(size)
    shape = mark.getchannel("A")
    glyph = mark.convert("L").point(lambda v: 255 if v > 128 else 0)
    bg = _gradient(size).convert("RGBA")
    bg.putalpha(shape)
    dark = Image.new("RGBA", (size, size), DARK_GLYPH + (255,))
    dark.putalpha(glyph)
    return Image.alpha_composite(bg, dark)


def _ico(path, frames_desc, label):
    """frames_desc: [(size, image)] 升序；最大档带 sizes= 保存（同旧脚本口径）。"""
    frames = [im for _s, im in frames_desc]
    sizes = [(s, s) for s, _ in frames_desc]
    frames[-1].save(path, format="ICO", sizes=sizes, append_images=frames[:-1])
    print("[brand] %s ← %d 档 %s" % (label, len(frames), [s for s, _ in frames_desc]))


def build():
    _ico(APP_OUT, [(s, _mark(s)) for s in sorted(SIZES)], "app.ico")
    _ico(TRAY_OUT, [(s, _tray_tile(s)) for s in sorted(SIZES)], "tray.ico")
    fav = _mark(256)
    fav.save(FAV_OUT, format="PNG")
    print("[brand] favicon.png ← 256x256")


if __name__ == "__main__":
    build()
    # 自检：app/tray 各档拼预览条（白底上肉眼确认小尺寸可读），惯例落 build/。
    tiles = []
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        tiles += [_mark(s), _tray_tile(s)]
    gap = 10
    W = sum(t.size[0] for t in tiles) + gap * (len(tiles) - 1)
    H = 256
    canvas = Image.new("RGB", (W, H * 2 + gap), (255, 255, 255))
    x = 0
    for row in range(2):
        x = 0
        for t in tiles[row * 8:(row + 1) * 8]:
            canvas.paste(t, (x, row * (H + gap) + H - t.size[1]), t)
            x += t.size[0] + gap
    _p = os.path.join(BASE, "build", "_brand_icons_preview.png")
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    canvas.save(_p)
    print("[brand] 预览 → %s" % _p)
