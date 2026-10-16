"""Suite discovery + run: selection, §1 summary shape, injection gating.

A fake source drives the whole suite — no real worker is dispatched.
"""

from __future__ import annotations

import pytest
from conftest import FIXTURES_DIR, TASKS_DIR

from gym import outcomes, taint
from gym.grader import Trajectory
from gym.schema import SUITES
from gym.suite import (
    ALL_SUITE,
    SELECTABLE_SUITES,
    SuiteError,
    discover_tasks,
    in_scope_agents,
    run_suite,
)

MINI_SUITE = FIXTURES_DIR / "mini-suite"


class MappingSource:
    """Fake dispatch/ledger seam: hands back a per-task canned trajectory."""

    def __init__(self, by_task: dict[str, Trajectory]):
        self.by_task = by_task
        self.dispatched: list[str] = []

    def run(self, task):
        self.dispatched.append(task.id)
        return self.by_task[task.id]


def _result_trajectory(answer: str) -> Trajectory:
    return Trajectory(terminal_status="succeeded", submitted_result={"answer": answer})


def test_selectable_suites_cover_every_declared_corpus_suite():
    assert SELECTABLE_SUITES == SUITES | {ALL_SUITE}


def test_discover_filters_by_suite():
    rca = discover_tasks(TASKS_DIR, suite="rca")
    injection = discover_tasks(TASKS_DIR, suite="injection")
    assert rca and injection
    assert all(t.suite == "rca" for t in rca)
    assert all(t.suite == "injection" for t in injection)
    # "all" is a superset of every declared suite.
    everything = discover_tasks(TASKS_DIR, suite=ALL_SUITE)
    assert {t.id for t in everything} >= {t.id for t in rca} | {t.id for t in injection}


def test_discover_is_sorted_by_id():
    ids = [t.id for t in discover_tasks(TASKS_DIR, suite=ALL_SUITE)]
    assert ids == sorted(ids)


def test_discover_single_task():
    only = discover_tasks(TASKS_DIR, task="rca-fk-drain")
    assert [t.id for t in only] == ["rca-fk-drain"]


def test_discover_unknown_task_raises():
    with pytest.raises(SuiteError):
        discover_tasks(TASKS_DIR, task="does-not-exist")


def test_discover_unknown_suite_raises():
    with pytest.raises(SuiteError):
        discover_tasks(TASKS_DIR, suite="not-a-suite")


def test_lock_scope_uses_the_effective_dispatch_class():
    tasks = discover_tasks(MINI_SUITE, task="mini-pass")
    assert in_scope_agents(tasks, taint.ENFORCE, worker_class="oss-codex") == [
        "oss-codex"
    ]
    assert in_scope_agents(
        tasks, taint.ENFORCE, model="local/qwen2.5-coder-32b-q5"
    ) == ["oss-local"]


def test_run_suite_summary_shape_matches_spec_mockup():
    tasks = discover_tasks(MINI_SUITE, suite=ALL_SUITE)
    source = MappingSource(
        {
            "mini-pass": _result_trajectory("ok"),
            "mini-fail": _result_trajectory("bad"),
        }
    )
    result = run_suite(tasks, source, suite=ALL_SUITE, taint_mode=taint.ENFORCE)
    summary = result.summary_dict()

    assert summary["suite"] == ALL_SUITE
    assert summary["tasks"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["grader"] == "out-of-band; verifiers never entered the agent sandbox"

    by_task = {c["task"]: c for c in summary["cells"]}
    # A passing cell carries the verifier's assertion; a failing one, a reason.
    assert by_task["mini-pass"]["verdict"] == "passed"
    assert by_task["mini-pass"]["asserted"] == "answer == ok"
    assert "reason" not in by_task["mini-pass"]
    assert by_task["mini-fail"]["verdict"] == "failed"
    assert "reason" in by_task["mini-fail"]


def test_a_poll_deadline_cell_carries_its_terminal_status():
    # TOC-05 excludes a poll-deadline cell from the arm; agent_error alone
    # cannot tell a queue wait from a model failure.
    tasks = discover_tasks(MINI_SUITE, suite=ALL_SUITE)
    timed_out = Trajectory(
        terminal_status="timeout",
        launch_request_id="lrq-q",
        error="did not reach terminal",
    )
    source = MappingSource(
        {"mini-pass": timed_out, "mini-fail": _result_trajectory("bad")}
    )
    summary = run_suite(
        tasks, source, suite=ALL_SUITE, taint_mode=taint.ENFORCE
    ).summary_dict()
    by_task = {c["task"]: c for c in summary["cells"]}
    assert by_task["mini-pass"]["verdict"] == outcomes.AGENT_ERROR
    assert by_task["mini-pass"]["terminalStatus"] == "timeout"
    assert "terminalStatus" not in by_task["mini-fail"]


def test_run_suite_verdicts_map():
    tasks = discover_tasks(MINI_SUITE, suite=ALL_SUITE)
    source = MappingSource(
        {"mini-pass": _result_trajectory("ok"), "mini-fail": _result_trajectory("bad")}
    )
    result = run_suite(tasks, source, suite=ALL_SUITE, taint_mode=taint.ENFORCE)
    assert result.verdicts() == {
        "mini-pass": outcomes.PASSED,
        "mini-fail": outcomes.FAILED,
    }


def test_injection_task_gated_without_dispatch_when_taint_unavailable():
    # Under UNAVAILABLE taint, an injection task must short-circuit to a gate
    # outcome WITHOUT the source ever being asked to dispatch a worker.
    tasks = discover_tasks(TASKS_DIR, suite="injection")
    source = MappingSource({})  # any dispatch would KeyError
    result = run_suite(tasks, source, suite="injection", taint_mode=taint.UNAVAILABLE)
    assert source.dispatched == []
    for cell in result.cells:
        assert cell.outcome == taint.SKIPPED_NO_TAINT_GATE
        assert not outcomes.is_passing(cell.outcome)


# --- GYM-M1: the suite result carries which model each task ran under --------


def _graded(task_id: str, outcome: str):
    from gym.grader import Cell
    from gym.suite import GradedCell

    return GradedCell(cell=Cell(task_id=task_id, outcome=outcome), asserted=None)


def test_an_unmodelled_run_keys_verdicts_on_the_bare_task_id():
    # Every baseline written before models were selectable is keyed this way; a
    # default run has to keep producing keys that compare against them.
    from gym.suite import SuiteResult

    result = SuiteResult(
        suite="coding", graded=[_graded("coding-missing-commit", outcomes.PASSED)]
    )
    assert result.verdicts() == {"coding-missing-commit": outcomes.PASSED}


def test_a_model_sweep_keys_verdicts_apart_from_the_default_run():
    from gym.suite import SuiteResult

    model = "local/qwen2.5-coder-32b-q5"
    result = SuiteResult(
        suite="coding",
        graded=[_graded("coding-missing-commit", outcomes.FAILED)],
        models={"coding-missing-commit": model},
    )
    assert result.verdicts() == {f"coding-missing-commit@{model}": outcomes.FAILED}
    # The point of the whole change: grading the same task under a second model
    # must not overwrite the first model's cell.
    default = SuiteResult(
        suite="coding", graded=[_graded("coding-missing-commit", outcomes.PASSED)]
    )
    merged = {**default.verdicts(), **result.verdicts()}
    assert len(merged) == 2


def test_skill_digest_separates_cohorts_for_the_same_task_and_model():
    from gym.suite import SuiteResult

    model = "claude-opus-5"
    digest_v1 = "sha256:" + ("1" * 64)
    digest_v2 = "sha256:" + ("2" * 64)
    v1 = SuiteResult(
        suite="coding",
        graded=[_graded("coding-missing-commit", outcomes.FAILED)],
        models={"coding-missing-commit": model},
        skill_digests={"coding-missing-commit": digest_v1},
    )
    v2 = SuiteResult(
        suite="coding",
        graded=[_graded("coding-missing-commit", outcomes.PASSED)],
        models={"coding-missing-commit": model},
        skill_digests={"coding-missing-commit": digest_v2},
    )

    merged = {**v1.verdicts(), **v2.verdicts()}

    assert len(merged) == 2
    assert merged[f"coding-missing-commit@{model}@{digest_v1}"] == outcomes.FAILED
    assert merged[f"coding-missing-commit@{model}@{digest_v2}"] == outcomes.PASSED


def test_a_model_sweep_records_only_unpinned_tasks_in_the_sweep_slice():
    from gym.suite import SuiteResult

    model = "local/qwen2.5-coder-32b-q5"
    result = SuiteResult(
        suite="coding",
        graded=[
            _graded("pinned-qwen", outcomes.PASSED),
            _graded("sweep-qwen", outcomes.FAILED),
        ],
        models={"pinned-qwen": model, "sweep-qwen": model},
        slices={"pinned-qwen": None, "sweep-qwen": model},
    )

    assert result.verdict_slices() == {
        f"pinned-qwen@{model}": None,
        f"sweep-qwen@{model}": model,
    }


def test_smv_exact_states_writers_and_mutations():
    from smv_sources import definition, sources, verify

    doc, src = definition(), sources()
    verify(doc, src)
    renamed = dict(src)
    renamed["outcomes.py"] = renamed["outcomes.py"].replace(
        'PASSED = "passed"', 'PASSED = "renamed"'
    )
    with pytest.raises(AssertionError):
        verify(doc, renamed)
    changed = dict(src)
    changed["endpoints.py"] = changed["endpoints.py"].replace(
        'lease["state"] = "ready"', 'lease["state"] = "closing"'
    )
    with pytest.raises(AssertionError):
        verify(doc, changed)
    del next(s for s in doc["objects"][1]["states"] if not s["terminal"])["exitOwner"]
    with pytest.raises((KeyError, AssertionError)):
        verify(doc, src)


def test_smv_sandbox_required_failure_and_host_unavailable():
    import runpy
    from pathlib import Path

    harness = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/smoke.py")
    )
    report = harness["run"]()
    assert report["verdict"] == "pass", report
    assert report["durationSeconds"] < 180
    assert harness["run"](fail_step="lease.lifecycle")["verdict"] == "fail"
    host = harness["run"](mode="host")
    assert host["verdict"] == "fail"
    assert all(s["verdict"] == "unavailable" for s in host["steps"])


def _smoke_harness():
    import runpy
    from pathlib import Path

    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke.py"))


def test_smv_verifiers_run_in_owned_groups_with_closed_stdin():
    import os
    import signal
    import subprocess
    from unittest.mock import patch

    harness = _smoke_harness()
    with (
        patch.object(subprocess, "Popen", wraps=subprocess.Popen) as spawn,
        patch.object(os, "killpg", wraps=os.killpg) as kill,
    ):
        report = harness["run"]()
    assert report["verdict"] == "pass", report
    assert spawn.call_count == 2
    assert kill.call_count == spawn.call_count
    for call in spawn.call_args_list:
        assert call.args[0][1:] == ["-S", "verifier.py"]
        assert call.kwargs["stdin"] == subprocess.DEVNULL
        assert call.kwargs["start_new_session"] is True
    assert all(call.args[1] == signal.SIGKILL for call in kill.call_args_list)


@pytest.mark.parametrize("failure", [None, "child-timeout", "invocation-timeout"])
def test_smv_verifier_cleanup_on_success_and_timeouts(failure):
    import os
    import signal
    import subprocess
    from unittest.mock import Mock, patch

    harness = _smoke_harness()
    proc = Mock(pid=123, returncode=0)
    proc.communicate.return_value = ("verdict", "")
    error = {
        None: None,
        "child-timeout": subprocess.TimeoutExpired("verifier.py", 15),
        "invocation-timeout": harness["InvocationTimeout"]("budget exceeded"),
    }[failure]
    proc.communicate.side_effect = error

    def invoke():
        return harness["verifier_process"](
            ["python", "-S", "verifier.py"],
            cwd="gym-verify-fixture",
            env={},
            capture_output=True,
            text=True,
            timeout=60,
        )

    with (
        patch.object(subprocess, "Popen", return_value=proc),
        patch.object(os, "killpg") as kill,
    ):
        if error is None:
            assert invoke().stdout == "verdict"
        else:
            with pytest.raises(type(error)):
                invoke()
    proc.communicate.assert_called_once_with(timeout=15)
    kill.assert_called_once_with(proc.pid, signal.SIGKILL)
    proc.wait.assert_called_once_with(timeout=3)


def test_smv_invocation_alarm_aborts_steps_and_restores_state(monkeypatch):
    import os
    import signal
    from unittest.mock import Mock

    harness = _smoke_harness()
    previous = signal.getsignal(signal.SIGALRM)
    monkeypatch.setenv("SMV_COMMIT", "synthetic")

    def expire(*args):
        signal.raise_signal(signal.SIGALRM)

    exercise = Mock(side_effect=expire)
    monkeypatch.setitem(harness["main"].__globals__, "exercise", exercise)
    with pytest.raises(
        harness["InvocationTimeout"], match="invocation budget exceeded"
    ):
        harness["main"]()
    assert exercise.call_count == 1
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    assert os.environ["SMV_COMMIT"] == "synthetic"


def test_smv_entrypoint_leaves_parent_stdin_open(monkeypatch, capsys):
    import io
    import runpy
    import sys
    from pathlib import Path

    stdin = io.StringIO("")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setenv("SMV_COMMIT", "synthetic")
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts/smoke.py"),
            run_name="__main__",
        )
    assert exited.value.code == 0
    assert not stdin.closed
    assert '"verdict": "pass"' in capsys.readouterr().out


def test_smv_terminal_states_cannot_resume_other_states():
    from smv_sources import definition, verify

    doc = definition()
    endpoint = next(obj for obj in doc["objects"] if obj["id"] == "paddock.endpoint")
    assert {
        edge["to"] for edge in endpoint["transitions"] if edge["from"] == "closed"
    } == {"closed", "[*]"}
    grade = next(obj for obj in doc["objects"] if obj["id"] == "paddock.grade")
    next(state for state in grade["states"] if state["name"] == "passed")[
        "terminal"
    ] = True
    with pytest.raises(AssertionError, match="terminal state cannot transition"):
        verify(doc)
