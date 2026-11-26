# -*- coding: utf-8 -*-
"""tests/contract/test_contract_b_admin.py — routes/b_admin.py 管理蓝图**契约锁**（评审 P1-2）。

b_admin.py（>1000 行）挂着 33 个管理端点。历史事故（v1.0.407）：拆分时遗留
一个孤儿装饰器 @bp.route("/models") GET，把 api_rules 错注册到 GET /api/models，
模型管理页拿到规则模块列表、添加模型全失效。本文件用 AST 锁住路由表形状：
蓝图名、(rule, methods) 不重复、endpoint 名唯一、关键端点齐全、孤儿 /models
装饰器不得回来。
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_SRC = io.open(os.path.join(ROOT, "routes", "b_admin.py"), encoding="utf-8").read()
_TREE = ast.parse(_SRC)


def _blueprint_name():
    for node in _TREE.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", "") == "Blueprint" and node.value.args:
                return ast.literal_eval(node.value.args[0])
    return None


def _routes():
    """[(函数名, rule, methods)]，只取模块级函数上的 @bp.route。"""
    out = []
    for node in _TREE.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for d in node.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "route" and d.args):
                continue
            rule = ast.literal_eval(d.args[0])
            methods = ()
            for kw in d.keywords:
                if kw.arg == "methods":
                    methods = tuple(ast.literal_eval(kw.value))
            out.append((node.name, rule, methods))
    return out


def test_blueprint_name_is_pinned():
    assert _blueprint_name() == "mgmt_admin", "蓝图名改了，前端 URL 前缀与注册点全部对不上"


def test_rule_method_pairs_are_unique():
    pairs = [(r, m) for _n, r, m in _routes()]
    dup = sorted({p for p in pairs if pairs.count(p) > 1})
    assert not dup, "重复的 (rule, methods) 会让 Flask 静默覆盖：%s" % dup


def test_endpoint_names_are_unique():
    # 一个函数挂多个 @bp.route 是合法的（如 api_rules 的 check/list）；
    # 真正的冲突是**两个模块级 def 同名**（后者静默覆盖前者）。
    defs = [n.name for n in _TREE.body if isinstance(n, ast.FunctionDef)]
    dup = sorted({n for n in set(defs) if defs.count(n) > 1})
    assert not dup, "endpoint 名重复定义：%s" % dup


def test_all_rules_are_absolute():
    assert all(r.startswith("/") for _n, r, _m in _routes())


def test_key_admin_endpoints_exist():
    pairs = {(r, m) for _n, r, m in _routes()}
    for p in (("/config", ("GET",)), ("/config", ("POST",)),
              ("/status", ()), ("/audit/abort", ("POST",)),
              ("/rules/check", ("POST",)), ("/rules/list", ("GET",))):
        assert p in pairs, "关键管理端点丢失：%s" % (p,)


def test_config_and_status_endpoints_are_defined():
    """配置读写 / 状态端点是前端启动即拉的两个接口，函数名被删就会 404。"""
    defs = {n.name for n in _TREE.body if isinstance(n, ast.FunctionDef)}
    for name in ("post_config", "get_config", "api_status", "api_rules"):
        assert name in defs, "管理端点函数丢失：%s" % name


def test_orphan_models_decorator_never_returns():
    """v1.0.407：孤儿 @bp.route(\"/models\") 曾把 api_rules 注册到 GET /api/models。

    源码注释里会复述这段事故，所以必须查 AST 里的真实装饰器，不能查文本子串。
    """
    assert not any(r == "/models" for _n, r, _m in _routes())
    rules = [r for n, r, _m in _routes() if n == "api_rules"]
    assert rules and all(r.startswith("/rules/") for r in rules), rules
