"""Hard-review corpus acceptance: multi-defect recall, decoys, cross-file and a long clean control."""

from __future__ import annotations

import fcntl
import importlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gym import grader, outcomes
from gym.grader import Trajectory
from gym.review_checks import result_schema_snippet
from gym.schema import load_task

GYM = Path(__file__).resolve().parents[1]
TASKS = tuple(sorted((GYM / "tasks").glob("hard-review-*/task.toml")))
MULTI = tuple(p for p in TASKS if p.parent.name.startswith("hard-review-multi-defect-"))
# HRX-02 reworks: every defect required, inside a long diff with decoys.
MULTI_V2 = tuple(p for p in MULTI if p.parent.name.endswith("-v2"))
MULTI_V1 = tuple(p for p in MULTI if p not in MULTI_V2)
DECOY = tuple(p for p in TASKS if p.parent.name.startswith("hard-review-decoy-"))
TOOL_CALLS_V2 = GYM / "tasks/hard-review-multi-defect-tool-calls-v2/task.toml"
CROSS = GYM / "tasks/hard-review-cross-file/task.toml"
CROSS_V2 = GYM / "tasks/hard-review-cross-file-v2/task.toml"
CROSS_ALL = (CROSS, CROSS_V2)
CLEAN = GYM / "tasks/hard-review-clean-long/task.toml"
WITH_DECOYS = (*DECOY, *MULTI_V2, CLEAN)
WITH_DEFECTS = tuple(p for p in TASKS if p != CLEAN)
# Ground-truth keys the tests use that are not review_checks.record kwargs.
TRUTH_ONLY = {
    "marker",
    "provenance",
    "obvious_defect",
    "contract_file",
    "caller_file",
    "decoy_claims",
    "context_defect",
    "indirection_file",
}


def expected(manifest):
    return json.loads((manifest.parent / "expected/reference.json").read_text())


def truth(manifest):
    return json.loads((manifest.parent / "expected/ground_truth.json").read_text())


def grade(manifest, result, *, read=True):
    calls = (
        [
            {"name": "Bash", "arguments": {"cmd": f"cat {path}"}}
            for path in truth(manifest)["read_files"]
        ]
        if read
        else []
    )
    trajectory = Trajectory(
        terminal_status="succeeded",
        submitted_result=result,
        transcript_rows=[],
        control_rows=[],
        harness_calls=calls,
    )
    return grader.grade(load_task(manifest.parent), trajectory)


def check(cell, name):
    return next(item for item in cell.subchecks if item["name"] == name)


def blocking_claim(claim):
    return {
        key: claim[key] for key in ("file", "line_start", "line_end", "category", "summary")
    } | {"blocking": True}


def windows(defect):
    return defect.get("any_of", [defect])


def changed_lines(diff):
    return sum(
        1
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++ ", "--- "))
    )


def diff_files(diff):
    return {line[6:] for line in diff.splitlines() if line.startswith("+++ b/")}


def test_nine_tasks_match_the_hard_review_shape():
    assert [p.parent.name for p in TASKS] == [
        "hard-review-clean-long",
        "hard-review-cross-file",
        "hard-review-cross-file-v2",
        "hard-review-decoy-decision-replies",
        "hard-review-decoy-job-leases",
        "hard-review-multi-defect-outbox",
        "hard-review-multi-defect-outbox-v2",
        "hard-review-multi-defect-tool-calls",
        "hard-review-multi-defect-tool-calls-v2",
    ]
    for path in TASKS:
        task = load_task(path.parent)
        assert task.id == path.parent.name
        assert task.suite == "hard-review"
        assert result_schema_snippet().strip() in path.read_text()
        assert path.read_text().startswith("# Source PR")
        assert (path.parent / "verifier.py").read_text().startswith("# Source PR")
    for path in MULTI:
        data = truth(path)
        files = {w["file"] for d in data["defects"] for w in windows(d)}
        assert 3 <= len(data["defects"]) <= 4
        assert len(files) >= 2
        assert len({d["expected_category"] for d in data["defects"]}) >= 2
    origins = {p.parent.name: truth(p)["provenance"]["kind"] for p in MULTI}
    assert origins["hard-review-multi-defect-tool-calls"] == "real-derived"
    for path in MULTI_V2:
        data = truth(path)
        assert data["min_recall"] == 1.0
        original = path.parent.name.removesuffix("-v2")
        assert data["provenance"]["reworks"] == original
        # Same defect themes as the original, by category.
        assert sorted(d["expected_category"] for d in data["defects"]) == sorted(
            d["expected_category"] for d in truth(GYM / "tasks" / original / "task.toml")["defects"]
        )
    source = (
        GYM / "tasks/hard-review-multi-defect-tool-calls/expected/source-review.txt"
    ).read_text()
    blocking = source.split("## Blocking issues", 1)[1].split("## Non-blocking issues", 1)[0]
    assert sum(line.startswith("- **") for line in blocking.splitlines()) >= 2
    for path in WITH_DECOYS:
        data = truth(path)
        assert len(data["decoys"]) >= 3
        assert data["max_decoy_blocking"] == 0
        assert len(data["decoy_claims"]) == len(data["decoys"])
    for path in DECOY:
        assert 1 <= len(truth(path)["defects"]) <= 2
    assert truth(CLEAN)["clean_control"] is True
    assert truth(CLEAN)["defects"] == []


@pytest.mark.parametrize("manifest", (*DECOY, *MULTI_V2, CLEAN), ids=lambda p: p.parent.name)
def test_long_diffs_change_at_least_400_lines(manifest):
    assert changed_lines((manifest.parent / "fixture/change.diff").read_text()) >= 400


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_reference_findings_pass(manifest):
    cell = grade(manifest, expected(manifest))
    assert cell.outcome == outcomes.PASSED, cell.reason
    assert all(item["ok"] for item in cell.subchecks)


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_reference_requires_read_evidence(manifest):
    cell = grade(manifest, expected(manifest), read=False)
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "read_evidence")["ok"]


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_flag_everything_fails(manifest):
    data = truth(manifest)
    findings = [{**finding, "blocking": True} for finding in expected(manifest)["findings"]] + [
        blocking_claim(claim) for claim in data.get("decoy_claims", [])
    ]
    for path in data["read_files"]:
        lines = len((manifest.parent / "fixture" / path).read_text().splitlines())
        findings += [
            dict(
                file=path,
                line_start=start,
                line_end=min(start + 9, lines),
                blocking=True,
                category="correctness",
                summary="Suspicious code",
            )
            for start in range(1, lines + 1, 10)
        ]
    cell = grade(manifest, {"findings": findings[:32]})
    assert cell.outcome == outcomes.FAILED
    failed = {item["name"] for item in cell.subchecks if not item["ok"]}
    assert failed & {"precision", "decoy_resistance", "no_blocking"}


@pytest.mark.parametrize("manifest", MULTI, ids=lambda p: p.parent.name)
def test_obvious_defect_only_fails_recall(manifest):
    data = truth(manifest)
    obvious = expected(manifest)["findings"][data["obvious_defect"]]
    cell = grade(manifest, {"findings": [obvious]})
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "recall")["ok"]
    # The obvious defect really is one of the declared windows.
    assert any(
        w["file"] == obvious["file"] and w["line_start"] <= obvious["line_start"] <= w["line_end"]
        for w in windows(data["defects"][data["obvious_defect"]])
    )


@pytest.mark.parametrize("manifest", MULTI_V1, ids=lambda p: p.parent.name)
def test_multi_defect_recall_tolerates_one_subtle_miss(manifest):
    data = truth(manifest)
    findings = expected(manifest)["findings"]
    for index in range(len(findings)):
        if index == data["obvious_defect"]:
            continue
        cell = grade(manifest, {"findings": findings[:index] + findings[index + 1 :]})
        assert cell.outcome == outcomes.PASSED, cell.reason
        assert check(cell, "recall")["ok"]
    cell = grade(manifest, {"findings": []})
    assert cell.outcome == outcomes.FAILED


@pytest.mark.parametrize("manifest", MULTI_V2, ids=lambda p: p.parent.name)
def test_multi_defect_v2_needs_every_defect(manifest):
    findings = expected(manifest)["findings"]
    for index in range(len(findings)):
        cell = grade(manifest, {"findings": findings[:index] + findings[index + 1 :]})
        assert cell.outcome == outcomes.FAILED, index
        assert not check(cell, "recall")["ok"]
        # Every other required subcheck still passes: the miss alone fails the task.
        assert all(
            item["ok"] for item in cell.subchecks if item["required"] and item["name"] != "recall"
        )


def test_tool_calls_v2_diff_matches_staged_source():
    diff = (TOOL_CALLS_V2.parent / "fixture/change.diff").read_text()
    section = diff.split("diff --git a/tool_calls.py b/tool_calls.py\n")[1]
    added = (
        "\n".join(
            line[1:]
            for line in section.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        + "\n"
    )
    assert added == (TOOL_CALLS_V2.parent / "fixture/tool_calls.py").read_text()


def tool_calls_v2_line(needle):
    lines = (TOOL_CALLS_V2.parent / "fixture/tool_calls.py").read_text().splitlines()
    return next(index for index, line in enumerate(lines, 1) if needle in line)


@pytest.mark.parametrize(
    "needle", ['"name": call["name"]', "parsed = parse_block", "content, calls = split_tool_calls"]
)
def test_tool_calls_v2_security_credited_at_each_causal_location(needle):
    findings = expected(TOOL_CALLS_V2)["findings"]
    line = tool_calls_v2_line(needle)
    findings[0] = {**findings[0], "line_start": line, "line_end": line}
    cell = grade(TOOL_CALLS_V2, {"findings": findings})
    assert cell.outcome == outcomes.PASSED, cell.reason
    assert check(cell, "recall")["ok"]
    assert check(cell, "decoy_resistance")["ok"]


@pytest.mark.parametrize("reverse", [False, True])
def test_tool_calls_v2_duplicate_security_cannot_replace_named_choice(reverse):
    findings = expected(TOOL_CALLS_V2)["findings"]
    # Both sites describe the same missing allowlist, even though the completion
    # site is adjacent to the named-choice guard and overlaps it with slack.
    line = tool_calls_v2_line("parsed = parse_block")
    duplicate = {**findings[0], "line_start": line, "line_end": line}
    findings = [duplicate, findings[0], findings[2]]
    if reverse:
        findings.reverse()
    cell = grade(TOOL_CALLS_V2, {"findings": findings})
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "recall")["ok"]
    assert check(cell, "recall")["reason"] == "found 2 of 3 defects; required 3"
    assert check(cell, "read_evidence")["ok"]


@pytest.mark.parametrize("manifest", MULTI_V2, ids=lambda p: p.parent.name)
def test_multi_defect_v2_context_defect_lives_in_an_unchanged_file(manifest):
    """One defect is wrong only because of an unchanged staged file the diff calls into."""
    data = truth(manifest)
    context = data["context_defect"]
    diff = (manifest.parent / "fixture/change.diff").read_text()
    assert context["file"] in data["read_files"]
    assert context["file"] not in diff_files(diff)
    assert f"a/{context['file']}" not in diff
    module = context["file"].removesuffix(".py")
    changed = {f for f in diff_files(diff) if f.endswith(".py")}
    imports = (f"from {module} import", f"import {module}")
    assert any(
        any(
            line.startswith(imports)
            for line in (manifest.parent / "fixture" / f).read_text().splitlines()
        )
        for f in changed
    )
    # The reference credits the defect inside the diff, not in the context file.
    defect = data["defects"][context["defect"]]
    finding = expected(manifest)["findings"][context["defect"]]
    assert finding["file"] in diff_files(diff)
    assert any(
        w["file"] == finding["file"] and w["line_start"] <= finding["line_start"] <= w["line_end"]
        for w in windows(defect)
    )


@pytest.mark.parametrize("manifest", WITH_DECOYS, ids=lambda p: p.parent.name)
def test_one_flagged_decoy_fails(manifest):
    for claim in truth(manifest)["decoy_claims"]:
        result = expected(manifest)
        result["findings"].append(blocking_claim(claim))
        cell = grade(manifest, result)
        assert cell.outcome == outcomes.FAILED, claim
        assert not check(cell, "decoy_resistance")["ok"]
        # The same note, non-blocking, is acceptable.
        result["findings"][-1]["blocking"] = False
        assert grade(manifest, result).outcome == outcomes.PASSED


@pytest.mark.parametrize("manifest", DECOY, ids=lambda p: p.parent.name)
def test_decoy_task_needs_every_real_defect(manifest):
    findings = expected(manifest)["findings"]
    for index in range(len(findings)):
        cell = grade(manifest, {"findings": findings[:index] + findings[index + 1 :]})
        assert cell.outcome == outcomes.FAILED
        assert not check(cell, "defect_found")["ok"]


@pytest.mark.parametrize("manifest", CROSS_ALL, ids=lambda p: p.parent.name)
def test_cross_file_defect_credited_at_either_location(manifest):
    data = truth(manifest)
    diff = (manifest.parent / "fixture/change.diff").read_text()
    assert data["contract_file"] in diff_files(diff)
    assert data["caller_file"] not in diff_files(diff)
    assert data["caller_file"] in data["read_files"]
    (defect,) = data["defects"]
    assert {w["file"] for w in defect["any_of"]} == {data["contract_file"], data["caller_file"]}
    for window in defect["any_of"]:
        finding = dict(window, blocking=True, category="api-contract", summary="contract break")
        cell = grade(manifest, {"findings": [finding]})
        assert cell.outcome == outcomes.PASSED, (window, cell.reason)
    elsewhere = dict(
        file=data["contract_file"],
        line_start=1,
        line_end=1,
        blocking=True,
        category="api-contract",
        summary="contract break",
    )
    assert grade(manifest, {"findings": [elsewhere]}).outcome == outcomes.FAILED


def read_only(manifest, paths):
    trajectory = Trajectory(
        terminal_status="succeeded",
        submitted_result=expected(manifest),
        transcript_rows=[],
        control_rows=[],
        harness_calls=[{"name": "Bash", "arguments": {"cmd": f"cat {path}"}} for path in paths],
    )
    return grader.grade(load_task(manifest.parent), trajectory)


@pytest.mark.parametrize("manifest", CROSS_ALL, ids=lambda p: p.parent.name)
def test_cross_file_needs_the_unchanged_caller_read(manifest):
    cell = read_only(manifest, [truth(manifest)["contract_file"]])
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "read_evidence")["ok"]


def test_cross_file_v2_consumer_is_reached_only_through_the_indirection():
    data = truth(CROSS_V2)
    fixture = CROSS_V2.parent / "fixture"
    diff = (fixture / "change.diff").read_text()
    caller = data["caller_file"].removesuffix(".py")
    indirection = data["indirection_file"]
    assert indirection in data["read_files"]
    assert indirection not in diff_files(diff)
    # No module imports the consumer, and the diff never names it or its handler:
    # searching from the changed contract does not find the broken consumer.
    handler = f"{caller}:"
    (target,) = [t for t in (fixture / indirection).read_text().split('"') if t.startswith(handler)]
    function = target.split(":", 1)[1]
    assert function not in diff and caller not in diff
    for path in fixture.glob("*.py"):
        text = path.read_text()
        assert f"import {caller}" not in text and f"from {caller} import" not in text
        if path.name != data["caller_file"]:
            assert function not in text or path.name == indirection
    # Reading the contract and the consumer without the indirection file fails.
    cell = read_only(CROSS_V2, sorted(set(data["read_files"]) - {indirection}))
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "read_evidence")["ok"]


def test_clean_control_fails_with_one_blocking_finding():
    finding = dict(
        file="notify.py",
        line_start=110,
        line_end=110,
        blocking=True,
        category="correctness",
        summary="tick ignores the rate limit",
    )
    cell = grade(CLEAN, {"findings": [finding]})
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "no_blocking")["ok"]
    finding["blocking"] = False
    assert grade(CLEAN, {"findings": [finding]}).outcome == outcomes.PASSED
    assert "intentional" in (CLEAN.parent / "fixture/pr.md").read_text().lower()


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_staged_sandbox_holds_no_ground_truth(manifest, tmp_path):
    directory = manifest.parent
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    subprocess.run(
        ["bash", str(directory / "stages/stage-input.sh"), str(sandbox)],
        check=True,
        capture_output=True,
    )
    data = truth(manifest)
    assert {p.name for p in sandbox.iterdir()} == {"change.diff", "pr.md", *data["read_files"]}
    assert not any(p.is_symlink() for p in sandbox.rglob("*"))
    staged = "\n".join(p.read_text() for p in sandbox.iterdir())
    for token in (
        "GYM-GROUND-TRUTH-NEVER-STAGE",
        "expected_category",
        "review_checks.record",
        "Recommended fix",
        "ground_truth",
        "decoy",
        "Decoy",
        "Maintainer provenance",
        "source-remediation",
    ):
        assert token not in staged
    for finding in expected(manifest)["findings"]:
        assert finding["summary"] not in staged
    for claim in data.get("decoy_claims", []):
        assert claim["summary"] not in staged
    review = directory / "expected/source-review.txt"
    if review.exists():
        prose = [line.strip() for line in review.read_text().splitlines()]
        for line in prose:
            if len(line) > 60 and "`" not in line:
                assert line not in staged
    remediation = directory / "expected/source-remediation.diff"
    if remediation.exists():
        # Lines the fix introduced (not merely moved or re-wrapped by the formatter)
        # must not appear anywhere staged.
        lines = remediation.read_text().splitlines()
        removed = "".join(
            "".join(line[1:].split())
            for line in lines
            if line.startswith("-") and not line.startswith("---")
        )
        added = {
            line[1:].strip()
            for line in lines
            if line.startswith("+")
            and not line.startswith("+++")
            and len(line.strip()) > 40
            and "".join(line[1:].split()) not in removed
        }
        assert len(added) >= 10
        assert not added & {line.strip() for line in staged.splitlines()}
    # Diff and post-change files agree: reverse then forward reproduces every byte.
    files = [
        f for f in data["read_files"] if f in diff_files((sandbox / "change.diff").read_text())
    ]
    originals = {f: (sandbox / f).read_bytes() for f in data["read_files"]}
    for args in (["--reverse"], []):
        subprocess.run(
            ["git", "apply", *args, str(sandbox / "change.diff")],
            cwd=sandbox,
            check=True,
            capture_output=True,
        )
    assert files
    assert {f: (sandbox / f).read_bytes() for f in data["read_files"]} == originals


# The first line of every defect window, per task, keyed by (file, line_start).
NEEDLES = {
    "hard-review-multi-defect-tool-calls": {
        ("tool_calls.py", 60): 'isinstance(call.get("name"), str)',
        ("tool_calls.py", 116): '"tool_choice") == "none"',
        ("tool_calls.py", 133): "parse_tool_text(text, tokenizer, tools)",
        ("server_supervisor.py", 161): 'payload.get("stream")',
        ("server_supervisor.py", 80): 'payload.get("tools")',
    },
    "hard-review-multi-defect-outbox": {
        ("dispatcher.py", 24): "message.attempts >= MAX_ATTEMPTS",
        ("dispatcher.py", 54): "except DeliveryError",
        ("sender.py", 12): "class PermanentFailure(DeliveryError)",
        ("store.py", 48): "rows = self._conn.execute(",
        ("store.py", 74): "def dead_letter(",
    },
    "hard-review-cross-file": {
        ("registry.py", 47): "def find_entry(",
        ("sync.py", 26): "try:",
    },
    "hard-review-multi-defect-outbox-v2": {
        ("dispatcher.py", 121): "except DeliveryError",
        ("sender.py", 20): "class PermanentFailure(DeliveryError)",
        ("store.py", 90): "due = self._db.execute(",
        ("store.py", 148): "cursor = self._db.execute(",
        ("store.py", 208): "def purge(",
    },
    "hard-review-multi-defect-tool-calls-v2": {
        ("tool_calls.py", 154): "parse_block(family",
        ("tool_calls.py", 174): '"name": call["name"]',
        ("tool_calls.py", 199): "split_tool_calls(text",
        ("tool_calls.py", 68): "def requires_call",
        ("tool_calls.py", 189): "if choice.requires_call:",
        ("tool_calls.py", 200): "if choice.requires_call and not calls",
        ("bridge.py", 158): "if request.uses_tools:",
    },
    "hard-review-cross-file-v2": {
        ("registry.py", 58): "def _emit(",
        ("audit.py", 12): "def record_change(",
    },
    "hard-review-decoy-decision-replies": {
        ("replies.py", 71): "decision = store.get(decision_id)",
        ("reconcile.py", 62): "if decision.id in receipts:",
    },
    "hard-review-decoy-job-leases": {
        ("jobs.py", 111): "cursor = self.db.execute(",
        ("sweeper.py", 33): "time.monotonic()",
    },
}


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_verifier_matches_ground_truth_and_staged_source(manifest):
    import ast

    data = truth(manifest)
    tree = ast.parse((manifest.parent / "verifier.py").read_text())
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
    )
    declared = ast.literal_eval(call.keywords[0].value)
    assert declared == {k: v for k, v in data.items() if k not in TRUTH_ONLY}
    assert sorted(data["read_files"]) == sorted(
        p.name
        for p in (manifest.parent / "fixture").iterdir()
        if p.name not in ("change.diff", "pr.md")
    )
    for defect in data["defects"]:
        for window in windows(defect):
            lines = (manifest.parent / "fixture" / window["file"]).read_text().splitlines()
            needle = NEEDLES[manifest.parent.name][window["file"], window["line_start"]]
            assert needle in lines[window["line_start"] - 1]
            assert window["line_end"] <= len(lines)
    for window in data.get("decoys", []):
        lines = (manifest.parent / "fixture" / window["file"]).read_text().splitlines()
        assert window["line_end"] <= len(lines)
    for claim in data.get("decoy_claims", []):
        assert any(
            claim["file"] == w["file"] and w["line_start"] <= claim["line_start"] <= w["line_end"]
            for w in data["decoys"]
        )


@pytest.fixture
def fixture_module(monkeypatch):
    """Import a fixture module with its sibling modules importable; undo afterwards."""
    # Never write __pycache__ into a task's fixture directory.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    saved_path = list(sys.path)
    saved_modules = {}

    def load(task_id, name):
        directory = GYM / "tasks" / task_id / "fixture"
        sys.path.insert(0, str(directory))
        importlib.invalidate_caches()
        for path in directory.glob("*.py"):
            saved_modules.setdefault(path.stem, sys.modules.get(path.stem))
            sys.modules.pop(path.stem, None)
        return importlib.import_module(name)

    yield load
    sys.path[:] = saved_path
    for stem, module in saved_modules.items():
        if module is None:
            sys.modules.pop(stem, None)
        else:
            sys.modules[stem] = module


# Fixture behaviour: every seeded defect reproduces, and every decoy is correct.


def test_outbox_defects_reproduce(fixture_module, tmp_path):
    dispatcher = fixture_module("hard-review-multi-defect-outbox", "dispatcher")
    store, sender = sys.modules["store"], sys.modules["sender"]
    path = str(tmp_path / "outbox.db")
    outbox = store.Outbox(path)
    attempts = []

    class Failing:
        def __init__(self, error):
            self.error = error

        def send(self, topic, body):
            attempts.append(topic)
            raise self.error

    outbox.enqueue("retry", "{}")
    worker = dispatcher.Dispatcher(outbox, Failing(sender.DeliveryError("503")))
    while worker.run_once():
        pass
    assert len(attempts) == 6  # pr.md: at most 5 attempts.
    assert outbox.dead_letters() == []  # pr.md: dead letters stay for replay.
    assert outbox.counts() == {}

    outbox.enqueue("rejected", "{}")
    dispatcher.Dispatcher(outbox, Failing(sender.PermanentFailure("400"))).run_once()
    assert outbox.counts() == {"pending": 1}  # pr.md: dead-lettered at once.

    first, second = store.Outbox(path), store.Outbox(path)
    message = outbox.enqueue("race", "{}")
    outbox._conn.execute("UPDATE outbox SET state = 'sent' WHERE id != ?", (message,))
    real = first._conn
    stolen = []

    class Interleave:
        def execute(self, sql, params=()):
            cursor = real.execute(sql, params)
            if sql.startswith("SELECT") and not stolen:
                rows = cursor.fetchall()
                stolen.extend(second.claim_batch("second"))
                return types.SimpleNamespace(fetchall=lambda: rows)
            return cursor

    first._conn = Interleave()
    mine = first.claim_batch("first")
    assert [m.id for m in mine] == [m.id for m in stolen] == [message]


def test_cross_file_caller_breaks_on_new_names(fixture_module, tmp_path):
    sync = fixture_module("hard-review-cross-file", "sync")
    registry = sys.modules["registry"].Registry(tmp_path / "registry.json")
    registry.create_entry("old", {"a": 1})
    with pytest.raises(AttributeError):
        sync.sync(registry, {"old": {"a": 1}, "new": {"a": 2}}, prune=False)


def outbox_v2(fixture_module, tmp_path):
    store = fixture_module("hard-review-multi-defect-outbox-v2", "store")
    path = str(tmp_path / "outbox.db")
    outbox = store.Outbox(importlib.import_module("db").Database(path))
    outbox.migrate()
    return outbox, path


class FailingSender:
    def __init__(self, error):
        self.error = error
        self.attempts = 0

    def send(self, topic, body):
        self.attempts += 1
        raise self.error


def test_outbox_v2_defects_reproduce(fixture_module, tmp_path):
    outbox, path = outbox_v2(fixture_module, tmp_path)
    store, db, sender = (importlib.import_module(n) for n in ("store", "db", "sender"))
    dispatcher = importlib.import_module("dispatcher")

    # pr.md: a permanently rejected message is dead-lettered at once. PermanentFailure
    # subclasses DeliveryError (sender.py), so the earlier clause retries it instead.
    rejected = outbox.enqueue("rejected", "{}", now=0)
    worker = dispatcher.Dispatcher(
        outbox, FailingSender(sender.PermanentFailure("400")), clock=lambda: 0, worker="w"
    )
    worker.run_once()
    assert outbox.state_of(rejected) == "pending"

    # pr.md: an expired claim on the fifth attempt goes to dead; it goes to pending.
    expired = outbox.enqueue("expired", "{}", now=0)
    outbox._db.execute("UPDATE outbox SET attempts = 4 WHERE id = ?", (expired,))
    outbox._db.execute("UPDATE outbox SET state = 'sent' WHERE id != ?", (expired,))
    (claimed,) = outbox.claim_batch("w", now=0, limit=10)
    assert claimed.attempts == store.MAX_ATTEMPTS
    assert outbox.reclaim_expired(now=store.CLAIM_TIMEOUT_SECONDS + 1) == 1
    assert outbox.state_of(expired) == "pending"

    # pr.md: discard is the only command that removes a dead letter; purge deletes it.
    outbox._db.execute("UPDATE outbox SET state = 'dead', updated_at = 0 WHERE id = ?", (expired,))
    assert outbox.purge(older_than=1, now=100) >= 1
    assert outbox.state_of(expired) is None

    # Two dispatchers both claim one message: the SELECT is outside the transaction.
    race = outbox.enqueue("race", "{}", now=0)
    first = store.Outbox(db.Database(path))
    second = store.Outbox(db.Database(path))
    real = first._db
    stolen = []

    class Interleave:
        def execute(self, sql, params=()):
            cursor = real.execute(sql, params)
            if sql.startswith("SELECT id") and not stolen:
                rows = cursor.fetchall()
                stolen.extend(second.claim_batch("second", now=0, limit=10))
                return types.SimpleNamespace(fetchall=lambda: rows)
            return cursor

        def transaction(self):
            return real.transaction()

    first._db = Interleave()
    mine = first.claim_batch("first", now=0, limit=10)
    assert [m.id for m in mine] == [m.id for m in stolen] == [race]


def test_outbox_v2_decoys_are_correct(fixture_module, tmp_path):
    outbox, path = outbox_v2(fixture_module, tmp_path)
    store, db, sender = (importlib.import_module(n) for n in ("store", "db", "sender"))
    dispatcher = importlib.import_module("dispatcher")

    # Five attempts, then dead; the waits after attempts 1..4 are 30, 60, 120, 240.
    message = outbox.enqueue("retry", "{}", now=0)
    failing = FailingSender(sender.DeliveryError("503"))
    clock = [0.0]
    worker = dispatcher.Dispatcher(outbox, failing, clock=lambda: clock[0], worker="w")
    waits = []
    while outbox.state_of(message) == "pending":
        due = outbox._db.execute(
            "SELECT next_attempt_at FROM outbox WHERE id = ?", (message,)
        ).fetchone()[0]
        waits.append(due - clock[0])
        clock[0] = due
        worker.run_once()
    assert failing.attempts == store.MAX_ATTEMPTS
    assert waits[1:] == [30, 60, 120, 240]
    assert outbox.state_of(message) == "dead"

    # A batch that raises is logged; its claim expires and is released.
    class Broken:
        def send(self, topic, body):
            raise RuntimeError("bug")

    held = outbox.enqueue("broken", "{}", now=clock[0])
    ticks = iter((False, True))
    dispatcher.Dispatcher(outbox, Broken(), clock=lambda: clock[0], worker="w").run_forever(
        idle_sleep=0, stop=lambda: next(ticks)
    )
    assert outbox.state_of(held) == "claimed"
    assert outbox.reclaim_expired(now=clock[0] + store.CLAIM_TIMEOUT_SECONDS + 1) == 1
    assert outbox.state_of(held) == "pending"

    # replay runs in a BEGIN IMMEDIATE transaction: a rival waits for the lock.
    rival = store.Outbox(db.Database(path, busy_timeout=0.05))
    with outbox._db.transaction():
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            rival.replay(message, now=0)
    assert outbox.replay(message, now=0)
    assert not outbox.replay(message, now=0)
    assert outbox.get(message).attempts == 0

    # discard deletes only dead letters, and pr.md makes it the operator's choice.
    assert not outbox.discard(message)
    assert outbox.state_of(message) == "pending"


def tool_bridge(fixture_module, text, *, tool_format="hermes", finish="stop"):
    bridge = fixture_module("hard-review-multi-defect-tool-calls-v2", "bridge")
    rendered = []

    class Backend:
        model_name = "local"
        tokenizer = types.SimpleNamespace(tool_format=tool_format)

        def generate(self, messages, max_tokens, tools=None):
            rendered.append(tools)
            return text, finish

        def stream(self, messages, max_tokens):
            yield text, finish

    return bridge.Bridge(Backend()), rendered


def tool(name, **properties):
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {"type": "object", "properties": properties},
        },
    }


TOOLS = [tool("read_file", path={"type": "string"}, n={"type": "integer"}), tool("search")]
USER = [{"role": "user", "content": "go"}]


@pytest.mark.parametrize("call_id", [[], {}, None, "", 42])
def test_tool_calls_v2_rejects_malformed_history_ids(fixture_module, call_id):
    server, _ = tool_bridge(fixture_module, "plain text")
    call = {"id": call_id, "type": "function", "function": {"name": "search", "arguments": "{}"}}
    status, body = server.route(
        "POST", "/v1/chat/completions", {"messages": [{"role": "assistant", "tool_calls": [call]}]}
    )
    assert status == 400
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.parametrize("function", [None, [], "search", {}, {"name": "search", "arguments": {}}])
def test_tool_calls_v2_rejects_malformed_history_functions(fixture_module, function):
    server, _ = tool_bridge(fixture_module, "plain text")
    call = {"id": "call_1", "type": "function", "function": function}
    status, body = server.route(
        "POST", "/v1/chat/completions", {"messages": [{"role": "assistant", "tool_calls": [call]}]}
    )
    assert status == 400
    assert body["error"]["type"] == "invalid_request_error"


def test_tool_calls_v2_defects_reproduce(fixture_module):
    call = '<tool_call>{"name": "delete_repo", "arguments": {}}</tool_call>'
    server, _ = tool_bridge(fixture_module, call)
    status, body = server.route("POST", "/v1/chat/completions", {"messages": USER, "tools": TOOLS})
    # A call to a function the request never offered is returned to the client.
    assert status == 200
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "delete_repo"

    named = {"type": "function", "function": {"name": "read_file"}}
    server, _ = tool_bridge(fixture_module, "plain text")
    payload = {"messages": USER, "tools": TOOLS, "tool_choice": named}
    # A named tool_choice must produce a call, as required does; plain text passes.
    assert server.route("POST", "/v1/chat/completions", payload)[0] == 200
    required = dict(payload, tool_choice="required")
    assert server.route("POST", "/v1/chat/completions", required)[0] == 422

    server, _ = tool_bridge(fixture_module, "plain text", tool_format=None)
    assert server.route("POST", "/v1/chat/completions", payload)[0] == 200
    assert server.route("POST", "/v1/chat/completions", required)[0] == 400

    # Streaming never resolves tool_choice, so a required call streams plain text.
    server, _ = tool_bridge(fixture_module, "plain text")
    status, chunks = server.route("POST", "/v1/chat/completions", dict(required, stream=True))
    assert status == 200
    assert "tool_calls" not in json.dumps(list(chunks))


def test_tool_calls_v2_decoys_are_correct(fixture_module):
    server, rendered = tool_bridge(fixture_module, "plain text")
    server.route("POST", "/v1/chat/completions", {"messages": USER, "tools": TOOLS})
    named = {"type": "function", "function": {"name": "search"}}
    server.route(
        "POST", "/v1/chat/completions", {"messages": USER, "tools": TOOLS, "tool_choice": named}
    )
    # OpenAI defaults tool_choice to auto when tools are present; a named choice
    # renders the one function the model is allowed to call.
    assert [[t["function"]["name"] for t in tools] for tools in rendered] == [
        ["read_file", "search"],
        ["search"],
    ]

    call = (
        "<tool_call><function=read_file><parameter=path>a.txt</parameter>"
        "<parameter=n>3</parameter></function></tool_call>"
    )
    server, _ = tool_bridge(fixture_module, call, tool_format="qwen3", finish="length")
    status, body = server.route("POST", "/v1/chat/completions", {"messages": USER, "tools": TOOLS})
    choice = body["choices"][0]
    # Parsers always return arguments as a dict, so json.dumps encodes them once.
    assert status == 200
    assert choice["message"]["tool_calls"][0]["function"]["arguments"] == (
        '{"path": "a.txt", "n": 3}'
    )
    # A truncated completion reports length even when it carries calls.
    assert choice["finish_reason"] == "length"
    hermes = '<tool_call>{"name": "search", "arguments": "{\\"q\\": \\"x\\"}"}</tool_call>'
    server, _ = tool_bridge(fixture_module, hermes)
    status, body = server.route("POST", "/v1/chat/completions", {"messages": USER, "tools": TOOLS})
    arguments = body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"q": "x"}


def test_cross_file_v2_audit_trail_breaks_through_the_wiring(fixture_module, tmp_path, monkeypatch):
    wiring = fixture_module("hard-review-cross-file-v2", "wiring")
    audit, webhooks = importlib.import_module("audit"), importlib.import_module("webhooks")
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(webhooks, "ENDPOINTS", ["http://u1", "http://u2"])
    monkeypatch.setattr(webhooks, "PENDING", webhooks.deque())
    bus = wiring.wire(importlib.import_module("events").EventBus())
    registry = importlib.import_module("registry").Registry(tmp_path / "registry.json", bus)
    registry.create("svc", {"replicas": 1}, tags=("prod",))
    registry.update("svc", {"replicas": 2})
    # The audit subscriber still expects an Entry; the dict payload makes it raise,
    # and the bus swallows that, so the compliance trail is silently empty.
    assert audit.read_trail() == []

    # Webhooks consume the snapshot correctly and keep per-endpoint order.
    posted = []

    def post(url, body):
        if url == "http://u1":
            raise OSError("down")
        posted.append((url, body["event"], body["settings"]))

    assert webhooks.flush(post=post) == 2
    assert posted == [
        ("http://u2", "entry.created", {"replicas": 1}),
        ("http://u2", "entry.updated", {"replicas": 2}),
    ]
    assert [url for url, _ in webhooks.PENDING] == ["http://u1", "http://u1"]
    assert [body["event"] for _, body in webhooks.PENDING] == ["entry.created", "entry.updated"]


def test_decision_replies_defects_reproduce(fixture_module, tmp_path):
    replies = fixture_module("hard-review-decoy-decision-replies", "replies")
    decisions = sys.modules["decisions"]
    reconcile = importlib.import_module("reconcile")
    store = decisions.DecisionStore(str(tmp_path / "decisions.db"))
    store.raise_decision("d1", "worker-1", "ship?", now=0)
    sent = []

    def dispatch(worker, answer):
        sent.append((worker, answer))
        return f"r{len(sent)}"

    # A concurrent reply wins the lock between the unlocked check and the lock.
    original = store.dispatched
    raced = []

    def dispatched(decision_id):
        result = original(decision_id)
        if not raced:
            raced.append(True)
            replies.reply(store, tmp_path, decision_id, "yes", dispatch, now=1)
        return result

    store.dispatched = dispatched
    with pytest.raises(sqlite3.IntegrityError):
        replies.reply(store, tmp_path, "d1", "yes", dispatch, now=2)
    assert len(sent) == 2  # pr.md: exactly once.

    store.raise_decision("d2", "worker-2", "merge?", now=0)
    receipts = tmp_path / "decisions/page-receipts.json"
    for minutes in (16, 61, 300):
        reconcile.page_deadlines(store, receipts, now=minutes * 60)
    assert store.pages("d2") == [15]  # pr.md: 15, 60 and 240.


def test_decision_replies_decoys_are_correct(fixture_module, tmp_path):
    replies = fixture_module("hard-review-decoy-decision-replies", "replies")
    decisions = sys.modules["decisions"]
    reconcile = importlib.import_module("reconcile")
    path = tmp_path / "decisions.db"
    store = decisions.DecisionStore(str(path))
    sent = []

    def dispatch(worker, answer):
        sent.append(answer)
        return f"r{len(sent)}"

    # Re-taking the reply lock in-process would block, so _lock_held is required.
    with replies.locked(tmp_path, "x"):
        with open(replies.lock_path(tmp_path, "x"), "a") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store.raise_decision("d1", "w", "q", now=0)
    store.record_answer("d1", "recorded", now=1)
    # The replay holds the lock and passes _lock_held=True; it must not deadlock.
    done = []
    worker = threading.Thread(
        target=lambda: done.append(
            reconcile.reconcile_answered(
                decisions.DecisionStore(str(path)), tmp_path, dispatch, now=2
            )
        )
    )
    worker.start()
    worker.join(timeout=10)
    assert done == [["d1"]]
    assert sent == ["recorded"]

    # with self._conn commits: another connection sees the dispatch row.
    assert sqlite3.connect(path).execute("SELECT receipt FROM dispatches").fetchall() == [("r1",)]

    # The first answer record is kept; vars() works on the non-slotted dataclass.
    first = replies.load_answer_record(tmp_path, "d1")
    again = replies._write_answer_record(tmp_path, store.get("d1"), "r9", now=9)
    assert again == first == replies.load_answer_record(tmp_path, "d1")
    assert first["decision"]["id"] == "d1"

    # fchmod repairs a pre-existing world-readable diagnostics log.
    log = replies.diagnostic_path(tmp_path)
    log.chmod(0o644)
    replies._log_diagnostic(tmp_path, "probe")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600

    # Legacy "cancelled" rows prune alongside "canceled"; values are bound.
    store._conn.execute(
        "INSERT INTO decisions (id, worker, question, status, raised_at, answered_at)"
        " VALUES ('legacy''); DROP TABLE decisions; --', 'w', 'q', 'cancelled', 0, 1)"
    )
    store.raise_decision("d3", "w", "q", now=0)
    store.cancel("d3", now=1)
    store.raise_decision("open", "w", "q", now=0)
    assert store.prune_terminal(older_than=100) == 3
    assert [d.id for d in store.open_decisions()] == ["open"]


def test_job_leases_defects_reproduce(fixture_module, tmp_path):
    jobs = fixture_module("hard-review-decoy-job-leases", "jobs")
    db = sys.modules["db"]
    sweeper = importlib.import_module("sweeper")
    queue = jobs.JobQueue(db.Database(tmp_path / "jobs.db"))
    job = queue.enqueue("build", {}, now=0)
    wall = time.time()
    queue.claim("a", now=wall - 120, seconds=60)
    # The lease expired a minute ago by wall clock, but the default sweep misses it.
    assert sweeper.sweep(queue) == {"queued": [], "dead": []}
    assert sweeper.sweep(queue, now=wall) == {"queued": [job], "dead": []}
    queue.claim("b", now=wall)
    queue.renew(job, "a", now=wall + 1)  # pr.md: the stale holder gets LeaseLost.
    assert queue.holder_of(job) == "b"


def test_job_leases_decoys_are_correct(fixture_module, tmp_path, monkeypatch):
    jobs = fixture_module("hard-review-decoy-job-leases", "jobs")
    db = sys.modules["db"]
    sweeper = importlib.import_module("sweeper")
    database = db.Database(tmp_path / "jobs.db")
    queue = jobs.JobQueue(database)
    rival = jobs.JobQueue(db.Database(tmp_path / "jobs.db", busy_timeout=0.05))
    job = queue.enqueue("build", {}, now=0)

    # BEGIN IMMEDIATE serialises claimers; the lock error arrives as BusyError.
    with database.transaction():
        with pytest.raises(db.BusyError):
            rival.claim("rival", now=0)
    assert queue.holder_of(job) is None

    # attempts counts claims, so the third failed attempt kills the job.
    states = []
    for attempt in range(3):
        assert queue.claim("a", now=attempt).attempts == attempt + 1
        states.append(queue.fail(job, "a", "boom", now=attempt))
    assert states == ["queued", "queued", "dead"]

    # The last busy attempt re-raises, so swept is never read unbound.
    monkeypatch.setattr(sweeper.time, "sleep", lambda seconds: None)

    def busy(queue, now):
        raise db.BusyError("database is locked")

    monkeypatch.setattr(sweeper, "_sweep_once", busy)
    with pytest.raises(db.BusyError):
        sweeper.sweep(queue, now=0)
    monkeypatch.undo()

    # The read-only connection yields named-column rows.
    assert sweeper.status_report(database) == {"states": {"dead": 1}, "leased": 0}

    # A failed transaction rolls back and re-raises.
    with pytest.raises(RuntimeError):
        with database.transaction():
            database.execute("INSERT INTO jobs (kind, payload, enqueued_at) VALUES ('x', '{}', 0)")
            raise RuntimeError("abort")
    assert queue.counts() == {"dead": 1}


def test_clean_control_is_correct(fixture_module, tmp_path):
    notify = fixture_module("hard-review-clean-long", "notify")
    alerts, digest_module = sys.modules["alerts"], sys.modules["digest"]
    quiet = sys.modules["quiet_hours"].QuietHours.parse("22:00-07:00", "America/New_York")

    def utc(*args):
        return datetime(*args, tzinfo=timezone.utc)

    # The midnight-wrapping window, half-open, across a DST change (EST then EDT).
    assert quiet.contains(utc(2026, 3, 7, 4, 30))
    assert quiet.contains(utc(2026, 3, 7, 11, 59))
    assert not quiet.contains(utc(2026, 3, 7, 12, 0))
    assert not quiet.contains(utc(2026, 3, 7, 18, 0))
    assert quiet.contains(utc(2026, 3, 9, 10, 59))
    assert not quiet.contains(utc(2026, 3, 9, 11, 0))

    sent = []
    clock = [utc(2026, 1, 10, 4, 30).timestamp()]
    notifier = notify.build_notifier(sent.append, tmp_path, "22:00-07:00", "America/New_York")
    notifier._clock = lambda: clock[0]

    def alert(severity, key, raised_at=None):
        return alerts.Alert(
            "src", key, severity, "text", clock[0] if raised_at is None else raised_at
        )

    assert notifier.notify(alert("info", "old", clock[0] - 5)) == "held"
    assert notifier.notify(alert("warning", "w")) == "held"
    assert notifier.notify(alert("warning", "w")) == "duplicate"
    assert notifier.notify(alert("critical", "c")) == "sent"
    state = tmp_path / notify.DIGEST_FILE
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert notifier.tick() == 0

    # A failed send keeps every held alert; the digest is most severe first.
    clock[0] += 9 * 3600

    def refuse(text):
        raise OSError("transport down")

    notifier._send = refuse
    with pytest.raises(OSError):
        notifier.tick()
    assert len(digest_module.Digest(state)) == 2
    notifier._send = sent.append
    assert notifier.tick() == 2
    assert sent[-1].splitlines()[2:] == ["- [warning] src/w: text", "- [info] src/old: text"]
    assert len(digest_module.Digest(state)) == 0

    # Critical alerts bypass the rate limit by design.
    outcomes_seen = notifier.notify_many([alert("info", f"r{i}") for i in range(25)])
    assert outcomes_seen == {"sent": 20, "dropped": 5}
    assert notifier.notify(alert("critical", "c2")) == "sent"
    assert os.path.exists(state)
