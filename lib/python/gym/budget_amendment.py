"""An owner-approved token-cap increase keeps binding evidence and STOP intact.

Callers hold the existing matrix run lock. Publication precedes the manifest
update so a crash can leave an unused approval, never an unapproved budget.
Only one amendment is supported; further changes require a new reviewed path.
"""

from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile

RECORD = "binding-budget-amendment.json"
SCHEMA = "meg-binding-budget-amendment/v1"


def hash_file(path):
    """Full SHA-256 hex of a file's bytes (the same digest as ``canonical_content.hash_file``)."""
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def amended_context(original, cap):
    try:
        previous = original["budget"]["max_total_tokens"]
    except (KeyError, TypeError):
        raise ValueError("budget amendment original context is malformed") from None
    if type(cap) is not int or type(previous) is not int or cap <= previous:
        raise ValueError("budget amendment requires a strictly increased integer token cap")
    overrides = original.get("budget_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("budget amendment original overrides are malformed")
    value = deepcopy(original)
    value["budget"]["max_total_tokens"] = cap
    # Quarantine context serializes an omitted manifest field as null.
    if overrides is None:
        value["budget_overrides"] = {}
    value["budget_overrides"]["max_total_tokens"] = cap
    return value


def matches_context(directory, quarantine, current):
    """Validate approval even when the manifest still has the original cap."""
    from gym import binding_recovery

    path = directory / RECORD
    if not path.exists() and not path.is_symlink():
        return current == quarantine["context"]
    if path.is_symlink() or not path.is_file():
        raise ValueError("budget amendment must be a local regular file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("budget amendment must be an object")
    approved = amended_context(quarantine["context"], value.get("max_total_tokens"))
    if (
        value.get("schema") != SCHEMA
        or value.get("run_id") != directory.name
        or value.get("quarantine_sha256") != hash_file(directory / binding_recovery.RECORD)
        or value.get("original_context") != quarantine["context"]
        or value.get("approved_context") != approved
        or value.get("recorded_tokens") != quarantine["recorded_tokens"]
        or not isinstance(value.get("reason"), str)
        or not value["reason"].strip()
        or not isinstance(value.get("evidence_reference"), str)
        or not value["evidence_reference"].strip()
    ):
        raise ValueError("budget amendment context or evidence differs")
    return current in (quarantine["context"], approved)


def _publish(path: Path, value, *, exclusive):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            if not exclusive:
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        except OSError as error:
            # Some filesystems (including macOS) cannot sync directories.
            # File sync above is mandatory; actual I/O failures still fail.
            if error.errno != errno.EINVAL:
                raise
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_amendment(directory, manifest, cells, *, max_total_tokens, reason, evidence_reference):
    """Restore budget while stopped, without rewriting cells or resuming work."""
    from gym import binding_recovery

    manifest_path = directory / "manifest.json"
    stop = directory / "stop"
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError("budget amendment requires the run directory owner")
    if stop.is_symlink() or not stop.is_file():
        raise ValueError("budget amendment requires a stopped run")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("budget amendment requires a regular manifest")
    saved = json.loads(manifest_path.read_text())
    if saved != json.loads(json.dumps(manifest)):
        raise ValueError("budget amendment requires the exact saved manifest")
    if any(cell["status"] == "in_flight" for cell in cells):
        raise ValueError("budget amendment requires accepted work to drain")
    quarantine = binding_recovery.load_record(directory, manifest, cells)
    if quarantine is None:
        raise ValueError("budget amendment requires original binding quarantine evidence")
    spent = binding_recovery.tokens(cells)
    if type(manifest.get("tokens_spent")) is not int or manifest["tokens_spent"] != spent:
        raise ValueError("budget amendment requires exact measured cumulative spending")
    approved = amended_context(quarantine["context"], max_total_tokens)
    if (
        max_total_tokens <= spent
        or not isinstance(reason, str)
        or not reason.strip()
        or not isinstance(evidence_reference, str)
        or not evidence_reference.strip()
    ):
        raise ValueError("budget amendment requires headroom, reason and approval evidence")
    value = dict(
        schema=SCHEMA,
        run_id=directory.name,
        quarantine_sha256=hash_file(directory / binding_recovery.RECORD),
        original_context=quarantine["context"],
        approved_context=approved,
        max_total_tokens=max_total_tokens,
        recorded_tokens=quarantine["recorded_tokens"],
        reason=reason,
        evidence_reference=evidence_reference,
    )
    path = directory / RECORD
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError("budget amendment already exists; never replace approval")
    else:
        try:
            _publish(path, value, exclusive=True)
        except FileExistsError:
            raise ValueError("budget amendment already exists; never replace approval") from None
    updated = deepcopy(saved)
    updated["budget"] = approved["budget"]
    updated["budget_overrides"] = approved["budget_overrides"]
    binding_recovery.load_record(directory, updated, cells)
    # Cells, spending, stop state, grades and all other manifest fields survive.
    _publish(manifest_path, updated, exclusive=False)
    return value
