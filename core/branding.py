# -*- coding: utf-8 -*-
"""core.branding — 产品品牌标识与改名兼容（v1.0.680）

v1.0.671 起**显示名**已全面改为「灵伴」（窗口标题/托盘/对话框/Toast/
版本资源 ProductName），但 exe/spec/内部标识一直沿用旧名 WeComCompanion。
v1.0.680（用户："文件名还是叫wecomcompanion 改成lingban"）把**技术标识**
也统一到 Lingban。

改名不是单纯替换字符串——有几处是**功能性依赖**，替换即断：

  ① 单实例 mutex：新旧 exe 若用不同 mutex 名，会同时跑起来抢同一个 CDP
     端口（用户看到两个实例、企微被两拨 agent 抢窗口）。
  ② 开机自启：注册表 Run 的**值名**与计划任务的**任务名**都指向 exe 路径，
     改名后老用户的自启会指向已不存在的旧路径（注册表）或旧文件（计划任务）。
  ③ %LOCALAPPDATA% 下的 webview_data：WebView2 的 localStorage（侧栏折叠、
     主题等界面偏好）在那儿，换目录等于清空界面状态。
  ④ build/_rollback/：老版本记录里的 exe 路径必须仍能读、仍能回滚。

因此本模块**集中**品牌常量，并显式给出兼容期的**旧名**常量——
凡是"新写入用新名、读旧数据要认旧名"的地方，都从这里取，
不许在各处各写一份字面量（同一语义两份实现，本项目已因此吃过多次亏）。

命名约定：新名常量不带前缀；旧名常量一律带 LEGACY_ 前缀。
"""
# -*- coding: utf-8 -*-

# ---------- 当前品牌（写入侧一律用这些） ----------
# v1.0.691（用户："软件重新命名Fisheep"）：技术标识与显示名统一 Fisheep。
# 名字链（最老→最新）：WeComCompanion → Lingban → Fisheep。
# 旧名识别一律走下方 LEGACY_*/LEGACY2_*/ALL_* 常量，禁止散落字面量。
APP_ID = "Fisheep"                  # 技术标识：exe/spec/目录名的基名
APP_TITLE = "Fisheep"                # 显示名：窗口标题/托盘/对话框/通知来源
EXE_NAME = APP_ID + ".exe"
SPEC_NAME = APP_ID + ".spec"
ONEDIR_NAME = APP_ID + "-onedir"

# 单实例 mutex 名（Windows 内核对象名，全局可见）
MUTEX_NAME = APP_ID + "_SingleInstance"

# Windows 通知 AUMID（HKCU\Software\Classes\AppUserModelId\{...}）
AUMID = "Fisheep." + APP_ID

# 开机自启：注册表 HKCU\...\Run 的**值名**（不是值内容——值内容是 exe 路径）
AUTOSTART_VALUE = APP_ID
# 开机自启（管理员模式）：计划任务名
ADMIN_TASK = APP_ID + "Admin"
# 管理员重启自举任务名（一次性占位任务，仅 /run 手动触发）
ADMIN_BOOTSTRAP_TASK = APP_ID + "AdminBootstrap"

# %LOCALAPPDATA% 下的本软件数据目录名（config 回退副本 + webview_data）
DATA_DIR = APP_ID

# HTTP User-Agent（对外标识，无兼容负担）
USER_AGENT = APP_ID + "/1.0"

# ---------- 旧名（兼容期：只用于"认旧数据 / 清旧痕迹"，不用于新写入） ----------
# 第 1 代旧名（v1.0.680 之前的 WeComCompanion 系，原样保留）
LEGACY_APP_ID = "WeComCompanion"
LEGACY_EXE_NAME = LEGACY_APP_ID + ".exe"
LEGACY_MUTEX_NAME = LEGACY_APP_ID + "_SingleInstance"
LEGACY_AUMID = "Fisheep." + LEGACY_APP_ID
LEGACY_AUTOSTART_VALUE = LEGACY_APP_ID
LEGACY_ADMIN_TASK = LEGACY_APP_ID + "Admin"
LEGACY_ADMIN_BOOTSTRAP_TASK = LEGACY_APP_ID + "AdminBootstrap"
LEGACY_DATA_DIR = LEGACY_APP_ID

# 旧 exe 的进程名（tasklist/taskkill 用；兼容期老版可能仍在跑）
LEGACY_EXE_STEM = LEGACY_APP_ID

# v1.0.691 改名：第 2 代旧名（上一代 Lingban 系）——老用户机器上 exe/mutex/
# 自启动值名/计划任务/数据目录都还挂这套名字，识别点必须同第 1 代一样认。
LEGACY2_APP_ID = "Lingban"
LEGACY2_EXE_NAME = LEGACY2_APP_ID + ".exe"
LEGACY2_MUTEX_NAME = LEGACY2_APP_ID + "_SingleInstance"
LEGACY2_AUMID = "Fisheep." + LEGACY2_APP_ID
LEGACY2_AUTOSTART_VALUE = LEGACY2_APP_ID
LEGACY2_ADMIN_TASK = LEGACY2_APP_ID + "Admin"
LEGACY2_ADMIN_BOOTSTRAP_TASK = LEGACY2_APP_ID + "AdminBootstrap"
LEGACY2_DATA_DIR = LEGACY2_APP_ID
LEGACY2_EXE_STEM = LEGACY2_APP_ID

# ---------- 全名链（最老→最新）：需要"认所有旧名"的枚举点直接用这些 ----------
# 用法约定：枚举点遍历 ALL_*；二元点（新名+单个旧名）若语义上必须覆盖全部
# 历史，同样换成 ALL_*（v1.0.691 逐点核对过，见各使用点注释）。
ALL_APP_IDS = (LEGACY_APP_ID, LEGACY2_APP_ID, APP_ID)
ALL_EXE_NAMES = tuple(n + ".exe" for n in ALL_APP_IDS)
ALL_EXE_STEMS = ALL_APP_IDS
ALL_MUTEX_NAMES = tuple(n + "_SingleInstance" for n in ALL_APP_IDS)
ALL_AUMIDS = tuple("Fisheep." + n for n in ALL_APP_IDS)
ALL_AUTOSTART_VALUES = ALL_APP_IDS
ALL_ADMIN_TASKS = tuple(n + "Admin" for n in ALL_APP_IDS)
ALL_ADMIN_BOOTSTRAP_TASKS = tuple(n + "AdminBootstrap" for n in ALL_APP_IDS)
ALL_DATA_DIRS = ALL_APP_IDS
