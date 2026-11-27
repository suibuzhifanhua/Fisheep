# -*- coding: utf-8 -*-
"""core.profile_bundle — 配置 / 技能 / 经验库 / 知识库 的迁移包（评审 P2-2）。

换机场景的痛点：DPAPI 绑定「用户 + 机器」，`config.json` 里 `enc:` 值到新机
解不开，旧行为是静默降级 `enc:weak:`；`core/relocate.py` 只解决目录归位，
`tools/import_profile.py` 从未进产品。本模块提供**产品内可用**的导出-导入核心：

  · 默认脱敏：token / 密钥 / 口令 / cookie 等键的值不进包（保留键结构）；
  · **口令加密（v1.0.756）**：`--password` 走 PBKDF2-SHA256(200k) + Fernet，
    产出 v2 加密容器；缺 `cryptography` 时 **fail-closed 拒绝导出**，绝不静默
    降级成明文（这正是 D9 登记里「自己实现弱加密比不加密更危险」的反面）；
  · 不传口令仍产出 v1 **脱敏明文 zip**（兼容历史包），CLI 会明确告警；
  · manifest 含每条文件的 sha256，导入前逐条校验，篡改即拒；
  · 导入前把将被覆盖的文件备份到 `产物库/_bundle_backup/<时间戳>/`；
  · `--dry-run` 只报告不落盘；路径穿越（`../`）一律拒。

DPAPI 绑定的 `enc:` 值跨机本就不可用，导出时按脱敏键处理，导入后由用户在新机
重新填一次凭据（迁移引导见 `docs/migration.md`）。

CLI: python -m core.profile_bundle export|inspect|import <path> \
        [--include-secrets] [--password PWD] [--dry-run] [--no-backup]
"""
import base64
import datetime
import hashlib
import io
import json
import os
import shutil
import time
import zipfile

from core import paths

FORMAT = "fisheep-profile-bundle/1"           # v1：脱敏明文 zip（兼容历史）
FORMAT_ENCRYPTED = "fisheep-profile-bundle/2"  # v2：口令加密容器
MANIFEST_NAME = "manifest.json"
BACKUP_DIRNAME = "_bundle_backup"
PAYLOAD_PREFIX = "payload/"

# v2 外层容器 = MAGIC + 16 字节随机 salt + Fernet token（加密的是整份明文 zip 字节）
MAGIC = b"FISHEEPPB2\n"
SALT_BYTES = 16
KDF_ITERATIONS = 200000

# (名称, 类型, 相对产物库的路径)
ITEMS = (
    ("config", "file", "config.json"),
    ("experiences", "file", "experiences.json"),
    ("knowledge", "dir", "知识库"),
    ("skills", "dir", "技能"),
)

_SENSITIVE_PARTS = ("token", "secret", "password", "api_key", "apikey",
                    "credential", "private_key", "cookie", "access_key")
REDACTED = "<redacted-by-profile-bundle>"


class BundleError(Exception):
    """格式/完整性/路径/口令/依赖不合法——一律 fail-closed，不做部分导入。"""


def _root():
    return paths.workspace_root()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _redact(obj):
    """递归脱敏：命中敏感键的值替换占位串，键结构保持不变。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            low = str(k).lower()
            if any(p in low for p in _SENSITIVE_PARTS):
                out[k] = REDACTED
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _desensitize_config(data):
    """配置脱敏；非 JSON / 非 UTF-8 时原样返回（不猜格式，也不猜加密容器）。"""
    try:
        return json.dumps(_redact(json.loads(data.decode("utf-8"))),
                          ensure_ascii=False, indent=2).encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return data


def _version():
    try:
        from version import get
        return get()
    except Exception:  # noqa: BLE001  silent-ok: 版本读不到不影响迁移包
        return "unknown"


def _require_crypto():
    """加密包必须有 cryptography；缺失时 fail-closed，绝不静默降级为明文 zip。

    返回导入的三元组（顺带让 pyflakes 看到名字被使用，不产生 F401 噪声）。
    """
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as e:
        raise BundleError(
            "加密迁移包需要 cryptography（缺失时 fail-closed，不降级为明文）：%s；"
            "请安装 cryptography==46.0.3 后重试，或改用不带口令的脱敏明文包" % e)
    return Fernet, hashes, PBKDF2HMAC


def _derive_key(password, salt):
    """PBKDF2-HMAC-SHA256 → 32 字节 → urlsafe-base64（Fernet key 格式）。"""
    _fernet, hashes, PBKDF2HMAC = _require_crypto()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=KDF_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _encrypt_container(plain_zip_bytes, password):
    Fernet, _hashes, _pbkdf2 = _require_crypto()
    salt = os.urandom(SALT_BYTES)
    token = Fernet(_derive_key(password, salt)).encrypt(plain_zip_bytes)
    return MAGIC + salt + token


def _decrypt_container(raw, password):
    Fernet, _hashes, _pbkdf2 = _require_crypto()
    if not password:
        raise BundleError("该迁移包已加密，需提供口令（--password / password=）")
    body = raw[len(MAGIC):]
    if len(body) < SALT_BYTES + 32:
        raise BundleError("加密包结构损坏（长度不足）")
    salt, token = body[:SALT_BYTES], body[SALT_BYTES:]
    try:
        from cryptography.fernet import InvalidToken
        return Fernet(_derive_key(password, salt)).decrypt(token)
    except InvalidToken:
        raise BundleError("口令错误或加密包已损坏（Fernet 完整性校验失败）")


def _build_plain_zip(target, desensitize, extra=None):
    """写一份明文 zip 到 target（路径或 file object），返回 manifest dict。"""
    root = _root()
    manifest = {"format": FORMAT,
                "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "app_version": _version(),
                "desensitized": bool(desensitize),
                "files": []}
    if extra:
        manifest.update(extra)

    def _add(z, name, entry, data):
        z.writestr(entry, data)
        manifest["files"].append({
            "name": name, "entry": entry,
            "sha256": _sha256(data), "size": len(data),
        })

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name, kind, rel in ITEMS:
            full = os.path.join(root, rel)
            if kind == "file":
                if not os.path.isfile(full):
                    continue
                with io.open(full, "rb") as f:
                    data = f.read()
                if desensitize and name == "config":
                    data = _desensitize_config(data)
                _add(z, name, PAYLOAD_PREFIX + rel.replace("\\", "/"), data)
            else:
                if not os.path.isdir(full):
                    continue
                for dirpath, dirnames, filenames in os.walk(full):
                    dirnames.sort()
                    for fn in sorted(filenames):
                        fp = os.path.join(dirpath, fn)
                        relfile = os.path.relpath(fp, root).replace("\\", "/")
                        with io.open(fp, "rb") as f:
                            data = f.read()
                        _add(z, name, PAYLOAD_PREFIX + relfile, data)
        z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def build_bundle(out_path, desensitize=True, password=None):
    """导出迁移包，返回 manifest dict。

    password=None → v1 脱敏明文 zip（兼容历史包；仍不安全，见 docs/migration.md）；
    password=str  → v2 口令加密容器（PBKDF2-SHA256 200k + Fernet，加密整份 zip）。
    缺 `cryptography` 时加密导出 **fail-closed 抛 BundleError**，绝不静默产明文。
    """
    if password is None:
        return _build_plain_zip(out_path, desensitize)
    _require_crypto()
    buf = io.BytesIO()
    manifest = _build_plain_zip(buf, desensitize, extra={
        "format": FORMAT_ENCRYPTED, "encrypted": True,
        "kdf": "pbkdf2-sha256", "iterations": KDF_ITERATIONS,
    })
    with io.open(out_path, "wb") as f:
        f.write(_encrypt_container(buf.getvalue(), password))
    return manifest


def _safe_rel(entry, root):
    """zip slip 防护：entry 必须在 payload/ 下，且解析后仍落在 root 内。"""
    if not entry.startswith(PAYLOAD_PREFIX):
        raise BundleError("非法 entry（不在 payload/ 下）：%s" % entry)
    rel = entry[len(PAYLOAD_PREFIX):]
    if not rel or rel.startswith("/") or ".." in rel.replace("\\", "/").split("/"):
        raise BundleError("非法相对路径：%s" % rel)
    full = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    if not full.startswith(os.path.abspath(root) + os.sep):
        raise BundleError("路径越界：%s" % rel)
    return rel, full


def _open_bundle_zip(bundle_path, password=None):
    """打开并完整校验迁移包，返回 (ZipFile, manifest, encrypted)。

    调用方负责 close 返回的 ZipFile。格式 / 完整性 / 口令任一不合格抛 BundleError，
    绝不返回「部分可用」的 zip。
    """
    try:
        with io.open(bundle_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise BundleError("包不可读：%s" % e)
    encrypted = raw.startswith(MAGIC)
    try:
        if encrypted:
            zf = zipfile.ZipFile(io.BytesIO(_decrypt_container(raw, password)), "r")
        else:
            zf = zipfile.ZipFile(bundle_path, "r")
    except BundleError:
        raise
    except (OSError, zipfile.BadZipFile, ValueError) as e:
        raise BundleError("包不可读：%s" % e)
    try:
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if manifest.get("format") not in (FORMAT, FORMAT_ENCRYPTED):
            raise BundleError("不兼容的包格式：%s" % manifest.get("format"))
        for item in manifest.get("files", []):
            data = zf.read(item["entry"])
            if _sha256(data) != item.get("sha256"):
                raise BundleError("完整性校验失败：%s" % item["entry"])
    except (KeyError, ValueError, zipfile.BadZipFile) as e:
        zf.close()
        raise BundleError("包不可读：%s" % e)
    except BundleError:
        zf.close()
        raise
    return zf, manifest, encrypted


def inspect_bundle(bundle_path, password=None):
    """校验格式 + 逐条 sha256（加密包需口令），返回 manifest。"""
    zf, manifest, _encrypted = _open_bundle_zip(bundle_path, password)
    zf.close()
    return manifest


def apply_bundle(bundle_path, dry_run=False, backup=True, password=None):
    """导入。校验全部通过后才会写盘（fail-closed）；返回执行摘要。"""
    root = _root()
    zf, manifest, _encrypted = _open_bundle_zip(bundle_path, password)
    try:
        plan = []
        for item in manifest["files"]:
            rel, full = _safe_rel(item["entry"], root)
            plan.append((rel, full, zf.read(item["entry"])))
    finally:
        zf.close()
    result = {"applied": [], "backup_dir": None, "dry_run": bool(dry_run)}
    if dry_run:
        result["applied"] = [p[0] for p in plan]
        return result
    backup_dir = None
    if backup and plan:
        backup_dir = os.path.join(root, BACKUP_DIRNAME,
                                  time.strftime("%Y%m%d-%H%M%S"))
    for rel, full, data in plan:
        if backup_dir and os.path.isfile(full):
            dst = os.path.join(backup_dir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(full, dst)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with io.open(full, "wb") as f:
            f.write(data)
        result["applied"].append(rel)
    result["backup_dir"] = backup_dir
    return result


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="配置 / 技能 / 经验库 / 知识库 迁移包")
    sub = p.add_subparsers(dest="cmd")
    pe = sub.add_parser("export")
    pe.add_argument("path")
    pe.add_argument("--include-secrets", action="store_true")
    pe.add_argument("--password", help="口令加密（PBKDF2+Fernet）；不传则为脱敏明文 zip")
    pi = sub.add_parser("inspect")
    pi.add_argument("path")
    pi.add_argument("--password", help="加密包的口令")
    pa = sub.add_parser("import")
    pa.add_argument("path")
    pa.add_argument("--dry-run", action="store_true")
    pa.add_argument("--no-backup", action="store_true")
    pa.add_argument("--password", help="加密包的口令")
    a = p.parse_args(argv)
    if a.cmd == "export":
        if not a.password:
            print("[bundle] ⚠️ 未传 --password：产出**脱敏明文 zip**，请放入加密介质；"
                  "需要口令加密请加 --password")
        m = build_bundle(a.path, desensitize=not a.include_secrets,
                         password=a.password)
        print("[bundle] 导出 %d 个文件 → %s（脱敏=%s，加密=%s，版本=%s）"
              % (len(m["files"]), a.path, m["desensitized"],
                 bool(m.get("encrypted")), m["app_version"]))
        return 0
    if a.cmd == "inspect":
        m = inspect_bundle(a.path, password=a.password)
        print("[bundle] 格式 %s / 文件 %d / 脱敏=%s / 加密=%s / 建于 %s"
              % (m["format"], len(m["files"]), m["desensitized"],
                 bool(m.get("encrypted")), m["created_at"]))
        for f in m["files"]:
            print("  %8d  %s" % (f["size"], f["entry"]))
        return 0
    if a.cmd == "import":
        r = apply_bundle(a.path, dry_run=a.dry_run,
                         backup=not a.no_backup, password=a.password)
        print("[bundle] %s %d 个文件；备份=%s"
              % ("预览" if r["dry_run"] else "已导入", len(r["applied"]),
                 r["backup_dir"] or "无"))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
