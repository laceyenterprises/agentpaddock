"""The append-only audit trail of registry changes, kept for compliance review."""

from __future__ import annotations

import json
import time
from pathlib import Path

AUDIT_PATH = Path("registry-audit.jsonl")


def record_change(kind: str, entry, *, path: Path | None = None, clock=time.time) -> None:
    """Append one JSON line describing the change to ``entry``."""
    line = {
        "at": clock(),
        "action": kind.rpartition(".")[2],
        "name": entry.name,
        "settings": dict(entry.settings),
        "tags": sorted(entry.tags),
    }
    with open(path or AUDIT_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def read_trail(path: Path | None = None) -> list[dict]:
    target = path or AUDIT_PATH
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
