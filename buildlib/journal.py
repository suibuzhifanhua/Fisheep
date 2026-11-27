# -*- coding: utf-8 -*-
"""buildlib.journal — 构建 journal + 空号自愈（v1.0.663，B7 硬杀洞补丁）。

背景：v1.0.661 空号事故——build.py 进程在 PyInstaller「Build complete!」之后被
外部硬杀（TerminateProcess 级别），finally 的版本回滚根本没机会执行，版本号 +1
留在 version.py 里却无合格产物。finally 只能覆盖**正常**异常路径；硬杀、断电、
任务管理器结束进程都绕过它。三层补丁：
  ① 行缓冲（build.py 顶部 reconfigure）——硬杀不再丢整块缓冲的构建输出；
  ② 本模块 journal——每个构建阶段 append + flush + fsync 一条记录到
     build/build_journal.log，硬杀后仍能从最后一条完整记录还原「走到哪一步」；
     v1.0.663（D2）：bump 之前先记一条 `bump_intent`（ver/frm 已填）——原实现
     `bump_version()` 先改 version.py、再记 `bump`，中间被硬杀时 journal 还停在上
     一次的终态记录，空号无人认领；intent 先落盘后，窗口内已是非终态可自愈形态；
  ③ self_heal()——下次构建启动时检测上次是否留下空号，是则回滚并留痕。

journal 失败永不阻塞构建（与日志系统同一口径：观测手段坏了不能把构建判死）。
"""
import json
import os
import time

from buildlib.env import BASE

JOURNAL = os.path.join(BASE, "build", "build_journal.log")

# 这些 stage 出现时上一次构建处于「已收尾」状态：
#   done        构建全程走通（含版本登记）
#   recorded    版本已登记（done 的前一步，硬杀可能恰好卡在这两者之间）
#   rolled_back 正常失败路径 finally 已回滚——version.py 已退回，无需再动
#   self_healed 上次自愈刚执行过——留痕防止反复误判
# 注意：`bump_intent` 与 `bump` **故意不在**终态集合里——它们就是「构建走到一半」
# 的证据；若写完 intent 就退出（bump 未发生），self_heal 判据②（版本号已等于 ver）
# 不成立，不会误回滚，无需为它单列终态。
_TERMINAL = {"done", "recorded", "rolled_back", "self_healed"}


def stage(name, **kv):
    """append 一条 journal 记录（立即 flush + fsync）。"""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": name}
    rec.update(kv)
    try:
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:  # noqa: BLE001
        # 静默 except-pass 会被 BARE_EXCEPT 棘轮拦——留痕（构建输出已行缓冲，看得见）。
        print(f"[build] 警告：journal 写入失败（不阻塞构建）：{e}")


def last_record():
    """journal 最后一条完整记录；无 journal / 尾部行损坏时返回 None。"""
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            return None
        rec = json.loads(lines[-1])
        return rec if isinstance(rec, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _registered(version):
    """version.py 的版本号是否已登记进 versions.json（产物库/版本/）。"""
    try:
        p = os.path.join(BASE, "产物库", "版本", "versions.json")
        recs = json.load(open(p, encoding="utf-8"))
        recs = recs if isinstance(recs, list) else (recs or {}).get("versions", [])
        return any(str(r.get("version")) == str(version) for r in recs
                   if isinstance(r, dict))
    except Exception:  # noqa: BLE001
        return False


def self_heal():
    """本次构建启动时执行：上次构建若被硬杀留下空号，回滚版本自愈。

    判据三条同时成立才动（缺一不动，宁可漏修不可误杀）：
      ① journal 最后一条 stage 非终态（done/recorded/rolled_back/self_healed）
         ——正常失败会由 finally 补一条 rolled_back，到这里说明那次收尾没走完；
         v1.0.663（D2）后 `bump_intent` 也属于需要检查的非终态：intent 已落盘而
         version.py 尚未 bump 时判据②不成立（不会误回滚），bump 后被杀则能还原；
      ② version.py 当前版本 == 那条记录 bump 上去的版本——正常失败路径 finally
         已把版本号退回，等式不成立即无空号；
      ③ 该版本未登记进 versions.json——登记过的版本是既有合格产物，不是空号。
    三条齐了就是「bump 过、无合格产物、回滚没执行」的空号形态（661 事故形态），
    回滚到 journal 记录的 from 并留 self_healed 痕。返回 True=本次执行了自愈。
    """
    last = last_record()
    if not last or last.get("stage") in _TERMINAL:
        return False
    ver, frm = last.get("ver"), last.get("frm")
    if not ver or not frm:
        return False
    try:
        from buildlib.versioning import current_version
        if current_version() != str(ver) or _registered(ver):
            return False
    except Exception:  # noqa: BLE001
        return False
    from buildlib.orchestration import rollback_version
    print(f"[build] ⚠️ 检测到上次构建未完成（v{ver} 未登记，停在 {last.get('stage')} 之后——"
          f"疑似进程被硬杀、finally 回滚未执行），版本号空号自愈：回滚至 v{frm}")
    if rollback_version(str(frm)):
        stage("self_healed", ver=ver, frm=frm, prev_stage=last.get("stage"))
        return True
    return False
