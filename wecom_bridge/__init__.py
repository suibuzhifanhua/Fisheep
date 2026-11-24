"""wecom_bridge — 兼容 shim（v1.0.761：包已物理改名为 bridge/）。

方向1（TargetProfile）批次2：`wecom_bridge/` 改名为 `bridge/`（泛化的 CDP 桥接层，
不再以「企微」命名）。为不扰动全仓 40+ 处 `import wecom_bridge as wb` /
`from wecom_bridge.X import ...` 的既有导入路径（企微 100% 不退化是第一验收），
本文件是**薄兼容层**，把老名字原样指回 bridge：

  import wecom_bridge as wb            → 所有符号经 PEP 562 __getattr__ 委托到 bridge
  from wecom_bridge import X           → 同上（getattr 命中 shim.__getattr__ → bridge）
  from wecom_bridge.frames import Y    → 子模块已在 sys.modules 注册别名，指向 bridge.frames
  import wecom_bridge.cdp_client as cc → 同上
  mock.patch("wecom_bridge.wecom_proc.f")→ 解析到 bridge.wecom_proc（同一模块对象）
  wecom_bridge._log / ._AUTO_PICK      → 活引用原样保留（委托 bridge 的 __getattr__）

关键设计（勿改成 `from bridge import *` 星号导入）：
  * **不**把任何符号绑进本模块 globals——`_log`/`_AUTO_PICK` 是 cdp_client/windows 里的
    活引用（set_logger 注入后 wb._log 必须始终返回当前值；_AUTO_PICK 随点击频繁变化）。
    若星号导入会在 import 那一刻快照进 globals，活引用契约即被破坏。
    故全部名字走 __getattr__ 委托 bridge（bridge 的 __getattr__ 负责活引用）。
  * 子模块对象显式注册进 sys.modules["wecom_bridge.<sub>"] 并挂到本模块 globals，
    保证 `import wecom_bridge.<sub>` / `mock.patch("wecom_bridge.<sub>.x")` 解析到
    与 bridge.<sub> **同一个**模块对象（打补丁打到真身，内部 `from . import X` 的引用同步可见）。

本文件是行为锁的一部分：删除它 = 所有 wecom_bridge.* 导入立即红
（见 tests/contract/test_bridge_compat.py）。"""
import importlib as _importlib
import sys as _sys

import bridge as _bridge

# 顶层符号兜底（PyInstaller 静态分析专用）：services/study_engine.py 里有
# `from wecom_bridge import list_targets`（全仓唯一一处「顶层符号」而非「子模块」的
# wecom_bridge 导入）。PyInstaller 无法看穿 PEP 562 __getattr__ 动态委托，静态判不到
# list_targets 在本模块里 → 报「missing name」。这里显式从 bridge 导入同一函数对象，
# 让它对静态分析可见（运行期与 __getattr__ 命中是同一 object，不破坏 identity 契约）。
from bridge import list_targets  # noqa: F401  PyInstaller 静态解析需要，勿删

# 包内全部子模块：老 import 路径 wecom_bridge.<sub> 都要能解析到 bridge.<sub>。
_SUBS = (
    "cdp_client", "wecom_proc", "windows", "read", "act", "download",
    "app_targets", "frames", "highlight", "tucker", "targets",
    "os_windows", "screenshot", "js_loader", "win_send",
)
for _s in _SUBS:
    _m = _importlib.import_module("bridge." + _s)
    _sys.modules["wecom_bridge." + _s] = _m
    globals()[_s] = _m

# 与 bridge 完全同源的对外契约（from wecom_bridge import * / __all__ 契约测试锁住）。
__all__ = list(_bridge.__all__)


def __getattr__(name):
    # 委托 bridge：普通符号直接命中；_log / _AUTO_PICK 走 bridge 的 __getattr__ 活引用。
    return getattr(_bridge, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_bridge)))
