# -*- coding: utf-8 -*-
"""v1.0.756 批次 A2 行为锁：shell 通道策略「写死在代码里」的完整性 / 运行时
消费 / 打包门禁接线 / deferred 复核版本。

核心判据（项目要求「改回坏形状必红」）：
把策略改回「只在 docs 里写一句」的旧形状——删常量、断消费、抹复核版本、
去掉 registry 标注——门禁必须拦截，而不是静默放行。
"""
import io
import os

import security_ops as S
import buildlib.gates as G

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POLICY_FILES = (
    "security_ops.py",
    "agent_tools/os_ops.py",
    "agent_tools/registry.py",
    "docs/deferred.md",
)


def _read(rel):
    with io.open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def _fake_base(tmp_path, overrides=None):
    """把真实 4 个文件复制进临时目录，按需改写，供门禁注入 base。"""
    overrides = overrides or {}
    for rel in POLICY_FILES:
        text = _read(rel)
        if rel in overrides:
            text = overrides[rel](text)
        dest = os.path.join(str(tmp_path), *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with io.open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    return str(tmp_path)


class TestPolicyConstant:
    def test_required_fields_present(self):
        p = S.SHELL_CHANNEL_POLICY
        for k in ("policy_version", "review_version", "default_channel",
                  "compat_channel", "compat_requires", "threat_model",
                  "sunset_conditions", "sunset_action", "deferred_ref"):
            assert k in p, "策略缺字段 %s" % k

    def test_default_channel_is_argv(self):
        assert S.SHELL_CHANNEL_POLICY["default_channel"] == "pc_run_argv"

    def test_four_guards_declared(self):
        guards = S.SHELL_CHANNEL_POLICY["compat_requires"]
        for g in ("approval", "audit", "command_scan", "path_guard"):
            assert g in guards

    def test_threat_model_has_residual_risk(self):
        tm = S.SHELL_CHANNEL_POLICY["threat_model"]
        assert tm["residual_risk"] and tm["mitigations"] and tm["expansion"]

    def test_sunset_is_not_empty(self):
        assert len(S.SHELL_CHANNEL_POLICY["sunset_conditions"]) >= 3
        assert S.SHELL_CHANNEL_POLICY["sunset_action"]

    def test_review_version_present(self):
        assert S.SHELL_CHANNEL_POLICY["review_version"]


class TestRuntimeGuard:
    def test_guard_passes_today(self):
        assert S.assert_shell_channel_guard() is None

    def test_guard_fails_closed_when_approval_removed(self, monkeypatch):
        broken = dict(S.SHELL_CHANNEL_POLICY)
        broken["compat_requires"] = ("audit", "command_scan", "path_guard")
        monkeypatch.setattr(S, "SHELL_CHANNEL_POLICY", broken)
        err = S.assert_shell_channel_guard()
        assert err and "approval" in err

    def test_guard_fails_closed_when_default_reverted(self, monkeypatch):
        broken = dict(S.SHELL_CHANNEL_POLICY)
        broken["default_channel"] = "pc_run"
        monkeypatch.setattr(S, "SHELL_CHANNEL_POLICY", broken)
        assert S.assert_shell_channel_guard()

    def test_guard_fails_closed_when_policy_deleted(self, monkeypatch):
        monkeypatch.setattr(S, "SHELL_CHANNEL_POLICY", None)
        assert S.assert_shell_channel_guard()


class TestGate:
    def test_repo_passes(self):
        assert G.check_shell_channel_policy() == 0

    def test_missing_policy_blocks(self, tmp_path):
        base = _fake_base(tmp_path, {
            "security_ops.py": lambda t: t.replace(
                "SHELL_CHANNEL_POLICY = {", "_DISABLED_SHELL_POLICY = {"),
        })
        assert G.check_shell_channel_policy(base) != 0

    def test_unconsumed_policy_blocks(self, tmp_path):
        base = _fake_base(tmp_path, {
            "agent_tools/os_ops.py": lambda t: t.replace(
                "assert_shell_channel_guard", "removed_guard_marker"),
        })
        assert G.check_shell_channel_policy(base) != 0

    def test_missing_review_version_blocks(self, tmp_path):
        base = _fake_base(tmp_path, {
            "docs/deferred.md": lambda t: "\n".join(
                ln for ln in t.splitlines() if not ln.startswith("| 复核版本 |")),
        })
        assert G.check_shell_channel_policy(base) != 0

    def test_registry_marker_removed_blocks(self, tmp_path):
        base = _fake_base(tmp_path, {
            "agent_tools/registry.py": lambda t: t.replace("【推荐】", ""),
        })
        assert G.check_shell_channel_policy(base) != 0

    def test_residual_risk_removed_blocks(self, tmp_path):
        base = _fake_base(tmp_path, {
            "security_ops.py": lambda t: t.replace(
                '"residual_risk": "', '"removed_risk": "'),
        })
        assert G.check_shell_channel_policy(base) != 0


class TestWiring:
    def test_gate_is_wired_into_build(self):
        body = _read("build.py")
        assert "check_shell_channel_policy" in body
        assert '_run_gate("shell 通道策略检查", check_shell_channel_policy' in body

    def test_gate_is_exported(self):
        body = _read("build.py")
        assert '"check_shell_channel_policy"' in body

    def test_deferred_has_ten_review_rows(self):
        assert _read("docs/deferred.md").count("| 复核版本 |") == 10
