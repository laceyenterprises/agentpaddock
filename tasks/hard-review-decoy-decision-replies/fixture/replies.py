"""Answer decisions and dispatch each answer to its worker exactly once."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from decisions import (
    ANSWERED_STATUS,
    OPEN_STATUS,
    Decision,
    DecisionError,
    DecisionStore,
)

# dispatch(worker, answer) delivers the answer and returns a delivery receipt.
Dispatch = Callable[[str, str], str]


def lock_path(root: Path, decision_id: str) -> Path:
    return root / "decisions" / f"{decision_id}.reply.lock"


def record_path(root: Path, decision_id: str) -> Path:
    return root / "decisions" / f"{decision_id}.answer.json"


def diagnostic_path(root: Path) -> Path:
    return root / "decisions" / "reply-diagnostics.log"


@contextlib.contextmanager
def locked(root: Path, decision_id: str) -> Iterator[None]:
    """Hold the decision's reply lock (an exclusive flock on its .reply.lock file).

    flock locks belong to the open file, so a second ``locked`` on the same
    decision blocks, even in the same process.
    """
    path = lock_path(root, decision_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def reply(
    store: DecisionStore,
    root: Path,
    decision_id: str,
    answer: str,
    dispatch: Dispatch,
    *,
    now: float | None = None,
    _lock_held: bool = False,
) -> dict[str, object]:
    """Answer ``decision_id`` and dispatch the answer; return the answer record.

    Replying again to a dispatched decision returns its existing record.
    ``_lock_held`` is for callers that already hold ``locked(root, decision_id)``.
    """
    now = time.time() if now is None else now
    decision = store.get(decision_id)
    if decision is None:
        raise DecisionError(f"unknown decision {decision_id!r}")
    if store.dispatched(decision_id) is not None:
        return load_answer_record(root, decision_id)
    guard = contextlib.nullcontext() if _lock_held else locked(root, decision_id)
    with guard:
        if decision.status == OPEN_STATUS:
            store.record_answer(decision_id, answer, now=now)
        elif decision.status == ANSWERED_STATUS:
            # Replaying an undispatched answer sends the recorded text.
            answer = decision.answer
        else:
            raise DecisionError(f"decision {decision_id!r} is {decision.status}")
        receipt = dispatch(decision.worker, answer)
        store.record_dispatch(decision_id, receipt, now=now)
        _log_diagnostic(root, f"dispatched {decision_id} receipt={receipt}")
        return _write_answer_record(root, store.get(decision_id), receipt, now=now)


def load_answer_record(root: Path, decision_id: str) -> dict[str, object]:
    return json.loads(record_path(root, decision_id).read_text())


def _write_answer_record(
    root: Path, decision: Decision, receipt: str, *, now: float
) -> dict[str, object]:
    """Atomically write the answer record, keeping the first one on a retry."""
    path = record_path(root, decision.id)
    existing = json.loads(path.read_text()) if path.exists() else None
    record = existing or {
        "decision": json.loads(decision.to_json()),
        "receipt": receipt,
        "recorded_at": now,
    }
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".answer-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise
    return record


def _log_diagnostic(root: Path, line: str) -> None:
    """Append one line to the operator-only diagnostics log."""
    path = diagnostic_path(root)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")
