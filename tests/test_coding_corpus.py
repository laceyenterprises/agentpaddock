"""The coding suite: every task loads, and its ground truth matches what is staged.

A verifier whose expected line number drifts from the file the stage actually
writes is worse than no task: it grades every model wrong and looks like a
model problem. These tests re-derive the ground truth from the staged output
instead of trusting the constant.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GYM = REPO_ROOT / "modules" / "adversarial-eval-gym"
sys.path.insert(0, str(GYM / "lib" / "python"))

from gym import grader, outcomes  # noqa: E402
from gym.grader import Trajectory  # noqa: E402
from gym.schema import load_task  # noqa: E402

CODING_TASKS = tuple(
    path.parent.name for path in sorted((GYM / "tasks").glob("coding-*/task.toml"))
)

NEW_REAL_REGRESSION_CASES = {
    "coding-nondict-migrate-audit": {
        "post_fix": {
            "defect_line": 4,
            "guard_needed": True,
            "non_dict_action": "skip-audit",
        },
        "pre_fix": {
            "defect_line": 4,
            "guard_needed": False,
            "non_dict_action": "raise-failure",
        },
        "opened": "daemon_excerpt.py",
    },
    "coding-pr-not-found-terminal": {
        "post_fix": {
            "defect_line": 14,
            "terminal_status": "skipped",
            "retryable": False,
        },
        "pre_fix": {
            "defect_line": 14,
            "terminal_status": "failed",
            "retryable": True,
        },
        "opened": "batch_activation.py",
    },
    "coding-schema-behind-wording": {
        "post_fix": {
            "defect_line": 4,
            "missing_wording": "behind Alembic head",
            "recoverable": True,
        },
        "pre_fix": {
            "defect_line": 4,
            "missing_wording": "not at Alembic head",
            "recoverable": False,
        },
        "opened": "schema_parser.py",
    },
    "coding-unknown-change-kind-sentinel": {
        "post_fix": {
            "defect_line": 3,
            "sentinel_handling": "fall-through",
            "invalid_explicit_still_errors": True,
        },
        "pre_fix": {
            "defect_line": 3,
            "sentinel_handling": "raise",
            "invalid_explicit_still_errors": True,
        },
        "opened": "change_kind.py",
    },
}


def _stage(task_id: str, dest: Path) -> None:
    script = GYM / "tasks" / task_id / "stages" / "stage-input.sh"
    subprocess.run(["bash", str(script), str(dest)], check=True, capture_output=True)


def _line_of(path: Path, needle: str) -> int:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return index
    raise AssertionError(f"{needle!r} not found in {path}")


def _verifier_const(task_id: str, name: str) -> str:
    for line in (GYM / "tasks" / task_id / "verifier.py").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name} ="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{name} not found in {task_id} verifier")


def _trajectory(result: dict[str, object], opened: str) -> Trajectory:
    return Trajectory(
        terminal_status="succeeded",
        submitted_result=result,
        transcript_rows=[
            {
                "ordinal": 1,
                "event_type": "tool_call",
                "event_role": "assistant",
                "tool_metadata_json": {
                    "name": "Bash",
                    "arguments": {"cmd": f"sed -n '1,120p' {opened}"},
                    "call_id": "call-1",
                },
                "content_json": None,
            }
        ],
        control_rows=[],
    )


def test_every_coding_task_loads_with_the_coding_suite():
    assert len(CODING_TASKS) >= 12
    for task_id in CODING_TASKS:
        task = load_task(GYM / "tasks" / task_id)
        assert task.id == task_id
        assert task.suite == "coding"
        assert task.result_schema, task_id
        assert task.stages, f"{task_id} must stage its input"


def test_ground_truth_matches_the_staged_files():
    """Re-derive each expected line from what the stage actually writes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        jsonb = root / "jsonb"
        jsonb.mkdir()
        _stage("coding-jsonb-like-predicate", jsonb)
        assert _line_of(jsonb / "query.sql", "metadata_json LIKE") == int(
            _verifier_const("coding-jsonb-like-predicate", "EXPECTED_LINE")
        )
        # The defect only exists because of the column type, so the schema must
        # actually declare it as jsonb.
        assert "metadata_json     jsonb" in (jsonb / "schema.sql").read_text(encoding="utf-8")

        commit = root / "commit"
        commit.mkdir()
        _stage("coding-missing-commit", commit)
        loop = commit / "loop.py"
        assert _line_of(loop, "conn.execute(") == int(
            _verifier_const("coding-missing-commit", "INSERT_LINE")
        )
        assert _line_of(loop, "owned.close()") == int(
            _verifier_const("coding-missing-commit", "TEARDOWN_LINE")
        )
        # The whole task is that no commit exists on the write path.
        assert "commit()" not in loop.read_text(encoding="utf-8")

        pipefail = root / "pipefail"
        pipefail.mkdir()
        _stage("coding-pipefail-silent-exit", pipefail)
        probe = pipefail / "probe.sh"
        assert _line_of(probe, "LATEST_META=") == int(
            _verifier_const("coding-pipefail-silent-exit", "EXPECTED_LINE")
        )
        # The trap is that the line already looks defended.
        assert (
            "2>/dev/null"
            in probe.read_text(encoding="utf-8").splitlines()[_line_of(probe, "LATEST_META=") - 1]
        )
        assert "set -euo pipefail" in probe.read_text(encoding="utf-8")

        nondict = root / "nondict"
        nondict.mkdir()
        _stage("coding-nondict-migrate-audit", nondict)
        assert _line_of(
            nondict / "daemon_excerpt.py", 'pg_auto_migrate_payload.get("outcome")'
        ) == int(_verifier_const("coding-nondict-migrate-audit", "EXPECTED_LINE"))

        pr_not_found = root / "pr-not-found"
        pr_not_found.mkdir()
        _stage("coding-pr-not-found-terminal", pr_not_found)
        assert _line_of(pr_not_found / "batch_activation.py", "return STATUS_FAILED") == int(
            _verifier_const("coding-pr-not-found-terminal", "EXPECTED_LINE")
        )

        behind_wording = root / "behind-wording"
        behind_wording.mkdir()
        _stage("coding-schema-behind-wording", behind_wording)
        assert _line_of(behind_wording / "schema_parser.py", "not at Alembic head") == int(
            _verifier_const("coding-schema-behind-wording", "EXPECTED_LINE")
        )

        unknown = root / "unknown"
        unknown.mkdir()
        _stage("coding-unknown-change-kind-sentinel", unknown)
        assert _line_of(unknown / "change_kind.py", "normalize_change_kind(explicit)") == int(
            _verifier_const("coding-unknown-change-kind-sentinel", "EXPECTED_LINE")
        )


def test_stages_never_write_the_answer_into_the_sandbox():
    """A stage that leaks ground truth makes the task free."""
    forbidden = ("type-mismatch", "missing-commit", "EXPECTED_")
    with tempfile.TemporaryDirectory() as tmp:
        for task_id in CODING_TASKS:
            dest = Path(tmp) / task_id
            dest.mkdir()
            _stage(task_id, dest)
            for staged in dest.rglob("*"):
                if not staged.is_file():
                    continue
                body = staged.read_text(encoding="utf-8", errors="replace")
                for token in forbidden:
                    assert token not in body, f"{task_id}: {staged.name} leaks {token!r}"


def test_unclean_run_with_a_submission_is_still_graded():
    """Supervision failing after the agent answered must not erase the answer.

    All three coding cells were killed by `launch_prompt_dispatch_failed`
    ("failed to acquire recovery lease"), an infrastructure flake unrelated to
    the task, and all three had already written the correct answer. The suite
    scored 0 passed. Grading infrastructure noise as model failure inverts the
    exact signal the gym exists to produce.
    """
    task = load_task(GYM / "tasks" / "coding-pipefail-silent-exit")
    answered = Trajectory(
        terminal_status="failed",
        submitted_result={
            "defect_line": 7,
            "shell_option": "pipefail",
            "suppressed_by_2devnull": False,
        },
        transcript_rows=[],
        control_rows=[],
    )
    cell = grader.grade(task, answered)
    assert cell.outcome != outcomes.AGENT_ERROR, cell.reason


def test_infra_error_with_a_submission_is_still_graded():
    """An infra error recorded after the answer must not erase the answer."""
    task = load_task(GYM / "tasks" / "coding-pipefail-silent-exit")
    answered = Trajectory(
        terminal_status="dispatch_failed",
        error="failed to acquire recovery lease",
        submitted_result={
            "defect_line": 7,
            "shell_option": "pipefail",
            "suppressed_by_2devnull": False,
        },
        transcript_rows=[],
        control_rows=[],
    )
    cell = grader.grade(task, answered)
    assert cell.outcome != outcomes.AGENT_ERROR, cell.reason


def test_unclean_run_without_a_submission_is_still_an_agent_error():
    """The gate is reordered, not removed."""
    task = load_task(GYM / "tasks" / "coding-pipefail-silent-exit")
    silent = Trajectory(
        terminal_status="failed", submitted_result=None, transcript_rows=[], control_rows=[]
    )
    cell = grader.grade(task, silent)
    assert cell.outcome == outcomes.AGENT_ERROR
    assert "no result was submitted" in cell.reason


def test_new_real_regression_verifiers_fail_pre_fix_and_pass_post_fix():
    for task_id, case in NEW_REAL_REGRESSION_CASES.items():
        task = load_task(GYM / "tasks" / task_id)
        pre_fix = grader.grade(task, _trajectory(case["pre_fix"], case["opened"]))
        assert pre_fix.outcome == outcomes.FAILED, task_id

        post_fix = grader.grade(task, _trajectory(case["post_fix"], case["opened"]))
        assert post_fix.outcome == outcomes.PASSED, task_id


def _harness_trajectory(result: dict[str, object], calls: list[dict[str, object]]) -> Trajectory:
    """A run the ledger cannot see (codex exec, ACP): no transcript rows."""
    return Trajectory(
        terminal_status="succeeded",
        submitted_result=result,
        transcript_rows=[],
        control_rows=[],
        harness_calls=calls,
    )


def test_read_evidence_is_graded_from_harness_calls_when_the_ledger_has_none():
    # OSSHARN-04 pilot (2026-09-28): every non-claude-code run failed "agent
    # answered without opening <file>" because only claude-code ingests tool
    # calls into the ledger. Codex exec and acpx keep their own record.
    import json

    from gym import harness_toolcalls

    for task_id, case in NEW_REAL_REGRESSION_CASES.items():
        task = load_task(GYM / "tasks" / task_id)
        opened = case["opened"]
        codex_exec = harness_toolcalls.calls_from_codex_exec_stdout(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_1",
                            "type": "command_execution",
                            "command": f"/bin/zsh -lc \"sed -n '1,120p' {opened}\"",
                        },
                    }
                )
            ]
        )
        acp = harness_toolcalls.calls_from_acpx_stream(
            [
                json.dumps(
                    {
                        "method": "session/update",
                        "params": {
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": "call-1",
                                "kind": "read",
                                "title": f"Read {opened}",
                                "locations": [{"path": f"/work/{opened}"}],
                            }
                        },
                    }
                )
            ]
        )
        claude = harness_toolcalls.calls_from_claude_stream_json(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call-1",
                                    "name": "Read",
                                    "input": {"file_path": f"/work/{opened}"},
                                }
                            ]
                        },
                    }
                )
            ]
        )
        for calls in (codex_exec, acp, claude):
            cell = grader.grade(task, _harness_trajectory(case["post_fix"], calls))
            assert cell.outcome == outcomes.PASSED, (task_id, cell.reason)

        blind = grader.grade(task, _harness_trajectory(case["post_fix"], []))
        assert blind.outcome == outcomes.FAILED, task_id


def _toc_trajectory(result: dict[str, object], commands: list[str], edits=()) -> Trajectory:
    calls = [{"name": "shell", "arguments": {"command": command}} for command in commands]
    calls += [{"name": name, "arguments": {"file_path": path}} for name, path in edits]
    return _harness_trajectory(result, calls)


def test_toc_commands_stage_builds_the_history_the_verifier_expects():
    subject = _verifier_const("coding-toc-commands", "EXPECTED_SUBJECT").strip("\"'")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        _stage("coding-toc-commands", dest)
        log = subprocess.run(
            ["git", "-C", str(dest), "log", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert log[0] == subject
        assert len(log) == 2
        assert 'return "bye"' in (dest / "main.py").read_text(encoding="utf-8")
        assert not subprocess.run(
            ["git", "-C", str(dest), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout


def test_toc_commands_verifier_grades_from_result_and_tool_calls():
    task = load_task(GYM / "tasks" / "coding-toc-commands")
    subject = _verifier_const("coding-toc-commands", "EXPECTED_SUBJECT").strip("\"'")
    good = {"status": "fixed", "regressing_commit_subject": subject}
    commands = [
        "git log --oneline",
        "pytest test.py",
        "sed -i '' s/bye/hello/ main.py",
        "pytest test.py",
    ]

    assert grader.grade(task, _toc_trajectory(good, commands)).outcome == outcomes.PASSED

    wrong_subject = {**good, "regressing_commit_subject": "greet: add greeting and its test"}
    assert grader.grade(task, _toc_trajectory(wrong_subject, commands)).outcome == outcomes.FAILED

    no_git = [c for c in commands if not c.startswith("git log")]
    assert grader.grade(task, _toc_trajectory(good, no_git)).outcome == outcomes.FAILED

    one_pytest = commands[:3]
    assert grader.grade(task, _toc_trajectory(good, one_pytest)).outcome == outcomes.FAILED

    edited_test = _toc_trajectory(good, commands, edits=[("Edit", "test.py")])
    assert grader.grade(task, edited_test).outcome == outcomes.FAILED


def test_toc_commands_verifier_counts_only_real_pytest_runs_and_shell_edits():
    task = load_task(GYM / "tasks" / "coding-toc-commands")
    subject = _verifier_const("coding-toc-commands", "EXPECTED_SUBJECT").strip("\"'")
    good = {"status": "fixed", "regressing_commit_subject": subject}
    fix = "sed -i '' s/bye/hello/ main.py"

    def grade(commands):
        return grader.grade(task, _toc_trajectory(good, commands)).outcome

    # A split-UID host needs safe.directory on the command line; chained fixes
    # and `python -m pytest` still count.
    assert (
        grade(
            ['git -c safe.directory="$PWD" log', "python3 -m pytest test.py", f"{fix} && pytest -q"]
        )
        == outcomes.PASSED
    )
    # Mentioning pytest is not running it.
    assert grade(["git log", "echo pytest", "pytest test.py", fix]) == outcomes.FAILED
    # A shell write to test.py fails like an edit tool does.
    for edit in (
        "sed -i s/hello/bye/ test.py",
        "printf 'x' > test.py",
        "echo x | tee test.py",
        "cp main.py test.py",
        "python3 -c \"open('test.py', 'w').write('')\"",
    ):
        assert grade(["git log", "pytest test.py", edit, "pytest test.py"]) == outcomes.FAILED, edit
    # Reading or backing up test.py is not an edit.
    assert (
        grade(["git log", "cat test.py", "cp test.py /tmp/test.py.bak", "pytest", fix, "pytest"])
        == outcomes.PASSED
    )
    # codex exec reports argv; the script is the last element of `bash -lc`.
    argv_calls = [
        {"name": "shell", "arguments": {"command": ["bash", "-lc", c]}}
        for c in ("git log --oneline", "pytest test.py", fix, "pytest test.py")
    ]
    assert grader.grade(task, _harness_trajectory(good, argv_calls)).outcome == outcomes.PASSED


def test_ambiguous_lines_and_all_field_checks():
    for task_id, accepted, rejected in (
        ("coding-pr-not-found-terminal", (12, 14), (13,)),
        ("coding-unknown-change-kind-sentinel", (2, 3), (5, 13)),
    ):
        case = NEW_REAL_REGRESSION_CASES[task_id]
        task = load_task(GYM / "tasks" / task_id)
        for line in accepted + rejected:
            result = {**case["post_fix"], "defect_line": line}
            cell = grader.grade(task, _trajectory(result, case["opened"]))
            assert cell.outcome == (outcomes.PASSED if line in accepted else outcomes.FAILED)
            assert cell.score == (1.0 if line in accepted else 0.75)
            assert len(cell.subchecks) == 4
        wrong = {**case["pre_fix"], "defect_line": 99}
        cell = grader.grade(task, _trajectory(wrong, case["opened"]))
        failed = {c["name"] for c in cell.subchecks if not c["ok"]}
        assert "defect_line" in failed
        assert ("terminal_status" if accepted == (12, 14) else "sentinel_handling") in failed
        assert len(cell.subchecks) == 4
        assert cell.to_dict()["score"] == cell.score


HARD_CODING_TASKS = (
    "coding-cache-context",
    "coding-commit-ack",
    "coding-generation-fence",
    "coding-registry-tail",
)


def _hard_answer(task_id, name="reference"):
    import json

    return json.loads((GYM / "tasks" / task_id / "expected" / f"{name}.json").read_text())


def _run_staged(dest, code):
    import json

    run = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(run.stdout)


def _replace_staged(dest, file, before, after):
    path = dest / file
    body = path.read_text()
    assert body.count(before) == 1
    path.write_text(body.replace(before, after))


def test_harder_verifiers_grade_reference_wrong_and_each_independent_field():
    """Every field matters, and a first failure cannot hide later sub-checks."""
    for task_id in HARD_CODING_TASKS:
        task = load_task(GYM / "tasks" / task_id)
        good = _hard_answer(task_id)
        wrong = _hard_answer(task_id, "known-wrong")
        cell = grader.grade(task, _trajectory(good, "scenario.py"))
        assert cell.outcome == outcomes.PASSED, (task_id, cell.reason)
        assert cell.score == 1.0
        assert {c["name"] for c in cell.subchecks} == set(good)
        cell = grader.grade(task, _trajectory(wrong, "scenario.py"))
        assert cell.outcome == outcomes.FAILED, task_id
        assert len(cell.subchecks) == len(good)
        for spec in task.result_schema:
            value = good[spec.name]
            bad = (
                next(v for v in spec.enum if v != value)
                if spec.enum
                else value + 1
                if isinstance(value, int)
                else "wrong"
            )
            cell = grader.grade(task, _trajectory({**good, spec.name: bad}, "scenario.py"))
            assert cell.outcome == outcomes.FAILED, (task_id, spec.name)
            assert [c["name"] for c in cell.subchecks if not c["ok"]] == [spec.name]
            assert cell.score == (len(good) - 1) / len(good)


def test_harder_cache_ground_truth_and_tenant_only_counterexample(tmp_path):
    task_id = "coding-cache-context"
    _stage(task_id, tmp_path)
    answer = _hard_answer(task_id)
    replay = "import json; from scenario import replay; print(json.dumps(replay()))"
    original = _run_staged(tmp_path, replay)
    assert ",".join(map(str, original)) == answer["original"]
    _replace_staged(
        tmp_path,
        "codec.py",
        "return wire_name(name), revision",
        "return tenant, wire_name(name), revision",
    )
    tenant_only = _run_staged(tmp_path, replay)
    assert ",".join(map(str, tenant_only)) == answer["tenant_only"]
    _replace_staged(
        tmp_path,
        "codec.py",
        "return tenant, wire_name(name), revision",
        'return tenant, unicodedata.normalize("NFC", name), revision',
    )
    fixed = _run_staged(
        tmp_path,
        """import json
from catalog import lookup
from scenario import replay
cache = {}
for request in [("red", "straße", 4), ("blue", "strasse", 4),
                ("red", "strasse", 4), ("red", "straße", 4)]:
    lookup(cache, *request)
print(json.dumps([replay(), len(cache)]))
""",
    )
    assert fixed == [[17, 29, answer["fixed_third"], 17], answer["fixed_entries"]]
    assert fixed[0] != tenant_only
    # Canonically equivalent spelling shares a key, but revisions do not.
    assert _run_staged(
        tmp_path,
        """import json
from codec import cache_key
print(json.dumps([cache_key("red", "e\\u0301", 4) == cache_key("red", "é", 4),
                  cache_key("red", "é", 4) != cache_key("red", "é", 5)]))
""",
    ) == [True, True]


def test_harder_commit_ground_truth_and_reorder_counterexample(tmp_path):
    task_id = "coding-commit-ack"
    _stage(task_id, tmp_path)
    answer = _hard_answer(task_id)
    replay = "import json; from scenario import replay; print(json.dumps(replay()))"
    assert _run_staged(tmp_path, replay) == [answer["original_balance"], 1]
    _replace_staged(
        tmp_path,
        "consumer.py",
        """            store.commit()
            store.begin()
            store.mark(event)
            store.commit()""",
        """            store.mark(event)
            store.commit()""",
    )
    assert _run_staged(tmp_path, replay) == [answer["reorder_balance"], 1]
    _replace_staged(
        tmp_path,
        "consumer.py",
        "            store.apply(event, amount)",
        """            if event in store.pending[1]:
                store.rollback()
                return
            store.apply(event, amount)""",
    )
    assert _run_staged(tmp_path, replay) == [answer["fixed_balance"], answer["fixed_receipts"]]
    # With acknowledgements delivered, and with one lost after durability,
    # the same reference fix still applies each event once.
    assert _run_staged(
        tmp_path,
        """import json
from store import Store
from consumer import consume
results = []
for lose_ack in (False, True):
    store = Store()
    store.lose_ack = lose_ack
    consume(store, "a", 13)
    consume(store, "a", 13)
    consume(store, "b", 7)
    results.append([store.balance, sorted(store.receipts)])
print(json.dumps(results))
""",
    ) == [[20, ["a", "b"]], [20, ["a", "b"]]]


def test_harder_ordering_ground_truth_exhausts_all_six_schedules(tmp_path):
    """No timing luck: enumerate all completion orders before and after the fix."""
    task_id = "coding-generation-fence"
    _stage(task_id, tmp_path)
    answer = _hard_answer(task_id)
    replay = """import json
from scenario import replay, SCHEDULES
print(json.dumps([replay(order) for order in SCHEDULES]))
"""
    original = _run_staged(tmp_path, replay)
    assert sum(trace[-1] != 30 for trace in original) == answer["original_wrong_schedules"]

    def trace_string(trace):
        return ",".join("null" if v is None else str(v) for v in trace)

    assert trace_string(original[4]) == answer["original_trace"]
    _replace_staged(
        tmp_path, "loader.py", "if not self.pending:", "if generation == self.generation:"
    )
    fixed = _run_staged(tmp_path, replay)
    assert sum(trace[-1] != 30 for trace in fixed) == answer["fixed_wrong_schedules"]
    assert trace_string(fixed[4]) == answer["fixed_trace"]
    for order, trace in zip(
        ((1, 2, 3), (1, 3, 2), (2, 1, 3), (2, 3, 1), (3, 1, 2), (3, 2, 1)), fixed
    ):
        assert trace == [None if 3 not in order[: i + 1] else 30 for i in range(3)]
    assert _run_staged(
        tmp_path,
        """import json
from loader import Loader
loader = Loader()
old = loader.start("a")
loader.finish(old, 10)
new = loader.start("b")
loader.finish(old, 99)  # Late duplicate callback cannot overwrite.
still_visible = loader.visible
loader.finish(new, 20)
print(json.dumps([still_visible, loader.visible, loader.pending]))
""",
    ) == [10, 20, {}]


def test_harder_long_file_ground_truth_and_shallow_copy_counterexample(tmp_path):
    task_id = "coding-registry-tail"
    _stage(task_id, tmp_path)
    answer = _hard_answer(task_id)
    registry = tmp_path / "registry.py"
    assert _line_of(registry, 'alias("selected", "tail")') > 700
    assert len(registry.read_text().splitlines()) > 740
    before = _run_staged(
        tmp_path,
        """import json
from registry import ALIASES
from consumer import SELECTED
print(json.dumps([ALIASES["selected"], SELECTED["limit"]]))
""",
    )
    assert before == [answer["profile"], answer["template_limit"]]
    replay = "import json; from scenario import replay; print(json.dumps(replay()))"
    original = _run_staged(tmp_path, replay)
    assert len(original[0]["tags"]) == answer["original_first_tags"]
    assert original[2]["limit"] == 34
    _replace_staged(
        tmp_path,
        "consumer.py",
        '    selected["limit"] = limit',
        '    selected["tags"] = SELECTED["tags"].copy()\n    selected["limit"] = limit',
    )
    fixed = _run_staged(tmp_path, replay)
    assert [row["limit"] for row in fixed] == [3, 7, 34]
    assert [len(row["tags"]) for row in fixed] == [1, 1, answer["fixed_template_tags"]]


def test_toc_absolute_shell_wrappers_and_harness_shapes():
    task = load_task(GYM / "tasks" / "coding-toc-commands")
    good = {
        "status": "fixed",
        "regressing_commit_subject": "greet: reuse the farewell banner string",
    }
    commands = [
        "git log --oneline -10",
        "git log --oneline -- main.py",
        "pytest test.py",
        "python3 -m pytest -q",
    ]
    wrappers = [
        lambda c: c,
        lambda c: f"/bin/zsh -lc '{c}'",
        lambda c: f'/any/path/bash -ic "{c}"',
        lambda c: f"sh -c {c}",
        lambda c: f"/usr/bin/env bash -l -c '{c}'",
        lambda c: ["/bin/zsh", "-lc", c],
        lambda c: ["/usr/bin/env", "bash", "-l", "-c", c, "arg0"],
    ]
    for wrap in wrappers:
        for name in ("shell", "Bash", "bash"):
            calls = [{"name": name, "arguments": {"command": wrap(c)}} for c in commands]
            cell = grader.grade(task, _harness_trajectory(good, calls))
            assert cell.outcome == outcomes.PASSED, (calls, cell.reason)
    calls = [
        {"name": "execute", "arguments": {"rawInput": {"cmd": ["/bin/zsh", "-lc", c]}}}
        for c in commands
    ]
    assert grader.grade(task, _harness_trajectory(good, calls)).outcome == outcomes.PASSED


def test_toc_verifier_recovers_quoted_assignments_and_outer_commands():
    task = load_task(GYM / "tasks" / "coding-toc-commands")
    good = {
        "status": "fixed",
        "regressing_commit_subject": "greet: reuse the farewell banner string",
    }
    commands = [
        'GIT_AUTHOR_NAME="A B" bash -c "pytest test.py" && git log --oneline',
        "env LABEL='before fix' sh -c \"sed -i s/bye/hello/ main.py\" && pytest -q",
    ]
    assert grader.grade(task, _toc_trajectory(good, commands)).outcome == outcomes.PASSED

    # A test-file write after a wrapper must remain visible to the edit guard.
    commands.append("bash -c 'echo done' && printf x > test.py")
    assert grader.grade(task, _toc_trajectory(good, commands)).outcome == outcomes.FAILED
