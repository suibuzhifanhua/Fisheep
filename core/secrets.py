# -*- coding: utf-8 -*-
"""
core.secrets — 灵伴密钥保护模块（v1.0.63 拆分自 backend.py）
跨系统兜底策略：
  - Windows：DPAPI（当前用户可见，CryptProtectData / CryptUnprotectData）
  - 非 Windows：降级为弱保护（base64 混淆）并明确告警，不再静默降级
  - 任一路径失败：自动降级并记录日志，绝不因加密失败导致配置无法读写
v1.0.647 归位：原顶层 security.py → core/secrets.py（不叫 core/security.py——
v1.0.646 刚因与原 core/security.py 同名不同义改过名）。它只 import 三个标准库，
却被 core/config.py、core/config_store.py 反向依赖——归位后 core 包不再依赖顶层模块。
"""
import base64
import ctypes
import sys

_ENCRYPT_PREFIX = "enc:v1:"
_WEAK_PREFIX = "enc:weak:"
IS_WINDOWS = sys.platform == "win32"

# 敏感 key 命中提示词（与 backend 原 _SENSITIVE_KEY_HINTS 保持一致）
# v1.0.373 审查修复（P3-8）：补 private_key/secret_key/pwd 变体——原命中词漏掉
# 非标准命名，此类密钥会明文落盘 config.json
# v1.0.510 优化（P1-4）：补 bank_account/bank_name——个人报销 PII（银行账号/开户行）
# 原先不在加密名单，config.json 明文落盘；加入后 DPAPI 加密 + 工具参数日志脱敏
# （core/config._mask_args 共用本名单）自动覆盖。注意：name 故意不加——
# 模型配置/连接器里大量正常 "name" 字段会被误加密。
_SENSITIVE_KEY_HINTS = ("api_key", "apikey", "webhook", "password", "passwd", "pwd",
                        "token", "secret", "authorization", "cookie", "access_key",
                        "private_key", "secret_key", "client_secret",
                        "bank_account", "bank_name")

# v1.0.588（P1）：按「路径」而非「键名」判定的敏感项。reimburse.name 是报销人真实
# 姓名（PII，原先明文落 config.json 且随 dist 一起分发），但不能把 "name" 加进上面的
# 命中词——模型配置/连接器里大量正常 name 字段会被误加密。故仅在 reimburse 段内生效。
_SENSITIVE_PATHS = {("reimburse", "name")}

# DPAPI DATA_BLOB 结构（protect/unprotect 共用，定义一次）
if IS_WINDOWS:
    from ctypes import wintypes as _wt

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", _wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

# 默认静默；由 backend 模块注入 log_event，避免跨模块循环依赖
_log = lambda msg: None

# v1.0.660（审查修复 L4）：密钥保护强度状态（供设置页提示"未加密"）。
# 现状：非 Windows 平台、或 DPAPI 调用失败时，_protect_secret 静默降级成 base64 弱保护，
# 只有一行日志，用户完全看不出"我的密钥其实没加密"。此处把降级事实记成可查询状态，
# 由 API 暴露给设置页显示警示。强保护=DPAPI（当前用户+机器绑定，不可跨机拷贝）。
_ENC_STATE = {"strong": True, "reason": "", "degraded_count": 0, "last_reason": ""}


def encryption_status():
    """返回密钥保护强度状态。

    strong=True  → DPAPI 强保护（Windows 当前用户可见，跨机拷贝解不开）
    strong=False → 弱保护/明文降级（reason 说明原因），设置页应显示"未加密"警示
    """
    return {
        "strong": bool(_ENC_STATE["strong"]),
        "reason": str(_ENC_STATE["reason"]),
        "degraded_count": int(_ENC_STATE["degraded_count"]),
        "platform": sys.platform,
    }


def _mark_degraded(reason):
    """记录一次降级（弱保护）事实——供 encryption_status 查询。"""
    _ENC_STATE["strong"] = False
    _ENC_STATE["degraded_count"] = int(_ENC_STATE["degraded_count"]) + 1
    _ENC_STATE["last_reason"] = str(reason)
    if not _ENC_STATE["reason"]:
        _ENC_STATE["reason"] = str(reason)


def set_logger(fn):
    """注入日志函数（backend 的 log_event），用于加密/解密告警。"""
    global _log
    _log = fn if callable(fn) else (lambda m: None)


def _is_sensitive_key(key):
    k = str(key).lower()
    return any(h in k for h in _SENSITIVE_KEY_HINTS)


def _walk_secrets(obj, fn, _path=()):
    """递归遍历 dict/list，对敏感 key 的字符串值应用 fn。
    _path 为自配置根的键路径，用于 _SENSITIVE_PATHS 的路径级判定。

    已带 enc: 前缀的值一律跳过（v1.0.590）：解密失败时我们会原样保留密文，若保存前
    再套一层 _protect_secret 就变成 enc:v1:<enc:v1:...>，越滚越长且永远解不开。
    加密必须是幂等的。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = _path + (str(k).lower(),)
            if isinstance(v, (dict, list)):
                out[k] = _walk_secrets(v, fn, p)
            elif (_is_sensitive_key(k) or p in _SENSITIVE_PATHS) and isinstance(v, str) \
                    and v and not v.startswith("enc:"):
                out[k] = fn(v)
            else:
                out[k] = v
        return out
    if isinstance(obj, list):
        return [_walk_secrets(x, fn, _path) for x in obj]
    return obj


def _dpapi_protect(plain):
    """Windows DPAPI 加密（当前用户可见）。返回 base64 串。"""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data = plain.encode("utf-16-le")
    buf = ctypes.create_string_buffer(data)
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI CryptProtectData failed, err={ctypes.GetLastError()}")
    try:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(raw).decode("ascii")


def _dpapi_unprotect(b64):
    """Windows DPAPI 解密。"""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    raw = base64.b64decode(b64)
    buf = ctypes.create_string_buffer(raw)
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI CryptUnprotectData failed, err={ctypes.GetLastError()}")
    try:
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
    return out.decode("utf-16-le")


def _protect_secret(plain):
    """对单个敏感值加密；Windows 用 DPAPI，非 Windows 降级弱保护并明确告警。

    v1.0.660（审查修复 L4）：降级路径除日志外再记 _ENC_STATE（弱保护事实），
    设置页据此显示"未加密"警示——原先只有一行日志，用户看不出密钥其实没加密。
    """
    if IS_WINDOWS:
        try:
            out = _ENCRYPT_PREFIX + _dpapi_protect(plain)
            # 强保护成功：清掉"曾降级"标记（同一进程内 DPAPI 恢复可用即回到强保护）
            _ENC_STATE["strong"] = True
            return out
        except Exception as e:  # noqa: BLE001
            _log(f"[config] DPAPI 加密失败，降级为弱保护存储：{e}")
            _mark_degraded(f"DPAPI 加密失败：{e}")
    else:
        _log(f"[config] 当前系统({sys.platform})无 DPAPI，敏感字段将以弱保护(base64)存储，请注意保管配置文件")
        _mark_degraded(f"当前系统（{sys.platform}）不支持 DPAPI，敏感字段以弱保护（base64）存储")
    return _WEAK_PREFIX + base64.b64encode(plain.encode("utf-8")).decode("ascii")


def _unprotect_secret(val):
    """解密带 enc: 前缀的敏感值。

    ⚠️ 解密失败必须**原样保留密文**，绝不能返回空串。原因（v1.0.590 修掉我自己引入的
    数据丢失）：load_config 解密 → 运行期改配置 → save_config 再加密，是一条回写链；
    一旦解密失败把值变成 ""，`_walk_secrets` 会跳过空值不再加密，下一次落盘就把这个
    字段写成真正的空字符串——**唯一的一份密文被销毁**。而 DPAPI 绑定 Windows 用户+机器，
    配置文件跨机/跨用户拷过来时必然解不开（实测 0x8009002B），这是常见情形而非异常。
    保留密文的代价是运行期拿到一串 base64（表现为远端 401），但那可诊断、可恢复；
    写空是不可恢复的。两种失败都落一条可操作日志提示重填。"""
    if not isinstance(val, str) or not val.startswith("enc:"):
        return val
    try:
        if val.startswith(_ENCRYPT_PREFIX):
            return _dpapi_unprotect(val[len(_ENCRYPT_PREFIX):])
        if val.startswith(_WEAK_PREFIX):
            # v1.0.660（L4）：读到 enc:weak: 说明磁盘上这批密钥确实是弱保护存的
            # （可能来自非 Windows 机器或历史 DPAPI 失败）——标记状态让设置页能警示。
            _mark_degraded("配置中存在弱保护（base64）保存的敏感字段")
            return base64.b64decode(val[len(_WEAK_PREFIX):]).decode("utf-8")
        _log(f"[config] ⚠️ 未知加密格式（前缀 {val[:8]}…），按原值保留")
        return val
    except Exception as e:  # noqa: BLE001
        _log(f"[config] ⚠️ 敏感字段解密失败，已原样保留密文（不会丢失）；"
             f"DPAPI 绑定当前 Windows 用户与机器，配置文件跨机/跨用户拷贝过来必然解不开，"
             f"请在设置页重新填写该项。错误：{e}")
        # v1.0.756（评审 P2-2 字面补做）：解密失败不再只有一行日志——登记到
        # 首启迁移引导状态机，首页据此给出三选一引导，而不是静默 enc:weak: 降级。
        try:
            from core import migration_guide as _mg
            _mg.notice(val[:12])
        except Exception as _mg_err:  # noqa: BLE001
            _log(f"[config] 迁移引导登记失败（不影响密文保留）：{_mg_err}")
        return val
