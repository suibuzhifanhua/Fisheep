# -*- coding: utf-8 -*-
"""v1.0.788（P1-6）迁移包：导出/导入测试
覆盖：home 路径归一化往返、skip 合并补缺、overwrite 备份整替、
zip-slip 防护、知识库 skip/overwrite 计数、路由接线与参数校验。"""
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import services.migration_pack as MP  # noqa: E402


def _ws(ws, monkeypatch):
    """workspace_root 改道到临时目录（core.paths 本体，导出/导入内部延迟 import 都吃到）。"""
    import core.paths as P
    monkeypatch.setattr(P, "workspace_root", lambda: str(ws))


def _cfg(ws, monkeypatch, data=None):
    """config_path 改道到临时目录里的 config.json。"""
    import core.config_store as cs
    p = ws / "config.json"
    p.write_text(json.dumps(data or {}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cs, "config_path", lambda: str(p))
    cs._CFG_CACHE["cfg"] = None
    cs._CFG_CACHE["mtime"] = None
    return p


def _mkzip(tmp_path, manifest=True, tool=None, experiences=None, skills=None,
           config=None, kb=None, arcnames_extra=()):
    """手工拼迁移包 zip（不走 export_pack，精确控制每节内容）。"""
    zp = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zp, "w") as z:
        if manifest:
            z.writestr("manifest.json", json.dumps({
                "tool": tool if tool is not None else MP._TOOL, "format": 1,
                "version": "1.0.0", "exported_at": "t", "home": "h"}))
        if config is not None:
            z.writestr("config.json", json.dumps(config, ensure_ascii=False))
        if experiences is not None:
            z.writestr("experiences.json", json.dumps(experiences, ensure_ascii=False))
        if skills is not None:
            z.writestr("custom_skills.json", json.dumps(skills, ensure_ascii=False))
        for name, content in (kb or {}).items():
            z.writestr("knowledge_base/" + name, content)
        for arc in arcnames_extra:
            z.writestr(arc, "pwn")
    return str(zp)


def test_normalize_denorm_roundtrip(tmp_path, monkeypatch):
    home = str(tmp_path / "home")
    h_fwd = home.replace(os.sep, "/")
    obj = {"a": h_fwd + "/data/x", "b": [home + "\\deep"], "c": "/" + h_fwd + "/y"}
    out = MP._normalize(obj, home)
    assert out["a"] == "<HOME>/data/x"
    assert out["b"] == ["<HOME>\\deep"]
    assert out["c"] == "<HOME>/y"
    home2 = str(tmp_path / "home2")
    monkeypatch.setattr(MP, "override_home", home2)
    back = MP._denormalize(out)
    assert back["a"].startswith(home2)
    assert back["b"][0].startswith(home2)
    assert back["c"].startswith(home2)


def test_export_normalizes_home_and_shape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    (ws / "知识库" / "课件").mkdir(parents=True)
    (ws / "知识库" / "课件" / "a.md").write_text("# hi", encoding="utf-8")
    monkeypatch.setattr(MP, "override_home", str(home))
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch, {"wecom": {"data": str(home).replace(os.sep, "/") + "/data"}})
    r = MP.export_pack()
    assert r["ok"], r
    zp = r["path"]
    assert zp.startswith(str(ws)) and "迁移包" in zp
    with zipfile.ZipFile(zp) as z:
        cfg = json.loads(z.read("config.json").decode("utf-8"))
        assert cfg["wecom"]["data"] == "<HOME>/data"
        man = json.loads(z.read("manifest.json").decode("utf-8"))
        assert man["tool"] == MP._TOOL and man["format"] == MP._FORMAT
        assert "knowledge_base/课件/a.md" in z.namelist()
    assert r["items"]["kb_files"] == 1
    assert r["items"]["experiences"] == 0


def test_import_skip_merges_missing_experiences(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch)
    import core.config_store as cs
    cs._save_experiences([{"id": "A", "problem": "local A"},
                          {"id": "C", "problem": "c"}])
    zp = _mkzip(tmp_path, experiences=[{"id": "A", "problem": "pkg A"},
                                      {"id": "B", "problem": "b"}])
    r = MP.import_pack(zp, conflict="skip")
    assert r["ok"], r
    by_id = {e["id"]: e for e in cs._load_experiences()}
    assert set(by_id) == {"A", "B", "C"}
    assert by_id["A"]["problem"] == "local A", "skip：本地条目优先，不被包覆盖"
    assert r["sections"]["experiences"].startswith("merged")


def test_import_overwrite_experiences_backs_up(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch)
    import core.config_store as cs
    cs._save_experiences([{"id": "A", "problem": "old"}])
    zp = _mkzip(tmp_path, experiences=[{"id": "A2", "problem": "new"},
                                      {"id": "B", "problem": "b"}])
    r = MP.import_pack(zp, conflict="overwrite")
    assert r["ok"], r
    assert {e["id"] for e in cs._load_experiences()} == {"A2", "B"}
    baks = [x for x in os.listdir(str(ws)) if x.startswith("experiences.json.bak-")]
    assert baks, "overwrite 必须先时间戳备份"
    assert any(os.path.basename(b).startswith("experiences.json.bak-") for b in r["backed_up"])


def test_import_skills_skip_keeps_local_and_merges(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch, {"custom_skills": [{"id": "X", "name": "local X"}]})
    import core.config_store as cs
    zp = _mkzip(tmp_path, skills=[{"id": "X", "name": "pkg X"}, {"id": "Y", "name": "y"}])
    r = MP.import_pack(zp, conflict="skip")
    assert r["ok"], r
    skills = cs.load_config().get("custom_skills") or []
    by_id = {s["id"]: s for s in skills}
    assert set(by_id) == {"X", "Y"}
    assert by_id["X"]["name"] == "local X"
    # overwrite：整替为包内容
    r = MP.import_pack(zp, conflict="overwrite")
    assert r["ok"], r
    skills = cs.load_config().get("custom_skills") or []
    assert {s["id"]: s["name"] for s in skills} == {"X": "pkg X", "Y": "y"}


def test_import_config_skip_keeps_local_overwrite_replaces(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    home2 = str(tmp_path / "home2")
    monkeypatch.setattr(MP, "override_home", home2)
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch, {"wecom": {"cdp_port": 9222}})
    zp = _mkzip(tmp_path, config={"wecom": {"cdp_port": 1234, "data": "<HOME>/data"}})
    r = MP.import_pack(zp, conflict="skip")
    assert r["ok"], r
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    assert cfg["wecom"]["cdp_port"] == 9222, "skip：本地配置原样"
    r = MP.import_pack(zp, conflict="overwrite")
    assert r["ok"], r
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    assert cfg["wecom"]["cdp_port"] == 1234
    assert cfg["wecom"]["data"].startswith(home2), "<HOME> 应归一为当前机 home"
    baks = [x for x in os.listdir(str(ws)) if x.startswith("config.json.bak-")]
    assert baks, "config overwrite 前先备份"


def test_import_rejects_bad_pack(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch)
    assert MP.import_pack(str(tmp_path / "不存在.zip"))["ok"] is False
    txt = tmp_path / "x.txt"
    txt.write_text("hi", encoding="utf-8")
    assert MP.import_pack(str(txt))["ok"] is False, "非 zip 拒绝"
    r = MP.import_pack(_mkzip(tmp_path, manifest=False))
    assert r["ok"] is False and "manifest" in r["error"]
    r = MP.import_pack(_mkzip(tmp_path, tool="别的工具"))
    assert r["ok"] is False and "tool" in r["error"]


def test_zip_slip_rejected(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch)
    zp = _mkzip(tmp_path, kb={"课件/ok.md": "ok"},
               arcnames_extra=("knowledge_base/../evil.txt",))
    r = MP.import_pack(zp, conflict="skip")
    assert r["ok"], r
    assert not os.path.exists(str(ws / "evil.txt")), "zip-slip 必须被拦下"
    assert r["kb"]["copied"] == 1 and r["kb"]["skipped"] == 0


def test_kb_skip_and_overwrite_counts(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "知识库" / "课件").mkdir(parents=True)
    a = ws / "知识库" / "课件" / "a.md"
    a.write_text("local", encoding="utf-8")
    _ws(ws, monkeypatch)
    _cfg(ws, monkeypatch)
    zp = _mkzip(tmp_path, kb={"课件/a.md": "new", "课件/b.md": "b"})
    r = MP.import_pack(zp, conflict="skip")
    assert r["ok"], r
    assert r["kb"]["skipped"] == 1 and r["kb"]["copied"] == 1
    assert a.read_text(encoding="utf-8") == "local"
    assert (ws / "知识库" / "课件" / "b.md").read_text(encoding="utf-8") == "b"
    r = MP.import_pack(zp, conflict="overwrite")
    assert r["ok"], r
    assert r["kb"]["replaced"] == 2 and r["kb"]["copied"] == 0
    assert a.read_text(encoding="utf-8") == "new"
    baks = [x for x in os.listdir(str(ws / "知识库" / "课件"))
            if x.startswith("a.md.bak-")]
    assert baks, "知识库覆盖前逐文件备份"


def test_route_migration_validation(app_client):
    c, _ = app_client
    r = c.post("/api/migration/import", json={})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    r = c.post("/api/migration/import", json={"path": "x", "conflict": "别的"})
    assert r.status_code == 400


def test_route_migration_export_wired(app_client, tmp_path, monkeypatch):
    c, _ = app_client
    ws = tmp_path / "ws"
    ws.mkdir()
    _ws(ws, monkeypatch)
    monkeypatch.setattr(MP, "override_home", str(tmp_path / "home"))
    r = c.post("/api/migration/export")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert os.path.isfile(d["path"]) and "迁移包" in d["path"]
