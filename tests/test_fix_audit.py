"""Audit security and operator inspection through the synthetic protocol."""

import io
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("gym.dispatch", reason="host integration is not shipped")

from gym import cli, fix_audit, grader, suite
from gym.schema import load_task
from test_fix_corpus import ROOT, stage


@pytest.mark.parametrize("filename", ["staged-manifest.json", "target.py"])
@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_fixture_read_failure_is_deferred_per_cell(
    tmp_path, monkeypatch, audit_keys, filename, error_type
):
    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    stage(task.task_dir / "task.toml", tmp_path)
    other = load_task(ROOT / "tests/fixtures/mini-suite/mini-pass")
    unreadable = task.task_dir / "fixture" / filename
    failure = error_type("fixture read unavailable")
    read_bytes = Path.read_bytes

    def read(path):
        if path == unreadable:
            raise failure
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    snapshot = grader.snapshot_verifiers([task, other])
    assert snapshot[unreadable] is failure
    for original in (task.task_dir / "fixture").rglob("*"):
        if original.is_file() and not original.is_symlink() and original != unreadable:
            assert snapshot[original] == read_bytes(original)
    assert snapshot[other.verifier_path] == read_bytes(other.verifier_path)

    class Source:
        def __init__(self):
            self.dispatched = []

        def run(self, selected):
            self.dispatched.append(selected.id)
            result = {"done": True} if selected == task else {"answer": "ok"}
            return grader.Trajectory("succeeded", result, sandbox_root=tmp_path)

    source = Source()
    result = suite.run_suite([task, other], source, taint_mode="enforce")
    assert source.dispatched == [task.id, other.id]
    assert [cell.outcome for cell in result.cells] == ["error", "passed"]
    audit = fix_audit.decrypt(result.cells[0].fix_audit, *map(str, audit_keys))
    assert audit["verdict"] == "error"
    assert audit["incomplete"] is True
    assert grader._VERIFIER_FILES.get() is None


def test_protocol_artifact_held_and_show(tmp_path, protocol_backend, audit_keys):
    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    stage(task.task_dir / "task.toml", tmp_path)
    target = tmp_path / "target.py"
    target.write_text((task.task_dir / "expected/reference.py").read_text() + "\n# GYM_RESULT\n")
    source = type(
        "Source",
        (),
        {
            "run": lambda self, task: grader.Trajectory(
                "succeeded", {"done": True}, sandbox_root=tmp_path, launch_request_id="audit-cell"
            )
        },
    )()
    cell = grader.grade_task(task, source)
    assert cell.outcome == "integrity_hold"
    result = suite.SuiteResult(suite="fix", graded=[suite.GradedCell(cell, "")])
    path = cli._store_model_result(result, None, None, tmp_path / "results")
    artifact = json.loads(path.read_text())["summary"]["cells"][0]["fix_audit"]
    plain = fix_audit.decrypt(artifact, *map(str, audit_keys))
    assert plain["integrity_flags"] == cell.integrity_flags
    assert plain["held_verdict"] == cell.held_verdict
    assert plain["files"][0]["submitted"] == target.read_text()
    assert "logging" in plain["files"][0]["diff"]
    assert len(plain["cases"]) == 3
    assert all(case["outcome"] == "passed" for case in plain["cases"])
    cases = json.loads((task.task_dir / task.hidden_tests_entrypoint).read_text())
    persisted = json.dumps(plain)
    for case in cases:
        assert json.dumps(case["call"])[1:-1] not in persisted
        assert json.dumps(case["expected"]) not in persisted
    assert target.read_text() not in path.read_text()
    out = io.StringIO()
    assert (
        cli.main(
            [
                "show",
                str(path),
                "--cell",
                "audit-cell",
                "--task",
                task.id,
                "--certificate",
                str(audit_keys[0]),
                "--key",
                str(audit_keys[1]),
            ],
            out=out,
        )
        == 0
    )
    assert "held_verdict" in out.getvalue()
    assert "staged/target.py" in out.getvalue()
    assert "elapsed_seconds" in out.getvalue()


def test_cap_and_wrong_key(tmp_path, audit_keys):
    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    (tmp_path / "target.py").write_text("x" * (1024 * 1024))
    payload = fix_audit.submission(task, tmp_path, None)
    assert payload["files"][0]["truncated"]
    envelope = fix_audit.seal(payload)
    assert len(json.dumps(envelope).encode()) <= fix_audit.CAP_BYTES
    assert fix_audit.decrypt(envelope, *map(str, audit_keys))["files"][0]["truncated"]
    wrong = tmp_path / "wrong.pem"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(wrong), "2048"],
        check=True,
        capture_output=True,
        timeout=20,
    )
    with pytest.raises(grader.GraderError):
        fix_audit.decrypt(envelope, str(audit_keys[0]), str(wrong))


def test_missing_recipient_prevents_dispatch(tmp_path, monkeypatch):
    monkeypatch.delenv("GYM_FIX_AUDIT_CERT", raising=False)
    monkeypatch.setattr("agent_os_config.roots.hq_root", lambda: tmp_path)
    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    source = type("Source", (), {"run": lambda *_: pytest.fail("must not dispatch")})()
    with pytest.raises(grader.GraderError, match="operator-provisioned"):
        grader.grade_task(task, source)


def test_case_outcomes_exclude_observations(tmp_path, monkeypatch, audit_keys):
    from dataclasses import replace
    from gym import hidden_tests

    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    task = replace(task, hidden_tests_expected_tests=5)
    hidden = task.task_dir / task.hidden_tests_entrypoint
    secret = "UNIQUE_HIDDEN_EXPECTATION_DO_NOT_PERSIST"
    cases = [{"call": "SECRET_CASE_SOURCE", "expected": {"ok": secret}} for _ in range(5)]
    original_execute = hidden_tests._execute
    calls = iter(
        [{"ok": secret}, {"ok": "wrong"}, {"raised": "builtins.ValueError"}, None, "timeout"]
    )

    def execute(command, work, timeout, *, input_data=b""):
        if not input_data:
            nonce_path = next(work.glob("gym-bind-*"))
            return 0, False, "gym-ready\n" + nonce_path.read_text() + "\n"
        response = next(calls)
        if response is None:
            return 1, False, "diagnostic " + secret
        if response == "timeout":
            return 0, True, secret
        nonce = json.loads(input_data)["nonce"]
        return 0, False, "GYM_RESULT " + json.dumps({"nonce": nonce, **response})

    monkeypatch.setattr(hidden_tests, "_command", lambda work, args: args)
    monkeypatch.setattr(hidden_tests, "_execute", execute)
    result = hidden_tests.run(task, tmp_path, {hidden: json.dumps(cases).encode()})
    monkeypatch.setattr(hidden_tests, "_execute", original_execute)
    assert [item["outcome"] for item in result["audit_cases"]] == [
        "passed",
        "failed",
        "raised",
        "error",
        "timed_out",
    ]
    envelope = fix_audit.seal({"cases": result["audit_cases"]})
    plain = fix_audit.decrypt(envelope, *map(str, audit_keys))
    assert secret not in json.dumps(plain)
    assert "SECRET_CASE_SOURCE" not in json.dumps(plain)
    assert plain["cases"][2]["raised_type"] == "builtins.ValueError"


def test_missing_submission_is_retained(tmp_path, audit_keys):
    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    stage(task.task_dir / "task.toml", tmp_path)
    cell = grader.grade(task, grader.Trajectory("succeeded", None, sandbox_root=tmp_path))
    assert cell.outcome == "no_submission"
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain["files"][0]["submitted"] == (tmp_path / "target.py").read_text()


def test_partial_cases_survive_sandbox_failure(tmp_path, monkeypatch):
    from dataclasses import replace
    from gym import hidden_tests

    task = replace(
        load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint"), hidden_tests_expected_tests=2
    )
    cases = [{"call": "result=1", "expected": {"ok": 1}}] * 2
    count = 0
    records = []

    def execute(command, work, timeout, *, input_data=b""):
        nonlocal count
        if not input_data:
            return 0, False, "gym-ready\n" + next(work.glob("gym-bind-*")).read_text() + "\n"
        count += 1
        if count == 2:
            raise grader.GraderError("sandbox unavailable")
        return (
            0,
            False,
            "GYM_RESULT " + json.dumps({"nonce": json.loads(input_data)["nonce"], "ok": 1}),
        )

    monkeypatch.setattr(hidden_tests, "_command", lambda work, args: args)
    monkeypatch.setattr(hidden_tests, "_execute", execute)
    with pytest.raises(grader.GraderError):
        hidden_tests.run(
            task,
            tmp_path,
            {task.task_dir / task.hidden_tests_entrypoint: json.dumps(cases).encode()},
            audit_cases=records,
        )
    assert records[0]["outcome"] == "passed"
    assert records[1]["outcome"] == "error"
    assert records[1]["executed"] is False


def test_invalid_recipient_prevents_dispatch(tmp_path, monkeypatch):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate")
    monkeypatch.setenv("GYM_FIX_AUDIT_CERT", str(bad))
    task = load_task(ROOT / "tasks/fix-corrupt-refresh-checkpoint")
    source = type("Source", (), {"run": lambda *_: pytest.fail("must not dispatch")})()
    with pytest.raises(grader.GraderError, match="cryptography failed"):
        grader.grade_task(task, source)


def test_extreme_cap_preserves_hold(audit_keys):
    envelope = fix_audit.seal(
        {
            "files": [{"submitted": "x" * (fix_audit.CAP_BYTES * 2)}],
            "cases": [],
            "integrity_flags": ["protocol_forgery"],
            "held_verdict": {"verdict": "failed"},
        }
    )
    assert len(json.dumps(envelope).encode()) <= fix_audit.CAP_BYTES
    plain = fix_audit.decrypt(envelope, *map(str, audit_keys))
    assert plain["truncated"] is True
    assert plain["integrity_flags"] == ["protocol_forgery"]
    assert plain["held_verdict"] == {"verdict": "failed"}


@pytest.mark.parametrize("override", [False, True])
def test_recipient_default_and_override(tmp_path, monkeypatch, audit_keys, override):
    monkeypatch.delenv("GYM_FIX_AUDIT_CERT", raising=False)
    monkeypatch.setattr("agent_os_config.roots.hq_root", lambda: tmp_path)
    certificate = tmp_path / "gym/audit/gym-audit-recipient.pem"
    certificate.parent.mkdir(parents=True)
    certificate.write_bytes(audit_keys[0].read_bytes())
    expected = str(certificate)
    if override:
        expected = str(audit_keys[0])
        monkeypatch.setenv("GYM_FIX_AUDIT_CERT", expected)
        monkeypatch.setattr("agent_os_config.roots.hq_root", lambda: pytest.fail("env wins"))
    monkeypatch.setenv("GYM_FIX_AUDIT_KEY", str(tmp_path / "unused-private-key.pem"))
    assert fix_audit.recipient() == expected


@pytest.mark.parametrize("state", ["missing", "directory", "empty", "unreadable"])
def test_unusable_default_names_tried_path(tmp_path, monkeypatch, state):
    monkeypatch.delenv("GYM_FIX_AUDIT_CERT", raising=False)
    monkeypatch.setattr("agent_os_config.roots.hq_root", lambda: tmp_path)
    certificate = tmp_path / "gym/audit/gym-audit-recipient.pem"
    certificate.parent.mkdir(parents=True)
    if state == "directory":
        certificate.mkdir()
    elif state in ("empty", "unreadable"):
        certificate.touch()
    if state == "unreadable":
        monkeypatch.setattr(Path, "open", lambda *_: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(grader.GraderError) as error:
        fix_audit.recipient()
    assert "operator-provisioned GYM_FIX_AUDIT_CERT" in str(error.value)
    assert str(certificate) in str(error.value)


def test_controller_has_no_private_key_default():
    source = Path(fix_audit.__file__).read_text()
    assert "GYM_FIX_AUDIT_KEY" not in source
    assert "private.pem" not in source
