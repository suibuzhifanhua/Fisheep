# -*- coding: utf-8 -*-
"""core.masking — 敏感信息脱敏的**唯一事实源**（v1.0.677 新建）。

为什么要有它（一次典型的"同一语义手写多份判据"事故）：

  · `core/config._mask_args`（工具参数日志）只按**键名**打码
    ——`{"command": "curl -u admin:s3cret ..."}` 的键名是 "command"，不在敏感
    键名单里，于是命令行里的口令**原样**写进 产物库/日志/companion.log 与
    产物库/对话记录/*.md。
  · `security_ops._mask_sensitive`（安全审计落盘）只按**值形态**打码
    ——能盖住 `password=xxx`，但只在这条链路上生效，工具参数日志那条链没有。

两份判据各管一段、互不知道对方存在 → 同一串口令走哪条路决定它明不明文。
本模块把两种口径合并成一个函数，两侧都改为调用它，判据只剩一份。

对外三个入口：
  · mask_sensitive(text)  —— 文本里的口令/token/认证头/长十六进制/长 base64
  · mask_args(args)       —— 工具参数：键名命中 + 字符串值再过一遍 mask_sensitive
  · is_sensitive_key(key) —— 键名判定（转发 core.secrets，避免两份名单漂移）

本模块只依赖标准库，不 import 任何本项目模块（core.secrets 是纯标准库实现，
可安全反向依赖），因此不会引入循环 import。
"""

import json
import re

# —— 值形态：键名 + 分隔符 + 值（password=xxx / "token": "xxx" / pwd:xxx）——
_SENSITIVE_KV_RE = re.compile(
    r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|secret[_-]?key|private[_-]?key|client[_-]?secret|auth[_-]?token|refresh[_-]?token"
    r"|session[_-]?key|credential)\b(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)")
# —— 认证头：Bearer / Basic + 凭证 ——
_SENSITIVE_AUTH_RE = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=-]{8,})")
# —— OpenAI 系密钥前缀 ——
_SENSITIVE_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
# —— 数据库客户端的 `-p<口令>` 形态（mysql -uroot -p123456）——
# 只认「客户端 + -p + 值」这一窄形态，不做通用 `-p` 匹配（psql -p 是端口、
# git -p 是 push、curl -p 是代理，通用匹配必然误伤）。
_SENSITIVE_CLI_RE = re.compile(
    r"(?i)\b(mysql|mysqldump|mariadb|mariadb-dump|psql|sqlcmd|mongosh)"
    r"([^\n]{0,80}?)(?<!\w)(-p)([^\s\"']{3,})")
# —— 超长十六进制 / base64 串（哈希、私钥体、签名）——
_SENSITIVE_HEX_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])")
_SENSITIVE_B64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")
# —— URL 里的 user:password@ 形态（curl -u / 连接串）——
_SENSITIVE_URLAUTH_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s/@]+)@")


def mask_sensitive(text):
    """把文本里的敏感片段替换成 ***。永不抛异常（失败就原样返回）。

    v1.0.660（M5）原实现保留在 security_ops：宁可漏打几个非常规写法，也不能把
    明文口令留在审计文件里；但也不能把普通命令糊掉——只认键名、认证头、固定前缀
    与超长十六进制/base64 串。
    v1.0.677 补 URL user:password@ 形态（连接串最常见的泄漏形态，原名单漏掉）。
    """
    s = str(text or "")
    if not s:
        return s
    try:
        s = _SENSITIVE_KV_RE.sub(lambda m: m.group(1) + m.group(2) + "***", s)
        s = _SENSITIVE_CLI_RE.sub(lambda m: m.group(1) + m.group(2) + "-p***", s)
        s = _SENSITIVE_AUTH_RE.sub(lambda m: m.group(1) + " ***", s)
        s = _SENSITIVE_SK_RE.sub("sk-***", s)
        s = _SENSITIVE_URLAUTH_RE.sub(lambda m: m.group(1) + ":***@", s)
        s = _SENSITIVE_HEX_RE.sub("***", s)
        s = _SENSITIVE_B64_RE.sub("***", s)
    except Exception:  # noqa: BLE001（脱敏失败就原样写，绝不因此中断日志）
        return str(text or "")
    return s


def is_sensitive_key(key):
    """键名是否敏感。转发 core.secrets 的名单，避免两份名单各自漂移。"""
    try:
        from core.secrets import _is_sensitive_key as _f
        return bool(_f(key))
    except Exception:  # noqa: BLE001（core.secrets 不可用时退回保守名单）
        k = str(key).lower()
        return any(h in k for h in ("password", "passwd", "pwd", "token", "secret",
                                    "api_key", "apikey", "access_key", "private_key",
                                    "secret_key", "client_secret", "authorization",
                                    "cookie", "webhook", "bank_account", "bank_name"))


def mask_args(args, limit=200):
    """工具参数脱敏：键名命中 → 打码；其余字符串值再过一遍 mask_sensitive。

    返回 JSON 字符串（调用方直接写日志）。这是 v1.0.677 的关键修复：
    旧实现只按键名打码，`pc_run` 的命令行里带的 `-u user:pass` /
    `password=xxx` 原样进日志与对话记录明文。
    """
    def walk(o):
        if isinstance(o, dict):
            return {k: ("******" if is_sensitive_key(k) else walk(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, str):
            return mask_sensitive(o)
        return o
    try:
        return json.dumps(walk(args or {}), ensure_ascii=False)[:limit]
    except Exception:  # noqa: BLE001（序列化失败不因脱敏而中断业务）
        return "******"
