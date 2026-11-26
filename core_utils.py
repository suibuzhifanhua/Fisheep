# -*- coding: utf-8 -*-
"""core_utils.py — 兼容转发壳（v1.0.402 阶段二拆分）

原 1395 行大杂烩已按域拆入 core/ 包：
  paths（路径/产物库常量）logging（日志）config（配置/脱敏/Schema）
  workspace_io（产物库写入/迁移/台账/DND）ocr（引擎/安装/更新）
  instance_lock（单实例，v1.0.646 改名）notify（通知中心/Toast）
  files（文件内容解析，v1.0.647 起含老格式 Office 转换）
  secrets（密钥保护，v1.0.647 自顶层 security.py 归位——壳不转发它，直接 core.secrets）

本文件仅为**存量 import 兼容**保留——所有符号从 core.* 转发。
新代码请直接 `from core.xxx import yyy`。

v1.0.655（B4）：AST 级引用分析 + 消费方逐名文本复核，撤除 30 个经壳零引用
转发项（BASE/_LOG_*/_OCR_ENGINE/_DND_* 等），转发面 85→55（_ocr_latest_version
因 test_ocr_install 经 cu 桩打保留）。
v1.0.661（「全做」审查）：同口径拟撤 5 个经壳零引用项，实测 4 个证据链不完整
已回滚（kb_dir/_OCR_SUB2PKG/_ocr_needed_pkgs/_verkey——test_kb_paths、
test_ocr_install 经 cu/C 壳别名直接取用，并非打定义处）；仅 _ocr_write_versions
确零引用，维持撤除。转发面 55→51。
v1.0.663（「全做」审查）：50 个生产文件的**顶层** `from core_utils import`
全部迁走（直接 from core.<域> import），壳的存量消费方只剩三类——
① `import core_utils` 后的属性访问（如 cu.threading，测试 patch 点依赖壳属性）；
② 函数级局部 from-import（测试 patch 点，调用时经壳属性重解析）；
③ tests/ 的 cu fixture 双打壳与定义处。转发面维持 51 不动。
"""
import threading  # v1.0.402：测试 patch cu.threading.Thread（OCR 安装线程 mock）需要
from core.paths import (resource_path, kb_dir,  # noqa: F401
                        workspace_root, ws_sub, ws_rel_path, companion_log_dir,
                        _WORKSPACE_SUBS, KB_ROOT, KB_CATEGORIES,
                        _ALLOWED_SUBS)
from core.logging import (_timestamp, _lazy_requests,  # noqa: F401
                          log_event, last_agent_activity_epoch,
                          READ_TRUNCATE_BYTES)
from core.config import (config_path, mask_key,  # noqa: F401
                         _mask_args, _pick_int, _pick_flex)
from core.workspace_io import (migrate_legacy_layout, migrate_knowledge_base,  # noqa: F401
                               save_workspace_file, append_ledger,
                               workspace_size_info, save_chat_log,
                               save_template_result, _safe_ws_path,
                               dnd_allow_paths, dnd_allowed, _safe_ws_read_path)
from core.ocr import (_OCR_RUNTIME_STATE, _OCR_WHEELS, _OCR_DL,  # noqa: F401
                      _OCR_SUB2PKG, _ocr_needed_pkgs, _verkey,
                      ensure_ocr_runtime,
                      _ocr_pick_wheel,
                      _ocr_latest_version,  # 测试经 cu 桩打
                      ocr_install_status, install_ocr_async,
                      ocr_versions, ocr_check_updates, ocr_ready_or_install,
                      ocr_selfcheck, ocr_size_hint)  # v1.0.609 实测体积
from core.instance_lock import acquire_single_instance_mutex  # noqa: F401
# v1.0.646 改名：原 core/security.py 与顶层 security.py（DPAPI 密钥保护）同名不同义，
# 前者是进程单实例 mutex，改名消除导航混淆；符号名与转发表面不变。
from core.notify import native_toast  # noqa: F401（兼容壳转发）
# v1.0.647：原 core/office.py 并入 core/files.py（唯一消费者），其 4 个 office 符号
# 在本壳的转发面全库 0 人取用，一并撤除（_LEGACY_OFFICE_MAP/convert_legacy_office 需用时
# 直接 from core.files import，与 core.ocr 系列同口径）。
from core.files import _parse_file_content  # noqa: F401
from core.config import extract_json, validate_schema, _valid_json_schema  # noqa: F401
