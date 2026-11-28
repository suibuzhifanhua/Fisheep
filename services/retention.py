# -*- coding: utf-8 -*-
"""services.retention — 产物库保留策略（v1.0.788，P2-8）：超期备份**归档不删除**
=================================================================
口径（用户拍板）：
  · 配置项 security.backup_retention_days（0 = 关闭，默认关）；
  · 超过 N 天的 产物库/备份/<YYYYMMDD>/ 日期目录**整体移入** 产物库/备份归档/<YYYYMMDD>/
    ——只移不删：归档目录不受 _trim_backup 的体积上限管（那是"备份总上限"口径，
    归档是用户主动留存的长尾，误删没有回头路）；
  · 挂点选 backup_file 写路径（按日历天节流，每天至多跑一次），而不是 scheduler——
    scheduler 是用户自建 cron 任务框架，没有内部 housekeeping job 机制，
    写路径是备份唯一必经之路，挂那里零新增线程、零新增配置。

同名冲突（归档里已有同日目录）：逐文件合并，同名文件跳过并记 skipped（源文件保留，
不静默覆盖、不删除）。
"""
import datetime
import os
import re

_DAY_RE = re.compile(r"^\d{8}$")
# 节流键（日历天）：测试用 monkeypatch 重置
_last_run_day = None


def archive_expired(ws_root, days, now=None):
    """把 备份/ 下超期日期目录移入 备份归档/。返回 {moved, skipped, total_bytes}。永不抛。"""
    rep = {"moved": [], "skipped": [], "total_bytes": 0}
    try:
        days = int(days or 0)
        if days <= 0:
            return rep
        src_root = os.path.join(str(ws_root), "备份")
        if not os.path.isdir(src_root):
            return rep
        now = now or datetime.datetime.now()
        cutoff = now - datetime.timedelta(days=days)
        dst_root = os.path.join(str(ws_root), "备份归档")
        for d in os.listdir(src_root):
            if not _DAY_RE.match(d):
                continue
            dd = os.path.join(src_root, d)
            if not os.path.isdir(dd):
                continue
            try:
                day_dt = datetime.datetime.strptime(d, "%Y%m%d")
            except ValueError:
                continue
            if day_dt > cutoff:
                continue
            dst = os.path.join(dst_root, d)
            os.makedirs(dst, exist_ok=True)
            day_bytes = 0
            for fn in os.listdir(dd):
                sfp = os.path.join(dd, fn)
                if not os.path.isfile(sfp):
                    continue
                tfp = os.path.join(dst, fn)
                if os.path.exists(tfp):
                    rep["skipped"].append(d + "/" + fn)
                    continue
                os.replace(sfp, tfp)
                try:
                    day_bytes += os.path.getsize(tfp)
                except OSError:
                    pass   # silent-ok: 取不到大小时只少算，不影响归档本身
            rep["total_bytes"] += day_bytes
            # 移空了才删源目录；还有残留（冲突跳过的文件）则保留原目录如实可见
            try:
                if not os.listdir(dd):
                    os.rmdir(dd)
                    rep["moved"].append(d)
            except OSError:
                pass
    except Exception as e:   # noqa: BLE001
        try:
            from core.logging import log_event
            log_event("[retention] 归档超期备份失败（忽略）：%s: %s" % (type(e).__name__, str(e)[:160]))
        except Exception:   # noqa: BLE001
            pass
    return rep


def apply_retention(cfg, ws_root=None):
    """写路径挂钩：读 backup_retention_days（0=关），按日历天节流每天至多跑一次。
    返回 report 或 None（关闭/当天已跑/无超期目录也返回 report）。"""
    global _last_run_day
    try:
        from core.paths import workspace_root
        from security_ops import sec_cfg
        days = int((sec_cfg(cfg) or {}).get("backup_retention_days") or 0)
        if days <= 0:
            return None
        today = datetime.date.today().strftime("%Y%m%d")
        if _last_run_day == today:
            return None
        _last_run_day = today
        root = ws_root or workspace_root()
        rep = archive_expired(root, days)
        if rep["moved"]:
            from core.logging import log_event
            log_event("[retention] 超期备份已归档：%s（共 %d 字节）"
                       % ("、".join(rep["moved"]), rep["total_bytes"]))
        return rep
    except Exception:   # noqa: BLE001
        return None


def status(ws_root):
    """归档两侧概况（设置页展示用）：日期目录数 + 总字节。"""
    def _sum(root):
        n, b = 0, 0
        if os.path.isdir(root):
            for d in os.listdir(root):
                dd = os.path.join(root, d)
                if _DAY_RE.match(d) and os.path.isdir(dd):
                    n += 1
                    for fn in os.listdir(dd):
                        fp = os.path.join(dd, fn)
                        try:
                            if os.path.isfile(fp):
                                b += os.path.getsize(fp)
                        except OSError:
                            pass   # silent-ok: 单文件取大小失败只少算
        return n, b

    base = str(ws_root)
    nb, bb = _sum(os.path.join(base, "备份"))
    na, ba = _sum(os.path.join(base, "备份归档"))
    return {"backup_days": nb, "backup_bytes": bb,
            "archive_days": na, "archive_bytes": ba}
