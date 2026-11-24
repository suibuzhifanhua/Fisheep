# -*- coding: utf-8 -*-
"""wecom_bridge.js_loader — 兼容转发（v1.0.761：包由 wecom_bridge 改名 bridge）。

这是**真实模块文件**，让 PyInstaller 静态分析能解析 `import wecom_bridge.js_loader` /
`from wecom_bridge.js_loader import X`——改名后 wecom_bridge/ 只剩 shim `__init__.py`、没有实体
子模块文件，静态分析会把 `wecom_bridge.js_loader` 判成「本项目包内模块缺失」中止打包；本文件
让该模块可被解析，且把公开子模块 bridge.js_loader 一并纳入 bundle。
执行时把本模块名指向 bridge.js_loader **同一对象**（sys.modules 别名），模块 identity 不变，
`mock.patch("wecom_bridge.js_loader.x")` 打到真身。

刻意**不用** `from bridge.js_loader import *`：星号导入触发 pyflakes F403（"unable to detect
undefined names"），而打包门禁把含 "undefined name" 的行一律当 F821 未定义名硬拦截，
星号导入会让构建必红。运行期 wecom_bridge/__init__.py 的循环已预登记
sys.modules["wecom_bridge.js_loader"]，本文件多为 PyInstaller 兜底，被运行时也是等价幂等。
"""
import sys as _sys

from bridge import js_loader as _real

_sys.modules[__name__] = _real
