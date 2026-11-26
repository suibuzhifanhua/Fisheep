# -*- coding: utf-8 -*-
"""buildlib.gate_scope — 打包门禁分层（v1.0.677，用户："每次优化修改打包和测试做回归的效率都不高"）。

背景：门禁恒跑全量 1317 条（~4min），但多数迭代只动图标/文案/样式——每次全额
付全量回归。本模块按 git diff 判改动面：

  fast 层：改动**全部**落在展示资产（app.ico/tray.ico/index.html/css/README/
           version 元信息）且不含任何 .py/.js → 跑 FAST_FILES 的 301 条护栏
           （前端渲染/结构/构建管线/资源一致性逻辑，实测 ~40s）。
  full 层：其余一切（含 Python/JS 逻辑改动、git 不可用、diff 取不到）→ 裸全量。

口径原则（fail-closed）：
  1. 判定失败（非 git 仓/git 报错/解析异常）一律 full——宁可多测，不可漏测。
  2. fast 是「白名单命中」而非黑名单：不在白名单扩展名里的文件出现 → full。
  3. fast 层测试集是硬编码文件清单（见 FAST_FILES），漏列一个都不由判定兜底——
     护栏测试 test_gate_scope.py 锁死清单与 build.py 的调用语义。

xdist 并行（_run_pytest_gate 侧）：全量跑 `-n 8 --dist loadfile`（同文件同 worker，
防同模块 fixture 串扰）；xdist 未安装时探测失败自动回退串行，不断包。
"""
import os
import subprocess

# 全量口径（与 v1.0.663 起 README「标准验证栈」一致：裸 pytest tests/）
FULL_TARGET = "tests"

# fast 层：展示资产改动时，这组护栏足以证明「前端/构建管线没被弄坏」。
# 301 条，实测 ~40s（2026-11-14 开发机）。
FAST_FILES = (
    "tests/contract/test_frontend_render.py",       # 134：前端渲染/主题/调色盘/结构锁
    "tests/contract/test_frontend_structure.py",   # 15：index.html 结构
    "tests/contract/test_build_guard.py",          # 102：构建管线护栏（spec/journal/版本/资源）
    "tests/contract/test_buildlib_units.py",       # 28：buildlib 单元
    "tests/contract/test_buildlib_more.py",        # 11：打包资源一致性逻辑
    "tests/contract/test_v667_audit_fixes.py",     # 5：审计修复锁
    "tests/contract/test_v668_theme_expand.py",    # 2：主题引擎锁
    "tests/contract/test_v669_theme_full_coverage.py",  # 4：调色盘全覆盖+设置导航锁
)

# fast 白名单：改动文件全部落在这些模式内才允许 fast。
# 注意：js/*.js 不在内——前端 JS 逻辑改动不被静态锁覆盖（运行时行为），必须全量。
FAST_EXACT = ("version.py",)   # 版本号元信息（build 自己也会动它，diff 里出现很正常）
# .css 只认 css/ 目录；.ico/.html/.md/.txt 任意目录。
# 其余一切（.py/.js/.spec/.pyi/未知扩展名）→ full：逻辑改动或拿不准，必须全量。


def _git_files(base):
    """git 改动面：已跟踪的修改/新增 + 未跟踪文件。返回 (files, err)。"""
    def run(*args):
        r = subprocess.run(["git", *args], cwd=base, capture_output=True, text=True,
                           timeout=30)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "").strip()[:200])
        return r.stdout
    files = set()
    out = run("diff", "--name-only", "HEAD")           # 已暂存+未暂存的已跟踪改动
    files.update(l for l in out.splitlines() if l.strip())
    out = run("status", "--porcelain")                 # 含未跟踪（?? ）
    for l in out.splitlines():
        if not l.strip():
            continue
        p = l[3:].strip().strip('"')
        if " -> " in p:                                 # rename：取新名
            p = p.split(" -> ")[-1]
        if l.startswith("??"):
            files.add(p)
        else:
            files.add(p)
    return files, None


def classify(base, changed=None):
    """判定门禁层。返回 (scope, files, why)。

    scope: "fast" | "full"。changed 可注入（测试用）；None 时自取 git diff。
    """
    if changed is None:
        try:
            changed, err = _git_files(base)
        except Exception as e:  # noqa: BLE001
            return "full", [], f"git 改动面取不到（{e}）——fail-closed 走全量"
        if err:
            return "full", [], f"git 报错（{err}）——fail-closed 走全量"
    if not changed:
        return "full", [], "无改动可判定（工作区与 HEAD 一致或为空）——走全量"
    for f in sorted(changed):
        low = f.lower()
        if low in FAST_EXACT:
            continue
        # .css 只认 css/ 目录下的；.ico/.html/.md/.txt 任意目录
        if low.endswith(".css"):
            if not low.startswith("css/"):
                return "full", sorted(changed), f"{f} 的 .css 不在 css/ 目录"
            continue
        if not low.endswith((".ico", ".html", ".md", ".txt")):
            return "full", sorted(changed), f"{f} 不在 fast 白名单（逻辑/代码或未知资产）"
    return "fast", sorted(changed), "改动全落展示资产白名单"
