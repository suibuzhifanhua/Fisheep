# -*- coding: utf-8 -*-
"""services/__init__.py 不得含 import 语句（v1.0.663 B 护栏）。

背景：`services` 是功能域包入口，调用方一律按需 `from services import version_store`
这类子模块导入。若 `__init__.py` 自己 import 子模块，任何 `import services` 都会把
整域拉起来——启动变慢、循环 import、冻结包把用不到的依赖一并带进 exe。此前这条只在
review 里口头约束，现在落成会变红的锁。

口径（按行判，不解析 AST）：去掉行内注释与前导空白后，以 `import ` 开头，或以
`from ` 开头且含 ` import `，即视为违规。这样 `from . import x`、`from x import (`
多行导入的首行、`import os.path as p` 都会被抓住。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICES_INIT = os.path.join(ROOT, "services", "__init__.py")


def _import_violations(text):
    """返回 [(行号, 归一化后的行内容)]；空列表=干净。"""
    out = []
    for i, raw in enumerate(str(text).splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("import ") or (line.startswith("from ") and " import " in line):
            out.append((i, line))
    return out


def test_services_init_no_imports():
    path = _SERVICES_INIT
    assert os.path.isfile(path), f"找不到 {path}"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    bad = _import_violations(text)
    assert not bad, (
        "services/__init__.py 不得含 import 语句——按需导入请写在调用点"
        "（否则 import services 会拉起整域，冻结包里还会多带依赖）：%r" % (bad,)
    )
