"""RFD review tasks grade byte-identically after HRF-02.

Every RFD-style call (no HRF parameters) runs through the current checks and
through the frozen pre-HRF copy in fixtures/review-checks-rfd/; the subchecks
must be equal on each RFD reference submission and on seeded perturbations of it.
"""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest
from gym import review_checks

GYM = Path(__file__).resolve().parents[1]
TASKS = tuple(sorted((GYM / "tasks").glob("review-*/task.toml")))
LEGACY_PATH = Path(__file__).resolve().parent / "fixtures/review-checks-rfd/review_checks_rfd.py"


def legacy():
    spec = importlib.util.spec_from_file_location("review_checks_rfd", LEGACY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEGACY = legacy()


def task_kwargs(manifest):
    truth = json.loads((manifest.parent / "expected/ground_truth.json").read_text())
    return {k: v for k, v in truth.items() if k not in ("marker", "provenance")}


def reference(manifest):
    return json.loads((manifest.parent / "expected/reference.json").read_text())["findings"]


def reads(kwargs):
    return [{"name": "Bash", "arguments": {"cmd": f"cat {path}"}} for path in kwargs["read_files"]]


def perturbations(findings, kwargs, rng):
    """Yield (findings, toolcalls) variants that exercise every RFD subcheck."""
    calls = reads(kwargs)
    files = [*kwargs["read_files"], "unrelated.py"]
    anchors = [*kwargs["defects"], *kwargs["tolerated"]] or [
        dict(file=kwargs["read_files"][0], line_start=10, line_end=10)
    ]
    yield findings, calls
    yield findings, []
    yield [], calls
    for _ in range(60):
        batch = []
        for _ in range(rng.randint(0, 5)):
            anchor = rng.choice(anchors)
            start = max(1, anchor["line_start"] + rng.randint(-8, 8))
            item = dict(
                file=rng.choice([anchor["file"], *files]),
                line_start=start,
                line_end=start + rng.randint(0, 4),
                blocking=rng.random() < 0.8,
                category=rng.choice([*review_checks.CATEGORIES, "invented"]),
                summary="perturbed",
            )
            if rng.random() < 0.1:
                item["line_end"] = item["line_start"] - 1
            batch.append(item)
        if rng.random() < 0.5:
            batch = [*findings, *batch]
        rng.shuffle(batch)
        yield batch, calls if rng.random() < 0.9 else calls[:-1]


def test_fixture_covers_the_six_rfd_review_tasks():
    assert len(TASKS) == 6
    assert "min_recall" not in LEGACY_PATH.read_text()


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_rfd_style_calls_match_pre_hrf_subchecks(manifest):
    kwargs = task_kwargs(manifest)
    rng = random.Random(manifest.parent.name)
    for findings, calls in perturbations(reference(manifest), kwargs, rng):
        assert review_checks.evaluate(findings, calls, **kwargs) == LEGACY.evaluate(
            findings, calls, **kwargs
        )


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_rfd_reference_record_is_unchanged(manifest):
    kwargs = task_kwargs(manifest)

    class Verifier:
        def __init__(self):
            self.checks = []

        def result(self):
            return {"findings": reference(manifest)}

        def toolcalls(self):
            return reads(kwargs)

        def check(self, **check):
            self.checks.append(check)

    current, frozen = Verifier(), Verifier()
    review_checks.record(current, **kwargs)
    LEGACY.record(frozen, **kwargs)
    assert current.checks == frozen.checks
    assert all(check["ok"] for check in current.checks)
    names = [check["name"] for check in current.checks]
    assert not {"recall", "decoy_resistance"} & set(names)
