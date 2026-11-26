# -*- coding: utf-8 -*-
"""buildlib.versioning — 版本号读取/递增/写入与 exe 版本资源生成（自 build.py 逐字迁入）。"""
import datetime
import os
import re

from buildlib.env import BASE, VPATH

# v1.0.660（审查修复 B20）：版本号写入的唯一实现。
# 此前 buildlib/orchestration.py 的回滚分支另写了一份正则重写，与这里的 bump_version
# 构成"双份实现"——两份正则一旦漂移，回滚就会写出 version.py 里没有的形态。
# 统一到 set_version()，bump_version() 与回滚都复用它。
_VERSION_RE = r'__version__\s*=\s*["\']\d+\.\d+\.\d+["\']'
_VERSION_PATTERN = re.compile(_VERSION_RE)


def current_version():
    with open(VPATH, "r", encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r'__version__\s*=\s*["\'](\d+)\.(\d+)\.(\d+)["\']', txt)
    return ".".join(m.groups()) if m else "0.0.0"


def next_version(v):
    a, b, c = map(int, v.split("."))
    c += 1
    return f"{a}.{b}.{c}"


def set_version(v):
    """把 version.py 里的 __version__ 写成 v，返回 (旧版本, 新版本)。

    v1.0.660（审查修复 B20）：版本号写入的唯一落点。bump 与回滚共用本函数，
    避免同一份正则散落在两个模块里各自演化。
    """
    with open(VPATH, "r", encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r'__version__\s*=\s*["\'](\d+)\.(\d+)\.(\d+)["\']', txt)
    old = ".".join(m.groups()) if m else "0.0.0"
    if not re.match(r"^\d+\.\d+\.\d+$", str(v)):
        raise ValueError(f"非法版本号：{v!r}（期望 X.Y.Z）")
    txt = _VERSION_PATTERN.sub(f'__version__ = "{v}"', txt)
    with open(VPATH, "w", encoding="utf-8") as f:
        f.write(txt)
    return old, v


def bump_version(explicit=None):
    """P3-4：显式传参指定版本则不自动 +1；未指定时 patch +1。返回 (旧版本, 新版本)。"""
    cur = current_version()
    new = explicit or next_version(cur)
    set_version(new)
    return cur, new


def _filetime_now():
    """把当前 UTC 时间编码成 Windows FILETIME 的 (dwFileDateMS, dwFileDateLS)。

    v1.0.660（审查修复 B16）：原先固定写 date=(0, 0)，资源管理器里 exe 的
    "创建时间"永远显示 0/0，版本元数据不完整。这里写入真实构建时间。
    """
    epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    ticks = int((now - epoch).total_seconds() * 10 ** 7)
    return (ticks >> 32) & 0xFFFFFFFF, ticks & 0xFFFFFFFF


def make_version_info(v):
    """生成 exe 的版本资源文件 version_info.txt，返回其路径。

    v1.0.660（审查修复 B16）：补齐 CompanyName / LegalCopyright，
    并把 date=(0, 0) 换成真实构建时间（FILETIME）。
    """
    a, b, c, d = (list(map(int, v.split("."))) + [0, 0, 0, 0])[:4]
    path = os.path.join(BASE, "version_info.txt")
    date_ms, date_ls = _filetime_now()
    # v1.0.680（改名 WeComCompanion → Lingban）/v1.0.691（改名 Fisheep）：
    # 版本资源里的 InternalName/OriginalFilename/ProductName 是 exe 的内部标识——
    # 资源管理器"属性 → 详细信息"里显示的就是它，一律取品牌常量（单一事实源）。
    from core.branding import APP_ID, APP_TITLE, EXE_NAME
    content = f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, {d}),
    prodvers=({a}, {b}, {c}, {d}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=({date_ms}, {date_ls})
  ),
  kids=[
    StringFileInfo(
      [StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'{APP_TITLE}'),
         StringStruct(u'FileDescription', u'{APP_TITLE}'),
         StringStruct(u'FileVersion', u'{a}.{b}.{c}.{d}'),
         StringStruct(u'InternalName', u'{APP_ID}'),
         StringStruct(u'LegalCopyright', u'Copyright (C) 2026 {APP_TITLE}'),
         StringStruct(u'OriginalFilename', u'{EXE_NAME}'),
         StringStruct(u'ProductName', u'{APP_TITLE}'),
         StringStruct(u'ProductVersion', u'{a}.{b}.{c}.{d}')])]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
'''
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
