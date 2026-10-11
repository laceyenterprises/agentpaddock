"""Quarantine completed launch aliases without rewriting or refunding attempts.

This record resolves report eligibility, not which aliased worker owned a cell.
Every implicated result is excluded. Its launch remains reserved and every
saved attempt remains charged by MatrixRun.total(). Callers hold the run lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

RECORD = "binding-recovery.json"
SCHEMA = "meg-binding-quarantine/v1"
CONTEXT = (
    "schema",
    "base_digest",
    "seed",
    "battery_id",
    "base_cells",
    "selected_cells",
    "repetitions",
    "budget",
    "order",
    "pending",
    "ignore_window",
    "ignore_quota_floor",
    "budget_overrides",
    "trim_digest",
    "measured_replacements",
)


def context(manifest):
    # Normalize tuples used by a newly prepared run to their JSON representation.
    return json.loads(json.dumps({key: manifest.get(key) for key in CONTEXT}))


def aliases(cells):
    owners = defaultdict(set)
    for cell in cells:
        launches = {cell.get("launch_request_id")} | {
            attempt.get("launch_request_id") for attempt in cell["attempts"]
        }
        for launch in launches - {None, ""}:
            owners[launch].add(cell["id"])
    return {launch: sorted(ids) for launch, ids in sorted(owners.items()) if len(ids) > 1}


def tokens(cells):
    spent = 0
    for cell in cells:
        for attempt in cell["attempts"]:
            total = (attempt.get("cell", {}).get("metrics") or {}).get("token_usage_running_total")
            if type(total) is not int or total < 0:
                raise ValueError("binding quarantine requires measured saved attempt tokens")
            spent += total
    return spent


def snapshot_path(directory, cell_id):
    path = directory / f"{cell_id}.json"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("binding quarantine snapshot must be a local regular file")
    return path


def snapshot_hash(directory, cell_id):
    return hashlib.sha256(snapshot_path(directory, cell_id).read_bytes()).hexdigest()


def preserve_snapshot(directory, cell, record):
    """Reject even an in-memory mutation; never reserialize a sealed snapshot."""
    expected = record["snapshot_hashes"].get(cell["id"])
    if expected is None:
        return False
    path = snapshot_path(directory, cell["id"])
    if snapshot_hash(directory, cell["id"]) != expected or json.loads(path.read_text()) != cell:
        raise ValueError("binding quarantine saved snapshot changed")
    return True


def load_record(directory, manifest, cells):
    from gym.budget_amendment import matches_context

    path = directory / RECORD
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("binding quarantine record cannot be a symlink")
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise ValueError("binding quarantine record must be an object")
    if (
        record.get("schema") != SCHEMA
        or record.get("run_id") != directory.name
        or type(record.get("recorded_tokens")) is not int
        or not isinstance(record.get("context"), dict)
        or not matches_context(directory, record, context(manifest))
        or not isinstance(record.get("reason"), str)
        or not record["reason"].strip()
        or not isinstance(record.get("evidence_reference"), str)
        or not record["evidence_reference"].strip()
    ):
        raise ValueError("binding quarantine context or evidence differs")
    groups = aliases(cells)
    if not groups or record.get("aliases") != groups:
        raise ValueError("binding quarantine launch owners differ")
    hashes = record.get("snapshot_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("binding quarantine snapshot evidence missing")
    by_id = {cell["id"]: cell for cell in cells}
    implicated = {key for ids in groups.values() for key in ids}
    if not implicated <= hashes.keys() or not hashes.keys() <= by_id.keys():
        raise ValueError("binding quarantine does not cover all launch owners")
    for cell_id in hashes:
        cell = by_id[cell_id]
        if cell["status"] != "done" or not cell["attempts"]:
            raise ValueError("binding quarantine only covers completed saved attempts")
        preserve_snapshot(directory, cell, record)
    floor = record.get("recorded_tokens")
    sealed = [by_id[key] for key in hashes]
    if type(floor) is not int or tokens(sealed) != floor:
        raise ValueError("binding quarantine recorded spending differs")
    return record


def write_record(directory: Path, manifest, cells, *, reason, evidence_reference):
    """Create separate evidence while stopped; never clear stop or launch work."""
    if directory.stat().st_uid != os.getuid():
        raise ValueError("binding quarantine requires the run directory owner")
    if not (directory / "stop").is_file():
        raise ValueError("binding quarantine requires a stopped run")
    if any(cell["status"] in {"dispatching", "in_flight"} for cell in cells):
        raise ValueError("binding quarantine requires all admissions to drain")
    if (directory / RECORD).exists():
        existing = load_record(directory, manifest, cells)
        if existing["reason"] != reason or existing["evidence_reference"] != evidence_reference:
            raise ValueError("binding quarantine evidence already exists; never replace it")
        return existing
    groups = aliases(cells)
    if not groups:
        raise ValueError("binding quarantine requires existing cross-cell aliases")
    sealed = [cell for cell in cells if cell["attempts"]]
    if any(cell["status"] != "done" for cell in sealed):
        raise ValueError("binding quarantine requires completed saved attempts")
    implicated = {key for ids in groups.values() for key in ids}
    if not implicated <= {cell["id"] for cell in sealed}:
        raise ValueError("binding quarantine requires attempts for every launch owner")
    spent = tokens(cells)
    if type(manifest.get("tokens_spent")) is not int or manifest["tokens_spent"] != spent:
        raise ValueError("binding quarantine manifest spending differs")
    if not reason.strip() or not evidence_reference.strip():
        raise ValueError("binding quarantine requires reason and evidence reference")
    record = dict(
        schema=SCHEMA,
        run_id=directory.name,
        context=context(manifest),
        aliases=groups,
        snapshot_hashes={c["id"]: snapshot_hash(directory, c["id"]) for c in sealed},
        recorded_tokens=spent,
        reason=reason,
        evidence_reference=evidence_reference,
    )
    for cell in sealed:
        preserve_snapshot(directory, cell, record)
    # Link a fully flushed temporary file exclusively; a crash cannot publish
    # truncated evidence or overwrite an existing operator record.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=directory, prefix=".binding-quarantine-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, directory / RECORD)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return load_record(directory, manifest, cells)


def quarantined_ids(record):
    return {key for ids in record["aliases"].values() for key in ids} if record else set()
