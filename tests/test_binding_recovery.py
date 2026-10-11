"""Real saved-run transitions; no HQ, ledger, quota or worker side effects."""

import json
import fcntl
from copy import deepcopy

import pytest

from gym import binding_recovery as recovery, matrix, matrix_report
from test_matrix import prepare, result, resume


def stopped_aliases(tmp_path, **kwargs):
    run = prepare(tmp_path, **kwargs)
    for cell in run.cells[:2]:
        cell.update(status="done", launch_request_id="shared", dispatch_count=1)
        cell["attempts"] = [dict(launch_request_id="shared", **result(tokens=400))]
    run.manifest["tokens_spent"] = 800
    run.save()
    (run.directory / "stop").touch()
    return run


def quarantine(run):
    return recovery.write_record(
        run.directory,
        run.manifest,
        run.cells,
        reason="Both aliased results excluded; every saved token stays charged",
        evidence_reference="owner-canonical-binding-proof.json",
    )


def saved_bytes(run):
    return {
        c["id"]: (run.directory / f"{c['id']}.json").read_bytes()
        for c in run.cells
        if c["attempts"]
    }


def test_quarantine_resume_preserves_bytes_charge_and_pending_cells(tmp_path):
    run = stopped_aliases(tmp_path)
    before = saved_bytes(run)
    record = quarantine(run)
    assert record["recorded_tokens"] == 800
    assert recovery.quarantined_ids(record) == {c["id"] for c in run.cells[:2]}
    assert (run.directory / "stop").exists()
    again = resume(run)
    assert again.total() == (800, [])
    assert again.manifest["budget"]["max_total_tokens"] == 1000
    assert again.manifest["repetitions"] == 1
    assert saved_bytes(again) == before
    assert not run.backend.dispatched
    again.tick()
    assert run.backend.dispatched == [c["id"] for c in run.cells[2:]]
    assert saved_bytes(again) == before
    assert again.total() == (800, [])


@pytest.mark.parametrize("kwargs", [{}, {"max_total_tokens": 900}])
def test_quarantine_resume_retains_approved_budget_override(tmp_path, kwargs):
    run = stopped_aliases(tmp_path, max_total_tokens=900)
    record = quarantine(run)
    before = saved_bytes(run)
    again = resume(run, **kwargs)
    assert again.manifest["budget"] == record["context"]["budget"]
    assert again.manifest["budget_overrides"] == {"max_total_tokens": 900}
    assert again.total() == (800, [])
    assert saved_bytes(again) == before
    assert not run.backend.dispatched


def test_quarantine_refuses_persisted_budget_drift_before_normalization(tmp_path):
    run = stopped_aliases(tmp_path, max_total_tokens=900)
    quarantine(run)
    path = run.directory / "manifest.json"
    doc = json.loads(path.read_text())
    doc["budget"]["min_weekly_remaining_pct"] = 0
    path.write_text(json.dumps(doc))
    before = {p: p.read_bytes() for p in run.directory.glob("*.json")}
    with pytest.raises(ValueError, match="binding quarantine"):
        resume(run, max_total_tokens=900)
    assert {p: p.read_bytes() for p in run.directory.glob("*.json")} == before
    assert (run.directory / "stop").exists()
    assert not run.backend.dispatched


def test_quarantined_launch_remains_reserved_against_future_reuse(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    run.backend.dispatch = lambda cell: ("shared", "/new-workspace")
    again = resume(run)
    again.tick()
    conflicting = again.cells[2]
    assert conflicting["status"] == "dispatching"
    assert conflicting["duplicate_launch_request_id"] == "shared"
    assert not conflicting["attempts"]
    assert "launch_request_id" not in conflicting
    assert again.total() == (800, [])
    assert "recover launch id" in again.manifest["halt_reason"]


@pytest.mark.parametrize("mutation", ["bytes", "cell", "record", "context", "owners"])
def test_recovery_evidence_drift_refuses_before_resume_writes(tmp_path, mutation):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    path = run.directory / f"{run.cells[0]['id']}.json"
    if mutation == "bytes":
        path.write_text(path.read_text() + " ")
    elif mutation == "cell":
        cell = json.loads(path.read_text())
        cell["attempts"][0]["cell"]["metrics"]["token_usage_running_total"] = 0
        path.write_text(json.dumps(cell))
    else:
        path = run.directory / recovery.RECORD
        doc = json.loads(path.read_text())
        if mutation == "record":
            doc["snapshot_hashes"] = {}
        elif mutation == "context":
            doc["context"]["budget"]["max_total_tokens"] = 999999
        else:
            doc["aliases"]["shared"] = [run.cells[0]["id"]]
        path.write_text(json.dumps(doc))
    before = {p: p.read_bytes() for p in run.directory.glob("*.json")}
    with pytest.raises(ValueError, match="binding quarantine"):
        resume(run)
    assert {p: p.read_bytes() for p in run.directory.glob("*.json")} == before
    assert (run.directory / "stop").exists()
    assert not run.backend.dispatched


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_total_tokens": 2000},
        {"repetitions": 2},
        {"ignore_window": True},
        {"ignore_quota_floor": ["codex"]},
        {"max_cells": 200},
    ],
)
def test_quarantine_cannot_expand_or_weaken_original_gates(tmp_path, kwargs):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    before = {p: p.read_bytes() for p in run.directory.glob("*.json")}
    with pytest.raises(ValueError, match="binding quarantine"):
        resume(run, **kwargs)
    assert {p: p.read_bytes() for p in run.directory.glob("*.json")} == before
    assert (run.directory / "stop").exists()


def test_active_or_unmeasured_attempts_cannot_be_quarantined(tmp_path):
    run = stopped_aliases(tmp_path)
    run.cells[0]["status"] = "in_flight"
    with pytest.raises(ValueError, match="drain"):
        quarantine(run)
    run.cells[0]["status"] = "done"
    run.cells[0]["attempts"][0]["cell"]["metrics"]["token_usage_running_total"] = None
    with pytest.raises(ValueError, match="measured"):
        quarantine(run)
    assert not (run.directory / recovery.RECORD).exists()


def test_creation_idempotent_never_clears_stop_or_replaces_evidence(tmp_path):
    run = stopped_aliases(tmp_path)
    before = saved_bytes(run)
    record = quarantine(run)
    path = run.directory / recovery.RECORD
    original = path.read_bytes()
    assert quarantine(run) == record
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="never replace"):
        recovery.write_record(
            run.directory, run.manifest, run.cells, reason="other", evidence_reference="other"
        )
    assert path.read_bytes() == original
    assert saved_bytes(run) == before
    assert (run.directory / "stop").exists()


def test_later_unmeasured_attempt_halts_normally_without_refunding_sealed_spend(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    again = resume(run)
    new = again.cells[2]
    new.update(status="done", launch_request_id="new-launch")
    new["attempts"] = [dict(launch_request_id="new-launch", **result(tokens=None))]
    again.backend.recovered = None
    before = saved_bytes(run)
    assert again.tick() is False
    assert again.manifest["halt_reason"] == "unmeasured tokens"
    assert again.total() == (800, [new["id"]])
    assert not again.backend.dispatched
    for key, raw in before.items():
        assert (again.directory / f"{key}.json").read_bytes() == raw


def test_sealed_completed_snapshot_cannot_change_in_memory_or_on_tick(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    again = resume(run)
    original = saved_bytes(again)
    again.cells[0]["attempts"][0]["cell"]["verdict"] = "passed"
    with pytest.raises(ValueError, match="snapshot changed"):
        again.save()
    assert saved_bytes(again) == original


def test_report_excludes_both_aliases_without_changing_budget_or_snapshots(tmp_path):
    run = stopped_aliases(tmp_path)
    record = quarantine(run)
    before = saved_bytes(run)
    cells = deepcopy(run.cells)
    for c in cells[:2]:
        c["attempts"][0]["cell"].update(verdict="passed", cost_usd=1, quota_share=0.1)
        c["attempts"][0]["cell"]["metrics"]["metrics_complete"] = True
    report = matrix_report.build_report(
        run.manifest,
        cells,
        {"codex": {"model": "m", "level": "low"}},
        quarantined=recovery.quarantined_ids(record),
    )
    assert sum(r["n"] for r in report["rows"]) == 0
    assert sum(r["excluded"]["binding_quarantined"] for r in report["rows"]) == 2
    assert all(r["dollars_total"] is None for r in report["rows"])
    assert report["token_cap"] == 1000
    assert saved_bytes(run) == before
    assert run.total() == (800, [])


def test_reports_never_count_known_aliases_before_quarantine(tmp_path):
    run = stopped_aliases(tmp_path)
    for c in run.cells[:2]:
        c["attempts"][0]["cell"].update(verdict="passed", cost_usd=1)
        c["attempts"][0]["cell"]["metrics"]["metrics_complete"] = True
    report = matrix_report.build_report(
        run.manifest,
        run.cells,
        {"codex": {"model": "m", "level": "low"}},
    )
    assert sum(r["n"] for r in report["rows"]) == 0
    assert sum(r["excluded"]["binding_quarantined"] for r in report["rows"]) == 2
    assert not (run.directory / recovery.RECORD).exists()


def test_missing_saved_attempt_cannot_publish_invalid_recovery(tmp_path):
    run = stopped_aliases(tmp_path)
    run.cells[0]["attempts"] = []
    with pytest.raises(ValueError, match="every launch owner"):
        quarantine(run)
    assert not (run.directory / recovery.RECORD).exists()


def test_public_command_requires_owner_run_lock_and_leaves_stop(tmp_path, monkeypatch, capsys):
    from gym import dispatch

    run = stopped_aliases(tmp_path / "gym" / "matrix")
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda _: tmp_path)
    args = [
        "quarantine-bindings",
        "run",
        "--reason",
        "alias incident",
        "--evidence-reference",
        "canonical-proof.json",
    ]
    before = saved_bytes(run)
    lock_path = run.directory.parent / "run.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            matrix.main(args)
        assert not (run.directory / recovery.RECORD).exists()
    assert matrix.main(args) == 0
    assert json.loads(capsys.readouterr().out)["recorded_tokens"] == 800
    assert (run.directory / "stop").exists()
    assert saved_bytes(run) == before
    assert not run.backend.dispatched
