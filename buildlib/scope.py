# -*- coding: utf-8 -*-
"""buildlib.scope — 源码扫描范围 / pytest 收集范围的**单一事实源**。

背景（评审 P1-3）：v1.0.751 与 v1.0.754 两次因为「新增含源码/测试的目录时漏同步
某一处排除名单」出事——前者漏写 dist 导致 pytest 收进第三方测试目录（10 error），
后者要把镜像目录名同步进 5 个模块 + pytest.ini + .gitignore + 文档。
AGENTS.md 第六节为此要求「根下新增含源码/测试的目录时，必须同步 3 处 + 8 处
静态扫描排除名单」——这份手工同步表本身就是维护税。

口径（**不要把所有集合合并成一个**）：各扫描器排除语义不同，合并会让棘轮计数漂移：
  - `gates.check_silent_pass` 要**扫** tests/（测试里的裸 except 计入基线 271）；
  - `gates._large_file_rows` 只数 .py/.md，另需跳构建备份目录；
  - `spec_refresh` 要跳 tests/tools/docs（它们不进 exe）；
  - `scan_silent_except` 只看数据安全路径，跳 tests/docs；
  - `test_wecom_windows` 数 ctypes 原型写，跳 tests/docs/js。
所以这里定义：① 所有扫描器共有的 CORE（各集合的**交集**）；② 各扫描器自己的
EXTRA（精确补齐原集合的差集）；③ pytest 的 NORECURSEDIRS。
各处一律 `CORE | EXTRA[profile]` 组装，且组装结果与原手写集合**逐字相等**——
见 `tests/contract/test_scope_consistency.py`，它同时钉住「改一处、处处生效」。

新增一个需要被所有扫描器排除的目录时，只需往 CORE_SKIP_DIRS 加一项；
只被某一扫描器排除的目录，往对应 EXTRA 加。
"""

# 公共镜像目录名。仓库根外（主目录级），但多数扫描器仍保留条目作保底：
# 历史上镜像曾在仓库根内（github_public/），那次正是计数翻倍的来源。
MIRROR_DIRNAME = "待发布版本"

# 所有扫描器共有的最小排除集 = 原 8 个集合的交集。
# 刻意**不含** 待发布版本 / .pytest_cache / 产物库 / tests / docs：是否排除由各处
# EXTRA 决定，这样组装结果才能与原实现逐字一致（扫描行为零变化）。
CORE_SKIP_DIRS = frozenset({"build", "dist", "__pycache__"})

# pytest.ini 的 norecursedirs。显式配置会**覆盖 pytest 默认列表**，
# 所以必须把默认项一并写全（v1.0.751 漏写 dist 的教训）。
# 顺序与 ini 文本一致，便于 tests/contract/test_scope_consistency.py 逐字比对。
# v1.0.768：产物库/ 补入——e2e 实跑前置把 OCR 旁挂运行时（numpy/onnxruntime 等
# 第三方包，225MB）复制进源树 产物库/ocr/ 后，其 *_test.py 被 pytest 收集
# （2 个 collection error：缺 hypothesis/psutil）。扫描器侧产物库本就走各自
# EXTRA 排除（silent_pass 等），此处只扩 pytest 收集口径，扫描行为零变化。
NORECURSEDIRS = (
    "build", MIRROR_DIRNAME, "dist", "产物库", "*.egg", ".*", "_darcs", "CVS",
    "node_modules", "venv", "{arch}",
)

# 各扫描器在 CORE 之上的额外排除项（精确复刻原手写集合的差集）。
SKIP_EXTRA = {
    # 裸 except-pass 棘轮：docs 不扫（文档不是代码），tests **要扫**。
    "silent_pass": frozenset({MIRROR_DIRNAME, ".pytest_cache", "产物库", "docs"}),
    # 文件规模软告警：构建备份 / 运行时段 / git 元数据。
    "hygiene": frozenset({MIRROR_DIRNAME, ".pytest_cache", "产物库", ".git", "组件",
                          "_dist_backup", "_rollback", "_chat_archive"}),
    # hiddenimports 生成：只扫会进 exe 的包，其余全跳。
    "spec_refresh": frozenset({MIRROR_DIRNAME, ".pytest_cache", "产物库", ".git",
                               "node_modules", "docs", "tools", "tests", "js", "css"}),
    # 数据安全静默声明：只扫生产码路径（本扫描器 ROOT 就是仓库根，
    # 镜像在根外走不到，故原集合没有镜像名，这里也不补）。
    "silent_except": frozenset({"node_modules", ".git", "docs", "产物库", "tests"}),
    # ctypes 原型写计数：只扫桌面/窗口相关生产码。
    "prototype_writes": frozenset({MIRROR_DIRNAME, "tests", "docs", ".git",
                                   "产物库", "js", "archive", "node_modules"}),
    # tests/unit/test_misc_fixes.py 两处源码遍历（默认段 / 名单字面量扫描）。
    "source_walk": frozenset({MIRROR_DIRNAME, "tests", "docs", ".git",
                              "产物库", "js", "archive"}),
    # tests/unit/test_perf_debt.py 的"第二份名单"扫描（集合最小）。
    "keep_list": frozenset({MIRROR_DIRNAME, "tests", "docs", ".git"}),
}


def skip_dirs(profile):
    """按 profile 返回组装好的排除集合（frozenset）。

    未知 profile 直接抛错——不静默回退到 CORE，避免有人拼错名字却拿到一个
    看起来能跑的集合，把漏排除藏到打包期。
    """
    if profile not in SKIP_EXTRA:
        raise KeyError("未知排除名单 profile: %r（可选 %s）"
                       % (profile, ", ".join(sorted(SKIP_EXTRA))))
    return CORE_SKIP_DIRS | SKIP_EXTRA[profile]
