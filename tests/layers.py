# -*- coding: utf-8 -*-
"""tests/layers.py — v1.0.756（评审 P1-2①）测试目录三层布局的**单一事实源**。

按能力域把 tests/ 下的测试文件分到 unit / contract / e2e 三层：

  unit      纯逻辑 / 解析 / 状态机 / 工具函数，不依赖真实外部环境或打包产物
  contract  行为锁：门禁 / 契约 / 棘轮 / 常量一致性 / 前端结构 / 资源一致性
            （「改回坏形状必须变红」的那批，含 FAST_FILES 的 8 个护栏文件）
  e2e       需要真实运行时 / 子进程 / 便携运行时 / 自修复 / HTTP / 打包产物的集成测试

分类规则写在这里而不是散在脚本里：迁移脚本、布局行为锁、打包门禁的
FAST_FILES 都从这里取口径，避免「目录挪了、清单没同步」（v1.0.751 的
10 error 事故形态）。文件不在任何规则里 → unit。

用法：
    from layers import layer_of, LAYERS
    layer_of("test_contract_security_gate.py")   # -> "contract"
"""
import re

LAYERS = ("unit", "contract", "e2e")

# contract：行为锁 / 契约 / 护栏。FAST_FILES 的 8 个文件全部归这一层。
_CONTRACT = (
    r"^test_contract_",
    r"^test_gate_scope\.py$",
    r"^test_build_guard\.py$",
    r"^test_buildlib_units\.py$",
    r"^test_buildlib_more\.py$",
    r"^test_frontend_structure\.py$",
    r"^test_frontend_render\.py$",
    r"^test_v667_audit_fixes",
    r"^test_v668_theme",
    r"^test_v669_theme",
    r"^test_ratchet",
    r"^test_scope",
    r"^test_doc_constants\.py$",
    r"^test_js_load_order\.py$",
    r"^test_esm_",
    r"^test_hiddenimport",
    r"^test_incremental_coverage\.py$",
    r"^test_coverage_gap\.py$",
    r"^test_pc_run_argv\.py$",
    r"^test_approval_dom\.py$",
    r"^test_run_trace\.py$",
    r"^test_profile_bundle",
    r"^test_release_metadata\.py$",
    r"^test_shell_channel_policy\.py$",
    r"^test_migration_guide\.py$",
    r"^test_run_replay\.py$",
    r"^test_layer_layout\.py$",
    r"^test_publish_github_path\.py$",
    r"^test_v\d+_audit",
    r"^test_v\d+_theme",
)

# e2e：真实环境 / 运行时 / 子进程 / 自修复 / 便携组件 / 打包产物
_E2E = (
    r"^test_portable_runtime",
    r"^test_self_repair",
    r"^test_smoke",
    r"^test_packaged",
    r"^test_frozen",
    r"^test_e2e",
    r"^test_integration",
    r"^test_api_auth\.py$",
    r"^test_instance_lock",
    r"^test_daemon",
    r"^test_tray",
    r"^test_close_guard",
    r"^test_component_reminder",
    r"^test_asr\.py$",
)

_CONTRACT_RE = re.compile("|".join(_CONTRACT))
_E2E_RE = re.compile("|".join(_E2E))


def layer_of(filename):
    """返回测试文件应归属的层名；未知一律 unit（fail-safe，不丢文件）。"""
    name = filename.split("/")[-1].split("\\")[-1]
    if not (name.startswith("test_") and name.endswith(".py")):
        return None
    if _CONTRACT_RE.search(name):
        return "contract"
    if _E2E_RE.search(name):
        return "e2e"
    return "unit"


def relpath(filename):
    """测试文件在三层布局下的相对路径（相对仓库根）。"""
    return "tests/%s/%s" % (layer_of(filename), filename)


if __name__ == "__main__":
    import collections
    import glob
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py"))]
    for p in ("unit", "contract", "e2e"):
        for q in glob.glob(os.path.join(here, p, "test_*.py")):
            files.append(os.path.basename(q))
    c = collections.Counter(layer_of(f) for f in files)
    print("layers:", dict(c), "total:", len(files))
