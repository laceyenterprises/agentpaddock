"""Every task declares its version; scrubbed tasks carry no private review text."""

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from gym.schema import load_task

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = tuple(sorted((ROOT / "tasks").glob("*/task.toml")))
FORBIDDEN = json.loads((ROOT / "tests/fixtures/private_review_forbidden_strings.json").read_text())
SCRUBBED = frozenset(FORBIDDEN["tasks"])
DECLARED = re.compile(r"^task_version = ([1-9][0-9]*)$", re.MULTILINE)


def tokens(text):
    return re.findall(r"[a-z0-9_]+", text.lower())


def digest(window):
    return hashlib.sha256(" ".join(window).encode()).hexdigest()[:16]


def windows(words, size):
    return {digest(words[i : i + size]) for i in range(len(words) - size + 1)}


def task_files(task):
    for path in sorted((ROOT / "tasks" / task).rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def test_corpus_is_discovered():
    assert len(MANIFESTS) > len(SCRUBBED)
    assert SCRUBBED <= {manifest.parent.name for manifest in MANIFESTS}


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.name)
def test_every_task_declares_task_version(manifest):
    text = manifest.read_text()
    declared = DECLARED.findall(text)
    assert len(declared) == 1, "task.toml must declare task_version literally, once"
    assert tomllib.loads(text)["task_version"] == int(declared[0])
    assert int(declared[0]) == (2 if manifest.parent.name in SCRUBBED else 1)
    assert load_task(manifest.parent).task_version == int(declared[0])


def test_forbidden_fixture_is_well_formed():
    assert len(SCRUBBED) == 7
    assert FORBIDDEN["review_shingles"]
    assert all(len(entry) == 16 for entry in FORBIDDEN["review_shingles"])
    labels = {entry["label"] for entry in FORBIDDEN["phrases"]}
    assert "original review card title" in labels
    assert "private pull request" in labels


@pytest.mark.parametrize("task", sorted(SCRUBBED))
def test_scrubbed_task_contains_no_original_review_text(task):
    size = FORBIDDEN["review_shingle_words"]
    shingles = frozenset(FORBIDDEN["review_shingles"])
    phrases = {}
    for entry in FORBIDDEN["phrases"]:
        phrases.setdefault(entry["words"], {})[entry["sha256"]] = entry["label"]
    leaks = []
    for path in task_files(task):
        words = tokens(path.read_bytes().decode("utf-8", errors="replace"))
        name = str(path.relative_to(ROOT))
        if windows(words, size) & shingles:
            leaks.append((name, "verbatim review text"))
        for width, table in phrases.items():
            leaks.extend((name, table[hit]) for hit in windows(words, width) & table.keys())
    assert not leaks
