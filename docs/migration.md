# 换机 / 多机型迁移规范

> v1.0.755 建立（评审 P2-2）。**本文件是「配置 / 技能 / 经验库 / 知识库」跨机迁移的唯一口径**。
> 配套实现：`core/profile_bundle.py`（随包发布）+ `docs/release-policy.md` + `docs/deferred.md` D9。

## 1. 为什么需要专门的迁移流程

| 坑 | 现状 | 后果 |
|---|---|---|
| DPAPI 绑定「用户 + 机器」 | `config.json` 里的 `enc:` 值由 Windows DPAPI 加密 | 换机后解密失败，旧行为**静默降级** `enc:weak:`（用户不知道凭据已经不保护了） |
| 配置位置变迁 | `config.json` 于 v1.0.739 收进 `产物库/`；老版在应用根目录 / `%LOCALAPPDATA%` 回退 | 只拷 exe 或直接拷整个目录会漏配或拷错份 |
| `core/relocate.py` 只管目录归位 | 解决「exe 放桌面 → 自动归位」，不解决数据迁移 | 目录对了、数据没了 |
| `tools/import_profile.py` 未进产品 | `tools/` 不进 exe（`Fisheep.spec` 明确排除） | 用户拿不到可用的迁移入口 |

## 2. 首次启动解密失败的迁移引导（规范要求）

v1.0.756 已按本规范**字面落地**（不再是「应改为」）：

1. 检测到 `enc:` 解密失败时**不要静默降级**，先写一条明确日志
   `[migration] 配置解密失败（疑似换机/换用户），需用户选择迁移方式`；
2. 界面给出三个选项：① 从迁移包导入；② 保留旧配置副本后以默认配置启动；
   ③ 明确知晓风险后使用弱加密（`enc:weak:`）——**必须显式勾选**；
3. 无论选哪项，先把原 `config.json` 复制到 `产物库/_migration_backup/<时间戳>/`；
4. 用户后续在新机重新填一次凭据（DPAPI 值跨机本就不可用，不尝试「解密后重加密」）。

> **v1.0.756 已闭环**：`core/migration_guide.py` 状态机 + `core/secrets.py` 解密失败
> 上报 + `backend.py` 首页三选一独立页 + `/api/migration/status|resolve` 全部落地；
> 独立页自带内联表单、不引用 `js/*.js`，因此与 v1.0.756 已完成的 ES Module
> 全量迁移天然解耦。
> 行为锁见 `tests/test_migration_guide.py`，D9 不再作为降级项。

## 3. 迁移包用法

`core/profile_bundle.py` 是随包发布的核心，同时带 CLI：

```bash
# 旧机：口令加密导出（推荐；PBKDF2-SHA256 200k + Fernet，产出 FISHEEPPB2 容器）
python -m core.profile_bundle export D:\profile.enc --password "强口令"

# 加密包：查看 / 导入都要带同一个口令；错口令或包被篡改一律 fail-closed 拒绝
python -m core.profile_bundle inspect D:\profile.enc --password "强口令"
python -m core.profile_bundle import D:\profile.enc --dry-run --password "强口令"

# 旧机：导出（默认脱敏，敏感值不进包）
python -m core.profile_bundle export D:\profile.zip

# 查看包内容 / 校验完整性
python -m core.profile_bundle inspect D:\profile.zip

# 新机：先预览会覆盖什么（不落盘）
python -m core.profile_bundle import D:\profile.zip --dry-run

# 正式导入（自动备份被覆盖的文件到 产物库/_bundle_backup/<时间戳>/）
python -m core.profile_bundle import D:\profile.zip

# 仅在**可信加密介质**内、且确实需要带凭据时用（会包含 token/密钥）
python -m core.profile_bundle export D:\profile-full.zip --include-secrets
```

收集项（`core/profile_bundle.py::ITEMS`）：

| 项 | 路径 | 说明 |
|---|---|---|
| config | `产物库/config.json` | 默认递归脱敏：`token` / `secret` / `password` / `api_key` / `credential` / `cookie` 等键 |
| experiences | `产物库/experiences.json` | 经验库 |
| knowledge | `产物库/知识库/` | 递归 |
| skills | `产物库/技能/` | 递归 |

缺的项自动跳过，不报错。manifest 含每条文件的 sha256，导入前**逐条校验**，
篡改即整体拒绝（`BundleError`，不做部分导入）；路径穿越（`../`）一律拒；
`--dry-run` 只报告不落盘；`--no-backup` 仅在明确知道后果时使用。

## 4. 安全边界（务必如实告知用户）

- **口令加密已支持（v1.0.756）**：`--password` 走 PBKDF2-HMAC-SHA256
  （200000 次迭代 + 16 字节随机 salt）+ Fernet，加密整份 zip 字节，产出
  `FISHEEPPB2` 容器；口令错误 / 包被篡改 / 缺 `cryptography` 一律
  `BundleError` fail-closed，**绝不静默降级为明文**。
- 不传口令仍产出**脱敏明文 zip**（兼容历史包，CLI 明确告警），请放入加密 U 盘 /
  加密压缩包 / 内网受限目录。`cryptography==46.0.3` 已钉进 `requirements.txt`
  并随 frozen 包收集；自己用标准库拼凑弱口令加密比不加密更危险，故不做。
- **脱敏不等于无敏感**：正文、经验库、知识库里的业务内容不会被脱敏，导出前
  按单位数据外发规定自行审阅。
- **DPAPI 值不可跨机**：`enc:` 字段按脱敏键处理，导入后需在新机重填凭据。
- **导入会覆盖同名文件**：靠自动备份兜底，但备份目录本身在 `产物库/` 内，
  换新机前请一并归档或删除。

## 5. 验收判据

- `python -m core.profile_bundle export` 产出的包里不含 `config.json` 中的
  token/密钥明文，且键结构完整（`tests/test_profile_bundle.py` 已锁）；
- 篡改包内任一字节 → `inspect` / `import` 均拒绝；
- 导入后目标文件恢复，且 `产物库/_bundle_backup/<时间戳>/` 里有导入前的副本；
- `--password` 导出的包不是 zip、不含明文 token；无口令 / 错口令 / 篡改 / 缺依赖
  四种坏形状全部 fail-closed 拒绝（`tests/test_profile_bundle_crypto.py` 已锁）；
- 首次启动解密失败时不再静默降级（v1.0.756 已落地，`tests/test_migration_guide.py` 锁：
  未勾选确认的弱保护分支必须被拒，三选一任一分支都先备份原配置）。
