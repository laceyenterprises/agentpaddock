"""Regeneration rejects unavailable formatters and paths that escape the fixture."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import regenerate_rfd_fix as generator

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("version", [None, "ruff 0.15.21", "ruff 0.15.20"])
def test_regeneration_requires_pinned_formatter(monkeypatch, version):
    monkeypatch.setattr(generator.shutil, "which", lambda _: "/ruff" if version else None)

    def run(argv, **kwargs):
        assert argv == ["/ruff", "--version"]
        assert kwargs["encoding"] == "utf-8"
        return subprocess.CompletedProcess(argv, 0, stdout=version + "\n")

    monkeypatch.setattr(generator.subprocess, "run", run)
    if version == "ruff 0.15.20":
        assert generator.require_ruff() == "/ruff"
    else:
        with pytest.raises(generator.RuffUnavailable, match="pip install ruff==0.15.20"):
            generator.require_ruff()


@pytest.mark.parametrize(
    "name", ["../outside.py", "nested/../../outside.py", "/absolute.py", "", "."]
)
def test_regeneration_rejects_escaping_paths(tmp_path, name):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    manifest = fixture / "staged-manifest.json"
    content = json.dumps({"files": [name], "editable": []})
    manifest.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="relative file path"):
        generator.regenerate(tmp_path, write=True)
    assert manifest.read_text(encoding="utf-8") == content


def test_incompatible_formatter_does_not_write(tmp_path, monkeypatch):
    task_dir = tmp_path / "task"
    shutil.copytree(ROOT / "tasks/fix-corrupt-refresh-checkpoint", task_dir)
    before = {path: path.read_bytes() for path in task_dir.rglob("*") if path.is_file()}

    def unavailable():
        raise generator.RuffUnavailable("incompatible formatter")

    monkeypatch.setattr(generator, "require_ruff", unavailable)
    with pytest.raises(generator.RuffUnavailable, match="incompatible formatter"):
        generator.regenerate(task_dir, write=True)
    assert {path: path.read_bytes() for path in before} == before
