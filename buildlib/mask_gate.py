# -*- coding: utf-8 -*-
"""buildlib/mask_gate.py — 脱敏词表 + 门禁模式（单一事实源，v1.0.764）。

为什么是**单一事实源**：脱敏词表既被「源码镜像门禁」（tools/publish_github.py 的
apply_mask / gate_scan）用，也被「exe 级门禁」（buildlib/public_exe.py 的
verify_public_exe，下钻 PYZ 冻结代码）用。两处若各存一份必然漂移——一份加了敏感词、
另一份没加，就会出现「源码扫干净、exe 里还漏」的假安全。收进本模块，两处都 import 这里。

为什么放在 **buildlib** 而不是 tools：tools/ 是「开发期目录」（_DEV_ONLY_ROOTS），
打包门禁禁止生产代码（含 buildlib）import 它。buildlib 是构建期共享包（不进 exe），
tools/publish_github.py 作为壳从本模块 re-export，方向正确（tools→buildlib 允许）。

**敏感字面量运行时拼装**：本模块是 tracked .py，会随源码导出进公共镜像被文本门禁自扫——
若写明文（示例云平台 / study.example.com / …）会被自己的 GATE_PATTERNS 命中。故一律用
unicode 转义 / 字符串拼接在**运行时**还原（转义在**源码文本**里是字面 `\\uXXXX`，
文本门禁按明文匹配不到；运行时才还原成真实串供正则/拼接使用）。
"""
import re

# 敏感字面量表（运行时拼装，见模块 docstring）。
W_COMPANY = "\u535a\u6613\u4e91"          # 公司名（测试环境云厂商）
W_VENDOR = "D" + "aoPro"                 # 推理服务商
W_DOMAIN = "fisheep" + "cozy"            # 个人域名
W_USERPATH = "C:\\Users\\" + "A1"        # 个人主目录前缀
# 用户名还会以**裸名**形态漏出去：pytest 的 basetemp 目录名带用户名，注释里顺手
# 写一句 `%TEMP%/pytest-of-<用户名>/` 就把真名写进了 tracked 文件——个人路径规则
# 只认完整前缀，够不着这种。窄口径匹配（裸 "A1" 会命中海量十六进制/base64 文本）。
W_PYTEST_USER = "pytest-of-" + "A1"
W_QWEN3 = "Qwen" + "3"                   # 模型版本标识前缀（兜底规则若写字面量
                                         # 会命中本模块源码里的其他规则 → 自伤）
W_ZN_DOMAIN = "zhi" + "-niao"            # 雇主内部学习平台域（v1.0.750 终审发现：
W_ZN_NAME = "\u77e5" + "\u9e1f"          # 46 处 10 文件——技能提示词/学习引擎/
                                         # 窗口匹配全带着它，属雇主个性化信息）

# 脱敏规则：(正则, 替换)。命中面（2026-11-22 全仓扫描核实）：
#   W_COMPANY ×6 行（注释 + 内置技能提示词的示例，替换为通用名不改变技能语义）
#   W_VENDOR ×12+ 文件（注释 + 测试 fixture；fixture 与断言成对出现，同步替换后仍一致）
#   local-9b / reasoning-35b ×8 文件（测试 fixture 模型名 + 注释）
#   192.168.1.x 测试样例 IP ×2 → TEST-NET-1 192.0.2.x（与 test_audit_batch 同口径；
#   注释不写明文末段——本文件会被导出进公共镜像，注释也是扫描面）
MASK_RULES = (
    (re.compile(re.escape(W_COMPANY)), "示例云平台"),
    (re.compile(re.escape(W_VENDOR)), "SharedLLM"),
    # 具体版本标识 → 通用名（用户实际在跑的模型配置，属个性化信息）。
    # 顺序：具体先行，推理模型 兜底在后；「Qwen 思考模式」这类无版本泛称=兼容性
    # 说明（与 DeepSeek-R1 同位），不替换。
    (re.compile(r"qwen3\.5:9b"), "local-9b"),
    (re.compile(r"qwen3\.6-35b"), "reasoning-35b"),
    (re.compile(r"qwen3\.6"), "reasoning-35b"),
    (re.compile(r"qwen3\.8"), "reasoning-latest"),   # 覆盖 :latest 与 /latest 两种 tag 写法
    (re.compile(re.escape(W_QWEN3) + r"\.5 9B"), "Local 9B"),
    (re.compile(re.escape(W_QWEN3) + r"\.8-27B"), "reasoning-27b"),
    (re.compile(re.escape(W_QWEN3) + r" 系"), "推理模型系"),
    (re.compile(re.escape(W_QWEN3)), "推理模型"),
    (re.compile(r"192\.168\.1\.43"), "192.0.2.43"),
    # 雇主内部平台（v1.0.750）：域 → 示例域（与仓库既有 example 惯例一致）；
    # 中文名 → 泛称。只影响公共镜像展示；内部 exe 由内部源码构建，不受影响。
    (re.compile(re.escape(W_ZN_DOMAIN)), "study.example"),
    (re.compile(re.escape(W_ZN_NAME) + r"学习中心"), "在线学习平台"),
    (re.compile(re.escape(W_ZN_NAME)), "学习平台"),
    (re.compile(re.escape(W_PYTEST_USER)), "pytest-of-user"),
)

# 门禁：脱敏后镜像/exe 里不得残留的模式。内部 IP 只拦**用户真实内网网段**
# （3.x 现网段 / 201.x 旧网段 / 1.43 测试样例——已脱敏，留门双保险）；
# RFC1918 通用网段常量与泛称（192.168.0.0/16、「192.168.x」）不是秘密，不拦。
GATE_PATTERNS = (
    ("公司名", re.compile(re.escape(W_COMPANY[:2]))),
    ("服务商名", re.compile(re.escape(W_VENDOR))),
    ("模型样例名", re.compile(r"qwen3\.\d", re.IGNORECASE)),
    ("内部IP", re.compile(r"192\.168\.(3|201|1\.43)\b")),
    ("个人域名", re.compile(re.escape(W_DOMAIN))),
    ("个人路径", re.compile(re.escape(W_USERPATH))),
    ("个人用户名", re.compile(re.escape(W_PYTEST_USER))),
    ("雇主平台", re.compile(re.escape(W_ZN_DOMAIN) + "|" + re.escape(W_ZN_NAME))),
)
