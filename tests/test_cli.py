"""`hq gym` CLI: run summary shape, baseline update round-trip, regression exit.

Every invocation injects a fake source and a recording alerter, so the CLI is
exercised end to end with no real worker dispatched and no live alert sent.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from conftest import FIXTURES_DIR, TASKS_DIR

from gym import cli, outcomes
from gym.alerting import (
    GYM_LEVEL_UNRESOLVED_ALERT_KEY,
    GYM_REGRESSION_ALERT_KEY,
    RecordingAlerter,
)
from gym.baseline import (
    baseline_document,
    cohort_key,
    load_baseline,
    serialize_baseline,
    verdict_key,
    write_baseline,
)
from gym.grader import Trajectory
from gym.locks import write_lock

MINI_SUITE = FIXTURES_DIR / "mini-suite"
# Fixture lock dir whose claude-code.lock.json matches _FIXTURE_SURFACE, so the
# GYM-05 setup check passes for the mini-suite (persona claude-code) and every
# existing CLI assertion is exercised without a worker-class registry.
LOCKS_FIXTURE = FIXTURES_DIR / "locks"
_FIXTURE_SURFACE = ["bash:safe", "read", "write"]

_NOW = "2026-07-14T00:00:00+00:00"


@pytest.fixture(autouse=True)
def isolated_results_dir(tmp_path, monkeypatch):
    from gym.host_services import LocalRunRecords

    monkeypatch.setattr(
        cli, "default_run_records", lambda: LocalRunRecords(tmp_path / "results")
    )


class MappingSource:
    def __init__(self, by_task):
        self.by_task = by_task
        self.dispatched = []

    def run(self, task):
        self.dispatched.append(task.id)
        return self.by_task[task.id]


class RaisingAlerter(RecordingAlerter):
    def emit(self, *, key, message, metadata):
        super().emit(key=key, message=message, metadata=metadata)
        raise RuntimeError("synthetic alert unavailable")


class FakeResolver:
    """Injected tool-surface resolver: returns a canned surface per agent."""

    def __init__(self, surface):
        self.surface = list(surface)

    def resolve(self, agent):
        return list(self.surface)


def _mixed_source():
    return MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="succeeded", submitted_result={"answer": "ok"}
            ),
            "mini-fail": Trajectory(
                terminal_status="succeeded", submitted_result={"answer": "bad"}
            ),
        }
    )


def _green_source():
    return MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="succeeded", submitted_result={"answer": "ok"}
            ),
            "mini-fail": Trajectory(
                terminal_status="succeeded", submitted_result={"answer": "ok"}
            ),
        }
    )


def test_reasoning_level_run_separates_results_and_records_mismatch(tmp_path):
    paths = []
    for level in (None, "xhigh"):
        args = [
            "run",
            "--task",
            "mini-pass",
            "--model",
            "claude-opus-5",
            "--json",
            "--no-compare",
            "--tasks-dir",
            str(MINI_SUITE),
            "--locks-dir",
            str(LOCKS_FIXTURE),
            "--results-dir",
            str(tmp_path),
            "--taint-mode",
            "enforce",
        ]
        if level:
            args += ["--reasoning-level", level]
        source = MappingSource(
            {
                "mini-pass": Trajectory(
                    terminal_status="succeeded",
                    submitted_result={"answer": "ok"},
                    resolved_reasoning_level="xhigh" if level else "high",
                )
            }
        )
        out = io.StringIO()
        assert (
            cli.main(
                args,
                source=source,
                resolver=FakeResolver(_FIXTURE_SURFACE),
                alerter=RecordingAlerter(),
                out=out,
            )
            == cli.EXIT_OK
        )
        payload = json.loads(out.getvalue())
        path = Path(payload["result_path"])
        saved = json.loads(path.read_text())
        paths.append(path.parent)
        if level:
            assert saved["reasoning_level"] == "xhigh"
            assert saved["verdicts"] == {
                verdict_key(
                    "mini-pass", "claude-opus-5", reasoning_level="xhigh"
                ): outcomes.PASSED
            }
            assert payload["cells"][0]["level_mismatch"] is False
        else:
            assert "reasoning_level" not in saved
            assert path.parent.name == cli.hashlib.sha256(b"claude-opus-5").hexdigest()
            assert saved["verdicts"] == {"mini-pass@claude-opus-5": outcomes.PASSED}
            assert payload["cells"][0]["resolved_reasoning_level"] == "high"
    assert paths[0] != paths[1]


def _leveled_argv(tmp_path, *extra):
    return [
        *extra,
        "--model",
        "claude-opus-5",
        "--reasoning-level",
        "high",
        "--tasks-dir",
        str(MINI_SUITE),
        "--locks-dir",
        str(LOCKS_FIXTURE),
        "--baseline",
        str(tmp_path / "gym-baseline.json"),
        "--taint-mode",
        "enforce",
    ]


def _unsupported_level_source():
    # Dispatch resolved the unsupported harness to "unsupported", not "high".
    return MappingSource(
        {
            task: Trajectory(
                terminal_status="succeeded",
                submitted_result={"answer": "ok"},
                resolved_reasoning_level="unsupported",
            )
            for task in ("mini-pass", "mini-fail")
        }
    )


def test_reasoning_level_mismatch_is_an_invalid_cell(tmp_path):
    # Review of #7391: a passing cell that did not run at the requested level
    # must not be recorded as a verdict for that level.
    out = io.StringIO()
    code = cli.main(
        [
            *_leveled_argv(tmp_path, "run", "--task", "mini-pass", "--json"),
            "--results-dir",
            str(tmp_path / "results"),
        ],
        source=_unsupported_level_source(),
        resolver=FakeResolver(_FIXTURE_SURFACE),
        alerter=RecordingAlerter(),
        out=out,
    )
    assert code == cli.EXIT_ERROR
    cell = json.loads(out.getvalue())["cells"][0]
    assert cell["verdict"] == outcomes.AGENT_ERROR
    assert cell["level_mismatch"] is True
    assert cell["resolved_reasoning_level"] == "unsupported"
    assert "'high'" in cell["reason"] and "'unsupported'" in cell["reason"]


def test_reasoning_level_mismatch_refuses_baseline_update(tmp_path, capsys):
    code = cli.main(
        _leveled_argv(tmp_path, "baseline", "update", "--confirm"),
        source=_unsupported_level_source(),
        resolver=FakeResolver(_FIXTURE_SURFACE),
        now=_NOW,
        out=io.StringIO(),
    )
    assert code == cli.EXIT_ERROR
    assert "refusing to establish baseline" in capsys.readouterr().err
    assert not (tmp_path / "gym-baseline.json").exists()


def test_unresolved_dispatched_level_alerts_even_without_a_regression(tmp_path):
    from gym.alerting import GYM_LEVEL_UNRESOLVED_ALERT_KEY

    source = MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="succeeded",
                launch_request_id="lrq-1",
                submitted_result={"answer": "ok"},
                resolved_reasoning_level=None,
            )
        }
    )
    alerter = RecordingAlerter()
    out = io.StringIO()
    code = cli.main(
        [*_leveled_argv(tmp_path, "run", "--task", "mini-pass", "--json")],
        source=source,
        resolver=FakeResolver(_FIXTURE_SURFACE),
        alerter=alerter,
        out=out,
    )
    assert code == cli.EXIT_ERROR
    payload = json.loads(out.getvalue())
    assert payload["level_mismatches"] == 1
    assert payload["level_unresolved"] == 1
    assert [alert["key"] for alert in alerter.alerts] == [
        GYM_LEVEL_UNRESOLVED_ALERT_KEY
    ]


@pytest.mark.parametrize("scenario", ["unresolved", "regressions", "both"])
@pytest.mark.parametrize("json_output", [True, False])
def test_run_alert_failure_preserves_results(tmp_path, capsys, scenario, json_output):
    level = "high" if scenario != "regressions" else None
    task_ids = ["mini-pass"] if scenario == "unresolved" else ["mini-fail", "mini-pass"]
    baseline_path = tmp_path / "baseline.json"
    write_baseline(
        baseline_path,
        baseline_document(
            {
                verdict_key(
                    task, "synthetic-model", reasoning_level=level
                ): outcomes.PASSED
                for task in task_ids
            },
            generated_at=_NOW,
        ),
    )
    argv = [
        "run",
        "--suite",
        "all",
        "--model",
        "synthetic-model",
        "--tasks-dir",
        str(MINI_SUITE),
        "--locks-dir",
        str(LOCKS_FIXTURE),
        "--baseline",
        str(baseline_path),
        "--results-dir",
        str(tmp_path / "results"),
        "--taint-mode",
        "enforce",
    ]
    if scenario == "unresolved":
        argv += ["--task", "mini-pass"]
    if level:
        argv += ["--reasoning-level", level]
    if json_output:
        argv += ["--json"]
    args = cli.build_parser().parse_args(argv)
    expected_keys = ([GYM_LEVEL_UNRESOLVED_ALERT_KEY] if level else []) + (
        []
        if scenario == "unresolved"
        else [GYM_REGRESSION_ALERT_KEY] * (2 if level is None else 1)
    )
    reference = None
    reference_alerts = None
    for alerter in (RecordingAlerter(), RaisingAlerter()):
        source = MappingSource(
            {
                task: Trajectory(
                    terminal_status="succeeded",
                    launch_request_id=f"synthetic-run-{index}",
                    submitted_result={
                        "answer": "ok" if scenario == "unresolved" else "bad"
                    },
                    resolved_reasoning_level=None if task == "mini-pass" else level,
                )
                for index, task in enumerate(task_ids)
            }
        )
        out = io.StringIO()
        code = cli.cmd_run(
            args,
            source=source,
            alerter=alerter,
            resolver=FakeResolver(_FIXTURE_SURFACE),
            out=out,
        )
        assert code == (
            cli.EXIT_ERROR if scenario == "unresolved" else cli.EXIT_REGRESSION
        )
        assert sorted(source.dispatched) == task_ids
        assert [alert["key"] for alert in alerter.alerts] == expected_keys
        undelivered = (
            [
                {"key": key, "reason": "synthetic alert unavailable"}
                for key in expected_keys
            ]
            if isinstance(alerter, RaisingAlerter)
            else []
        )
        stderr_lines = capsys.readouterr().err.splitlines()
        assert stderr_lines == [
            f"[hq gym] alert undelivered: {failure['key']}: {failure['reason']}"
            for failure in undelivered
        ]
        if json_output:
            payload = json.loads(out.getvalue())
            assert payload.pop("alerts_undelivered") == undelivered
            saved = json.loads(Path(payload.pop("result_path")).read_text())
            assert saved["summary"]["cells"] == payload["cells"]
            assert payload["tasks"] == len(task_ids)
            assert payload["model"] == "synthetic-model"
            assert payload["regression"]["established"] is True
            assert len(payload["regression"]["regressions"]) == expected_keys.count(
                GYM_REGRESSION_ALERT_KEY
            )
            observed = payload
        else:
            lines = out.getvalue().splitlines()
            assert lines[0].startswith("[gym-scheduled] suite=all")
            assert f"{len(task_ids)} tasks" in lines[0]
            assert sum(
                "  REGRESSION:" in line for line in lines
            ) == expected_keys.count(GYM_REGRESSION_ALERT_KEY)
            assert [
                line for line in lines if line.startswith("  alert undelivered:")
            ] == [
                f"  alert undelivered: {failure['key']}: {failure['reason']}"
                for failure in undelivered
            ]
            observed = [
                line
                for line in lines
                if not line.startswith(("  alert undelivered:", "  results stored"))
            ]
        if reference is None:
            reference, reference_alerts = observed, alerter.alerts
        else:
            assert observed == reference
            assert alerter.alerts == reference_alerts


def test_model_ids_with_an_effort_segment_are_refused():
    # Review of #7391: model "provider@effort:high" would share the key and
    # result directory of model "provider" at level "high".
    for args in (["run"], ["baseline", "update"], ["lock", "check"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([*args, "--model", "provider@effort:high"])
        parsed = cli.build_parser().parse_args(
            [*args, "--model", "vertex/claude@20240229"]
        )
        assert parsed.model == "vertex/claude@20240229"


def test_reasoning_level_validation_is_shared_by_gym_commands(monkeypatch):
    from gym import host_services

    def normalize(value, *, field):
        if value == "max":
            return "xhigh"
        raise ValueError(f"{field}: unknown reasoning level")

    monkeypatch.setattr(host_services.current(), "normalize_reasoning_level", normalize)
    for args in (["run"], ["baseline", "update"], ["lock", "check"]):
        parsed = cli.build_parser().parse_args([*args, "--reasoning-level", "max"])
        assert parsed.reasoning_level == "xhigh"
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([*args, "--reasoning-level", "unknown"])


def test_reasoning_level_without_worker_pool_is_available(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "cwp_dispatch.reasoning_level", None)
    parsed = cli.build_parser().parse_args(
        ["lock", "check", "--reasoning-level", "high"]
    )
    assert parsed.reasoning_level == "high"
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["lock", "check", "--reasoning-level", " "])
    assert excinfo.value.code == 2


def _run(argv, *, source=None, alerter=None, resolver=None, now=None):
    out = io.StringIO()
    # Point run/baseline at the fixture lock dir so the setup-phase lock check
    # runs against a committed fixture instead of the shipped locks/.
    argv = list(argv) + ["--locks-dir", str(LOCKS_FIXTURE)]
    code = cli.main(
        argv,
        source=source if source is not None else _mixed_source(),
        alerter=alerter if alerter is not None else RecordingAlerter(),
        resolver=resolver if resolver is not None else FakeResolver(_FIXTURE_SURFACE),
        now=now,
        out=out,
    )
    return code, out.getvalue()


@pytest.mark.parametrize("cohort", ["default", "model", "reasoning", "pinned"])
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("json_output", [False, True])
def test_run_compares_qualified_versions(tmp_path, cohort, version, json_output):
    tasks_dir = tmp_path / "tasks"
    shutil.copytree(MINI_SUITE, tasks_dir)
    manifest = tasks_dir / "mini-pass/task.toml"
    text = manifest.read_text().replace("task_version = 1", f"task_version = {version}")
    model = "claude-opus-5" if cohort != "default" else None
    level = "high" if cohort == "reasoning" else None
    if cohort == "pinned":
        text += f'\n[dispatch]\nmodel = "{model}"\n'
    manifest.write_text(text)
    key = verdict_key("mini-pass", model, reasoning_level=level)
    baseline_path = tmp_path / "baseline.json"
    write_baseline(
        baseline_path,
        baseline_document(
            {key: outcomes.PASSED},
            generated_at=_NOW,
            slices={key: None if cohort == "pinned" else cohort_key(model, level)},
        ),
    )
    argv = [
        "run",
        "--task",
        "mini-pass",
        "--tasks-dir",
        str(tasks_dir),
        "--baseline",
        str(baseline_path),
        "--taint-mode",
        "enforce",
    ]
    if cohort in {"model", "reasoning"}:
        argv += ["--model", model]
    if level:
        argv += ["--reasoning-level", level]
    if json_output:
        argv += ["--json"]
    alerter = RecordingAlerter()
    code, output = _run(
        argv,
        source=MappingSource(
            {
                "mini-pass": Trajectory(
                    terminal_status="succeeded",
                    submitted_result={"answer": "bad"},
                    resolved_reasoning_level=level,
                )
            }
        ),
        alerter=alerter,
    )
    assert code == (cli.EXIT_OK if version == 2 else cli.EXIT_REGRESSION)
    assert len(alerter.alerts) == (0 if version == 2 else 1)
    if json_output:
        payload = json.loads(output)
        report = payload["regression"]
        assert report["version_changed"] == ([key] if version == 2 else [])
        assert [r["task"] for r in report["regressions"]] == (
            [] if version == 2 else [key]
        )
        saved = json.loads(Path(payload["result_path"]).read_text())
        assert saved["task_versions"] == {key: version}
        assert saved["summary"]["cells"][0]["task_version"] == version
    else:
        assert (f"VERSION CHANGED: {key}" in output) == (version == 2)


@pytest.mark.parametrize("held_outcome", [outcomes.FAILED, outcomes.PASSED])
def test_qualified_comparison_preserves_held_verdict_policy(
    tmp_path, monkeypatch, held_outcome
):
    original = cli.suite_mod.run_suite

    def held_suite(*args, **kwargs):
        result = original(*args, **kwargs)
        result.cells[0].outcome = outcomes.INTEGRITY_HOLD
        result.cells[0].held_verdict = {"verdict": held_outcome}
        kwargs["on_progress"](result)
        return result

    monkeypatch.setattr(cli.suite_mod, "run_suite", held_suite)
    key = verdict_key("mini-pass", "claude-opus-5")
    baseline_path = tmp_path / "baseline.json"
    write_baseline(
        baseline_path, baseline_document({key: outcomes.PASSED}, generated_at=_NOW)
    )
    alerter = RecordingAlerter()
    code, output = _run(
        [
            "run",
            "--task",
            "mini-pass",
            "--model",
            "claude-opus-5",
            "--json",
            "--tasks-dir",
            str(MINI_SUITE),
            "--baseline",
            str(baseline_path),
            "--taint-mode",
            "enforce",
        ],
        alerter=alerter,
    )
    assert code == (
        cli.EXIT_REGRESSION if held_outcome == outcomes.FAILED else cli.EXIT_OK
    )
    assert len(alerter.alerts) == (1 if held_outcome == outcomes.FAILED else 0)
    payload = json.loads(output)
    saved = json.loads(Path(payload["result_path"]).read_text())
    assert saved["verdicts"] == {key: outcomes.INTEGRITY_HOLD}
    assert [r["task"] for r in payload["regression"]["regressions"]] == (
        [key] if held_outcome == outcomes.FAILED else []
    )


@pytest.mark.parametrize(
    "defect_line, score, verdict",
    [(13, 0.75, "failed"), (12, 1.0, "passed"), (14, 1.0, "passed")],
)
def test_partial_credit_survives_cli_json_and_saved_model_result(
    tmp_path, defect_line, score, verdict
):
    task_id = "coding-pr-not-found-terminal"
    model = "claude-opus-5"
    source = MappingSource(
        {
            task_id: Trajectory(
                terminal_status="succeeded",
                submitted_result={
                    "defect_line": defect_line,
                    "terminal_status": "skipped",
                    "retryable": False,
                },
                harness_calls=[
                    {"name": "Bash", "arguments": {"cmd": "cat batch_activation.py"}}
                ],
            )
        }
    )
    code, output = _run(
        [
            "run",
            "--task",
            task_id,
            "--model",
            model,
            "--json",
            "--no-compare",
            "--tasks-dir",
            str(TASKS_DIR),
            "--results-dir",
            str(tmp_path),
        ],
        source=source,
    )
    assert code == cli.EXIT_OK
    payload = json.loads(output)
    saved = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert source.dispatched == [task_id]
    assert saved["verdicts"] == {verdict_key(task_id, model): verdict}
    for summary in (payload, saved["summary"]):
        cell = summary["cells"][0]
        assert cell["verdict"] == verdict
        assert cell["score"] == score
        assert cell["subchecks"] == [
            {
                "name": "defect_line",
                "ok": defect_line in {12, 14},
                "reason": "defect_line must identify the exact-match guard (12) or failed fallback (14)",
                "required": True,
            },
            {
                "name": "terminal_status",
                "ok": True,
                "required": True,
                "reason": "a no-PullRequest response is terminal for this reference and should be skipped",
            },
            {
                "name": "retryable",
                "ok": True,
                "required": True,
                "reason": "retryable must be false: retry cannot turn an issue into a pull request",
            },
            {
                "name": "read_evidence",
                "ok": True,
                "required": True,
                "reason": "agent answered without opening batch_activation.py",
            },
        ]


def test_oss_run_checks_override_lock_and_stores_model_result(tmp_path):
    model = "litellm-local-openai/glm-5.2"
    worker_class = "oss-codex"
    locks_dir = tmp_path / "locks"
    write_lock(locks_dir / f"{worker_class}.lock.json", worker_class, _FIXTURE_SURFACE)
    baseline_path = tmp_path / "gym-baseline.json"
    baseline_path.write_text(
        serialize_baseline(
            baseline_document({"mini-pass": outcomes.PASSED}, generated_at=_NOW)
        ),
        encoding="utf-8",
    )
    before = baseline_path.read_bytes()
    out = io.StringIO()
    source = _green_source()
    code = cli.main(
        [
            "run",
            "--task",
            "mini-pass",
            "--json",
            "--model",
            model,
            "--worker-class",
            worker_class,
            "--tasks-dir",
            str(MINI_SUITE),
            "--locks-dir",
            str(locks_dir),
            "--results-dir",
            str(tmp_path / "results"),
            "--baseline",
            str(baseline_path),
            "--taint-mode",
            "enforce",
        ],
        source=source,
        alerter=RecordingAlerter(),
        resolver=FakeResolver(_FIXTURE_SURFACE),
        out=out,
    )
    assert code == cli.EXIT_OK
    assert source.dispatched == ["mini-pass"]
    payload = json.loads(out.getvalue())
    saved = json.loads(Path(payload["result_path"]).read_text())
    assert saved["model"] == model
    assert saved["worker_class"] == worker_class
    assert saved["verdicts"] == {verdict_key("mini-pass", model): outcomes.PASSED}
    for summary in (payload, saved["summary"]):
        assert summary["cells"][0]["subchecks"] == []
        assert summary["cells"][0]["score"] is None
    assert baseline_path.read_bytes() == before


def test_oss_run_missing_override_lock_fails_closed(tmp_path, capsys):
    source = _green_source()
    code = cli.main(
        [
            "run",
            "--task",
            "mini-pass",
            "--model",
            "litellm-local-openai/glm-5.2",
            "--worker-class",
            "oss-pi",
            "--tasks-dir",
            str(MINI_SUITE),
            "--locks-dir",
            str(tmp_path / "locks"),
            "--results-dir",
            str(tmp_path / "results"),
            "--taint-mode",
            "enforce",
            "--no-compare",
        ],
        source=source,
        alerter=RecordingAlerter(),
        resolver=FakeResolver(_FIXTURE_SURFACE),
        out=io.StringIO(),
    )
    assert code == cli.EXIT_ERROR
    assert "oss-pi" in capsys.readouterr().err
    assert source.dispatched == []
    assert not (tmp_path / "results").exists()


def test_lock_check_accepts_oss_override(tmp_path):
    locks_dir = tmp_path / "locks"
    write_lock(locks_dir / "oss-opencode.lock.json", "oss-opencode", _FIXTURE_SURFACE)
    out = io.StringIO()
    code = cli.main(
        [
            "lock",
            "check",
            "--task",
            "mini-pass",
            "--worker-class",
            "oss-opencode",
            "--model",
            "litellm-local-openai/kimi-k3",
            "--tasks-dir",
            str(MINI_SUITE),
            "--locks-dir",
            str(locks_dir),
            "--taint-mode",
            "enforce",
        ],
        resolver=FakeResolver(_FIXTURE_SURFACE),
        out=out,
    )
    assert code == cli.EXIT_OK
    assert "oss-opencode" in out.getvalue()


# --------------------------------------------------------------------------- #
# hq gym run — §1 mockup shape
# --------------------------------------------------------------------------- #


def test_run_json_matches_spec_mockup_shape(tmp_path):
    code, text = _run(
        [
            "run",
            "--suite",
            "all",
            "--json",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--no-compare",
        ]
    )
    assert code == cli.EXIT_OK
    payload = json.loads(text)
    assert payload["suite"] == "all"
    assert payload["tasks"] == 2
    assert payload["passed"] == 1
    assert payload["failed"] == 1
    assert payload["grader"].startswith("out-of-band")
    assert {c["task"] for c in payload["cells"]} == {"mini-pass", "mini-fail"}


def test_run_single_task_filter(tmp_path):
    code, text = _run(
        [
            "run",
            "--task",
            "mini-pass",
            "--json",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--no-compare",
        ]
    )
    assert code == cli.EXIT_OK
    payload = json.loads(text)
    assert payload["tasks"] == 1
    assert payload["cells"][0]["task"] == "mini-pass"


def test_run_suite_filter_selects_only_that_suite(tmp_path):
    # mini-suite tasks are both suite=rca; selecting injection there is empty ->
    # a SuiteError surfaces as a usage error, not a crash.
    code, _ = _run(
        [
            "run",
            "--suite",
            "injection",
            "--tasks-dir",
            str(MINI_SUITE),
            "--no-compare",
        ]
    )
    assert code == cli.EXIT_ERROR


# --------------------------------------------------------------------------- #
# hq gym run — baseline diff + gym_regression alert
# --------------------------------------------------------------------------- #


def test_run_emits_gym_regression_alert_on_pass_to_fail(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    # Baseline says mini-fail PASSED; HEAD (fake source) fails it -> regression.
    write_baseline(
        baseline_path,
        baseline_document(
            {"mini-pass": outcomes.PASSED, "mini-fail": outcomes.PASSED},
            generated_at=_NOW,
        ),
    )
    alerter = RecordingAlerter()
    code, text = _run(
        [
            "run",
            "--suite",
            "all",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--baseline",
            str(baseline_path),
        ],
        alerter=alerter,
    )
    assert code == cli.EXIT_REGRESSION
    assert len(alerter.alerts) == 1
    assert alerter.alerts[0]["key"] == GYM_REGRESSION_ALERT_KEY
    assert alerter.alerts[0]["metadata"]["task"] == "mini-fail"
    assert "REGRESSION: mini-fail" in text


def test_run_new_pass_is_logged_not_alerted(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    # Baseline says mini-pass FAILED; HEAD passes it -> a new pass, no alert.
    write_baseline(
        baseline_path,
        baseline_document(
            {"mini-pass": outcomes.FAILED, "mini-fail": outcomes.FAILED},
            generated_at=_NOW,
        ),
    )
    alerter = RecordingAlerter()
    code, text = _run(
        [
            "run",
            "--suite",
            "all",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--baseline",
            str(baseline_path),
        ],
        alerter=alerter,
    )
    assert code == cli.EXIT_OK
    assert alerter.alerts == []
    assert "NEW PASS: mini-pass" in text


def test_run_agent_error_is_visible_and_non_clean(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    write_baseline(
        baseline_path,
        baseline_document({"mini-pass": outcomes.PASSED}, generated_at=_NOW),
    )
    source = MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="dispatch_failed",
                error="hq dispatch failed before launch: missing --ticket",
            ),
        }
    )
    alerter = RecordingAlerter()

    code, text = _run(
        [
            "run",
            "--task",
            "mini-pass",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--baseline",
            str(baseline_path),
        ],
        source=source,
        alerter=alerter,
    )

    assert code == cli.EXIT_ERROR
    assert alerter.alerts == []
    assert "0 passed  0 failed  1 errors" in text
    assert (
        "AGENT ERROR: mini-pass: hq dispatch failed before launch: missing --ticket"
        in text
    )
    assert "non-clean: agent error" in text
    assert "no regression" not in text


def test_run_json_agent_error_reports_error_count_and_reason(tmp_path):
    source = MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="dispatch_failed",
                error="worker id gym-mini-pass-1234 was refused",
            ),
        }
    )

    code, text = _run(
        [
            "run",
            "--task",
            "mini-pass",
            "--json",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--no-compare",
        ],
        source=source,
    )

    payload = json.loads(text)
    assert code == cli.EXIT_ERROR
    assert payload["passed"] == 0
    assert payload["failed"] == 0
    assert payload["errors"] == 1
    assert payload["cells"] == [
        {
            "task": "mini-pass",
            "task_version": 1,
            "verdict": outcomes.AGENT_ERROR,
            "reason": "worker id gym-mini-pass-1234 was refused",
            "subchecks": [],
            "score": None,
        }
    ]


def test_run_against_unestablished_baseline_is_clean(tmp_path):
    from gym.baseline import unestablished_baseline

    baseline_path = tmp_path / "gym-baseline.json"
    baseline_path.write_text(serialize_baseline(unestablished_baseline()))
    alerter = RecordingAlerter()
    code, text = _run(
        [
            "run",
            "--suite",
            "all",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--baseline",
            str(baseline_path),
        ],
        alerter=alerter,
    )
    assert code == cli.EXIT_OK
    assert alerter.alerts == []
    assert "unestablished" in text


def test_run_reports_malformed_baseline_without_traceback(tmp_path, capsys):
    baseline_path = tmp_path / "gym-baseline.json"
    baseline_path.write_text("{not-json", encoding="utf-8")
    alerter = RecordingAlerter()
    code, text = _run(
        [
            "run",
            "--suite",
            "all",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--baseline",
            str(baseline_path),
        ],
        alerter=alerter,
    )

    captured = capsys.readouterr()
    assert code == cli.EXIT_ERROR
    assert text == ""
    assert alerter.alerts == []
    assert "[hq gym] cannot read baseline" in captured.err
    assert "Traceback" not in captured.err


# --------------------------------------------------------------------------- #
# hq gym baseline update — operator-gated round-trip
# --------------------------------------------------------------------------- #


def test_baseline_update_requires_confirm(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    code, _ = _run(
        [
            "baseline",
            "update",
            "--tasks-dir",
            str(MINI_SUITE),
            "--baseline",
            str(baseline_path),
        ]
    )
    assert code == cli.EXIT_ERROR
    assert not baseline_path.exists()  # nothing written without --confirm


def test_baseline_update_refuses_non_green_suite(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    code, text = _run(
        [
            "baseline",
            "update",
            "--confirm",
            "--tasks-dir",
            str(MINI_SUITE),
            "--baseline",
            str(baseline_path),
            "--taint-mode",
            "enforce",
        ]
    )

    assert code == cli.EXIT_ERROR
    assert text == ""
    assert not baseline_path.exists()


def test_baseline_update_round_trip_then_clean_run(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    # 1. Update writes the baseline from the fresh (fake) suite verdicts.
    code, _ = _run(
        [
            "baseline",
            "update",
            "--confirm",
            "--tasks-dir",
            str(MINI_SUITE),
            "--baseline",
            str(baseline_path),
            "--taint-mode",
            "enforce",
        ],
        source=_green_source(),
        now=_NOW,
    )
    assert code == cli.EXIT_OK
    loaded = load_baseline(baseline_path)
    assert loaded.established is True
    assert loaded.verdicts == {
        "mini-pass": outcomes.PASSED,
        "mini-fail": outcomes.PASSED,
    }
    # Deterministic / sorted on disk.
    assert list(json.loads(baseline_path.read_text())["verdicts"]) == [
        "mini-fail",
        "mini-pass",
    ]

    # 2. An immediately-following comparison run over the same verdicts is clean.
    alerter = RecordingAlerter()
    code, _ = _run(
        [
            "run",
            "--suite",
            "all",
            "--tasks-dir",
            str(MINI_SUITE),
            "--taint-mode",
            "enforce",
            "--baseline",
            str(baseline_path),
        ],
        source=_green_source(),
        alerter=alerter,
    )
    assert code == cli.EXIT_OK
    assert alerter.alerts == []


def test_baseline_update_replaces_only_the_swept_model_slice(tmp_path):
    baseline_path = tmp_path / "gym-baseline.json"
    qwen = "local/qwen2.5-coder-32b-q5"
    claude = "claude-opus-5"
    write_baseline(
        baseline_path,
        baseline_document(
            {
                "default-only": outcomes.PASSED,
                verdict_key("mini-pass", claude): outcomes.PASSED,
                verdict_key("stale-deleted-task", qwen): outcomes.PASSED,
                verdict_key("mini-pass", qwen): outcomes.FAILED,
            },
            generated_at="2026-08-24T00:00:00+00:00",
        ),
    )

    code, _ = _run(
        [
            "baseline",
            "update",
            "--confirm",
            "--tasks-dir",
            str(MINI_SUITE),
            "--baseline",
            str(baseline_path),
            "--taint-mode",
            "enforce",
            "--model",
            qwen,
        ],
        source=_green_source(),
        now=_NOW,
    )

    assert code == cli.EXIT_OK
    assert load_baseline(baseline_path).verdicts == {
        "default-only": outcomes.PASSED,
        verdict_key("mini-pass", claude): outcomes.PASSED,
        verdict_key("mini-fail", qwen): outcomes.PASSED,
        verdict_key("mini-pass", qwen): outcomes.PASSED,
    }
