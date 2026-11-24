# -*- coding: utf-8 -*-
"""tests/contract/test_bridge_compat.py — wecom_bridge→bridge 改名的**兼容 shim 行为锁**（v1.0.761）。

方向1（TargetProfile）批次2 把 `wecom_bridge/` 物理改名为 `bridge/`（泛化 CDP 桥接层，不再
以「企微」命名），`wecom_bridge/` 降级为薄兼容 shim（PEP 562 `__getattr__` 全委托 bridge +
子模块 sys.modules 别名）。为让全仓 40+ 处既有 `import wecom_bridge` / `from wecom_bridge.X
import ...` 一字不改继续工作、且企微 100% 不退化，本文件把「兼容契约」锁死——
**删掉 shim 或改坏它，这里必红**：

  * __all__ 与 bridge 完全同源（对外契约不漂移）
  * 每个对外符号经 shim 取到的对象 **is** bridge 上的同一对象（不是拷贝）
  * 每个 bridge 子模块经 wecom_bridge.<sub> 取到 **is** bridge.<sub>（同一模块对象，
    故 mock.patch("wecom_bridge.<sub>.x") 打到真身）
  * _log / _AUTO_PICK 活引用未被快照进 shim globals（set_logger 注入后 wb._log 仍是当前值）
  * bridge/ 目录里**每个** .py 子模块都必须可经 wecom_bridge.<sub> 解析（加新子模块漏改 shim 即红）
  * shim 源码走 __getattr__ 委托、禁用 `from bridge import *`（星号导入会快照活引用）
  * wecom_bridge/<sub>.py 是**真实薄转发文件**（PyInstaller 静态解析兜底）——运行期由 shim
    循环预登记 sys.modules 而从不执行，故本测试**强制执行**每个文件体，锁死「_real 绑到
    bridge.<sub> 同一对象 + 自身 sys.modules 别名」；顺带喂饱增量覆盖率门槛（否则这 15 个
    从不执行的打包兜底文件会把新增行覆盖率拖到 70% 以下）
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import bridge  # noqa: E402
import wecom_bridge  # noqa: E402

# 与 wecom_bridge/__init__.py 的 _SUBS 同源——两者必须一致。
# test_every_bridge_submodule_is_shimmed 用文件系统兜底，防止只改了一处。
_SUBS = (
    "cdp_client", "wecom_proc", "windows", "read", "act", "download",
    "app_targets", "frames", "highlight", "tucker", "targets",
    "os_windows", "screenshot", "js_loader", "win_send",
)
# 活引用（PEP 562 动态解析）：绝不能被快照进 shim globals。
_LIVE_REFS = ("_log", "_AUTO_PICK")


def test_shim_all_identical_to_bridge():
    """__all__ 契约同源——shim 少导出/多导出一个名字必红。"""
    assert wecom_bridge.__all__ == bridge.__all__


def test_every_public_symbol_is_same_object():
    """每个对外符号经 shim 取到的是 bridge 上**同一对象**（is，非拷贝）。
    改回「shim 里存一份快照」或删掉 shim → 这里必红。活引用单独锁（见下）。"""
    for name in bridge.__all__:
        if name in _LIVE_REFS:
            continue
        assert getattr(wecom_bridge, name) is getattr(bridge, name), \
            "wecom_bridge.%s 不是 bridge.%s 同一对象（兼容 shim 断链）" % (name, name)


def test_submodule_identity_and_sysmodules_alias():
    """wecom_bridge.<sub> 必须 **is** bridge.<sub>，且 sys.modules 别名指向同一模块对象。
    这样 mock.patch("wecom_bridge.<sub>.x") 打到真身，包内 `from . import X` 的引用同步可见。"""
    for sub in _SUBS:
        assert getattr(wecom_bridge, sub) is getattr(bridge, sub), \
            "wecom_bridge.%s 不是 bridge.%s 同一模块对象" % (sub, sub)
        assert sys.modules["wecom_bridge." + sub] is sys.modules["bridge." + sub], \
            "sys.modules 别名断裂：%s" % sub


def test_every_bridge_submodule_is_shimmed():
    """bridge/ 目录里**每个** .py 子模块都必须经 wecom_bridge.<sub> 解析且是同一对象。
    在 bridge/ 加新子模块却忘了进 shim 的 _SUBS → `__import__` 直接失败/对象不同 → 必红。
    （文件系统兜底，不依赖上面手抄的 _SUBS。）"""
    pkg_dir = os.path.dirname(os.path.abspath(bridge.__file__))
    real_subs = sorted(
        os.path.splitext(fn)[0]
        for fn in os.listdir(pkg_dir)
        if fn.endswith(".py") and fn != "__init__.py"
    )
    for sub in real_subs:
        mod = __import__("wecom_bridge." + sub, fromlist=["__name__"])
        assert mod is getattr(bridge, sub), \
            "新子模块 bridge.%s 未在 wecom_bridge shim 里别名到同一对象" % sub


def test_live_refs_not_snapshot_in_shim():
    """_log / _AUTO_PICK 是活引用：绝不能被快照进 shim 的 globals（否则 set_logger 注入后
    wb._log 返回 import 时的旧值，功能静默失效——这正是原单模块 __getattr__ 存在的理由）。
    改回「from bridge import *」星号导入 → 名字落进 globals → 这里必红。"""
    shim_globals = vars(wecom_bridge)
    for live in _LIVE_REFS:
        assert live not in shim_globals, "%s 被快照进 shim globals（活引用被破坏）" % live
    # 经 shim 取到的 _log 就是 cdp_client 里的当前值（活引用）
    assert wecom_bridge._log is bridge.cdp_client._log
    # _AUTO_PICK 经 shim 与直接经 bridge 取到一致（当前值；测试态通常 None）
    assert wecom_bridge._AUTO_PICK == bridge._AUTO_PICK


def test_shim_source_is_thin_delegate():
    """shim 源码必须走 PEP 562 __getattr__ 委托 bridge，**禁止**真实的
    `from bridge import *` 语句（星号导入会把活引用符号快照进 globals）。
    用 AST 判定真实语句，而非子串匹配（shim docstring 里提到该写法不算）。"""
    shim_src = io.open(os.path.join(ROOT, "wecom_bridge", "__init__.py"),
                       encoding="utf-8").read()
    tree = ast.parse(shim_src)
    has_getattr = any(
        isinstance(n, ast.FunctionDef) and n.name == "__getattr__" for n in tree.body
    )
    assert has_getattr, "shim 丢了 PEP 562 __getattr__ 委托"
    for n in ast.walk(tree):
        if (isinstance(n, ast.ImportFrom) and n.module == "bridge"
                and any(a.name == "*" for a in n.names)):
            raise AssertionError("shim 用 `from bridge import *` 会快照活引用")


def test_thin_fallback_files_alias_to_bridge_submodule():
    """wecom_bridge/<sub>.py 是**真实薄转发文件**，专供 PyInstaller 静态解析兜底。

    改名后 wecom_bridge/ 只剩 shim __init__、没有实体子模块，PyInstaller 的静态分析会把
    `import wecom_bridge.<sub>` 判成「本项目包内模块缺失」而中止打包——所以为每个 <sub> 放
    一份真实文件：`from bridge import <sub> as _real; sys.modules[__name__] = _real`。
    运行期这些文件**从不执行**（shim __init__ 的循环已把 sys.modules["wecom_bridge.<sub>"]
    预登记成 bridge.<sub> 同一对象，import 直接命中别名、不触发文件体）。

    正因运行期永不执行，本测试**主动用沙盒模块名强制执行每个文件体**，锁死转发契约：
      · 文件体必须 `from bridge import <sub> as _real`（_real 是 bridge.<sub> 同一对象）；
      · 执行后必须把自身 sys.modules[__name__] 别名到 bridge.<sub>。
    改坏任一薄文件（指错子模块 / 删掉别名行 / 退回星号导入）→ 这里必红，PyInstaller 兜底随之失效。
    （副产物：这 15 个文件的 3 行因此被测到，喂饱 buildlib.coverage_gap 的增量覆盖率门槛。）
    """
    import importlib.util

    shim_dir = os.path.dirname(os.path.abspath(wecom_bridge.__file__))
    for sub in _SUBS:
        path = os.path.join(shim_dir, sub + ".py")
        assert os.path.isfile(path), \
            "缺薄转发文件 wecom_bridge/%s.py（PyInstaller 静态解析兜底）" % sub
        mod_name = "_bridge_compat_thin_%s" % sub
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # 强制执行文件体（3 行 → 增量覆盖）
            assert getattr(mod, "_real") is getattr(bridge, sub), \
                "wecom_bridge/%s.py 的 _real 未绑到 bridge.%s 同一对象" % (sub, sub)
        finally:
            # 文件体执行会把 sys.modules[mod_name] 设成 bridge.<sub>；清掉沙盒键，不污染真实包
            sys.modules.pop(mod_name, None)
