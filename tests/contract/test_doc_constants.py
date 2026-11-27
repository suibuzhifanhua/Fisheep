# -*- coding: utf-8 -*-
"""tests/contract/test_doc_constants.py — 文档常量与代码实现的**一致性锁**（v1.0.755，评审 P0-1）。

评审实测 README 有 8 处技术参数与代码不符，其中两处会直接误导运维：
  · 压缩阈值写 80000，代码是 40000（v1.0.287 就改了）；
  · CDP 启动参数写 `--remote-allow-origins=*`，代码自 v1.0.646 起是 Origin 白名单。
本文件把这些可数数字从「人肉维护」改成「从代码读取后断言文档」：
改了代码里的常量 / 工具数 / 技能数 / 前端文件数，文档没跟上 → 本文件红。
"""
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from buildlib import scope  # noqa: E402


def _read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def _literal(rel, name):
    """从模块 AST 里取模块级赋值字面量，不 import（避免触发副作用）。"""
    tree = ast.parse(_read(rel))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("%s 里找不到模块级 %s" % (rel, name))


def _list_len(rel, name):
    tree = ast.parse(_read(rel))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return len(node.value.elts)
    raise AssertionError("%s 里找不到 list %s" % (rel, name))


class TestReadmeConstants:
    def _readme(self):
        return _read("README.md")

    def test_context_threshold_matches_code(self):
        val = _literal("agent/session.py", "CONTEXT_COMPRESS_THRESHOLD")
        doc = self._readme()
        assert ("CONTEXT_COMPRESS_THRESHOLD=%d" % val) in doc
        # 旧值只准出现在"历史沿革"说明里，不准作为当前口径出现
        assert "CONTEXT_COMPRESS_THRESHOLD=80000" not in doc

    def test_tool_and_skill_counts_match_code(self):
        tools = _list_len("agent_tools/registry.py", "TOOLS")
        skills = _list_len("agent_tools/skills.py", "SKILLS")
        doc = self._readme()
        assert ("%d 个内置技能" % skills) in doc, "README 技能数与实际不符"
        assert ("内置技能（%d 个" % skills) in doc
        # 注释里不再写死条数（会过期）
        assert "TOOLS 42 项" not in _read("agent_tools/__init__.py")
        assert "SKILLS，19 项" not in _read("agent_tools/skills.py")
        assert tools > 0

    def test_js_file_count_matches_code(self):
        # v1.0.756（P0-3）：js/ 下 19 个 .js，但 js/_entry.js 是 ES Module 单一
        # 入口、不是功能域文件。README 的「功能域文件」数只数 18 个功能域模块，
        # 同时必须说明这个入口（否则计数与事实源脱节）。
        files = [f for f in os.listdir(os.path.join(ROOT, "js")) if f.endswith(".js")]
        assert "_entry.js" in files, "ES Module 单一入口缺失"
        n = len([f for f in files if f != "_entry.js"])
        doc = self._readme()
        assert ("前端 %d 个功能域文件" % n) in doc
        assert "_entry.js" in doc, "README 应说明单一 ES Module 入口"

    def test_cdp_origin_is_whitelist_not_wildcard(self):
        """README 关键链路与速查里的启动命令不得再用 `*`。"""
        doc = self._readme()
        assert "--remote-allow-origins=*" not in doc
        assert "CDP_CLIENT_ORIGIN" in doc
        # 速查命令里的白名单值必须等于代码常量
        origin = _literal("bridge/cdp_client.py", "CDP_CLIENT_ORIGIN")
        assert ("--remote-allow-origins=" + origin) in doc

    def test_no_hardcoded_version_or_line_count_in_arch_tree(self):
        """发版会 +1、改代码会变行数——架构树里不许写死这两个数。"""
        doc = self._readme()
        assert "当前对齐 v1.0." not in doc
        assert "~880 行" not in doc
        assert re.search(r"~1392 行", doc) is None

    def test_changelog_archive_boundary_is_explicit(self):
        doc = _read("CHANGELOG.md")
        assert "归档边界" in doc
        assert "不再向前追加" in doc
        assert "历史合并条目" in doc


class TestCommentCountDiscipline:
    """评审 P2-3：`TOOLS N 项` / `SKILLS N 项` 这类注释里写死的数字会过期
    （实测曾出现 42 / 50 / 19 / 28 三方不一致）。本轮已把注释改成「不写死」，
    这里兜底两层：① 还写死就必须与代码实际长度一致；② 三个核心模块不许再写死。

    走全量 pytest 门禁生效，不额外往 build.py 静态门禁序列里塞检查（改动面更小，
    且 `tests/` 本来就在每次打包的 pytest 门禁里跑）。
    """
    _PATTERN = re.compile(r"\b(TOOLS|SKILLS)\b[^\n]{0,24}?(\d+)\s*项")

    def test_any_hardcoded_count_matches_code(self):
        tools = _list_len("agent_tools/registry.py", "TOOLS")
        skills = _list_len("agent_tools/skills.py", "SKILLS")
        skip = (set(scope.skip_dirs("source_walk"))
                | {"tools", "css", "node_modules", ".pytest_cache", "组件",
                   "_dist_backup", "_rollback", "_chat_archive"})
        bad = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                src = io.open(full, encoding="utf-8", errors="replace").read()
                for m in self._PATTERN.finditer(src):
                    expected = tools if m.group(1) == "TOOLS" else skills
                    if int(m.group(2)) != expected:
                        bad.append((os.path.relpath(full, ROOT), m.group(0), expected))
        assert not bad, "注释里写死的条数与代码不一致：%s" % bad

    def test_core_modules_never_hardcode_counts(self):
        for rel in ("agent_tools/__init__.py", "agent_tools/skills.py",
                    "agent_tools/registry.py"):
            assert not self._PATTERN.search(_read(rel)), rel
