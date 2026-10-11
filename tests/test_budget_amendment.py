"""Approved cap restoration never rewrites quarantined attempts or resumes work."""

from copy import deepcopy
import errno
import json
import os
import stat
from unittest.mock import patch

import pytest

from gym import binding_recovery, budget_amendment as amendment, matrix
from test_binding_recovery import quarantine, stopped_aliases
from test_matrix import resume


def approve(run, cap=2000, **kwargs):
    return amendment.write_amendment(
        run.directory,
        run.manifest,
        run.cells,
        max_total_tokens=cap,
        reason="Human approved additional headroom",
        evidence_reference="operator-cap-choice.json",
        **kwargs,
    )


def all_bytes(run):
    return {p.name: p.read_bytes() for p in run.directory.glob("*.json")}


def reload(run):
    run.manifest = json.loads((run.directory / "manifest.json").read_text())


def test_restore_only_approved_cap_then_normal_resume_retains_charge_and_gates(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    before = all_bytes(run)
    original = json.loads(json.dumps(run.manifest))
    approve(run)
    reload(run)
    expected = deepcopy(original)
    expected["budget"]["max_total_tokens"] = 2000
    expected.setdefault("budget_overrides", {})["max_total_tokens"] = 2000
    assert run.manifest == expected
    assert (run.directory / "stop").is_file()
    assert not run.backend.dispatched
    assert run.total() == (800, [])
    for key, raw in before.items():
        if len(key) == 29:
            assert (run.directory / key).read_bytes() == raw
    assert (run.directory / binding_recovery.RECORD).read_bytes() == before[binding_recovery.RECORD]
    record_bytes = (run.directory / amendment.RECORD).read_bytes()
    assert approve(run)["max_total_tokens"] == 2000
    assert (run.directory / amendment.RECORD).read_bytes() == record_bytes
    again = resume(run)
    assert again.manifest["budget"]["max_total_tokens"] == 2000
    assert again.manifest["budget"]["min_weekly_remaining_pct"] == 50
    assert again.manifest["budget"]["window"] == "22:00-07:00"
    assert again.total() == (800, [])
    assert not run.backend.dispatched
    with pytest.raises(ValueError, match="binding quarantine"):
        resume(again, ignore_window=True)
    with pytest.raises(ValueError, match="binding quarantine"):
        resume(again, max_total_tokens=3000)


def test_crash_after_approval_before_manifest_can_finish_without_replacing_evidence(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    before = all_bytes(run)
    publish = amendment._publish

    def crash(path, value, *, exclusive):
        if not exclusive:
            raise OSError("simulated manifest publication failure")
        return publish(path, value, exclusive=exclusive)

    with patch.object(amendment, "_publish", side_effect=crash):
        with pytest.raises(OSError, match="simulated"):
            approve(run)
    for key, raw in before.items():
        assert (run.directory / key).read_bytes() == raw
    approval = (run.directory / amendment.RECORD).read_bytes()
    approve(run)
    assert (run.directory / amendment.RECORD).read_bytes() == approval
    assert (run.directory / "stop").is_file()
    assert not run.backend.dispatched


@pytest.mark.parametrize("cap", [False, 0, 799, 1000, -1, "2000"])
def test_non_increase_or_invalid_cap_cannot_write(tmp_path, cap):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    before = all_bytes(run)
    with pytest.raises(ValueError, match="budget amendment"):
        approve(run, cap)
    assert all_bytes(run) == before
    assert (run.directory / "stop").is_file()


@pytest.mark.parametrize("mutation", ["foreign", "cap", "context", "anchor", "evidence"])
def test_malformed_or_foreign_approval_refuses_before_resume_writes(tmp_path, mutation):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    approve(run)
    path = run.directory / amendment.RECORD
    value = json.loads(path.read_text())
    if mutation == "foreign":
        value["run_id"] = "foreign"
    elif mutation == "cap":
        value["max_total_tokens"] = 3000
    elif mutation == "context":
        value["approved_context"]["budget"]["min_weekly_remaining_pct"] = 0
    elif mutation == "anchor":
        value["quarantine_sha256"] = "0" * 64
    else:
        value["evidence_reference"] = ""
    path.write_text(json.dumps(value))
    before = all_bytes(run)
    with pytest.raises(ValueError, match="budget amendment"):
        resume(run)
    assert all_bytes(run) == before
    assert (run.directory / "stop").is_file()


def test_unknown_acceptance_survives_budget_restoration_and_blocks_admission(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    unknown = run.cells[2]
    unknown.update(status="dispatching", reason="receipt unknown")
    run.save()
    before = (run.directory / f"{unknown['id']}.json").read_bytes()
    approve(run)
    again = resume(run)
    assert (run.directory / f"{unknown['id']}.json").read_bytes() == before
    assert again.tick() is False
    assert "dispatch acceptance unknown" in again.manifest["halt_reason"]
    assert not run.backend.dispatched
    assert again.total() == (800, [])


def test_cli_restores_cap_while_stopped_and_refuses_active_run_lock(tmp_path, monkeypatch, capsys):
    import fcntl
    from gym import dispatch

    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda _: tmp_path)
    directory = tmp_path / "gym" / "matrix"
    directory.mkdir(parents=True)
    run = stopped_aliases(directory)
    quarantine(run)
    command = [
        "amend-quarantine-budget",
        run.directory.name,
        "--max-total-tokens",
        "2000",
        "--reason",
        "operator approval",
        "--evidence-reference",
        "cap-choice.json",
    ]
    with (directory / f"{run.directory.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit) as error:
            matrix.main(command)
        assert error.value.code == 2
        assert "currently active" in capsys.readouterr().err
        assert not (run.directory / amendment.RECORD).exists()
    assert matrix.main(command) == 0
    assert json.loads(capsys.readouterr().out)["max_total_tokens"] == 2000
    reload(run)
    assert run.manifest["budget"]["max_total_tokens"] == 2000
    assert (run.directory / "stop").is_file()
    assert not run.backend.dispatched


@pytest.mark.parametrize(
    "mutation", ["stop", "active", "unmeasured", "snapshot", "manifest", "symlink"]
)
def test_approval_refuses_unsafe_or_changed_saved_evidence_without_writes(tmp_path, mutation):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    if mutation == "stop":
        (run.directory / "stop").unlink()
    elif mutation == "active":
        run.cells[2]["status"] = "in_flight"
        run.save()
    elif mutation == "unmeasured":
        run.cells[2]["status"] = "done"
        run.cells[2]["attempts"] = [{"cell": {"metrics": {"token_usage_running_total": None}}}]
        run.save()
    elif mutation == "snapshot":
        path = run.directory / f"{run.cells[0]['id']}.json"
        path.write_text(path.read_text() + " ")
    elif mutation == "manifest":
        run.manifest["ignore_window"] = True
    else:
        target = tmp_path / "foreign-approval.json"
        target.write_text("{}")
        (run.directory / amendment.RECORD).symlink_to(target)
    before = all_bytes(run)
    with pytest.raises(ValueError):
        approve(run)
    assert all_bytes(run) == before
    assert not run.backend.dispatched


def test_later_approval_cannot_replace_published_cap_or_evidence(tmp_path):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    approve(run)
    reload(run)
    before = all_bytes(run)
    with pytest.raises(ValueError, match="never replace"):
        approve(run, 3000)
    assert all_bytes(run) == before
    assert (run.directory / "stop").is_file()


@pytest.mark.parametrize("overrides", [None, {}, {"max_cells": 4}])
def test_amended_context_accepts_optional_overrides_without_mutating_original(overrides):
    original = {"budget": {"max_total_tokens": 1000}, "budget_overrides": overrides}
    before = deepcopy(original)
    approved = amendment.amended_context(original, 2000)
    assert approved["budget_overrides"] == {**(overrides or {}), "max_total_tokens": 2000}
    assert original == before
    del original["budget_overrides"]
    assert amendment.amended_context(original, 2000)["budget_overrides"] == {
        "max_total_tokens": 2000
    }
    assert "budget_overrides" not in original


@pytest.mark.parametrize("overrides", [[], "bad", False, 1])
def test_amended_context_rejects_malformed_overrides(overrides):
    with pytest.raises(ValueError, match="overrides are malformed"):
        amendment.amended_context(
            {"budget": {"max_total_tokens": 1000}, "budget_overrides": overrides}, 2000
        )


def test_omitted_manifest_overrides_can_be_approved_and_resumed(tmp_path):
    run = stopped_aliases(tmp_path)
    del run.manifest["budget_overrides"]
    run.save()
    sealed = quarantine(run)
    assert sealed["context"]["budget_overrides"] is None
    before = all_bytes(run)
    approve(run)
    reload(run)
    assert run.manifest["budget_overrides"] == {"max_total_tokens": 2000}
    for name, raw in before.items():
        if name != "manifest.json":
            assert (run.directory / name).read_bytes() == raw
    assert (run.directory / "stop").is_file()
    assert resume(run).manifest["budget"]["max_total_tokens"] == 2000
    assert run.total() == (800, [])
    assert not run.backend.dispatched


@pytest.mark.parametrize("exclusive", [True, False])
def test_publish_tolerates_unsupported_directory_sync(tmp_path, monkeypatch, exclusive):
    path = tmp_path / "publication" / "published.json"
    path.parent.mkdir()
    if not exclusive:
        path.write_text("{}")
    fsync = os.fsync
    synced = []

    def unsupported_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            synced.append("directory")
            raise OSError(errno.EINVAL, "directory sync unsupported")
        synced.append("file")
        fsync(descriptor)

    monkeypatch.setattr(amendment.os, "fsync", unsupported_directory)
    amendment._publish(path, {"approved": True}, exclusive=exclusive)
    assert json.loads(path.read_text()) == {"approved": True}
    assert synced == ["file", "directory"]
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("exclusive", [True, False])
@pytest.mark.parametrize("directory,error", [(True, errno.EIO), (False, errno.EINVAL)])
def test_publish_propagates_real_sync_errors(tmp_path, monkeypatch, exclusive, directory, error):
    path = tmp_path / "publication" / "published.json"
    path.parent.mkdir()
    if not exclusive:
        path.write_text("{}")
    fsync = os.fsync

    def failed_sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) == directory:
            raise OSError(error, "sync failed")
        fsync(descriptor)

    monkeypatch.setattr(amendment.os, "fsync", failed_sync)
    with pytest.raises(OSError) as failure:
        amendment._publish(path, {"approved": True}, exclusive=exclusive)
    assert failure.value.errno == error
    if directory:
        assert json.loads(path.read_text()) == {"approved": True}
    elif exclusive:
        assert not path.exists()
    else:
        assert path.read_text() == "{}"
    assert set(path.parent.iterdir()) <= {path}


def test_exclusive_publication_collision_preserves_winner_and_manifest(tmp_path, monkeypatch):
    run = stopped_aliases(tmp_path)
    quarantine(run)
    before = all_bytes(run)
    link = os.link
    winner = b'{"competing": "approval"}\n'

    def competing_publication(source, destination):
        destination.write_bytes(winner)
        link(source, destination)

    monkeypatch.setattr(amendment.os, "link", competing_publication)
    with pytest.raises(ValueError, match="already exists; never replace approval"):
        approve(run)
    assert (run.directory / amendment.RECORD).read_bytes() == winner
    for name, raw in before.items():
        assert (run.directory / name).read_bytes() == raw
    assert (run.directory / "stop").is_file()
    assert not run.backend.dispatched


def test_hash_file_matches_canonical_content(tmp_path):
    # PDK-02 replaced the canonical_content import with a stdlib digest.
    from canonical_content import hash_file

    for name, payload in [("empty", b""), ("small", b"x"), ("large", os.urandom(3 << 20))]:
        path = tmp_path / name
        path.write_bytes(payload)
        assert amendment.hash_file(path) == hash_file(path)
