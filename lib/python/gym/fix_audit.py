"""Bounded operator-only fix audit envelopes; plaintext never goes to disk."""

from __future__ import annotations

import base64
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess

CAP_BYTES = 256 * 1024
# Reserve space for CMS/base64 expansion, metadata and bounded case records.
PLAINTEXT_CAP = 180 * 1024
# A hard-fix task edits at most this many files (HRF-03); RFD fix tasks edit one.
MAX_EDITABLE = 4


def _openssl(args: list[str], data: bytes) -> bytes:
    from gym.grader import GraderError

    binary = shutil.which("openssl")
    if not binary:
        raise GraderError("fix audit requires OpenSSL CMS")
    try:
        result = subprocess.run([binary, "cms", *args], input=data, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GraderError("fix audit cryptography unavailable") from exc
    if result.returncode:
        # Never persist crypto diagnostics, paths, or decrypted data.
        raise GraderError("fix audit cryptography failed; check operator certificate/key")
    return result.stdout


def recipient() -> str:
    from gym.grader import GraderError

    certificate = os.environ.get("GYM_FIX_AUDIT_CERT")
    if certificate is None:
        from gym import host_services

        # The host's optional audit-encryption recipient; None means it has none.
        provided = host_services.current().audit_recipient()
        certificate = str(provided) if provided is not None else ""
    try:
        if not certificate or not Path(certificate).is_file():
            raise OSError("certificate is not a file")
        with Path(certificate).open("rb") as stream:
            if not stream.read(1):
                raise OSError("certificate is empty")
    except OSError as exc:
        raise GraderError(
            f"fix audit requires operator-provisioned GYM_FIX_AUDIT_CERT; tried {certificate!r}"
        ) from exc
    return certificate


def editable_files(task, files: dict | None) -> list[str]:
    """The trusted staged manifest's editable list, validated (HRF-03).

    A fix-family task may edit up to MAX_EDITABLE files. Each is captured and
    ships a fixture placeholder, including a regression-test file the agent
    writes, so the audit diff and the original variant never meet a gap.
    """
    from gym.grader import GraderError

    originals = files or {}

    def trusted(path: Path):
        if path in originals:
            return originals[path]
        return path.read_bytes() if path.is_file() else None

    fixture = task.task_dir / "fixture"
    try:
        manifest = json.loads(trusted(fixture / "staged-manifest.json"))
    except (TypeError, ValueError) as exc:
        raise GraderError(f"task {task.id}: staged manifest unreadable") from exc
    editable = manifest.get("editable") if isinstance(manifest, dict) else None
    if (
        not isinstance(editable, list)
        or len(editable) > MAX_EDITABLE
        or any(not isinstance(name, str) for name in editable)
        or len(set(editable)) != len(editable)
    ):
        raise GraderError(
            f"task {task.id}: staged manifest must list at most {MAX_EDITABLE} distinct editable files"
        )
    for name in editable:
        if name not in task.capture_files:
            raise GraderError("audit editable file must be declared for capture")
        if not isinstance(trusted(fixture / name), bytes):
            raise GraderError(f"task {task.id}: editable {name} needs a fixture placeholder")
    if task.regression_test_path is not None and task.regression_test_path not in editable:
        raise GraderError(f"task {task.id}: regression_test.path must be an editable file")
    return editable


def submission(task, captured: Path, files: dict | None) -> dict:
    """Use the trusted staged manifest/originals, never worker-selected paths."""
    originals = files or {}
    entries = []
    # Bounded previews explicitly disclose truncation, while keeping metadata.
    budget = 96 * 1024
    for name in editable_files(task, files):
        if budget < 1024:
            entries.append({"file": name, "truncated": True})
            continue
        path = captured / name
        if not path.exists():
            entries.append({"file": name, "missing": True})
            continue
        raw = path.read_bytes()
        original_path = task.task_dir / "fixture" / name
        original = (
            originals[original_path] if original_path in originals else original_path.read_bytes()
        )
        text = raw[:budget].decode("utf-8", errors="replace")
        diff = "".join(
            difflib.unified_diff(
                original.decode("utf-8", errors="replace").splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile="staged/" + name,
                tofile="submitted/" + name,
            )
        )
        # JSON escaping can multiply size; fit the actual serialized entry.
        entry = {"file": name, "submitted": text, "diff": diff, "truncated": len(raw) > budget}
        while len(json.dumps(entry).encode()) > budget and (entry["submitted"] or entry["diff"]):
            entry["submitted"] = entry["submitted"][: len(entry["submitted"]) // 2]
            entry["diff"] = entry["diff"][: len(entry["diff"]) // 2]
            entry["truncated"] = True
        budget -= len(json.dumps(entry).encode())
        entries.append(entry)
    return {"files": entries}


def seal(payload: dict) -> dict:
    from gym.grader import GraderError

    data = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    if len(data) > PLAINTEXT_CAP:
        # Preserve verdict metadata; disclose omitted records instead of losing the run.
        payload = dict(payload)
        payload["cases"] = []
        payload["files"] = []
        payload["truncated"] = True
        data = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    if len(data) > PLAINTEXT_CAP:
        raise GraderError("fix audit metadata exceeds cap")
    encrypted = _openssl(
        ["-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm", recipient()], data
    )
    envelope = {
        "version": 1,
        "format": "cms-der",
        "ciphertext": base64.b64encode(encrypted).decode(),
    }
    if len(json.dumps(envelope).encode()) > CAP_BYTES:
        raise GraderError("fix audit ciphertext exceeds cap")
    return envelope


def decrypt(envelope: dict, certificate: str, key: str) -> dict:
    if envelope.get("version") != 1 or envelope.get("format") != "cms-der":
        raise ValueError("unsupported audit envelope")
    if len(json.dumps(envelope).encode()) > CAP_BYTES:
        raise ValueError("oversize audit envelope")
    encrypted = base64.b64decode(envelope["ciphertext"], validate=True)
    return json.loads(
        _openssl(
            ["-decrypt", "-binary", "-inform", "DER", "-recip", certificate, "-inkey", key],
            encrypted,
        )
    )
