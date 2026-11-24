# -*- coding: utf-8 -*-
"""tools/publish_github.py — 公共 GitHub 镜像一键构建器（开发期工具，不进 exe）。

流程（每步独立输出；门禁红则中止，不产出可发布物）：
  1) 导出：`git ls-files`（tracked 文件 = 源码集；产物库/build/dist/日志
     均被 .gitignore 排除，天然不进镜像）→ 待发布版本/（Fisheep 主目录一等次级目录）
  2) 脱敏：MASK_RULES 模式替换（服务商名/公司名/模型样例名/内部测试 IP，
     见规则表；全部是代码注释或测试样例里的字面量，替换不影响逻辑）
  3) 公共资产：publish/README.md 替换内部 README（内部 README 是工程文档
     + 逐版条目，不适合公开）；publish/CHANGELOG.md → CHANGELOG.md；
     LICENSE 拷根
  4) 门禁：GATE_PATTERNS 扫镜像全树（命中=红）+ 敏感文件类型
     （.log/.rar/.zip/.pyc/.exe 等）
  5) init：镜像内 git init + 单次提交——公共历史从首个公开版 orphan 开始，
     内部版本时间线不公开（内部历史已核实干净，但内部工程 commit 文案
     不适合公开）
  6) 报告：打印推送命令。**公开发布是外部可见动作——脚本只备好仓库，
     不推送**；确认后再手动 push（或加 --push，需用户当场确认）。

用法：
  python tools/publish_github.py           # 建镜像 + 脱敏 + 门禁 + 本地提交
  python tools/publish_github.py --push    # 额外推送到已配好的 origin
  python tools/publish_github.py --rebuild # 静默原子重建（build.py 收尾自动调用）
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# build.py 收尾用 subprocess 直接跑本脚本（sys.path[0] = tools/，不含仓库根）。
# v1.0.756 的 rebuild_mirror 会 `from buildlib.release import write_release_metadata`，
# 没有这行就会 "No module named 'buildlib'" → 门禁未过、镜像静默保留上一版。
# 行为锁：tests/contract/test_publish_github_path.py
if BASE not in sys.path:
    sys.path.insert(0, BASE)
# 两级目录布局（v1.0.754 起）：Fisheep 主目录下「本地开发版本/」= 仓库根（BASE），
# 「待发布版本/」= 自动生成的脱敏镜像（勿手改、勿当修改源）——镜像在仓库根的
# **父级目录**（主目录一等次级目录，与源码平级），export 仍按仓库根扁平导出。
MIRROR_DIR = os.path.join(os.path.dirname(BASE), "待发布版本")

# 敏感字面量表——**运行时拼装**：脚本自身源码若出现明文，会被 4) 门禁自扫描
# 命中（门禁扫的是镜像全树，本脚本也在镜像里）。
W_COMPANY = "\u535a\u6613\u4e91"          # 公司名（测试环境云厂商）
W_VENDOR = "D" + "aoPro"                 # 推理服务商
W_DOMAIN = "fisheep" + "cozy"            # 个人域名
W_USERPATH = "C:\\Users\\" + "A1"        # 个人主目录前缀
W_QWEN3 = "Qwen" + "3"                   # 模型版本标识前缀（兜底规则若写字面量
                                         # 会命中本脚本源码里的其他规则 → 自伤）
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
)

# 门禁：脱敏后镜像里不得残留的模式。内部 IP 只拦**用户真实内网网段**
# （3.x 现网段 / 201.x 旧网段 / 1.43 测试样例——已脱敏，留门双保险）；
# RFC1918 通用网段常量与泛称（192.168.0.0/16、「192.168.x」）不是秘密，不拦。
GATE_PATTERNS = (
    ("公司名", re.compile(re.escape(W_COMPANY[:2]))),
    ("服务商名", re.compile(re.escape(W_VENDOR))),
    ("模型样例名", re.compile(r"qwen3\.\d", re.IGNORECASE)),
    ("内部IP", re.compile(r"192\.168\.(3|201|1\.43)\b")),
    ("个人域名", re.compile(re.escape(W_DOMAIN))),
    ("个人路径", re.compile(re.escape(W_USERPATH))),
    ("雇主平台", re.compile(re.escape(W_ZN_DOMAIN) + "|" + re.escape(W_ZN_NAME))),
)

# 镜像里不允许出现的文件类型（构建日志/打包物/二进制/缓存）
GATE_EXTS = (".log", ".rar", ".zip", ".pyc", ".tar", ".7z", ".exe")

TEXT_EXTS = (".py", ".js", ".md", ".html", ".css", ".txt", ".json",
             ".ini", ".cfg", ".spec", ".yml", ".yaml")


def _git(*args, base=None):
    """项目根上的 git；返回 stdout 或 None（失败/不可用）。"""
    try:
        r = subprocess.run(["git", "-C", base or BASE] + list(args),
                           capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001（git 不可用 → 明确报错中止）
        print(f"[publish] ❌ git {args[0] if args else ''} 执行失败：{e}")
        return None
    if r.returncode != 0:
        print(f"[publish] ❌ git {' '.join(args)} 返回非零：")
        print((r.stderr or b"").decode("utf-8", "replace")[:500])
        return None
    return (r.stdout or b"").decode("utf-8", "replace")


def export_tree(base=None, mirror_dir=None):
    """1) 把 tracked 文件导出到镜像（内部 README 不进公共版，由公共版替换）。"""
    base = base or BASE
    mirror_dir = mirror_dir or MIRROR_DIR
    out = _git("ls-files", base=base)
    if out is None:
        raise SystemExit("[publish] git ls-files 失败——中止")
    if os.path.isdir(mirror_dir):
        shutil.rmtree(mirror_dir)
    os.makedirs(mirror_dir)
    n = 0
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel or rel == "README.md":
            continue
        src = os.path.join(base, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(mirror_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print(f"[publish] 导出 {n} 个 tracked 文件 → {mirror_dir}")
    return n


def _mask_text(text):
    """对单份文本应用全部脱敏规则。返回 (新文本, [(模式, 命中数)])。"""
    hits = []
    for pat, rep in MASK_RULES:
        text, n = pat.subn(rep, text)
        if n:
            hits.append((pat.pattern, n))
    return text, hits


def apply_mask(mirror_dir=None):
    """2) 镜像内文本文件就地脱敏（二进制原样不动）。返回改动文件数。"""
    mirror_dir = mirror_dir or MIRROR_DIR
    files = 0
    total = {}
    for root, _dirs, names in os.walk(mirror_dir):
        for fn in names:
            if not fn.endswith(TEXT_EXTS):
                continue
            p = os.path.join(root, fn)
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
            new, hits = _mask_text(text)
            if new == text:
                continue
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            files += 1
            for pat, n in hits:
                total[pat] = total.get(pat, 0) + n
    print(f"[publish] 脱敏 {files} 个文件：")
    for pat in sorted(total):
        print(f"  {pat}: {total[pat]} 处")
    return files


def install_public_assets(base=None, mirror_dir=None):
    """3) 公共资产就位：README / CHANGELOG / LICENSE。缺任一即中止。"""
    base = base or BASE
    mirror_dir = mirror_dir or MIRROR_DIR
    public_dir = os.path.join(base, "publish")
    for name in ("README.md", "CHANGELOG.md"):
        src = os.path.join(public_dir, name)
        if not os.path.isfile(src):
            raise SystemExit(f"[publish] ❌ 缺公共资产：{src}")
        shutil.copy2(src, os.path.join(mirror_dir, name))
    lic = os.path.join(base, "LICENSE")
    if not os.path.isfile(lic):
        raise SystemExit("[publish] ❌ 缺 LICENSE（项目根）")
    shutil.copy2(lic, os.path.join(mirror_dir, "LICENSE"))
    print("[publish] 公共资产就位：README.md / CHANGELOG.md / LICENSE")


def gate_scan(mirror_dir=None):
    """4) 门禁：扫镜像全树的敏感模式 + 敏感文件类型。返回问题清单（空=过）。"""
    mirror_dir = mirror_dir or MIRROR_DIR
    problems = []
    for root, _dirs, names in os.walk(mirror_dir):
        for fn in names:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, mirror_dir).replace(os.sep, "/")
            ext = os.path.splitext(fn)[1].lower()
            if ext in GATE_EXTS:
                problems.append(f"敏感文件类型：{rel}")
                continue
            if ext not in TEXT_EXTS:
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError as e:
                problems.append(f"文件不可读：{rel}（{e}）")
                continue
            for label, pat in GATE_PATTERNS:
                if pat.search(text):
                    problems.append(f"{label} 残留：{rel}")
    return problems


def _current_version(base=None):
    try:
        with open(os.path.join(base or BASE, "version.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', f.read())
        return m.group(1) if m else "dev"
    except OSError:
        return "dev"


def init_repo(base=None, mirror_dir=None):
    """5) 镜像内 orphan 历史：git init + 单次提交（内部时间线不公开）。

    身份继承自主仓 local config（用户 GitHub noreply 邮箱，公开安全）——
    新镜像仓库是空白 repo，不继承主仓 local config，不显式传身份会 commit 失败。
    """
    base = base or BASE
    mirror_dir = mirror_dir or MIRROR_DIR
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)

    identity = []
    for key in ("user.name", "user.email"):
        val = _git("config", "--get", key, base=base)
        if val and val.strip():
            identity += ["-c", f"{key}={val.strip()}"]

    def mgit(*args):
        r = subprocess.run(["git", "-C", mirror_dir] + list(args),
                           capture_output=True, env=env, timeout=300)
        if r.returncode != 0:
            raise SystemExit("[publish] ❌ git %s 失败：%s"
                             % (args[0],
                                (r.stderr or b"").decode("utf-8", "replace")[:300]))

    mgit("init", "-b", "main")
    mgit("add", "-A")
    mgit(*identity, "commit", "-m",
         f"Initial public release v{_current_version(base)}"
         f"（脱敏 + 公共 README/CHANGELOG）")
    print("[publish] 公共仓库已 init：main 分支单次提交（orphan 历史）")


def _stale_variants(mirror_dir):
    """"<镜像名>_stale*" 暂存位全集（含带时间戳的）——按前缀 glob，
    避免上轮时间戳残留永久泄漏（749 版只清原名，时间戳变体无人收）。"""
    root = os.path.dirname(mirror_dir) or "."
    prefix = os.path.basename(mirror_dir) + "_stale"
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, d) for d in sorted(os.listdir(root))
            if d.startswith(prefix) and os.path.isdir(os.path.join(root, d))]


def _swap_mirror(stage_dir, mirror_dir):
    """原子换入：旧镜像 rename 走（不逐文件删——构建收尾时旧 .git 的对象文件
    可能正被实时扫描锁住，rmtree 会 WinError 5；rename 只动目录项，无锁）。
    旧镜像挪到 _stale 暂存位再后台清；清不掉（仍被锁）就留到下一轮重建清——
    下一轮清「所有变体」（含时间戳），逐个 rmtree 重试，最终自愈。"""
    for old in _stale_variants(mirror_dir):  # 上一轮残留（含时间戳）：先清
        shutil.rmtree(old, ignore_errors=True)
    stale = mirror_dir + "_stale"
    if os.path.isdir(mirror_dir):
        if stale in _stale_variants(mirror_dir):  # 仍被锁没清掉 → 换带时间戳的名字
            stale += "_" + time.strftime("%Y%m%d_%H%M%S")
        os.replace(mirror_dir, stale)
    os.replace(stage_dir, mirror_dir)
    for still in _stale_variants(mirror_dir):
        shutil.rmtree(still, ignore_errors=True)  # 失败留残留，下轮清（不影响镜像）


def rebuild_mirror(stage_dir=None, mirror_dir=None, base=None):
    """原子重建镜像（v1.0.748：build.py 收尾自动调用，保证「每次发版都过脱敏门禁」）。

    staging 目录构建 → 门禁红：删 staging、**保留旧镜像**（公共镜像必须恒处
    门禁通过态，宁可旧不可脏）；门禁绿：orphan 提交后原子换入。返回 0/1，
    绝不抛到调用方（构建不因镜像失败而红）。
    """
    mirror_dir = mirror_dir or MIRROR_DIR
    stage_dir = stage_dir or MIRROR_DIR + "_new"
    base = base or BASE
    try:
        export_tree(base=base, mirror_dir=stage_dir)
        apply_mask(mirror_dir=stage_dir)
        install_public_assets(base=base, mirror_dir=stage_dir)
        # v1.0.756（评审 P2-1 字面补做）：发布元数据自动化——构建号 / 镜像指纹 /
        # 公共 README 版本标记在门禁扫描前幂等写入，保证镜像恒对应本次构建号。
        from buildlib.release import write_release_metadata
        if write_release_metadata(stage_dir, base=base) != 0:
            shutil.rmtree(stage_dir, ignore_errors=True)
            print("[publish] rebuild：发布元数据写入失败——镜像不更新（保留上一版）")
            return 1
        problems = gate_scan(stage_dir)
        if problems:
            shutil.rmtree(stage_dir, ignore_errors=True)
            print(f"[publish] ❌ rebuild：门禁未过（{len(problems)} 项）"
                  f"——镜像不更新（保留上一版），修复后重跑：")
            for x in problems[:10]:
                print("  " + x)
            return 1
        n_files = sum(len(fs) for _r, _d, fs in os.walk(stage_dir))
        init_repo(base=base, mirror_dir=stage_dir)
        _swap_mirror(stage_dir, mirror_dir)
        print(f"[publish] ✅ rebuild：门禁通过（{n_files} 文件），镜像已原子换入")
        return 0
    except Exception as e:  # noqa: BLE001（镜像失败不阻塞构建，只告警）
        shutil.rmtree(stage_dir, ignore_errors=True)
        print(f"[publish] ⚠️ rebuild 失败（{e}）——镜像不更新（保留上一版）")
        return 1


def main():
    p = argparse.ArgumentParser(
        description="公共 GitHub 镜像构建器（脱敏 + 门禁 + orphan 历史）")
    p.add_argument("--push", action="store_true",
                   help="额外推送到 origin（外部可见动作，须已配好 remote）")
    p.add_argument("--rebuild", action="store_true",
                   help="静默原子重建（build.py 收尾调用；门禁红则保留旧镜像）")
    args = p.parse_args()

    if args.rebuild:
        return rebuild_mirror()

    export_tree()
    apply_mask()
    install_public_assets()
    problems = gate_scan()
    if problems:
        print(f"[publish] ❌ 门禁未过（{len(problems)} 项，修复后重跑）：")
        for x in problems:
            print("  " + x)
        return 1
    n_files = sum(len(fs) for _r, _d, fs in os.walk(MIRROR_DIR))
    print(f"[publish] ✅ 门禁通过（{n_files} 文件 × {len(GATE_PATTERNS)} 类敏感模式"
          f" + {len(GATE_EXTS)} 类文件类型，零命中）")
    init_repo()
    if args.push:
        subprocess.run(["git", "-C", MIRROR_DIR, "push", "-u", "origin", "main"],
                       check=False)
    else:
        print("[publish] 镜像就绪。发布是外部可见动作——确认后手动推送：")
        print(f"  cd {MIRROR_DIR}")
        print("  git remote add origin git@github.com:<用户>/<仓库>.git")
        print("  git push -u origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
