# -*- coding: utf-8 -*-
"""make_tray_icon — 生成 tray.ico（v1.0.671 用户："托盘图标颜色配色改下 浅色系高级一些"）。

v1.0.674（用户："不要绿色配色的图标 要素色黑白高级一点"）：托盘改**反相黑白**——
近白浅灰渐变底 + 近黑线条"伴"字 + 哑光，与近黑窗口标 app.ico（tools/make_app_icon.py）
构成同一单色语言的反相，在深色/浅色任务栏上都读得清。
v1.0.676（用户："做成这样线条型"）：字形改线条描边（实心 mask 腐蚀出细环），
与 app.ico 同参数（0.009S 线宽、0.58S 字号）。

生成 16/20/24/32/48 五档（托盘不需要更大），每档按目标分辨率单独栅格化。
消费位：desktop/tray.py::_make_icon 优先读 tray.ico（缺失回退 app.ico → 绿圆兜底）。
复跑：python tools/make_tray_icon.py （PIL 必须）
"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tray.ico")
SIZES = (48, 32, 24, 20, 16)
FONT_PATHS = (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf")
# 近白浅灰渐变（v1.0.674 反相黑白）：顶 #f4f4f5 → 底 #dcdddf，保持空气感
TOP = (244, 244, 245)
BOT = (220, 221, 223)
# 近黑"灵"字（与浅底形成克制对比，不刺眼）
GLYPH = (24, 24, 27)


def _font(sz):
    for p in FONT_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, sz, index=0)
    raise SystemExit("找不到 CJK 粗体字体（msyhbd.ttc）")


def rounded_mask(size, rad):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=rad, fill=255)
    return m


def render(size):
    S = 4 * size
    # 浅薄荷渐变底
    base = Image.new("RGB", (S, S))
    px = base.load()
    for y in range(S):
        t = y / (S - 1)
        r = int(TOP[0] + (BOT[0] - TOP[0]) * t)
        g = int(TOP[1] + (BOT[1] - TOP[1]) * t)
        b = int(TOP[2] + (BOT[2] - TOP[2]) * t)
        for x in range(S):
            px[x, y] = (r, g, b)
    # 右上极淡柔光（提一点空气感，克制成哑光）
    glow = Image.new("L", (S, S), 0)
    gr = int(S * 0.22)
    ImageDraw.Draw(glow).ellipse((int(S * 0.60) - gr, int(S * 0.10) - gr,
                                  int(S * 0.60) + gr, int(S * 0.10) + gr), fill=45)
    glow = glow.filter(ImageFilter.GaussianBlur(int(S * 0.06)))
    base = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), base, glow.point(lambda v: min(255, v)))
    # "伴"字——v1.0.676 线条型（同 app.ico 语言）：实心 mask 腐蚀出 0.009S 细环，
    # 16 档融合成近黑字形色块（CJK 物理极限，可辨）
    glyph_full = Image.new("L", (S, S), 0)
    f = _font(int(S * 0.58))
    d = ImageDraw.Draw(glyph_full)
    bb = d.textbbox((0, 0), "伴", font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((S - tw) / 2 - bb[0], (S - th) / 2 - bb[1] - int(S * 0.01)), "伴", font=f, fill=255)
    ew = max(1, int(S * 0.009))
    glyph = ImageChops.subtract(glyph_full, glyph_full.filter(ImageFilter.MinFilter(2 * ew + 1)))
    shadow = Image.new("L", (S, S), 0)
    shadow.paste(glyph, (int(S * 0.010), int(S * 0.014)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(S * 0.012)))
    base = base.convert("RGB")
    sh_rgb = Image.new("RGB", (S, S), (0, 0, 0))
    base = Image.composite(sh_rgb, base, shadow.point(lambda v: min(255, int(v * 0.25))))
    # composite(A,B,mask)=mask 高处取 A：要"伴"字=近黑 → A=近黑、B=浅底
    g_rgb = Image.new("RGB", (S, S), GLYPH)
    base = Image.composite(g_rgb, base, glyph)
    # squircle 蒙版
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(base, (0, 0), rounded_mask(S, int(S * 0.24)))
    return img.resize((size, size), Image.LANCZOS)


def main():
    frames = [render(s) for s in SIZES]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[1:])
    import struct
    data = open(OUT, "rb").read()
    n = struct.unpack("<H", data[2:4])[0]
    print(f"OK tray.ico: {n} frames {SIZES}  {len(data)} bytes")


if __name__ == "__main__":
    main()
