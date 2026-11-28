# -*- coding: utf-8 -*-
"""v1.0.788（P0-1）发版自检：更新检查只走 GitHub Releases + 一键升级。

锁住的契约：
  · _ver_tuple/_cmp_version 版本比较（解析失败 → None，不强下结论）；
  · check_update 只读不抛：网络/404/5xx 全回 ok:False + 人话原因；
  · install_update：无新版拒装、下载大小不符拒装（删半截文件）、
    快照失败拒装、staged 换 exe（直接拷 / PermissionError 排队）、防重入锁。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _rel(tag="v1.0.999", size=1024, body="notes"):
    return {
        "tag_name": tag, "name": "Fisheep v" + tag.lstrip("v"),
        "published_at": "2026-11-28T10:00:00Z", "body": body,
        "assets": [
            {"name": "Fisheep.exe", "browser_download_url": "https://gh/dl/Fisheep.exe",
             "size": size},
            {"name": "README.md", "browser_download_url": "https://gh/dl/README.md",
             "size": 10},
        ],
    }


# ================= 版本比较 =================

def test_ver_tuple_parses():
    from services.update_check import _ver_tuple
    assert _ver_tuple("v1.0.787") == (1, 0, 787)
    assert _ver_tuple("1.0.787") == (1, 0, 787)
    assert _ver_tuple("v1.0.787-rc1") == (1, 0, 787)
    assert _ver_tuple("1.2") is None
    assert _ver_tuple("") is None
    assert _ver_tuple(None) is None


def test_cmp_version_orders():
    from services.update_check import _cmp_version
    assert _cmp_version("v1.0.788", "1.0.787") == 1
    assert _cmp_version("v1.0.787", "1.0.787") == 0
    assert _cmp_version("1.0.787", "v1.0.788") == -1
    assert _cmp_version("1.0.787", "?") is None


# ================= check_update =================

def test_check_update_newer_available(monkeypatch):
    import services.update_check as uc
    monkeypatch.setattr("services.version_store.list_versions",
                        lambda: {"current": "1.0.787", "versions": [], "keep": 3})
    monkeypatch.setattr(uc, "_gh_latest", lambda: (_rel(tag="v1.0.788"), ""))
    d = uc.check_update()
    assert d["ok"] and d["update_available"] is True
    assert d["latest"] == "1.0.788" and d["current"] == "1.0.787"
    assert d["asset"]["name"] == "Fisheep.exe"   # 只认 exe 附件
    assert d["asset"]["size"] == 1024
    assert "notes" in d["body"]


def test_check_update_same_version(monkeypatch):
    import services.update_check as uc
    monkeypatch.setattr("services.version_store.list_versions",
                        lambda: {"current": "1.0.999", "versions": [], "keep": 3})
    monkeypatch.setattr(uc, "_gh_latest", lambda: (_rel(tag="v1.0.999"), ""))
    d = uc.check_update()
    assert d["ok"] and d["update_available"] is False and d["comparable"] is True


def test_check_update_network_failure_never_raises(monkeypatch):
    import services.update_check as uc
    monkeypatch.setattr("services.version_store.list_versions",
                        lambda: {"current": "1.0.787", "versions": [], "keep": 3})
    monkeypatch.setattr(uc, "_gh_latest", lambda: (None, "连不上 GitHub（网络/代理问题）：boom"))
    d = uc.check_update()
    assert d["ok"] is False and "GitHub" in d["error"]


def test_check_update_body_capped(monkeypatch):
    import services.update_check as uc
    monkeypatch.setattr("services.version_store.list_versions",
                        lambda: {"current": "1.0.787", "versions": [], "keep": 3})
    monkeypatch.setattr(uc, "_gh_latest",
                        lambda: (_rel(body="x" * 3000), ""))
    d = uc.check_update()
    assert len(d["body"]) == 1500


def test_gh_latest_status_paths(monkeypatch):
    """_gh_latest 各状态码路径（monkeypatch 真 requests.get，不碰网络）：
    200+dict 成功 / 404 / 5xx / 坏 JSON（json() 抛）/ 非 dict（无 tag_name）。"""
    import requests
    import services.update_check as uc

    class _R:
        def __init__(self, code, payload=None, raise_json=False):
            self.status_code = code
            self._payload = payload
            self._raise_json = raise_json

        def json(self):
            if self._raise_json:
                raise ValueError("not json")
            return self._payload

    def _run(code, payload=None, raise_json=False):
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: _R(code, payload, raise_json))
        return uc._gh_latest()

    d, why = _run(200, _rel())
    assert isinstance(d, dict) and d["tag_name"] == "v1.0.999" and why == ""
    d, why = _run(404)
    assert d is None and "没找到" in why
    d, why = _run(503)
    assert d is None and "503" in why
    d, why = _run(200, raise_json=True)
    assert d is None and "无法解析" in why
    d, why = _run(200, "not-a-dict")
    assert d is None and "信息不完整" in why


def test_gh_latest_network_exception(monkeypatch):
    import requests
    import services.update_check as uc

    def _boom(*a, **k):
        raise ConnectionError("dial tcp: no route to host")

    monkeypatch.setattr(requests, "get", _boom)
    d, why = uc._gh_latest()
    assert d is None and "连不上 GitHub" in why


# ================= install_update =================

class _FakeResp:
    """requests.get(stream=True) 的假：支持 with + iter_content。"""

    def __init__(self, payload, code=200):
        self._payload = payload
        self.status_code = code

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, n):
        yield self._payload


def _mk_install_env(monkeypatch, tmp_path, chk):
    """install_update 的公共桩：查版结果固定 + 快照/换 exe 记录调用。"""
    import services.update_check as uc
    import services.version_store as vs
    monkeypatch.setattr(uc, "check_update", lambda: dict(chk))
    snaps, swaps = [], []

    def _stage(src, ver):
        swaps.append((src, ver))
        return True, "已替换"

    monkeypatch.setattr(vs, "record_version", lambda note="": snaps.append(note))
    monkeypatch.setattr(vs, "stage_exe_swap", _stage)
    monkeypatch.setattr(vs, "_app_root", lambda: str(tmp_path))
    return uc, snaps, swaps


def test_install_rejects_when_not_newer(monkeypatch, tmp_path):
    import services.update_check as uc
    _mk_install_env(monkeypatch, tmp_path,
                    {"ok": True, "update_available": False,
                     "latest": "1.0.787", "current": "1.0.787", "asset": None})
    d = uc.install_update()
    assert d["ok"] is False and "已是最新" in d["error"]


def test_install_rejects_older(monkeypatch, tmp_path):
    import services.update_check as uc
    _mk_install_env(monkeypatch, tmp_path,
                    {"ok": True, "update_available": False,
                     "latest": "1.0.100", "current": "1.0.787", "asset": None})
    d = uc.install_update()
    assert d["ok"] is False and "只升级不降级" in d["error"]


def test_install_happy_path_stages_exe(monkeypatch, tmp_path):
    import requests
    import services.update_check as uc
    uc, snaps, swaps = _mk_install_env(monkeypatch, tmp_path,
                                       {"ok": True, "update_available": True,
                                        "latest": "1.0.999", "current": "1.0.787",
                                        "asset": {"name": "Fisheep.exe",
                                                   "url": "https://gh/dl/Fisheep.exe",
                                                   "size": 8}})
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _FakeResp(b"01234567"))
    d = uc.install_update()
    assert d["ok"] is True and d["version"] == "1.0.999", d
    assert snaps == ["升级前自动快照"], "升级前必须自动快照"
    assert len(swaps) == 1 and swaps[0][1] == "1.0.999"
    staged = os.path.join(str(tmp_path), "build", "_update", "Fisheep.exe")
    assert not os.path.exists(staged), "换完 exe 应清掉暂存文件"


def test_install_download_size_mismatch(monkeypatch, tmp_path):
    import requests
    import services.update_check as uc
    uc, snaps, swaps = _mk_install_env(monkeypatch, tmp_path,
                                       {"ok": True, "update_available": True,
                                        "latest": "1.0.999", "current": "1.0.787",
                                        "asset": {"name": "Fisheep.exe",
                                                   "url": "https://gh/dl/Fisheep.exe",
                                                   "size": 1000}})
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _FakeResp(b"0123"))
    d = uc.install_update()
    assert d["ok"] is False and "大小不符" in d["error"]
    assert snaps == [], "快照在下载成功前不该发生"
    assert swaps == [], "下载校验不过绝不换 exe"
    staged = os.path.join(str(tmp_path), "build", "_update", "Fisheep.exe")
    assert not os.path.exists(staged), "半截文件必须删干净"


def test_install_download_http_error(monkeypatch, tmp_path):
    import requests
    import services.update_check as uc
    uc, _snaps, swaps = _mk_install_env(monkeypatch, tmp_path,
                                        {"ok": True, "update_available": True,
                                         "latest": "1.0.999", "current": "1.0.787",
                                         "asset": {"name": "Fisheep.exe",
                                                  "url": "https://gh/dl/Fisheep.exe",
                                                  "size": 8}})
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _FakeResp(b"", code=404))
    d = uc.install_update()
    assert d["ok"] is False and "404" in d["error"]
    assert swaps == []


def test_install_no_asset(monkeypatch, tmp_path):
    import services.update_check as uc
    _mk_install_env(monkeypatch, tmp_path,
                    {"ok": True, "update_available": True,
                     "latest": "1.0.999", "current": "1.0.787", "asset": None})
    d = uc.install_update()
    assert d["ok"] is False and "没有 exe 附件" in d["error"]


def test_install_check_failed(monkeypatch, tmp_path):
    import services.update_check as uc
    _mk_install_env(monkeypatch, tmp_path,
                    {"ok": False, "error": "连不上 GitHub（网络/代理问题）：x"})
    d = uc.install_update()
    assert d["ok"] is False and "GitHub" in d["error"]


def test_install_reentrancy_lock(monkeypatch, tmp_path):
    import services.update_check as uc
    _mk_install_env(monkeypatch, tmp_path,
                    {"ok": True, "update_available": True,
                     "latest": "1.0.999", "current": "1.0.787",
                     "asset": {"name": "Fisheep.exe",
                               "url": "https://gh/dl/Fisheep.exe", "size": 8}})
    assert uc._INSTALL_LOCK.acquire(blocking=False)
    try:
        d = uc.install_update()
    finally:
        uc._INSTALL_LOCK.release()
    assert d["ok"] is False and "正在安装" in d["error"]


# ================= version_store.stage_exe_swap =================

def _vs_client(tmp_path, monkeypatch):
    import services.version_store as vs
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "Fisheep.exe").write_bytes(b"old")
    monkeypatch.setattr(vs, "_dist_exe", lambda: str(dist / "Fisheep.exe"))
    return vs


def test_stage_swap_direct_copy(tmp_path, monkeypatch):
    vs = _vs_client(tmp_path, monkeypatch)
    src = tmp_path / "new.exe"
    src.write_bytes(b"newexe")
    ok, note = vs.stage_exe_swap(str(src))
    assert ok is True
    assert (tmp_path / "dist" / "Fisheep.exe").read_bytes() == b"newexe"
    assert not (tmp_path / "dist" / ".version_switch").exists()


def test_stage_swap_perm_error_queues_next(tmp_path, monkeypatch):
    vs = _vs_client(tmp_path, monkeypatch)
    src = tmp_path / "new.exe"
    src.write_bytes(b"newexe")
    real_copy2 = vs.shutil.copy2
    calls = {"n": 0}

    def _copy2(a, b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("文件被占用")
        return real_copy2(a, b)

    monkeypatch.setattr(vs.shutil, "copy2", _copy2)
    ok, note = vs.stage_exe_swap(str(src), "1.0.999")
    assert ok is True and "下次启动" in note
    nxt = tmp_path / "dist" / "Fisheep.exe.next"
    assert nxt.is_file() and nxt.read_bytes() == b"newexe"
    marker = json.loads((tmp_path / "dist" / ".version_switch").read_text(encoding="utf-8"))
    assert marker == {"version": "1.0.999"}


def test_stage_swap_total_failure(tmp_path, monkeypatch):
    vs = _vs_client(tmp_path, monkeypatch)
    src = tmp_path / "new.exe"
    src.write_bytes(b"newexe")

    def _boom(a, b):
        raise PermissionError("都拷不动")

    monkeypatch.setattr(vs.shutil, "copy2", _boom)
    ok, note = vs.stage_exe_swap(str(src))
    assert ok is False and "都拷不动" in note


# ================= HTTP 接线 =================

def test_routes_wired_offline(monkeypatch):
    """/api/update/check 接线 + 离线不 500（_gh_latest 打桩成网络失败形态）。"""
    import backend
    import services.update_check as uc
    monkeypatch.setattr(uc, "_gh_latest",
                         lambda: (None, "连不上 GitHub（网络/代理问题）：stub"))
    c = backend.app.test_client(use_cookies=False)
    r = c.get("/api/update/check")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is False and "GitHub" in d["error"]
    assert c.post("/api/update/install").status_code in (200, 405)  # 端点存在即接线成功
