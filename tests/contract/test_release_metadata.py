# -*- coding: utf-8 -*-
"""v1.0.756 批次 B3 行为锁：发布元数据自动化（评审 P2-1 字面补做）。

红线：镜像 RELEASE.json 的 build_version 恒等于 version.py；公共 README 版本
标记幂等不叠加；发布号只从 publish/RELEASE.json 同步，脚本不自己产生；
镜像指纹排除 RELEASE.json 自身；失败必须 fail-closed。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import version  # noqa: E402
from buildlib import release as R  # noqa: E402


@pytest.fixture
def mirror(tmp_path):
    m = tmp_path / "mirror"
    m.mkdir()
    (m / "README.md").write_text(
        "# Fisheep · AI 桌面自动化工作台\n\n正文段落\n", encoding="utf-8")
    (m / "foo.py").write_text("print(1)\n", encoding="utf-8")
    return str(m)


def _release(mirror):
    with io.open(os.path.join(mirror, R.RELEASE_JSON_NAME), encoding="utf-8") as f:
        return json.load(f)


def _readme(mirror):
    with io.open(os.path.join(mirror, "README.md"), encoding="utf-8") as f:
        return f.read()


class TestMetadata:
    def test_build_version_matches_version_py(self, mirror):
        assert R.write_release_metadata(mirror) == 0
        data = _release(mirror)
        assert data["build_version"] == version.get()
        assert data["source"] == "version.py::__version__"
        assert data["schema"] == 1

    def test_readme_marker_present_once(self, mirror):
        R.write_release_metadata(mirror)
        text = _readme(mirror)
        assert text.count(R.README_MARKER) == 1
        assert "v" + version.get() in text
        assert text.startswith("# Fisheep"), "标记不能吃掉标题"

    def test_idempotent_no_marker_pileup(self, mirror):
        R.write_release_metadata(mirror)
        R.write_release_metadata(mirror)
        R.write_release_metadata(mirror)
        text = _readme(mirror)
        assert text.count(R.README_MARKER) == 1
        assert _release(mirror)["build_version"] == version.get()

    def test_digest_excludes_release_json_itself(self, mirror):
        R.write_release_metadata(mirror)
        d1 = _release(mirror)["mirror_tree_sha256"]
        assert len(d1) == 64
        with io.open(os.path.join(mirror, R.RELEASE_JSON_NAME), "w",
                     encoding="utf-8") as f:
            f.write("tampered-placeholder")
        R.write_release_metadata(mirror)
        assert _release(mirror)["mirror_tree_sha256"] == d1, \
            "指纹不得包含 RELEASE.json 自身，否则每次写入都自指抖动"

    def test_mirror_files_counted(self, mirror):
        R.write_release_metadata(mirror)
        assert _release(mirror)["mirror_files"] >= 3  # README + foo.py + RELEASE.json


class TestReleaseTag:
    def test_publish_dir_is_single_source(self, mirror, tmp_path):
        base = tmp_path / "base"
        (base / "publish").mkdir(parents=True)
        (base / "version.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
        (base / "publish" / "RELEASE.json").write_text(json.dumps({
            "release_tag": "v1.0.700", "released_at": "2026-01-01",
            "summary": "对外发版"}), encoding="utf-8")
        assert R.write_release_metadata(mirror, base=str(base)) == 0
        data = _release(mirror)
        assert data["build_version"] == "9.9.9", "构建号取传入 base 的 version.py"
        assert data["release_tag"] == "v1.0.700"
        assert data["released_at"] == "2026-01-01"
        assert data["summary"] == "对外发版"

    def test_no_publish_file_means_no_release_tag(self, mirror, tmp_path):
        base = tmp_path / "bare"
        base.mkdir()
        (base / "version.py").write_text('__version__ = "8.8.8"\n', encoding="utf-8")
        R.write_release_metadata(mirror, base=str(base))
        assert _release(mirror)["release_tag"] is None


class TestFailClosed:
    def test_missing_readme_refuses_to_write(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert R.write_release_metadata(str(empty)) != 0
        assert not (empty / R.RELEASE_JSON_NAME).exists()

    def test_unreadable_version_refuses_to_write(self, mirror, tmp_path):
        bare = tmp_path / "noversion"
        bare.mkdir()
        assert R.write_release_metadata(mirror, base=str(bare)) != 0


class TestWiring:
    def test_rebuild_mirror_calls_writer(self):
        with io.open(os.path.join(ROOT, "tools", "publish_github.py"),
                     encoding="utf-8") as f:
            src = f.read()
        assert "write_release_metadata(stage_dir, base=base)" in src
        assert "from buildlib.release import write_release_metadata" in src

    def test_policy_doc_marks_steps_automated(self):
        with io.open(os.path.join(ROOT, "docs", "release-policy.md"),
                     encoding="utf-8") as f:
            doc = f.read()
        assert "【自动】" in doc and "D10 由此闭环" in doc

    def test_publish_release_template_exists(self):
        p = os.path.join(ROOT, "publish", "RELEASE.json")
        assert os.path.isfile(p)
        with io.open(p, encoding="utf-8") as f:
            data = json.load(f)
        assert data["release_tag"] is None
