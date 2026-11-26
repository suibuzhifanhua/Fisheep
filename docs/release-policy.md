# 版本与发布策略

> v1.0.755 建立（评审 P2-1）。**本文件是「构建号 / 发布号 / 公共镜像」的唯一口径**。
> 与 `AGENTS.md` 第十节（推送纪律）、`README.md` 顶部变更记录、`CHANGELOG.md` 归档边界配套阅读。

## 1. 现状事实（不要再每轮重新论证）

| 项 | 事实 | 出处 |
|---|---|---|
| 版本号唯一来源 | `version.py::__version__`，`build.py` 打包时自动 +1 并写回；**不要手改** | `version.py` 头注释 |
| 本地构建铁律 | 每次代码改动 → 版本 +1 → 全量回归 → `python build.py --yes`；`--note="..."` 必须等号连接 | `AGENTS.md` |
| 变更记录分层 | `CHANGELOG.md` **归档至 v1.0.544**（首行是拆分时随迁的历史合并条目，不表示仍在追加）；其后各版见 `README.md` 顶部 blockquote | 两份文档 |
| 公共镜像 | `待发布版本/` 是每次构建自动生成的**脱敏只读镜像**，勿手改；推送 = 进该目录 `git push` | 根 `README.md` |
| 推送纪律 | 远端长期落后于本地；推送需新凭据 + force push，**必须用户明确确认**；本轮及日常构建一律不 push | `AGENTS.md` 第十节 |
| 门禁 | 打包前 F821 / 开发期目录引用 / 巨型函数棘轮 / 裸 except 棘轮 / 静默 except / 依赖一致性 / 前端结构 / 包内资源逐字节一致 + 全量 pytest | `buildlib/gates.py`、`build.py` |

## 2. 构建号 vs 发布号

不新增第二套版本号系统（双号必然漂移），而是**给同一个构建号加一层发布标记**：

| 概念 | 载体 | 何时变化 | 是否写 changelog |
|---|---|---|---|
| **构建号** | `version.py::__version__` | 本地每次打包自动 +1 | 否。它是工程提交号，不是对外承诺 |
| **发布号** | 发布清单里的 `release_tag`（= 某一个构建号）+ 发布时间 + 摘要 | **仅对外发版时**由人确认产生 | 是，写 `README.md` 顶部 blockquote 一条发版条目 |

好处：本地一天打包十次不再污染对外变更记录；用户下载公共镜像时能一眼看到
「这个镜像对应哪个构建号」，不会拿到 5 个版本前的产物。

## 3. 发布清单（每次对外发版逐步执行，人工确认不可省）

```
[ ] 1. 本地：改动落地 → 全量回归（py_compile → node --check / 前端门禁 → pytest 全量）
[ ] 2. 本地：python build.py --yes --note="..."，确认版本号已 +1、门禁全绿、冒烟通过
[ ] 3. 本地：实跑验证（启动 exe → 关键链路 curl → 界面点一遍）
[ ] 4. 本地：在 README.md 顶部 blockquote 写一条**发版条目**（版本 / 日期 / 变更摘要）
[ ] 5. 本地：确认 待发布版本/ 已由本次构建刷新，且与 本地开发版本/ 的包内资源逐字节一致
[ ] 6. 【自动】构建收尾由 buildlib.release.write_release_metadata 在镜像根幂等写
       RELEASE.json（build_version / generated_at / mirror_files / mirror_tree_sha256），
       并在公共 README 顶部插入「本公共镜像对应构建版本 vX」标记；无需手改镜像
[ ] 7. 本地：对外发版时编辑 publish/RELEASE.json 填 release_tag / released_at / summary
       （发布号唯一人工来源；本地日常打包不填，保持 null）
[ ] 8. 校验：version.py、待发布版本/README.md 标记、待发布版本/RELEASE.json 三处
       构建版本号完全一致（tests/test_release_metadata.py 已锁）
[ ] 9. 【人工确认】向用户展示上述 8 项结果与待推送 diff，得到明确确认
[ ] 10. 推送：进 待发布版本/ 执行 git push（需要 force 时再次单独确认）
[ ] 11. 回写：发布完成后在 README.md 发版条目补上推送结果
```

第 6、8 步已于 **v1.0.756 自动化**：`tools/publish_github.py::rebuild_mirror`
在门禁扫描前调用 `buildlib/release.py::write_release_metadata`，每次镜像重建幂等写入。
第 7 步的发布号仍**保留人工确认**——`publish/RELEASE.json` 是唯一来源，脚本只同步、
不产生发布号、不推送。`docs/deferred.md` D10 由此闭环。

## 4. 验收判据

- 任取一次公共镜像，`RELEASE.json` 的 `version` 与 `version.py` 一致，且能在
  `README.md` 顶部找到对应发版条目；
- 本地连续打包 N 次但**未**发版时，对外变更记录不新增条目；
- 未经用户明确确认，没有任何 push 发生（git 仅限本地存档）。

## 5. 不做的事

- 不把 `version.py` 改成语义化版本（Major.Minor.Patch）：会打断 `build.py` 的
  单调递增校验与 23 个 `test_vNNN_*` 版本回归文件的命名约定；
- 不自动 force push、不在构建脚本里内置任何凭据；
- 不删除 `CHANGELOG.md` 归档内容（历史证据链）。
