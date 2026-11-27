# -*- coding: utf-8 -*-
"""make_app_icon — 生成 app.ico（v1.0.671 更名「灵伴」，glyph 微→灵；v1.0.670 初版为 8 档多分辨率）。

旧 app.ico 只有单张 16×16 位图（181 字节）——任务栏/Alt-Tab/托盘放大后糊成一团。
本脚本生成 16/20/24/32/48/64/128/256 八档；**每档单独按目标分辨率重新栅格化**
（不缩放 256 母版——16px 下缩样的汉字笔画会糊，分档重画才锐利）。

口径：v1.0.674（用户："不要绿色配色的图标 要素色黑白高级一点"）——**黑白单色系统**，
高级感来自材质而非颜色：
  · 底色：近黑竖向微渐变（顶 #232326 → 底 #101012），哑光「一块料」；
  · 氛围光：大而弥散的顶部径向白光（半径 ~0.5S，低强度）——近黑底上的"顶光"纵深；
  · 顶部玻璃微光：顶缘向下淡出的白渐变（alpha 极低），不靠亮线；
  · 字：白**线条型**"伴"（v1.0.676，用户："做成这样线条型"——实心 mask 腐蚀出
    0.009S 细环）＋轻浮线影。
窗口图标 / 任务栏 / build.py 打包图标共用这一份；托盘走独立反色 tray.ico
（近白底＋近黑字，同一语言反相，见 tools/make_tray_icon.py）。
复跑：python tools/make_app_icon.py （PIL 必须；字体用系统 微软雅黑粗体）
"""
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.ico")
SIZES = (256, 128, 64, 48, 32, 24, 20, 16)
FONT_PATHS = (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf")
# 黑白单色（v1.0.674）：近黑竖向微渐变——顶 #232326 → 底 #101012，哑光有纵深
TOP = (35, 35, 38)
BOT = (16, 16, 18)


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
    # 渐变底
    base = Image.new("RGB", (S, S))
    px = base.load()
    for y in range(S):
        t = y / (S - 1)
        r = int(TOP[0] + (BOT[0] - TOP[0]) * t)
        g = int(TOP[1] + (BOT[1] - TOP[1]) * t)
        b = int(TOP[2] + (BOT[2] - TOP[2]) * t)
        for x in range(S):
            px[x, y] = (r, g, b)
    # 氛围光：一个**大而弥散**的顶部径向柔光——像从上头打下来的一缕光，
    # 半径 ~0.5S、低强度、大模糊。取代旧的小而硬光斑，让底色像一块被打光的料。
    glow = Image.new("L", (S, S), 0)
    gr = int(S * 0.50)
    cx, cy = int(S * 0.5), int(S * 0.12)
    ImageDraw.Draw(glow).ellipse((cx - gr, cy - gr, cx + gr, cy + gr), fill=46)
    glow = glow.filter(ImageFilter.GaussianBlur(int(S * 0.18)))
    base = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), base, glow.point(lambda v: min(255, v)))
    # 顶部玻璃微光：从顶缘向下淡出的白渐变（占 ~52% 高度，alpha 极低）。
    # 哑光玻璃的高级暗示——不靠亮线，靠"从顶亮一点点往下没"的过渡。
    glass = Image.new("L", (S, S), 0)
    for y in range(int(S * 0.52)):
        a = int(26 * (1 - y / (S * 0.52)) ** 2)   # 顶部 ~26，二次方衰减到 0
        ImageDraw.Draw(glass).line((0, y, S, y), fill=a)
    base = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), base, glass)
    # "伴" 字——v1.0.676 线条型（用户："做成这样线条型"，参考 Qoder 标语言）：
    # 实心字 mask 用 MinFilter 腐蚀掉内芯，只留 **0.009S 的细环**＝白线条描边。
    # 字号 0.58S（比实心版略大，线条型要撑满）。16 档细环会融合成白色字形色块——
    # CJK 10 画字在 16px 的物理极限，仍保"白色字形"可辨（托盘 16px 场景）。
    glyph_full = Image.new("L", (S, S), 0)
    f = _font(int(S * 0.58))
    d = ImageDraw.Draw(glyph_full)
    bb = d.textbbox((0, 0), "伴", font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((S - tw) / 2 - bb[0], (S - th) / 2 - bb[1] - int(S * 0.01)), "伴", font=f, fill=255)
    ew = max(1, int(S * 0.009))
    glyph = ImageChops.subtract(glyph_full, glyph_full.filter(ImageFilter.MinFilter(2 * ew + 1)))
    # 线框轻浮投影（线条版：阴影也取线环，比实心版更"轻"）
    shadow = Image.new("L", (S, S), 0)
    shadow.paste(glyph, (int(S * 0.006), int(S * 0.012)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(int(S * 0.012)))
    base = base.convert("RGB")
    sh_rgb = Image.new("RGB", (S, S), (0, 0, 0))
    base = Image.composite(sh_rgb, base, shadow.point(lambda v: min(255, int(v * 0.45))))
    # Image.composite(A,B,mask)=mask 高处取 A：要「伴」线条=白 → A=白、B=底
    base = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), base, glyph)
    # squircle 蒙版
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(base, (0, 0), rounded_mask(S, int(S * 0.225)))
    return img.resize((size, size), Image.LANCZOS)


def main():
    frames = [render(s) for s in SIZES]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[1:])
    import struct
    data = open(OUT, "rb").read()
    n = struct.unpack("<H", data[2:4])[0]
    print(f"OK app.ico: {n} frames {SIZES}  {len(data)} bytes")
    if len(data) > 5_000_000:
        print("!! 体积异常，检查"); sys.exit(1)


if __name__ == "__main__":
    main()
