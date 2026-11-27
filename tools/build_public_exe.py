# -*- coding: utf-8 -*-
"""tools/build_public_exe.py — 脱敏公开 exe 打包 + exe 级脱敏门禁（独立 CLI，v1.0.764）。

背景：build.py 打的是**未脱敏**的开发版 exe（含雇主内部配置，不能发 public）。
本工具从脱敏镜像树（待发布版本/）PyInstaller 重打包出「脱敏 exe」，并用官方
CArchiveReader 逐 entry（含下钻 PYZ 冻结代码）解压做 exe 级脱敏门禁——fail-closed。

用法：
  python tools/build_public_exe.py                 # 从 待发布版本/ 打包脱敏 exe + 跑门禁
  python tools/build_public_exe.py --verify-only   # 只验现有 待发布版本/dist/Fisheep.exe（不重打）
  python tools/build_public_exe.py --mirror <路径>  # 指定镜像树

退出码：0=门禁通过（可发 public）；1=门禁未过 / 打包失败（不可发）。
也可由 `build.py --yes --public-exe` 在发布构建收尾自动调用（同一执行入口
buildlib.public_exe.run_public_release）。
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 直接 `python tools/...` 时 sys.path[0]=tools/，不含仓库根 → 显式补上（同 publish_github 契约）。
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from buildlib.public_exe import (  # noqa: E402
    MIRROR_DIR, EXE_NAME, run_public_release, verify_public_exe,
)


def main(argv=None):
    p = argparse.ArgumentParser(description="脱敏公开 exe 打包 + exe 级脱敏门禁")
    p.add_argument("--verify-only", action="store_true",
                   help="只验现有 exe（不重打包）——用于复检 / 已打包后复核")
    p.add_argument("--mirror", default=MIRROR_DIR,
                   help="镜像树路径（默认 待发布版本/）")
    args = p.parse_args(argv)

    if args.verify_only:
        exe = os.path.join(args.mirror, "dist", EXE_NAME)
        if not os.path.isfile(exe):
            print("❌ 未找到 %s（先打包再复检）" % exe)
            return 1
        problems, stats = verify_public_exe(exe)
        print("门禁：entries=%(entries)d 模块=%(code_modules)d 数据=%(data_files)d "
              "敏感命中=%(sensitive_hits)d 对照=%(controls_found)s" % stats)
        if problems:
            for x in problems[:30]:
                print("  ❌ " + x)
            if len(problems) > 30:
                print("  …另有 %d 项" % (len(problems) - 30))
            print("❌ 脱敏门禁未过（%d 项）——该 exe **不可发 public**" % len(problems))
            return 1
        print("✅ 脱敏门禁通过——该 exe 可发 public")
        return 0

    ok, lines = run_public_release(mirror_dir=args.mirror)
    for l in lines:
        print(l)
    if not ok:
        print("❌ 脱敏公开 exe 未就绪（打包失败或门禁未过）——不可发 public")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
