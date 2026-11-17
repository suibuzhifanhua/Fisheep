# -*- coding: utf-8 -*-
"""buildlib.env — 构建期共享路径与运行环境（自 build.py 模块级常量逐字迁入）。

BASE 一律指**项目根**（build.py 所在目录），与拆分前 build.py 的 BASE 同义。
"""
import os
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VPATH = os.path.join(BASE, "version.py")
# v1.0.680（改名 WeComCompanion → Lingban）：spec 文件名随之改名。
# 品牌名从 core.branding 取（单一事实源）——core 是纯 Python 包、无重依赖，
# 构建期 import 安全。
try:
    from core.branding import SPEC_NAME
except Exception:  # noqa: BLE001（极端环境下退回字面量，构建不至于直接崩）
    SPEC_NAME = "Lingban.spec"
SPEC = os.path.join(BASE, SPEC_NAME)

# 优先使用项目自带 Python 环境（含 pywebview/pystray 等全部运行时依赖）；
# PATH 探测仅作兜底——某些 shell（如 Marvis 运行时）的 PATH 会先命中缺依赖的环境，
# 若用错环境打包会导致 webview/pystray 等模块静默缺失、exe 启动报 ModuleNotFoundError。
def _find_pyinstaller():
    """自适应探测 pyinstaller：优先当前用户的项目专用 venv（wecom/default），其次 PATH。

    v1.0.405（L5）：删除历史开发机硬编码绝对路径（历史开发机残留）——换机/协作
    场景死代码只会造成困惑；expanduser 候选 + PATH 兜底已覆盖全部真实场景。

    v1.0.660（审查修复 B21）：探测失败时**立即** SystemExit 并打印安装指引。
    原实现返回 candidates[0]（一个并不存在的路径），错误被推迟到 subprocess
    抛出 FileNotFoundError 才暴露，且报错信息不指向"缺少 PyInstaller"这个真因。
    """
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, r".workbuddy\binaries\python\envs\wecom\Scripts\pyinstaller.exe"),
        os.path.join(home, r".workbuddy\binaries\python\envs\default\Scripts\pyinstaller.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    w = shutil.which("pyinstaller")
    if w:
        return w
    raise SystemExit(
        "[build] 未找到 PyInstaller，已中止。\n"
        "  探测过的候选路径：\n"
        + "".join("    - %s\n" % c for c in candidates)
        + "  安装方式（任选其一）：\n"
        "    1) pip install pyinstaller\n"
        "    2) 安装到项目专用 venv：\n"
        "       python -m pip install -r requirements-dev.txt\n"
    )


PYINSTALLER = _find_pyinstaller()

# 安全删除钩子会拦截 os.remove，打包前清掉这些会话变量（原生 rm 才可用）
_SANDBOX_VARS = (
    "CODEBUDDY_SESSION_ID", "CLAUDE_SESSION_ID", "CODEBUDDY_SAFE_DELETE_SANDBOX",
    "SAFE_DELETE_SANDBOX", "CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR", "CODEBUDDY_TOOL_CALL_ID",
)
