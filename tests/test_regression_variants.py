"""HRX-01: named wrong fixes must be distinguished by behavioral tests."""

import json
import shutil

import pytest

from gym import grader, hidden_tests, outcomes
from gym.grader import GraderError
from gym.schema import REGRESSION_VARIANTS, TaskSchemaError, load_task, parse_task_dict
from test_regression_tests import (
    GOOD_TEST,
    Source,
    _contained_test,
    _task,
    checks,
    copy_probe,
    outcomes_of,
    probe,
    run_test,
    stage,
    test_hostile_test_contained as hostile_fixture,
)

CONTRACT_TEST = (
    GOOD_TEST
    + """
    def test_preserves_first_spelling(self):
        self.assertEqual(dedupe([" A", "a"]), [" A"])
"""
)


def named_probe(tmp_path):
    root = copy_probe(tmp_path)
    variant = root / "hidden/variants/plausible-wrong"
    shutil.copytree(root / "hidden/variants/reference", variant)
    target = variant / "target.py"
    target.write_text(target.read_text().replace("kept.append(name)", "kept.append(key)"))
    manifest = root / "task.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "[regression_test]", '[regression_test]\nmust_fail_variants = ["plausible-wrong"]'
        )
    )
    return load_task(root)


@pytest.mark.parametrize("test,passes", [(CONTRACT_TEST, True), (GOOD_TEST, False)])
def test_named_variant_pins_contract(tmp_path, protocol_backend, test, passes):
    task = named_probe(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cell = grader.grade_task(task, Source(stage(workspace, test)))
    assert cell.outcome == (outcomes.PASSED if passes else outcomes.FAILED), cell.reason
    assert checks(cell)["regression_test_fails_on_plausible-wrong"] == passes
    assert all(
        ok
        for name, ok in checks(cell).items()
        if name != "regression_test_fails_on_plausible-wrong"
    )
    assert all(item["required"] for item in cell.subchecks)


@pytest.mark.parametrize(
    "value",
    [
        ["a", "b", "c", "d"],
        *[[name] for name in REGRESSION_VARIANTS],
        ["same", "same"],
        ["../escape"],
        [""],
        [1],
        "wrong",
        [["unhashable"]],
    ],
)
def test_invalid_names_rejected(value):
    with pytest.raises(TaskSchemaError, match="must_fail_variants"):
        parse_task_dict(_task(must_fail_variants=value))


def test_unknown_key_rejected():
    with pytest.raises(TaskSchemaError, match="unknown key"):
        parse_task_dict(_task(must_fail_variant=["wrong"]))


def test_missing_directory_rejected(tmp_path):
    with pytest.raises(TaskSchemaError, match="missing variant directory"):
        parse_task_dict(_task(must_fail_variants=["missing"]), task_dir=tmp_path)


@pytest.mark.parametrize("suite", ["fix", "hard-fix"])
def test_named_variants_parse_without_task_directory(suite):
    task = parse_task_dict(_task(suite, must_fail_variants=["one", "two", "three"]))
    assert task.task_dir is None
    assert task.regression_test_must_fail_variants == ("one", "two", "three")


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_named_variant_requires_exact_targets(tmp_path, change):
    task = named_probe(tmp_path)
    root = task.task_dir / "hidden/variants/plausible-wrong"
    if change == "missing":
        (root / "rules.py").unlink()
    else:
        (root / "extra.py").write_text("")
    with pytest.raises(GraderError, match="must ship exactly"):
        run_test(task, tmp_path)


def test_no_names_result_is_byte_identical(tmp_path, protocol_backend, monkeypatch):
    task = probe()
    workspace = stage(tmp_path, GOOD_TEST)
    # Hold nondeterministic timing/nonce fields fixed to compare serialized envelopes.
    monkeypatch.setattr(hidden_tests, "_monotonic", lambda: 1)
    monkeypatch.setattr(hidden_tests.secrets, "token_hex", lambda _: "synthetic-nonce")
    result = run_test(task, workspace)
    # Pin the pre-HRX grading/audit envelope; random scratch paths in diagnostic
    # tracebacks do not enter the verdict or sealed regression run records.
    result.pop("output_tail")
    expected_runs = []
    for variant in REGRESSION_VARIANTS:
        failed = variant in {"original", "insufficient"}
        expected_runs.append(
            {
                "variant": variant,
                "outcome": "failed" if failed else "passed",
                "counts": {
                    "run": 1,
                    "failures": int(failed),
                    "errors": 0,
                    "skipped": 0,
                    "expected_failures": 0,
                    "unexpected_successes": 0,
                },
                "exit_code": 0,
                "timed_out": False,
                "elapsed_seconds": 0,
            }
        )
    expected = {"status": "completed", "path": "test_regression.py", "runs": expected_runs}
    assert json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True)
    expected_checks = [
        {"name": name, "ok": True, "reason": None, "required": True}
        for name in (
            "regression_test_fails_on_original",
            "regression_test_fails_on_insufficient",
            "regression_test_passes_on_reference",
            "regression_test_passes_on_submission",
        )
    ]
    assert json.dumps(hidden_tests.regression_subchecks(result)) == json.dumps(expected_checks)


def test_named_subcheck_is_grader_owned(tmp_path, protocol_backend):
    task = named_probe(tmp_path)
    verifier = task.verifier_path
    verifier.write_text(
        verifier.read_text().replace(
            "gv.passed()", 'gv.check("regression_test_fails_on_plausible-wrong", True)\ngv.passed()'
        )
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(GraderError, match="grader-owned"):
        grader.grade_task(task, Source(stage(workspace, CONTRACT_TEST)))


@pytest.mark.parametrize("body", hostile_fixture.pytestmark[0].args[1])
def test_named_hostile_containment(tmp_path, docker_backend, body, monkeypatch):
    task = named_probe(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requests, containers = [], []
    command, execute = hidden_tests._command, hidden_tests._execute

    def record_command(work, args):
        built = command(work, args)
        containers.append(built[built.index("--name") + 1])
        return built

    def record_execute(command, work, timeout, *, input_data=b""):
        if input_data:
            requests.append(json.loads(input_data))
        return execute(command, work, timeout, input_data=input_data)

    monkeypatch.setattr(hidden_tests, "_command", record_command)
    monkeypatch.setattr(hidden_tests, "_execute", record_execute)
    body += "\nimport sys\nframe = sys._getframe()\nwhile frame:\n    self.assertNotIn('nonce', frame.f_globals)\n    self.assertNotIn('nonce', frame.f_locals)\n    frame = frame.f_back"
    result = run_test(
        task, stage(workspace, _contained_test(body)), work_root=hidden_tests.shared_work_root()
    )
    assert set(outcomes_of(result).values()) == {"passed"}, result["output_tail"]
    assert len(requests) == len({r["nonce"] for r in requests}) == 5
    assert len(containers) == len(set(containers)) == 6  # readiness plus five variants


@pytest.mark.parametrize("suite", ["fix", "hard-fix"])
def test_three_names_allowed_in_fix_family(tmp_path, suite):
    for name in ("one", "two", "three"):
        (tmp_path / "hidden/variants" / name).mkdir(parents=True)
    task = parse_task_dict(
        _task(suite, must_fail_variants=["one", "two", "three"]), task_dir=tmp_path
    )
    assert task.regression_test_must_fail_variants == ("one", "two", "three")


def test_named_variants_use_snapshot(tmp_path, protocol_backend):
    task = named_probe(tmp_path)
    files = grader.snapshot_verifiers([task])
    shutil.rmtree(task.task_dir / "hidden/variants/plausible-wrong")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run_test(task, stage(workspace, CONTRACT_TEST), files=files)
    assert all(c["ok"] for c in hidden_tests.regression_subchecks(result))


def test_named_variant_not_run_when_hidden_failed(tmp_path, protocol_backend):
    task = named_probe(tmp_path)
    result = run_test(task, tmp_path, hidden_passed=False)
    assert [r["variant"] for r in result["runs"]] == [*REGRESSION_VARIANTS, "plausible-wrong"]
    assert set(outcomes_of(result).values()) == {"not_run"}
    assert not any(c["ok"] for c in hidden_tests.regression_subchecks(result))


def test_incomplete_audit_includes_named_variant(tmp_path, audit_keys, monkeypatch):
    from gym import fix_audit

    task = named_probe(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def broken(*args, **kwargs):
        raise GraderError("synthetic verifier failure")

    monkeypatch.setattr(grader, "_run_verifier_out_of_band", broken)
    with pytest.raises(GraderError) as raised:
        grader.grade_task(task, Source(stage(workspace, CONTRACT_TEST)))
    audit = fix_audit.decrypt(raised.value.fix_audit, *map(str, audit_keys))
    assert audit["incomplete"]
    assert [r["variant"] for r in audit["regression_runs"]] == [
        *REGRESSION_VARIANTS,
        "plausible-wrong",
    ]
    assert {r["outcome"] for r in audit["regression_runs"]} == {"not_run"}
