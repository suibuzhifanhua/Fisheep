# -*- coding: utf-8 -*-
"""services.migration_pack — 迁移包（v1.0.788，P1-6）：配置/经验/技能/知识库 导出导入
=================================================================
打包结构（zip，落 产物库/系统/迁移包/——系统/ 段不在文件树，与待审批队列同级防护）：
  manifest.json       {tool, format, version, exported_at, home}
  config.json         配置快照（绝对路径归一：源机 home 前缀 → <HOME>，防换机后死路径）
  experiences.json    经验库原始列表
  custom_skills.json  技能列表
  knowledge_base/     知识库目录整树

导入冲突两种口径（用户拍板二选一，不静默）：
  skip      已有数据优先；只写入本地缺的条目/文件
  overwrite 覆盖——覆盖前一律先做时间戳备份（与 v1.0.782 覆盖前自动备份同口径，
             备份保留最近 3 份）；知识库逐文件备份后替换

纪律：
  · zip-slip 防护：knowledge_base/ 下的相对路径一律剥 .. / 拒绝对路；
  · 任何单节失败记入 report 不中断整包（能救多少救多少，如实汇报）；
  · 全程不碰模型凭据以外的系统文件；导出包含用户自己的配置（含 enc: 加密键），
    只给本用户跨机迁移用。
"""
import json
import os
import shutil
import time
import zipfile

_TOOL = "fisheep-migration"
_FORMAT = 1
_HOME_TAG = "<HOME>"
_BACKUP_KEEP = 3   # 与 version_store._KEEP 同数量级：留 3 份回退底
override_home = None   # 测试注入口：固定归一化基准 home（生产恒 None → expanduser("~")）


def _home_dir():
    """归一化基准：源机 home 目录。模块属性 override_home 供测试注入固定路径。"""
    import services.migration_pack as m
    ov = getattr(m, "override_home", None)
    return str(ov) if ov else os.path.expanduser("~")


def _normalize(obj, home):
    """把字符串里的 home 前缀替换成 <HOME>（递归 dict/list/str）。
    覆盖 4 种形态：反斜杠/正斜杠/带前导斜杠——配置里 JSON 存的多是正斜杠，
    只认 os.sep 一种会把换机后最关键的绝对路径漏归一。"""
    if isinstance(obj, str):
        out = obj
        # 长变体（带前导斜杠）优先——短变体先跑会把 /C:/... 留成 /<HOME>/
        variants = dict.fromkeys([home, home.replace(os.sep, "/"),
                                  "/" + home.replace(os.sep, "/")])
        for v in sorted(variants, key=len, reverse=True):
            if v:
                out = out.replace(v, _HOME_TAG)
        return out
    if isinstance(obj, dict):
        return {k: _normalize(v, home) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v, home) for v in obj]
    return obj


def _denormalize(obj):
    """<HOME> → 当前机 home（导入侧）。"""
    home = _home_dir()
    if isinstance(obj, str):
        return obj.replace(_HOME_TAG, home)
    if isinstance(obj, dict):
        return {k: _denormalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_denormalize(v) for v in obj]
    return obj


def _backup_once(p):
    """覆盖前备份：p → p.bak-<ts>，保留最近 _BACKUP_KEEP 份。返回备份路径（无则 ""）。"""
    if not os.path.isfile(p):
        return ""
    ts = time.strftime("%Y%m%d%H%M%S")
    bak = "%s.bak-%s" % (p, ts)
    try:
        shutil.copy2(p, bak)
    except Exception:   # noqa: BLE001
        return ""
    olds = sorted(x for x in os.listdir(os.path.dirname(p))
                  if x.startswith(os.path.basename(p) + ".bak-"))
    for o in olds[:-_BACKUP_KEEP]:
        try:
            os.remove(os.path.join(os.path.dirname(p), o))
        except OSError:
            pass   # silent-ok: 旧备份删不掉只是多占空间
    return bak


def _zip_rel_path(arcname):
    """zip-slip 防护：knowledge_base/ 内的相对路径。非法（绝对路/..）→ None。"""
    rel = str(arcname or "").replace("\\", "/")
    if not rel.startswith("knowledge_base/"):
        return None
    rel = rel[len("knowledge_base/"):]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    return rel


def export_pack():
    """导出迁移包。返回 {ok, path, size_kb, items:{...}} 或 {ok:False, error}。"""
    try:
        from core.config_store import load_config, _load_experiences
        from core.paths import workspace_root
        from version import get as vget
        home = _home_dir()
        cfg = load_config() or {}
        cfg_norm = _normalize(cfg, home)
        exps = _load_experiences()
        skills = cfg.get("custom_skills") or []
        kb_root = os.path.join(workspace_root(), "知识库")
        kb_files = []
        if os.path.isdir(kb_root):
            for dp, _dn, fns in os.walk(kb_root):
                for fn in fns:
                    kb_files.append(os.path.relpath(os.path.join(dp, fn), kb_root))
        out_dir = os.path.join(workspace_root(), "系统", "迁移包")
        os.makedirs(out_dir, exist_ok=True)
        zp = os.path.join(out_dir, "Fisheep迁移包_v%s_%s.zip" % (
            vget(), time.strftime("%Y%m%d_%H%M%S")))
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps({
                "tool": _TOOL, "format": _FORMAT, "version": vget(),
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "home": home,
            }, ensure_ascii=False))
            z.writestr("config.json", json.dumps(cfg_norm, ensure_ascii=False, indent=1))
            z.writestr("experiences.json",
                       json.dumps(exps, ensure_ascii=False, indent=1))
            z.writestr("custom_skills.json",
                       json.dumps(skills, ensure_ascii=False, indent=1))
            for rel in kb_files:
                z.write(os.path.join(kb_root, rel), "knowledge_base/" + rel.replace(os.sep, "/"))
        size_kb = os.path.getsize(zp) // 1024
        return {"ok": True, "path": zp, "size_kb": size_kb,
                "items": {"config": "已归一化", "experiences": len(exps),
                          "custom_skills": len(skills), "kb_files": len(kb_files)}}
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": "导出失败：%s: %s" % (type(e).__name__, str(e)[:160])}


def _import_config(z, report):
    """config 节：skip=已存在即跳过；overwrite=备份后整体替换。
    路径走 config_store 的 config_path 别名（与 load/update 同一函数对象，
    生产行为一致；测试 patch config_store 别名即可整条链改道——
    直接 import core.config 会绕过测试 patch，误写真实配置）。"""
    from core.config_store import config_path
    p = config_path()
    data = None
    if "config.json" in z.namelist():
        try:
            data = json.loads(z.read("config.json").decode("utf-8"))
        except Exception:   # noqa: BLE001
            report["sections"]["config"] = "config.json 解析失败，跳过"
            return
    data = _denormalize(data or {})
    if not isinstance(data, dict):
        report["sections"]["config"] = "格式不符，跳过"
        return
    if report["conflict"] == "skip" and os.path.isfile(p):
        report["sections"]["config"] = "skip（本地已有配置）"
        return
    if os.path.isfile(p):
        bak = _backup_once(p)
        if bak:
            report["backed_up"].append(bak)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    try:
        import core.config_store as cs
        cs._CFG_CACHE["cfg"] = None
        cs._CFG_CACHE["mtime"] = None
    except Exception:   # noqa: BLE001
        pass   # silent-ok: 缓存失效失败只影响下次读缓存的新鲜度
    report["sections"]["config"] = "overwrite（已备份）" if report["backed_up"] else "overwrite（本地原无配置）"


def _import_list_section(z, arcname, report, name, get_items, set_items, backup_path=None):
    """experiences/custom_skills 共用的列表节逻辑：skip=按 id 合并补缺，overwrite=备份后整替。

    backup_path：可备份的落盘路径 getter（config 内的节没有独立文件，传 None）。"""
    if arcname not in z.namelist():
        report["sections"][name] = "包内无此节"
        return
    try:
        items = json.loads(z.read(arcname).decode("utf-8"))
    except Exception:   # noqa: BLE001
        report["sections"][name] = "解析失败，跳过"
        return
    if not isinstance(items, list):
        report["sections"][name] = "格式不符（非列表），跳过"
        return
    if report["conflict"] == "skip":
        cur = get_items() or []
        have = {str(x.get("id")) for x in cur if isinstance(x, dict)}
        add = [i for i in items if isinstance(i, dict) and str(i.get("id")) not in have]
        if not add:
            report["sections"][name] = "skip（无新增条目，共 %d 条已在本地）" % len(have)
            return
        set_items(cur + add)
        report["sections"][name] = "merged（新增 %d / 共 %d）" % (len(add), len(cur) + len(add))
        return
    if backup_path:
        bak = _backup_once(backup_path())
        if bak:
            report["backed_up"].append(bak)
    set_items(_denormalize(items))
    report["sections"][name] = "overwrite（%d 条）" % len(items)


def import_pack(path, conflict="skip"):
    """导入迁移包。conflict ∈ skip/overwrite。返回 {ok, sections, backed_up, kb:{...}}。"""
    report = {"conflict": conflict, "sections": {}, "backed_up": []}
    try:
        zp = str(path or "").strip()
        if not zipfile.is_zipfile(zp):
            return {"ok": False, "error": "不是有效的 zip 迁移包：%s" % zp[:120]}
        with zipfile.ZipFile(zp) as z:
            try:
                man = json.loads(z.read("manifest.json").decode("utf-8"))
            except Exception:   # noqa: BLE001
                return {"ok": False, "error": "包内缺 manifest.json（不是本工具打的包？）"}
            if man.get("tool") != _TOOL:
                return {"ok": False, "error": "manifest.tool 不符（%s），拒绝导入" % man.get("tool")}
            from core.paths import workspace_root
            kb_root = os.path.join(workspace_root(), "知识库")

            def _exp_get():
                from core.config_store import _load_experiences
                return _load_experiences()

            def _exp_set(items):
                from core.config_store import _save_experiences
                _save_experiences(items)

            def _exp_path():
                from core.config_store import _experiences_path
                return _experiences_path()

            def _skill_get():
                from core.config_store import load_config
                return (load_config() or {}).get("custom_skills") or []

            def _skill_set(items):
                from core.config_store import update_config
                update_config(lambda c: (c.__setitem__("custom_skills", items), True)[1])

            _import_config(z, report)
            _import_list_section(z, "experiences.json", report, "experiences",
                                 _exp_get, _exp_set, backup_path=_exp_path)
            _import_list_section(z, "custom_skills.json", report, "custom_skills",
                                 _skill_get, _skill_set)

            kb = {"copied": 0, "skipped": 0, "replaced": 0}
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                rel = _zip_rel_path(zi.filename)
                if not rel:
                    continue
                dest = os.path.join(kb_root, *rel.split("/"))
                d = os.path.dirname(dest)
                if d and not os.path.isdir(d):
                    os.makedirs(d, exist_ok=True)
                if os.path.isfile(dest):
                    if conflict == "skip":
                        kb["skipped"] += 1
                        continue
                    bak = _backup_once(dest)
                    if bak:
                        report["backed_up"].append(bak)
                    kb["replaced"] += 1
                else:
                    kb["copied"] += 1
                with z.open(zi) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            report["kb"] = kb
        return {"ok": True, **report}
    except Exception as e:   # noqa: BLE001
        report["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return {"ok": False, "error": report["error"]}
