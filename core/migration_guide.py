# -*- coding: utf-8 -*-
"""core.migration_guide — 首次启动配置解密失败的迁移引导（评审 P2-2，v1.0.756）。

背景：DPAPI 绑定「用户 + 机器」，配置跨机拷过来后 `enc:` 值必然解不开。
旧行为只有一行日志、静默 `enc:weak:` 降级，用户不知道凭据已失效。
现在 `core/secrets.py::_unprotect_secret` 解密失败时调用 notice()，本模块置
pending 状态；`backend.py::index()` 检测到 pending 就把首页换成下面的独立
三选一页面（自带内联表单，不引用 js/*.js，与首页的单一 ESM 入口解耦）。

三个选项（docs/migration.md 第 2 节）：
  ① import_bundle  从迁移包导入（支持 v2 口令加密包）
  ② default_start  备份原配置后以默认配置启动（凭据重填）
  ③ weak_explicit  显式勾选确认后使用弱保护——不勾选一律拒绝，绝不静默降级
任一分支都**先备份**原 config.json 到 产物库/_migration_backup/<时间戳>/。
"""
import io
import json
import os
import shutil
import threading
import time

from core import paths
from core.logging import log_event

BACKUP_DIRNAME = "_migration_backup"
CHOICES = ("import_bundle", "default_start", "weak_explicit")
REENTER = "<re-enter-credential>"

_STATE = {
    "pending": False,
    "failed_values": [],     # 只存前缀提示，绝不存密文
    "noticed_at": None,
    "backup_dir": None,
    "resolved": None,
    "resolved_at": None,
}
_lock = threading.Lock()


def notice(value_hint=""):
    """解密失败时调用：写明确日志 + 置 pending（不再只留一行日志静默降级）。"""
    with _lock:
        hint = str(value_hint or "")[:12]
        if hint and hint not in _STATE["failed_values"]:
            _STATE["failed_values"].append(hint)
        _STATE["pending"] = True
        _STATE["noticed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _STATE["resolved"] = None
    log_event("[migration] 配置解密失败（疑似换机/换用户），需用户选择迁移方式")
    return status()


def status():
    """返回状态快照（副本，调用方改动不污染内部状态）。"""
    with _lock:
        return dict(_STATE, failed_values=list(_STATE["failed_values"]))


def reset():
    """清状态。生产由 resolve() 成功收尾调用；测试 fixture 也用它做隔离。"""
    with _lock:
        _STATE.update({"pending": False, "failed_values": [],
                       "backup_dir": None, "resolved": None,
                       "resolved_at": None, "noticed_at": None})


def backup_config():
    """三选一处置前先备份原 config.json；返回备份目录，文件不存在返回 None。"""
    root = paths.workspace_root()
    src = os.path.join(root, "config.json")
    if not os.path.isfile(src):
        return None
    dst_dir = os.path.join(root, BACKUP_DIRNAME,
                           time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dst_dir, "config.json"))
    log_event("[migration] 已备份原配置 -> %s" % dst_dir)
    return dst_dir


def _strip_undecryptable(cfg):
    """把解不开的 enc: 值换成占位（保留键结构，用户后续重填凭据）。"""
    def _walk(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if isinstance(v, str) and v.startswith("enc:"):
                    o[k] = REENTER
                else:
                    _walk(v)
        elif isinstance(o, list):
            for x in o:
                _walk(x)
    _walk(cfg)
    return cfg


def _read_config():
    p = os.path.join(paths.workspace_root(), "config.json")
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_config(cfg):
    p = os.path.join(paths.workspace_root(), "config.json")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
    try:
        from core import config_store as _cs
        _cs._invalidate_cfg_cache()
    except Exception as e:  # noqa: BLE001
        log_event("[migration] 配置缓存作废失败（重启后自动生效）：%s" % e)


def resolve(choice, bundle_path=None, password=None, confirm_weak=False):
    """三选一处置，返回 (ok, message)。任何分支先备份；③ 必须显式勾选。"""
    if choice not in CHOICES:
        return False, "未知处置方式：%s" % choice
    if choice == "weak_explicit" and not confirm_weak:
        return False, "使用弱保护必须显式勾选确认（不静默降级）"
    try:
        backup = backup_config()
        if choice == "import_bundle":
            if not bundle_path or not os.path.isfile(bundle_path):
                return False, "迁移包路径不存在：%s" % bundle_path
            from core import profile_bundle as PB
            PB.apply_bundle(bundle_path, password=password)
            msg = "已从迁移包导入"
        elif choice == "default_start":
            _write_config(_strip_undecryptable(_read_config()))
            msg = "已备份原配置并以默认配置启动（凭据需重填）"
        else:
            _write_config(_strip_undecryptable(_read_config()))
            log_event("[migration] 用户显式确认使用弱保护（已备份原配置）")
            msg = "已按显式确认改用弱保护（凭据需重填）"
    except Exception as e:  # noqa: BLE001
        return False, "处置失败（原配置已备份）：%s: %s" % (type(e).__name__, e)
    with _lock:
        _STATE["pending"] = False
        _STATE["resolved"] = choice
        _STATE["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _STATE["backup_dir"] = backup
    return True, msg


MIGRATION_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>首次启动 · 配置迁移</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f5f6f8;color:#222;
     margin:0;padding:40px;display:flex;justify-content:center}
.card{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);
      max-width:680px;width:100%;padding:32px}
h1{font-size:22px;margin:0 0 8px}h2{font-size:16px;margin:24px 0 10px}
p{line-height:1.7;color:#444}.warn{background:#fff7e6;border-left:4px solid #e6a23c;
     padding:12px 16px;border-radius:4px;margin:16px 0}
label{display:block;margin:8px 0;font-size:14px}
input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:8px 10px;
     border:1px solid #d0d3d8;border-radius:6px;font-size:14px}
button{display:block;width:100%;margin:10px 0;padding:11px;border:0;border-radius:6px;
     background:#2f6feb;color:#fff;font-size:15px;cursor:pointer}
button.secondary{background:#eef1f6;color:#333}
small{color:#777}
</style></head><body><div class="card">
<h1>需要确认配置迁移方式</h1>
<p class="warn">{{STATUS_LINE}}</p>
<p>配置文件里的凭据由 Windows DPAPI 加密，绑定当前用户与机器。换机或换用户后
无法解密，旧版本会静默降级为弱保护，你可能不知道凭据已经失效。请选一种方式：</p>

<h2>① 从迁移包导入（推荐）</h2>
<label>迁移包路径 <input type="text" id="bundle_path" placeholder="D:\\profile.enc"></label>
<label>包口令（明文包可留空） <input type="password" id="password"></label>
<button onclick="submitChoice('import_bundle')">导入并重启界面</button>

<h2>② 保留副本后以默认配置启动</h2>
<p><small>原配置会先备份，解不开的凭据字段清空，之后在设置页重新填写。</small></p>
<button class="secondary" onclick="submitChoice('default_start')">备份并以默认配置启动</button>

<h2>③ 明确知晓风险后使用弱保护</h2>
<label><input type="checkbox" id="confirm_weak"> 我已知晓弱保护不等于加密，凭据将以可逆编码保存</label>
<button class="secondary" onclick="submitChoice('weak_explicit')">确认使用弱保护</button>
</div>
<script>
function submitChoice(c){
  var p={choice:c};
  if(c==='import_bundle'){
    p.bundle_path=document.getElementById('bundle_path').value;
    p.password=document.getElementById('password').value;
  }
  if(c==='weak_explicit'){
    p.confirm_weak=document.getElementById('confirm_weak').checked;
    if(!p.confirm_weak){alert('必须先勾选确认，不能静默降级');return;}
  }
  fetch('/api/migration/resolve',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})
    .then(function(r){return r.json()}).then(function(d){
      if(d.ok){location.href='/';}else{alert(d.message||d.error||'处置失败');}
    }).catch(function(e){alert('请求失败：'+e)});
}
</script></body></html>
"""


def render_page():
    """把当前状态摘要注入独立引导页。"""
    s = status()
    line = ("检测到 %d 处配置解密失败（疑似换机 / 换用户）。"
            "请先选择迁移方式，原配置会自动备份。" % len(s["failed_values"]))
    return MIGRATION_PAGE.replace("{{STATUS_LINE}}", line)
