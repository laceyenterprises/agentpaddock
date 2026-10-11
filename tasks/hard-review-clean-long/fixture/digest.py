"""The quiet-hours digest: alerts held back for one summary message."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from alerts import SEVERITIES, SEVERITY_RANK, Alert

# The digest lists at most this many alerts; the rest are counted.
MAX_LISTED = 20


class Digest:
    """Alerts held during quiet hours, persisted so a restart does not lose them.

    The state file is replaced atomically on every change. ``tempfile.mkstemp``
    creates the replacement 0600 and ``os.replace`` keeps that mode, so the
    file stays operator-only.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._pending: list[Alert] = self._load()

    def __len__(self) -> int:
        return len(self._pending)

    def _load(self) -> list[Alert]:
        try:
            state = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        return [Alert.from_record(record) for record in state["pending"]]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".digest-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(
                    {"pending": [alert.to_record() for alert in self._pending]},
                    handle,
                    indent=2,
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)
            raise

    def add(self, alert: Alert) -> None:
        self._pending.append(alert)
        self._save()

    def render(self) -> str:
        """The digest text: counts by severity, then alerts most severe first, oldest first."""
        counts = Counter(alert.severity for alert in self._pending)
        ordered = sorted(
            self._pending,
            key=lambda alert: (-SEVERITY_RANK[alert.severity], alert.raised_at),
        )
        lines = [f"Quiet-hours digest: {len(ordered)} alerts held"]
        lines.append(
            ", ".join(
                f"{counts[severity]} {severity}"
                for severity in reversed(SEVERITIES)
                if counts[severity]
            )
        )
        for alert in ordered[:MAX_LISTED]:
            lines.append(f"- [{alert.severity}] {alert.source}/{alert.key}: {alert.text}")
        hidden = len(ordered) - MAX_LISTED
        if hidden > 0:
            lines.append(f"... and {hidden} more")
        return "\n".join(lines)

    def flush(self, send: Callable[[str], None]) -> int:
        """Send the digest, then clear it; return how many alerts it held.

        The pending list is cleared only after ``send`` returns, so a failed
        send keeps every held alert for the next attempt.
        """
        if not self._pending:
            return 0
        send(self.render())
        flushed = len(self._pending)
        self._pending = []
        self._save()
        return flushed
