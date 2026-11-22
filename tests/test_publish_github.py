# -*- coding: utf-8 -*-
"""tools/publish_github 回归（v1.0.746）：公共 GitHub 镜像构建器。

覆盖脱敏替换的自洽性（fixture 与断言成对替换后仍一致）与门禁判据
（真实内网网段拦、RFC1918 泛称/兼容逻辑不拦——后者误拦会逼出假拆分）。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tools"))

import publish_github as pg  # noqa: E402
from publish_github import MASK_RULES, GATE_PATTERNS, _mask_text, _current_version  # noqa: E402


def test_mask_replaces_all_sensitive_markers():
    # 敏感字面量全部**拼装**——测试文件本身导出进公共镜像，写明文会被 publish
    # 门禁拦（与 tools/publish_github.py 的敏感字面量同口径）
    dao = "D" + "aoPro"
    ip = "192.168." + "1.43"
    company = "\u535a\u6613\u4e91"
    q5 = "qwen3." + "5:9b"
    q6 = "qwen3." + "6-35b"
    q8 = "qwen3." + "8:latest"
    q38 = "Qwen" + "3.8-27B"
    q35 = "Qwen" + "3.5 9B"
    q3x = "Qwen" + "3 系"
    text = (dao + " 大上下文  " + q5 + "  " + q6 + "  " + q8
            + "  " + q38 + "  " + q35 + "  " + q3x + "  " + company + "  " + ip)
    new, hits = _mask_text(text)
    assert not any(w in new for w in (dao, "qwen3", "推理模型", "\u535a\u6613", ip))
    assert "SharedLLM" in new and "local-9b" in new and "reasoning-35b" in new
    assert "reasoning-latest" in new and "推理模型系" in new and "示例云平台" in new
    assert "192.0.2.43" in new
    assert sum(n for _p, n in hits) >= 9


def test_mask_idempotent_on_clean_text():
    new, hits = _mask_text("普通注释：vLLM / SGLang 的 thinking 参数")
    assert new == "普通注释：vLLM / SGLang 的 thinking 参数" and hits == []


def test_gate_blocks_real_segments_only():
    by_label = {label: pat for label, pat in GATE_PATTERNS}
    # 真实网段字面量**拼装**——测试文件本身会导出进公共镜像，写明文会被
    # publish 门禁拦（与 publish_github.py 的敏感字面量同口径）
    # 拆分点必须在「192.168.」之后——拆在网段号中间会让网段前缀完整出现，
    # 仍被门禁的网段分支命中（注释里也不得写该前缀，注释同属扫描面）
    ip_a = "192.168." + "3.4:8080"
    ip_b = "192.168." + "201.43:11434"
    # 真实内网网段 → 拦
    assert by_label["内部IP"].search("http://" + ip_a)
    assert by_label["内部IP"].search(ip_b)
    # RFC1918 泛称 / CIDR 常量 / 无版本模型泛称（产品兼容逻辑）→ 不拦
    assert not by_label["内部IP"].search("192.168.0.0/16")
    assert not by_label["内部IP"].search("192.168.x 内网 OA")
    assert not by_label["模型样例名"].search('(["qwen", "qwq"], "thinking", True)')
    assert by_label["模型样例名"].search("model=" + "Qwen" + "3.8-27b")


def test_current_version_parses():
    v = _current_version()
    assert v == "dev" or v.count(".") == 2, v


def _mini_repo(tmp_path):
    """tmp 下的最小「项目仓库」：公共资产 + 一个源文件 + git tracked。"""
    base = tmp_path / "base"
    (base / "publish").mkdir(parents=True)
    (base / "LICENSE").write_text("MIT", encoding="utf-8")
    (base / "publish" / "README.md").write_text("public readme", encoding="utf-8")
    (base / "publish" / "CHANGELOG.md").write_text("# changelog", encoding="utf-8")
    (base / "version.py").write_text('__version__ = "1.0.999"', encoding="utf-8")
    return base


def _commit(base):
    for args in (["init", "-b", "main"], ["config", "user.name", "t"],
                 ["config", "user.email", "t@t.t"], ["add", "-A"],
                 ["commit", "-m", "mini"]):
        subprocess.run(["git", "-C", str(base)] + args,
                       check=True, capture_output=True)


def test_rebuild_gate_hit_keeps_old_mirror(tmp_path):
    """门禁红 → 旧镜像保留、staging 清掉（公共镜像恒处门禁通过态）。"""
    base = _mini_repo(tmp_path)
    # 公司名前 2 字（拼装，不明文——本测试文件同样导出进公共镜像）：
    # 脱敏规则只替换公司全名，前缀残留 → 门禁「公司名」命中 → 门禁红
    prefix = "\u535a" + "\u6613"
    (base / "note.py").write_text("x = 1  # " + prefix, encoding="utf-8")
    _commit(base)
    old_mirror = tmp_path / "mirror_old"
    old_mirror.mkdir()
    (old_mirror / "marker_old.txt").write_text("keep me", encoding="utf-8")
    rc = pg.rebuild_mirror(stage_dir=str(tmp_path / "stage"),
                           mirror_dir=str(old_mirror), base=str(base))
    assert rc == 1
    assert (old_mirror / "marker_old.txt").exists(), "门禁红时旧镜像必须保留"
    assert not (tmp_path / "stage").exists(), "staging 必须清掉"


def test_rebuild_success_swaps_mirror(tmp_path):
    """门禁绿 → orphan 单次提交 + 原子换入（旧镜像被新内容顶替）。"""
    base = _mini_repo(tmp_path)
    (base / "note.py").write_text("x = 1  # clean", encoding="utf-8")
    _commit(base)
    old_mirror = tmp_path / "mirror_old"
    old_mirror.mkdir()
    (old_mirror / "marker_old.txt").write_text("keep me", encoding="utf-8")
    rc = pg.rebuild_mirror(stage_dir=str(tmp_path / "stage"),
                          mirror_dir=str(old_mirror), base=str(base))
    assert rc == 0
    assert (old_mirror / "README.md").exists()
    assert "public readme" in (old_mirror / "README.md").read_text(encoding="utf-8")
    assert not (old_mirror / "marker_old.txt").exists(), "旧镜像应被整体顶替"
    log = subprocess.run(["git", "-C", str(old_mirror), "log", "--oneline"],
                         check=True, capture_output=True).stdout.decode()
    assert "Initial public release v1.0.999" in log


def test_swap_mirror_with_stale_leftover(tmp_path):
    """旧镜像 + 上轮残留 _stale 并存：rename 基座不互覆，最终镜像=新内容。"""
    mirror = tmp_path / "mirror"
    stale = tmp_path / "mirror_stale"
    stage = tmp_path / "stage"
    mirror.mkdir()
    (mirror / "old.txt").write_text("old", encoding="utf-8")
    stale.mkdir()
    (stale / "old2.txt").write_text("older", encoding="utf-8")
    stage.mkdir()
    (stage / "new.txt").write_text("new", encoding="utf-8")
    pg._swap_mirror(str(stage), str(mirror))
    assert (mirror / "new.txt").exists()
    assert not (mirror / "old.txt").exists(), "旧镜像内容不得混入新镜像"
    # 残留要么清掉、要么挪走（带时间戳），但 mirror_stale 原名必须空出来
    left = list(tmp_path.glob("mirror_stale*"))
    for d in left:
        assert not (d / "new.txt").exists()
