# -*- coding: utf-8 -*-
"""services.update_check — 发版自检（v1.0.788，P0-1）
=================================================================
更新检查只走 GitHub Releases（用户确认口径：不查其他任何渠道）：
  · check_update()   —— 查公开 releases/latest（免 token，公开仓库），
    与本地当前版本（version.py）对比，回传是否可升级 + exe 下载地址/大小
    + 版本介绍摘要（release body）；
  · install_update() —— 一键升级：先对当前版本自动快照（record_version，
    登记表天然保留最近 3 版，版本管理页随时可回退）→ 下载最新 exe →
    大小与 API 声明校验 → 走 version_store.stage_exe_swap 的 staged
    替换（直接拷；被运行中进程占用则 .next + 标记，下次启动
    apply_pending_switch() 完成换 exe；重启生效）。

纪律（与 event_notify / version_store 一致）：
  · 全链路用户显式触发（设置页按钮），不自动轮询、不静默动源码
    （release 包只换 exe，本地源码树原样——升级≠回滚，回退仍走版本管理）；
  · 任何异常都回 ok:False + 人话原因，绝不抛；
  · 下载按字节数校验（对不上 API 声明即视为不完整，删暂存文件重来）。
"""
import os
import re
import threading

# 更新检查唯一目标：GitHub Releases（公开 API，免 token）。
_REPO = "suibuzhifanhua/Fisheep"
_LATEST_URL = "https://api.github.com/repos/%s/releases/latest" % _REPO
_UA = "Fisheep-update-check/1.0"

# 防重入：连点两次「安装」不能同时往同一个暂存文件里写
_INSTALL_LOCK = threading.Lock()


def _ver_tuple(v):
    """'v1.0.787' / '1.0.787' → (1, 0, 787)；解析不出 → None。"""
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", str(v or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _cmp_version(a, b):
    """a 更新 → 1；相同 → 0；a 更旧 → -1；任一侧解析不出 → None（不强下结论）。"""
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    if not ta or not tb:
        return None
    return (ta > tb) - (ta < tb)


def _gh_latest():
    """查 GitHub 公开 releases/latest。返回 (release dict | None, 原因 | "")。

    这里把「连不上 / 404 / 5xx / 解析失败」全部收成 (None, 人话原因)——
    调用方只管展示，不再各自处理异常形态。"""
    try:
        import requests
        r = requests.get(_LATEST_URL,
                        headers={"User-Agent": _UA,
                                 "Accept": "application/vnd.github+json"},
                        timeout=(8, 30))
    except Exception as e:   # noqa: BLE001
        return None, "连不上 GitHub（网络/代理问题）：%s" % str(e)[:120]
    if r.status_code == 404:
        return None, "GitHub 上没找到该仓库的 release（还没发版或仓库改名）"
    if r.status_code != 200:
        return None, "GitHub 返回 %d（限流或服务端异常，稍后再试）" % r.status_code
    try:
        d = r.json()
    except Exception:   # noqa: BLE001
        return None, "GitHub 返回内容无法解析"
    if not (isinstance(d, dict) and d.get("tag_name")):
        return None, "GitHub release 信息不完整"
    return d, ""


def _exe_asset(rel):
    """release 资产里取 exe（取第一个 .exe 附件；没有 → None）。"""
    for a in (rel.get("assets") or []):
        if isinstance(a, dict) and str(a.get("name") or "").endswith(".exe"):
            return {"name": a.get("name"), "url": a.get("browser_download_url"),
                     "size": a.get("size")}
    return None


def check_update():
    """本地当前版本 vs GitHub 最新 release。任何失败都回 ok:False，不抛。"""
    try:
        from services.version_store import list_versions
        cur = str(list_versions().get("current") or "").strip()
        rel, why = _gh_latest()
        if not rel:
            return {"ok": False, "error": why or "未找到 GitHub release"}
        latest = str(rel.get("tag_name") or "").lstrip("v").strip()
        cmp = _cmp_version(latest, cur)
        return {
            "ok": True,
            "current": cur,
            "latest": latest,
            "update_available": cmp == 1,
            "comparable": cmp is not None,
            "published_at": str(rel.get("published_at") or ""),
            "name": str(rel.get("name") or ""),
            # body 截 1500 字：前端只展示摘要，整篇 release notes 塞进 JSON 没意义
            "body": str(rel.get("body") or "")[:1500],
            "asset": _exe_asset(rel),
        }
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": "更新检查失败：%s" % str(e)[:160]}


def _download(url, dst, want_size):
    """流式下载。返回 (ok, 原因, 字节数)；失败删掉半截文件（不留垃圾）。"""
    got = 0
    ok, why = False, "下载失败"
    try:
        import requests
        with requests.get(url, stream=True, timeout=(15, 600),
                          headers={"User-Agent": _UA}) as r:
            if r.status_code != 200:
                why = "下载失败（GitHub 返回 %d）" % r.status_code
            else:
                with open(dst, "wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            got += len(chunk)
                if want_size is not None and got != int(want_size):
                    why = ("大小不符：下到 %d 字节，API 声明 %s——"
                           "不完整或文件不对，已放弃" % (got, want_size))
                else:
                    ok = True
    except Exception as e:   # noqa: BLE001
        why = "下载中断：%s" % str(e)[:160]
    if not ok:
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass   # silent-ok: 删不掉的半截文件只是 build 目录里一点垃圾，不影响正确性
    return ok, why, got


def install_update():
    """一键升级：查最新版 → 快照当前版 → 下载 exe（校验大小）→ staged 换 exe。

    快照用 version_store.record_version（按版本号去重、登记表保留最近 3 版）：
    升级后用户仍可走 /api/versions/switch 一键回退到升级前的版本。"""
    if not _INSTALL_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "已有升级正在安装，请稍候"}
    try:
        chk = check_update()
        if not chk.get("ok"):
            return {"ok": False, "error": chk.get("error") or "查询最新版本失败"}
        if not chk.get("update_available"):
            if chk.get("latest") and chk.get("latest") == chk.get("current"):
                return {"ok": False, "error": "当前 v%s 已是最新版本" % chk["current"]}
            return {"ok": False,
                    "error": "最新 v%s 不比当前 v%s 新（只升级不降级，"
                             "回退请用版本管理页）" % (chk.get("latest"), chk.get("current"))}
        asset = chk.get("asset") or {}
        url = str(asset.get("url") or "")
        if not url:
            return {"ok": False, "error": "该 release 没有 exe 附件，无法安装"}
        from services import version_store as vs
        # 1) 下载到暂存目录（流式 + 大小校验）——下载不过关就不许动版本登记表
        staged_dir = os.path.join(vs._app_root(), "build", "_update")
        os.makedirs(staged_dir, exist_ok=True)
        staged = os.path.join(staged_dir, str(asset.get("name") or "update.exe"))
        ok_dl, why_dl, _got = _download(url, staged, asset.get("size"))
        if not ok_dl:
            return {"ok": False, "error": why_dl}
        # 2) 当前版本自动快照（登记去重、保留最近 3 版；失败必须挡——没快照就没回退底）
        try:
            vs.record_version(note="升级前自动快照")
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "error": "当前版本自动快照失败：%s" % str(e)[:120]}
        # 3) staged 换 exe（直接拷 / 被占用则排队下次启动）
        ok, note = vs.stage_exe_swap(staged, chk["latest"])
        if not ok:
            return {"ok": False, "error": "替换程序文件失败：%s" % note}
        try:
            os.remove(staged)
        except OSError:
            pass   # silent-ok: 同上，暂存残留只是垃圾
        return {"ok": True, "version": chk["latest"], "detail": note}
    finally:
        _INSTALL_LOCK.release()
