# AI 鱼&羊 Logo 图标项目 · 使用说明

> 品牌：FISHEEP.L  ——  "AI" 字母标识，**A 内藏鱼**（尖吻带眼 / 纺锤身 / 燕尾），**I 上承公羊角**（外弧饱满 / 内端小涡），I 顶端为干笔散锋。
> 版权：© 2026 Fisheep.L
> 本包由 `01_source/pack_logo.py` 一键生成，几何真源为 `logo_final.py`，**改参数后重跑即可整体重建**。

---

## 一、目录结构

```
Fisheep_AI_Logo/
├── README.md                 # 本说明文档
├── 01_source/                # 可编辑源文件（几何真源 + 渲染/生成脚本）
│   ├── geom.py               #   几何工具：贝塞尔采样 / 变宽描边(ribbon) / 路径序列化
│   ├── logo_final.py         #   ★ 主几何（唯一真源）：A+鱼 / I+羊角+散锋+衬线
│   ├── logo_small.py         #   ★ 小尺寸简化几何（≤32px 专用，眼加大/锋丝减根/内涡减圈）
│   ├── renderer.py           #   cairo 渲染器：render_logo / render_crop / build_path
│   ├── make_mark.py          #   圆角方形 mark 生成（自动内缩放居中）
│   ├── brand_kit.py          #   品牌套件生成：10 颜色类型 + 品牌总览图（含 STAMP 版权戳开关）
│   ├── deliver.py            #   原始交付脚本（SVG + PNG + favicon）
│   ├── deliver2.py           #   场景 / 灰阶检查图
│   ├── overview.py           #   交付总览图
│   ├── verify_svg.py         #   SVG 独立解析校验
│   ├── compare_horn.py       #   （辅助）羊角形态对照
│   ├── compare_before_after.py# （辅助）改动前后对照
│   ├── pack_logo.py          #   ★ 本包打包脚本（改完重跑即复现整个桌面包）
│   └── requirements.txt      #   依赖：pycairo / Pillow / numpy
├── 02_svg/                   # 矢量图（★首选，无限缩放，任意背景成立）
│   ├── logo_primary_black_alpha.svg   # 正形·黑·透明底
│   ├── logo_reverse_white_alpha.svg   # 反形·白·透明底
│   ├── logo_mono_black_white.svg      # 单色黑·白底（印刷）
│   ├── logo_mono_white_dark.svg       # 单色白·深底（印刷）
│   └── logo_small_alpha.svg           # 小尺寸简化·透明底（极小场景）
├── 03_png_main/              # 位图主图（1024×1024，10 种颜色类型，RGB 展示母版）
├── 04_png_web/               # 正形·黑·透明底（★Web/App 通用，真透明 RGBA，常用尺寸）
├── 05_png_mark/              # mark 圆角方形（★头像/favicon 首选，透明四角 RGBA，常用尺寸）
├── 06_reference/             # 参考/验收图（设计验收用，非直接调用素材）
└── 07_originals/             # 原始素材备份（未整理前的原始文件，保留原始文件名）
    ├── py/                   #   原始 .py 全量
    └── images/               #   原始 .svg / .png + brand_kit/ 子目录
```

---

## 二、文件清单与用途

### 02_svg（矢量，首选）
| 文件 | 形态 | 用途 |
|------|------|------|
| `logo_primary_black_alpha.svg` | 黑图 + 透明底 | 深色/浅色底都能放的正形主标识，最通用 |
| `logo_reverse_white_alpha.svg` | 白图 + 透明底 | 深色底/彩色底上的反形（鱼眼自动透底） |
| `logo_mono_black_white.svg` | 黑图 + 白底 | 单色印刷（黑白报纸/单色激光打印） |
| `logo_mono_white_dark.svg` | 白图 + 深底 | 单色反白印刷（深底/烫白） |
| `logo_small_alpha.svg` | 简化几何 | 16–32px 极小尺寸专用，避免糊成一团 |

### 03_png_main（1024 主图，10 颜色类型）
| 文件 | 说明 |
|------|------|
| `logo_primary_black_alpha_1024.png` | 正形·黑·透明底（棋盘格展示） |
| `logo_primary_black_white_1024.png` | 正形·黑·白底 |
| `logo_reverse_white_alpha_1024.png` | 反形·白·透明底（棋盘格展示） |
| `logo_reverse_white_dark_1024.png` | 反形·白·深底 |
| `logo_reverse_white_blue_1024.png` | 反形·白·品牌蓝 |
| `logo_reverse_white_red_1024.png` | 反形·白·红底 |
| `logo_reverse_green_green_1024.png` | 深绿·绿底（品牌绿强调版） |
| `logo_mono_black_white_1024.png` | 单色黑·白底（印刷） |
| `logo_mono_white_dark_1024.png` | 单色白·深底（印刷） |
| `logo_mark_dark_1024.png` | 圆角 mark·深底（头像/favicon 母版） |

### 04_png_web（正形·黑·透明底，真透明）
`logo_primary_black_{512,256,192,180,128,64,48,32,16}.png`
→ 网页 logo、App 图标源、文档插图等需要透明背景的通用尺寸。

### 05_png_mark（圆角方形，透明四角）
`logo_mark_{512,256,192,180,128,64,32,16}.png`
→ 头像、favicon、桌面/移动端图标首选（圆角方形 + 透明四角，直接可用）。

### 06_reference（验收参考，勿直接调用）
`brand_overview.png`（品牌总览）· `check_backgrounds.png`（多背景）· `check_grayscale.png`（灰阶）· `check_small_sizes.png`（小尺寸阶梯）· `compare_horn.png`（羊角对照）。

### 07_originals（原始备份）
打包前 workspace 里全部 Logo 相关 `.py`/`.svg`/`.png` 的原始副本（保留原文件名），用于追溯与对比，不参与日常调用。

---

## 三、颜色类型速查（10 类）

| # | 类型 | 图形 / 底色 | 推荐场景 |
|---|------|-----------|---------|
| 01 | 正形·黑·透明底 | 黑图 + 透明 | 浅色/白色界面主标识 |
| 02 | 正形·黑·白底 | 黑图 + 白 | 需要白底的场合 |
| 03 | 反形·白·透明底 | 白图 + 透明 | 深色/彩色底 |
| 04 | 反形·白·深底 | 白图 + #12141C | 深色 UI |
| 05 | 反形·白·品牌蓝 | 白图 + 品牌蓝 | 品牌蓝场景 |
| 06 | 反形·白·红底 | 白图 + 红 | 红色场景 |
| 07 | 深绿·绿底 | 深绿图 + 绿 | 品牌绿强调 |
| 08 | 单色黑·印刷白底 | 黑图 + 白 | 单色印刷 |
| 09 | 单色白·印刷深底 | 白图 + #111 | 单色反白印刷 |
| 10 | 圆角 mark·深底 | 深底圆角方 + 白图 | 头像 / favicon |

> 配色：品牌蓝 `#1C59B8`、红 `#C2292E`、绿 `#4DB86B`、深底 `#12141C`、mark 底 `#111`。

---

## 四、在项目中的调用方式

**选型原则**：需要缩放/换色/嵌进设计稿 → 用 `02_svg`；需要位图/透明背景 → 用 `04_png_web`；需要头像/favicon → 用 `05_png_mark`；极小尺寸(≤32px) → 用 `logo_small_alpha.svg` 或对应小尺寸 mark。

**1) 网页（HTML）**
```html
<!-- 矢量，随 CSS 换色最灵活（fill 用 currentColor）-->
<img src="02_svg/logo_primary_black_alpha.svg" alt="AI Logo" width="64">
<!-- 或内联 SVG 后用 CSS： svg{ fill:#1C59B8; } -->
```

**2) Web favicon / App 图标**
```html
<link rel="icon"   href="05_png_mark/logo_mark_32.png">
<link rel="icon"   sizes="180x180" href="05_png_mark/logo_mark_180.png">
<link rel="apple-touch-icon" href="05_png_mark/logo_mark_180.png">
```

**3) Python（Pillow，如截图管线/贴图）**
```python
from PIL import Image
logo = Image.open("04_png_web/logo_primary_black_256.png")   # RGBA 透明
# 反形：换一张 02_svg 或 03/04 里对应的白色版即可
```

**4) 游戏引擎 / 需要透明底**
优先 `02_svg/logo_primary_black_alpha.svg`（矢量，任意 DPI）；引擎不支持 SVG 时取 `04_png_web` 里最大 512 或 `03_png_main` 1024，按需要缩放。

---

## 五、编辑与替换注意事项

**改几何（形状/羊角/鱼/散锋）** → 只改 `01_source/logo_final.py` 的控制点函数（`letter_a / fish / ram_horn / nib / i_stem / i_foot`）。
- 改完**必须**重跑 `python pack_logo.py` 让所有 SVG/PNG 同步更新；**不要**手改 `02_svg`/`03/04/05` 里的成品文件（改了会被下次重建覆盖）。
- 极小尺寸单独改 `logo_small.py`（它与主几何是两套，互不影响）。

**改颜色/加颜色类型** → 改 `01_source/brand_kit.py` 顶部的 `VARIANTS`（图形色/底色）与 `pack_logo.py` 里的 `MAIN10` 映射，重跑即可。

**版权戳** → `brand_kit.py` 顶部 `STAMP = True/False`。当前为 `False`（干净图标）；`True` 时每张图右下角 + SVG 文本层会带 `© 2026 Fisheep.L`。

**鱼眼镂空** → 鱼身与眼圆合并成一条 path 的两个子路径（`fill-rule="evenodd"`），**任意背景下鱼眼都自动透底**，反形不会变成实心黑点。改几何时别把鱼身和眼圆拆成两条独立 path，否则反形会破。

**SVG 合法性** → title 里若含 `& < > "`，`svg_document` 已做 XML 转义（本次打包已修复原 `logo_ai.svg` 因 `Fish & Ram` 裸 `&` 导致的 XML 非法问题）。新增标题时勿绕过 `svg_document` 直接拼字符串。

**替换整包** → 把 `Fisheep_AI_Logo` 整个文件夹拷进项目、或直接引用 `02_svg` / `04_png_web` / `05_png_mark` 三个子目录即可，其余为源/参考/备份。

**依赖环境**：Python 3.12 + `pycairo` + `Pillow` + `numpy`（见 `01_source/requirements.txt`）。

---

## 六、本次打包校验结果

- **源文件**：12 个 `.py` 全部 `py_compile` 通过。
- **SVG**：5 个矢量全部通过 XML 结构解析（path 数正常，`viewBox=0 0 1280 1280`）。
- **修复**：修正 `logo_final.py` / `logo_small.py` 的 title 未转义问题（根 `logo_ai.svg` 原为非法 XML，现已合法）。
- **PNG**：全部可正常打开，尺寸符合预期（主图 1024、web/mark 各尺寸、mark 透明四角 RGBA）。
- **几何**：鱼（眼镂空）、羊角（内涡）、散锋、衬线在正形/反形/单色/彩色/mark 下均一致成立。

> 一句话：**要缩放要换色就用 `02_svg`，要透明位图用 `04_png_web`，要做图标头像用 `05_png_mark`；改形状去 `01_source/logo_final.py` 改完重跑 `pack_logo.py`。**
