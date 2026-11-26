# -*- coding: utf-8 -*-
"""tests/contract/test_layer_layout.py — v1.0.756（评审 P1-2①）三层布局行为锁。

「改回坏形状必红」：文件放错层、根级又冒出 test_*.py、分类清单与实物不符、
FAST_FILES 指向不存在的文件、pytest.ini 把三层目录排掉导致漏收集、
契约目录没接线、ignore 没跟着挪、历史版本文件被删——任一都红。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)
import layers  # noqa: E402


def _collected():
    out = []
    for layer in layers.LAYERS:
        d = os.path.join(TESTS, layer)
        for fn in sorted(os.listdir(d)):
            if fn.startswith("test_") and fn.endswith(".py"):
                out.append((layer, fn))
    return out


def test_three_layer_dirs_exist_and_are_nonempty():
    for layer in layers.LAYERS:
        d = os.path.join(TESTS, layer)
        assert os.path.isdir(d), ("缺能力域目录 tests/%s/" % layer)
        assert any(fn.startswith("test_") and fn.endswith(".py") for fn in os.listdir(d)), (
            "tests/%s/ 是空的，重组没真做" % layer)


def test_no_root_level_test_files():
    """历史断言已物理归并到三层；根级只剩 conftest / layers / 辅助模块。"""
    leftovers = [fn for fn in os.listdir(TESTS)
                 if fn.startswith("test_") and fn.endswith(".py")]
    assert not leftovers, ("根级仍有未归类的测试文件：%s" % leftovers)


def test_every_file_is_in_its_declared_layer():
    for layer, fn in _collected():
        assert layers.layer_of(fn) == layer, (
            "%s 应在 tests/%s/，实际在 tests/%s/" % (fn, layers.layer_of(fn), layer))


def test_all_collected_files_are_unique():
    names = [fn for _l, fn in _collected()]
    assert len(names) == len(set(names)), "同名测试文件跨层重复（pytest import mismatch）"
    assert len(names) >= 120, ("收集到的测试文件数明显偏少：%d" % len(names))


def test_fast_files_still_point_to_real_tests():
    from buildlib import gate_scope as g
    missing = [f for f in g.FAST_FILES if not os.path.isfile(os.path.join(ROOT, f))]
    assert not missing, ("FAST_FILES 指向不存在的文件（目录重组漏同步）：%s" % missing)
    assert all("/contract/" in f for f in g.FAST_FILES), g.FAST_FILES


def test_pytest_ini_does_not_exclude_the_three_layers():
    ini = io.open(os.path.join(ROOT, "pytest.ini"), encoding="utf-8").read()
    m = re.search(r"^norecursedirs\s*=\s*(.+)$", ini, re.M)
    assert m, "pytest.ini 缺 norecursedirs"
    excluded = set(m.group(1).split())
    for layer in layers.LAYERS:
        assert layer not in excluded, (
            "pytest.ini 把 tests/%s/ 排掉了，该层测试会被静默漏收集" % layer)


def test_classifier_is_total_and_deterministic():
    for layer, fn in _collected():
        assert layers.layer_of(fn) in layers.LAYERS
        assert layers.layer_of(fn) == layers.layer_of(fn)
    assert layers.layer_of("test_totally_new_module.py") == "unit"
    assert layers.layer_of("conftest.py") is None


def test_every_test_file_uses_three_level_root():
    """搬进子目录后 ROOT 必须多一层 dirname，否则整批路径断言打到错目录。"""
    for layer, fn in _collected():
        src = io.open(os.path.join(TESTS, layer, fn), encoding="utf-8").read()
        m = re.search(r"^ROOT\s*=\s*os\.path\.dirname\((.+?)\)\s*$", src, re.M)
        if not m:
            continue
        assert m.group(1).count("os.path.dirname(") == 2, (
            "%s 的 ROOT 仍是两层 dirname（移到子目录后必须三层）" % fn)


def test_legacy_version_files_are_preserved_and_still_hold_assertions():
    """评审 P1-2①「保留文件做兼容转发即可，不必删历史」：23 个 test_vNNN_* 必须
    全部还在三层里、且仍含测试函数，不许借重组之名把历史断言删掉。"""
    legacy = [(layer, fn) for layer, fn in _collected() if re.match(r"^test_v\d+_", fn)]
    assert len(legacy) >= 20, ("历史版本测试文件被删/漏迁移：%d" % len(legacy))
    for layer, fn in legacy:
        assert layer == layers.layer_of(fn), (fn, layer)
        src = io.open(os.path.join(TESTS, layer, fn), encoding="utf-8").read()
        assert re.search(r"^\s*def test_", src, re.M), (
            "%s 变成空壳了——历史断言必须保留或显式转发" % fn)


def test_contract_dir_is_wired_into_coverage_gap():
    """契约测试挪到 tests/contract/ 后，CONTRACT_DIR 必须跟着改，否则 find_gaps
    会把 5 个高风险模块全判「缺契约测试」并在打包期硬拦。"""
    from buildlib import coverage_gap as CG
    assert CG.CONTRACT_DIR == "tests/contract", CG.CONTRACT_DIR
    assert CG.find_gaps(ROOT) == [], "接线后仍报契约缺口"


def test_self_repair_ignore_tracks_moved_dual_model():
    src = io.open(os.path.join(ROOT, "services", "self_repair.py"), encoding="utf-8").read()
    assert '"tests", "unit", "test_dual_model.py"' in src, (
        "self_repair 的 --ignore 仍指向根级，挪走后收集期会重新拉起内网 Ollama")


def test_no_stale_root_test_paths_in_production_code():
    """评审 P0-1 的「文档/注释不许漂移」纪律扩展到路径引用：生产代码注释里
    指向**实际存在**的测试文件时，必须写成 tests/<layer>/<name>.py。
    写回根级旧路径 = 目录重组后指路失效，必红。

    （历史已删除的 test_vNNN_*.py 引用是纯指路，不在判红范围。）
    """
    stale = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)")
    hits = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("tests", "待发布版本", "build", "dist", ".git",
                                    "node_modules", "__pycache__", ".pytest_cache",
                                    "产物库", "venv")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            src = io.open(fp, encoding="utf-8").read()
            for name in stale.findall(src):
                if os.path.isfile(os.path.join(TESTS, layers.layer_of(name), name)):
                    hits.append((os.path.relpath(fp, ROOT).replace("\\", "/"), name))
    assert not hits, ("生产代码里仍写根级旧测试路径：%s" % hits[:10])
