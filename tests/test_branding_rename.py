# -*- coding: utf-8 -*-
"""tests/test_branding_rename.py — 改名的兼容锁（v1.0.680 WeComCompanion→Lingban，
v1.0.691 Lingban→Fisheep）。

改名不是替换字符串：老用户机器上仍有**旧名**的痕迹（老 exe、自启动注册表值、
计划任务、LOCALAPPDATA 数据目录、webview localStorage、回滚备份）。
这些"旧名兼容"是最容易漏、且一旦漏了就会**静默损坏用户环境**的地方
（自启失效、界面偏好清零、密钥副本读不到），所以逐条钉住。

判据原则：**新写入一律用新名（Fisheep）；读旧数据必须认所有旧名
（两代：WeComCompanion 系 + Lingban 系）。**
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import branding  # noqa: E402


def _src(*rel):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(root, *rel), encoding="utf-8").read()


# ---------------- 品牌常量本身 ----------------

def test_new_brand_names():
    assert branding.APP_ID == "Fisheep"
    assert branding.EXE_NAME == "Fisheep.exe"
    assert branding.SPEC_NAME == "Fisheep.spec"
    assert branding.ONEDIR_NAME == "Fisheep-onedir"
    assert branding.MUTEX_NAME == "Fisheep_SingleInstance"
    assert branding.AUTOSTART_VALUE == "Fisheep"
    assert branding.ADMIN_TASK == "FisheepAdmin"
    assert branding.ADMIN_BOOTSTRAP_TASK == "FisheepAdminBootstrap"
    assert branding.DATA_DIR == "Fisheep"


def test_legacy_names_preserved():
    """第 1 代旧名（WeComCompanion 系）常量必须保留——兼容层全靠它认旧痕迹。"""
    assert branding.LEGACY_APP_ID == "WeComCompanion"
    assert branding.LEGACY_EXE_NAME == "WeComCompanion.exe"
    assert branding.LEGACY_MUTEX_NAME == "WeComCompanion_SingleInstance"
    assert branding.LEGACY_AUTOSTART_VALUE == "WeComCompanion"
    assert branding.LEGACY_ADMIN_TASK == "WeComCompanionAdmin"
    assert branding.LEGACY_ADMIN_BOOTSTRAP_TASK == "WeComCompanionAdminBootstrap"
    assert branding.LEGACY_DATA_DIR == "WeComCompanion"


def test_legacy2_names_preserved():
    """v1.0.691：第 2 代旧名（Lingban 系）同样必须可认——这一代用户量最大，
    漏了等于老用户的自启/数据目录/单实例保护全部失效。"""
    assert branding.LEGACY2_APP_ID == "Lingban"
    assert branding.LEGACY2_EXE_NAME == "Lingban.exe"
    assert branding.LEGACY2_MUTEX_NAME == "Lingban_SingleInstance"
    assert branding.LEGACY2_AUTOSTART_VALUE == "Lingban"
    assert branding.LEGACY2_ADMIN_TASK == "LingbanAdmin"
    assert branding.LEGACY2_ADMIN_BOOTSTRAP_TASK == "LingbanAdminBootstrap"
    assert branding.LEGACY2_DATA_DIR == "Lingban"


def test_all_name_chains_complete():
    """全名链必须三代齐全（最老→最新），枚举点靠它认全旧名。"""
    assert branding.ALL_APP_IDS == ("WeComCompanion", "Lingban", "Fisheep")
    assert branding.ALL_EXE_NAMES == ("WeComCompanion.exe", "Lingban.exe", "Fisheep.exe")
    assert branding.ALL_MUTEX_NAMES == (
        "WeComCompanion_SingleInstance", "Lingban_SingleInstance", "Fisheep_SingleInstance")
    assert branding.ALL_DATA_DIRS == ("WeComCompanion", "Lingban", "Fisheep")
    assert branding.ALL_AUTOSTART_VALUES == ("WeComCompanion", "Lingban", "Fisheep")
    assert branding.ALL_ADMIN_TASKS == ("WeComCompanionAdmin", "LingbanAdmin", "FisheepAdmin")
    assert branding.ALL_ADMIN_BOOTSTRAP_TASKS == (
        "WeComCompanionAdminBootstrap", "LingbanAdminBootstrap", "FisheepAdminBootstrap")


def test_new_and_legacy_names_differ():
    """改名必须是真改名——若两边相等，本文件其余用例就失去意义（防"改了个寂寞"）。"""
    assert branding.APP_ID not in (branding.LEGACY_APP_ID, branding.LEGACY2_APP_ID)
    assert branding.EXE_NAME != branding.LEGACY_EXE_NAME
    assert branding.EXE_NAME != branding.LEGACY2_EXE_NAME
    assert branding.MUTEX_NAME not in (branding.LEGACY_MUTEX_NAME, branding.LEGACY2_MUTEX_NAME)
    assert branding.AUTOSTART_VALUE not in (branding.LEGACY_AUTOSTART_VALUE,
                                           branding.LEGACY2_AUTOSTART_VALUE)
    assert branding.DATA_DIR not in (branding.LEGACY_DATA_DIR, branding.LEGACY2_DATA_DIR)


# ---------------- 单实例 mutex：三代 ----------------

def test_single_instance_uses_all_mutex_names():
    """新版必须同时持三代 mutex——否则任一旧版 exe 还在跑时，
    新版会另起一个实例，多个实例抢同一个企微 CDP 端口。"""
    src = _src("backend.py")
    assert "branding.ALL_MUTEX_NAMES" in src, "backend.py 必须用品牌全名链取 mutex 锁名"


def test_mutex_names_are_distinct_kernel_objects():
    assert len(set(branding.ALL_MUTEX_NAMES)) == 3


# ---------------- 开机自启：读所有旧名 / 写新名 ----------------

def test_autostart_reads_legacy_registry_values():
    """老用户机器上注册表 Run 的值名仍是旧名（两代）——只读新名会把
    "已开启开机自启"显示成关闭（用户以为功能坏了）。"""
    from routes import b_admin
    assert b_admin.LEGACY_AUTOSTART_NAMES == branding.ALL_AUTOSTART_VALUES
    assert "LEGACY_AUTOSTART_NAMES" in _src("routes", "b_admin.py"), "自启动必须认旧名值"


def test_autostart_writes_new_name_and_clears_legacy():
    """写新名 + 清所有旧名：旧值内容指向旧 exe 路径，留着会让开机拉起旧版
    （或指向已删除的 exe → 自启静默失效）。"""
    from routes import b_admin
    assert b_admin.AUTOSTART_NAME == branding.AUTOSTART_VALUE
    import inspect
    src = inspect.getsource(b_admin._hkcu_write)
    assert "SetValueEx(k, AUTOSTART_NAME" in src, "必须写新名值"
    assert "LEGACY_AUTOSTART_NAMES" in src, "必须清旧名值"


def test_admin_task_checks_all_names():
    """管理员自启的计划任务名两代都改过——只查新名会让开关显示成关闭，
    且旧任务继续拉起旧 exe。

    判据读**源文件**而不是 inspect.getsource——conftest 的 autouse fixture
    会把这几个函数 monkeypatch 掉，getsource 拿到的是桩。
    """
    from routes import b_admin
    assert b_admin._LEGACY_AUTOSTART_TASKS == branding.ALL_ADMIN_TASKS
    src = _src("routes", "b_admin.py")
    assert "_LEGACY_AUTOSTART_TASKS" in src, "管理员任务查询必须认旧名"
    assert "_LEGACY_ADMIN_TASKS" in src, "自举任务查询必须认旧名"


# ---------------- 数据目录：兼容老目录（两代） ----------------

def test_fallback_dir_prefers_existing_legacy_dir(monkeypatch, tmp_path):
    """老用户的配置回退副本在 %LOCALAPPDATA%\\WeComCompanion 下——
    换目录名等于"配置不见了"。老目录存在且新目录不存在时必须沿用老目录。"""
    from core import paths
    local = tmp_path / "LOCALAPPDATA"
    (local / branding.LEGACY_DATA_DIR).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(local))
    got = paths._fallback_dir()
    assert os.path.normcase(os.path.abspath(got)) == os.path.normcase(os.path.abspath(
        str(local / branding.LEGACY_DATA_DIR))), "老目录存在时必须沿用老目录（保住配置副本）"


def test_fallback_dir_prefers_existing_legacy2_dir(monkeypatch, tmp_path):
    """v1.0.691：中间代（Lingban）目录同样要认——从 Lingban 版直接升级到
    Fisheep 版的用户，数据在 Lingban 目录里。"""
    from core import paths
    local = tmp_path / "LOCALAPPDATA"
    (local / branding.LEGACY2_DATA_DIR).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(local))
    got = paths._fallback_dir()
    assert os.path.normcase(os.path.abspath(got)) == os.path.normcase(os.path.abspath(
        str(local / branding.LEGACY2_DATA_DIR))), "Lingban 目录存在且 Fisheep 目录不存在时必须沿用"


def test_fallback_dir_uses_new_dir_when_available(monkeypatch, tmp_path):
    """新目录存在时用新目录（改名后的正常路径）。"""
    from core import paths
    local = tmp_path / "LOCALAPPDATA"
    (local / branding.LEGACY_DATA_DIR).mkdir(parents=True)
    (local / branding.DATA_DIR).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(local))
    got = paths._fallback_dir()
    assert os.path.normcase(os.path.abspath(got)) == os.path.normcase(os.path.abspath(
        str(local / branding.DATA_DIR)))


def test_fallback_dir_new_dir_when_no_legacy(monkeypatch, tmp_path):
    """全新用户（无老目录）→ 新目录。"""
    from core import paths
    local = tmp_path / "LOCALAPPDATA"
    local.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(local))
    got = paths._fallback_dir()
    assert os.path.basename(got) == branding.DATA_DIR


# ---------------- 对话框 owner 过滤 ----------------

def test_save_dialog_filter_accepts_all_exe_names():
    """save_dialog_auto 按 exe 名过滤对话框 owner——改名过渡期跑旧版时，
    下载保存框不再被自动关闭（会一直挂着）。三代 exe 名都要在白名单里。"""
    import inspect
    from desktop import save_dialog_auto
    src = inspect.getsource(save_dialog_auto._is_wecom_dialog)
    assert "ALL_EXE_NAMES" in src, "新旧 exe 名都要在白名单里"


# ---------------- 版本回滚：旧记录仍可读 ----------------

def test_version_store_uses_new_exe_name():
    from services import version_store
    assert version_store._dist_exe().endswith(branding.EXE_NAME)


def test_version_store_pending_switch_falls_back_to_legacy_next():
    """改名过渡期可能残留旧名的 .next（老版排队切换时写的）——
    新名找不到时必须回退查所有旧名，否则那次排队的切换被静默丢弃。"""
    import inspect
    from services import version_store
    src = inspect.getsource(version_store.apply_pending_switch)
    assert "ALL_EXE_NAMES" in src, ".next 必须回退查旧名"


# ---------------- 构建链 ----------------

def test_spec_file_renamed():
    """主 spec 文件名必须已改名（构建入口按 branding.SPEC_NAME 找）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(root, branding.SPEC_NAME)), "Fisheep.spec 应存在"
    assert not os.path.isfile(os.path.join(root, "WeComCompanion.spec")), "第 1 代旧 spec 应已移除"
    assert not os.path.isfile(os.path.join(root, "Lingban.spec")), "第 2 代旧 spec 应已移除"


def test_build_env_points_at_new_spec():
    from buildlib import env
    assert os.path.normcase(os.path.basename(env.SPEC)) == os.path.normcase(branding.SPEC_NAME)


def test_version_resource_uses_new_name():
    """版本资源里的 InternalName/OriginalFilename/ProductName 是资源管理器
    "属性 → 详细信息"显示的内部标识——必须取品牌常量，不许硬编码。"""
    import inspect
    from buildlib import versioning
    src = inspect.getsource(versioning.make_version_info)
    assert "APP_ID" in src and "EXE_NAME" in src and "APP_TITLE" in src, "版本资源必须取品牌常量"
    assert "u'灵伴'" not in src, "版本资源不许残留旧显示名字面量（历史注释里的旧名不算）"
