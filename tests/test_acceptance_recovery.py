"""Disposable saved runs and canonical receipts; no production dispatch or reads."""

import fcntl
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("gym.acceptance_recovery", reason="host integration is not shipped")
pytest.importorskip("gym.matrix", reason="host integration is not shipped")
pytest.importorskip("gym.ledger", reason="host integration is not shipped")

from gym import acceptance_recovery as recovery, matrix
from gym.ledger import LedgerReader
from test_matrix import prepare, result


class Receipts:
    def __init__(self, status=None):
        self.calls = []
        self.status = status
        self.close = Mock()

    def acceptance_receipts(self, launch):
        self.calls.append(launch)
        return dict(
            launch_request_id=launch,
            launches=[],
            workers=[] if self.status is None else [dict(run_id="original", status=self.status)],
            historical_absence_proven=False,
            logical_ownership_checked=False,
            process_history_checked=False,
        )


def stopped_unknown(tmp_path):
    run = prepare(tmp_path)
    run.cells[0].update(status="dispatching", dispatch_intent_key="gym-original")
    run.cells[1].update(status="done", launch_request_id="lrq_charged")
    run.cells[1]["attempts"] = [dict(launch_request_id="lrq_charged", **result(tokens=400))]
    run.manifest["tokens_spent"] = 400
    run.save()
    (run.directory / "stop").touch()
    (run.directory.parent / "run.lock").touch()
    # Opaque original quarantine bytes must survive the investigation too.
    (run.directory / "original-quarantine.json").write_bytes(b'{"original": true}\n')
    return run


def snapshot(run):
    return {p.name: p.read_bytes() for p in run.directory.iterdir() if p.is_file()}


def report(run, reader, **kwargs):
    return recovery.report_locked(
        run.directory,
        cell_id=run.cells[0]["id"],
        launch_ids=["lrq_original"],
        evidence_reference="archived-unfulfilled-and-pre-lrq-refusal.json",
        hq_root=run.directory.parent,
        reader=reader,
        **kwargs,
    )


@pytest.mark.parametrize("status", [None, "failed", "succeeded", "running", "queued", "unknown"])
def test_current_receipts_never_certify_historical_nonacceptance(tmp_path, status):
    run = stopped_unknown(tmp_path)
    before = snapshot(run)
    receipt = Receipts(status)
    outcome = report(run, receipt)
    assert outcome["outcome"] == "unresolved"
    assert outcome["confirmed_nonacceptance"] is False
    assert outcome["acceptance_gate_resolved"] is False
    assert outcome["resume_authorized"] is False
    assert outcome["tokens_spent"] == 400
    assert outcome["stop_retained"] is True
    assert outcome["snapshot_hashes"][f"{run.cells[0]['id']}.json"]
    assert receipt.calls == ["lrq_original"]
    assert "lrq_original: intent binding is unverified or differs" in outcome["residual_gates"]
    receipt.close.assert_not_called()
    if status in {"running", "queued", "unknown"}:
        assert any(
            "current or unknown worker ownership" in gate for gate in outcome["residual_gates"]
        )
    assert snapshot(run) == before
    assert not run.backend.dispatched


def test_foreign_cell_receipt_is_refused_and_stays_charged(tmp_path):
    run = stopped_unknown(tmp_path)
    run.cells[0]["duplicate_launch_request_id"] = "lrq_charged"
    run.save()
    before = snapshot(run)
    outcome = report(run, Receipts("failed"))
    assert any("another cell owns" in gate for gate in outcome["residual_gates"])
    assert outcome["tokens_spent"] == 400
    assert snapshot(run) == before


def test_read_failure_does_not_mean_absence_or_leak_error(tmp_path):
    run = stopped_unknown(tmp_path)

    class Failed:
        def acceptance_receipts(self, launch):
            raise TimeoutError("private database contents")

    outcome = report(run, Failed())
    assert outcome["receipts"][0]["read_error"] == "TimeoutError"
    assert "private database contents" not in str(outcome)
    assert outcome["outcome"] == "unresolved"


def test_native_lock_and_stop_are_required_without_writes(tmp_path):
    run = stopped_unknown(tmp_path)
    before = snapshot(run)
    receipt = Receipts()
    with (run.directory.parent / "run.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="currently active"):
            report(run, receipt)
    assert not receipt.calls
    assert snapshot(run) == before
    (run.directory / "stop").unlink()
    with pytest.raises(ValueError, match="STOP"):
        report(run, receipt)
    assert not (run.directory / "stop").exists()


def test_missing_native_lock_is_refused_without_reads_or_writes(tmp_path):
    run = stopped_unknown(tmp_path)
    lock_path = run.directory.parent / "run.lock"
    lock_path.unlink()
    before = snapshot(run)
    receipt = Receipts()
    with pytest.raises(ValueError, match="requires a regular existing run lock"):
        report(run, receipt)
    assert not receipt.calls
    assert not lock_path.exists()
    assert snapshot(run) == before


def test_public_command_reports_missing_lock_cleanly(tmp_path, monkeypatch, capsys):
    from gym import dispatch

    run = stopped_unknown(tmp_path / "gym" / "matrix")
    lock_path = run.directory.parent / "run.lock"
    lock_path.unlink()
    before = snapshot(run)
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda _: tmp_path)
    open_reader = Mock()
    monkeypatch.setattr(LedgerReader, "open_read_only", open_reader)
    with pytest.raises(SystemExit) as error:
        matrix.main(
            [
                "acceptance-report",
                "run",
                "--cell-id",
                run.cells[0]["id"],
                "--evidence-reference",
                "proof.json",
            ]
        )
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "acceptance report requires a regular existing run lock" in output.err
    assert "Traceback" not in output.err
    assert not output.out
    open_reader.assert_not_called()
    assert not lock_path.exists()
    assert snapshot(run) == before


def test_evidence_drift_and_symlink_refuse(tmp_path):
    run = stopped_unknown(tmp_path)

    class Drift(Receipts):
        def acceptance_receipts(self, launch):
            (run.directory / "manifest.json").write_text("{}")
            return super().acceptance_receipts(launch)

    with pytest.raises(ValueError, match="changed during"):
        report(run, Drift())
    path = run.directory / "original-quarantine.json"
    path.unlink()
    path.symlink_to(run.directory / "manifest.json")
    with pytest.raises(ValueError, match="regular file"):
        report(run, Receipts())


def test_public_command_reports_nonzero_without_mutation(tmp_path, monkeypatch, capsys):
    from gym import dispatch

    run = stopped_unknown(tmp_path / "gym" / "matrix")
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda _: tmp_path)
    monkeypatch.setattr(LedgerReader, "open_read_only", lambda _: Receipts())
    before = snapshot(run)
    assert (
        matrix.main(
            [
                "acceptance-report",
                "run",
                "--cell-id",
                run.cells[0]["id"],
                "--launch-request-id",
                "lrq_original",
                "--evidence-reference",
                "proof.json",
            ]
        )
        == 2
    )
    assert '"confirmed_nonacceptance": false' in capsys.readouterr().out
    assert snapshot(run) == before
    assert not run.backend.dispatched


def test_canonical_adapter_uses_all_runs_and_projects_private_payload(tmp_path, monkeypatch):
    from session_ledger.canonical_views import tool_output_compression_ab as toc
    from session_ledger.canonical_views import worker_pool_control as control

    reader = LedgerReader(None)
    target = object()
    monkeypatch.setattr(reader, "_read_args", lambda: (None, target))
    calls = []

    def launches(conn, lrq, *, target):
        calls.append((conn, lrq, target))
        return [
            dict(
                request_payload_json={
                    "workerId": "original",
                    "idempotencyKey": "gym-original",
                    "prompt": "private",
                }
            )
        ]

    def workers(conn, lrq, *, target):
        calls.append((conn, lrq, target))
        return [dict(run_id="old", status="running"), dict(run_id="new", status="failed")]

    monkeypatch.setattr(toc, "fetch_gym_recovery_launch", launches)
    monkeypatch.setattr(control, "fetch_launch_request_worker_runs", workers)
    receipt = reader.acceptance_receipts("lrq_original")
    assert [r["run_id"] for r in receipt["workers"]] == ["old", "new"]
    assert calls == [(None, "lrq_original", target)] * 2
    assert "private" not in str(receipt)
    assert receipt["historical_absence_proven"] is False


def test_candidate_limits_and_snapshot_identity_are_enforced(tmp_path):
    run = stopped_unknown(tmp_path)
    receipt = Receipts()
    with pytest.raises(ValueError, match="eight LRQ"):
        recovery.build_report(
            run,
            cell_id=run.cells[0]["id"],
            launch_ids=[f"lrq_{i}" for i in range(9)],
            evidence_reference="proof",
            reader=receipt,
        )
    cell = deepcopy(run.cells[0])
    cell["id"] = "different"
    import json

    (run.directory / f"{run.cells[0]['id']}.json").write_text(json.dumps(cell))
    with pytest.raises(ValueError, match="identity differs"):
        report(run, receipt)
    assert not receipt.calls


def test_budget_headroom_and_matching_intent_do_not_resolve_ownership(tmp_path):
    run = stopped_unknown(tmp_path)
    run.manifest["budget"]["max_total_tokens"] = 100000000
    run.save()

    class Bound(Receipts):
        def acceptance_receipts(self, launch):
            receipt = super().acceptance_receipts(launch)
            receipt["launches"] = [{"workerId": "original", "idempotencyKey": "gym-original"}]
            return receipt

    before = snapshot(run)
    for path in run.directory.glob("*.json"):
        path.chmod(0o400)
    outcome = report(run, Bound("failed"))
    assert outcome["max_total_tokens"] == 100000000
    assert outcome["unknown_acceptance_tokens"] is None
    assert outcome["confirmed_nonacceptance"] is False
    assert outcome["resume_authorized"] is False
    assert any("logical ownership" in gate for gate in outcome["residual_gates"])
    assert not any("intent binding" in gate for gate in outcome["residual_gates"])
    assert snapshot(run) == before


def test_canonical_setup_failure_produces_unresolved_report(tmp_path, monkeypatch):
    run = stopped_unknown(tmp_path)
    before = snapshot(run)

    def unavailable(root):
        raise TimeoutError("private connection info")

    monkeypatch.setattr(LedgerReader, "open_read_only", unavailable)
    outcome = recovery.report_locked(
        run.directory,
        cell_id=run.cells[0]["id"],
        launch_ids=["lrq_original"],
        evidence_reference="proof",
        hq_root=run.directory.parent,
    )
    assert outcome["receipts"][0]["read_error"] == "RuntimeError"
    assert outcome["outcome"] == "unresolved"
    assert "private connection info" not in str(outcome)
    assert snapshot(run) == before


@pytest.mark.parametrize("backend", ["postgres", "sqlite"])
@pytest.mark.parametrize("build_fails", [False, True])
def test_owned_reader_closes_on_success_and_failure(tmp_path, monkeypatch, backend, build_fails):
    run = stopped_unknown(tmp_path)
    before = snapshot(run)
    path = tmp_path / "ledger.sqlite"
    if backend == "sqlite":
        sqlite3.connect(path).close()
    target = SimpleNamespace(backend=backend, db_path=path)
    monkeypatch.setattr("gym.ledger._ledger_target", lambda _: target)
    reader = LedgerReader.open_read_only()
    conn, _ = reader._read_args()
    close = Mock(wraps=reader.close)
    monkeypatch.setattr(reader, "close", close)
    monkeypatch.setattr(reader, "acceptance_receipts", Receipts().acceptance_receipts)
    monkeypatch.setattr(LedgerReader, "open_read_only", lambda _: reader)
    if build_fails:
        monkeypatch.setattr(recovery, "build_report", Mock(side_effect=ValueError("bad report")))
    kwargs = dict(
        cell_id=run.cells[0]["id"],
        launch_ids=["lrq_original"],
        evidence_reference="proof",
        hq_root=tmp_path,
    )
    if build_fails:
        with pytest.raises(ValueError, match="bad report"):
            recovery.report_locked(run.directory, **kwargs)
    else:
        assert recovery.report_locked(run.directory, **kwargs)["outcome"] == "unresolved"
    close.assert_called_once_with()
    if conn is not None:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
    assert snapshot(run) == before


def test_reader_close_uses_database_lifecycle():
    db = Mock()
    LedgerReader(db).close()
    db.close.assert_called_once_with()
    db.conn.close.assert_not_called()


@pytest.mark.parametrize("missing", ["manifest", "attempts"])
def test_incomplete_saved_evidence_has_actionable_error(tmp_path, missing):
    run = stopped_unknown(tmp_path)
    if missing == "manifest":
        (run.directory / "manifest.json").unlink()
        message = "manifest.json"
    else:
        del run.cells[1]["attempts"]
        run.save()
        message = "saved attempts"
    before = snapshot(run)
    reader = Receipts()
    with pytest.raises(ValueError, match=message):
        report(run, reader)
    assert not reader.calls
    assert snapshot(run) == before
