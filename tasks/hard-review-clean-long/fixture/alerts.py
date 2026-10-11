"""Alert records shared by the notifier and the quiet-hours digest."""

from __future__ import annotations

import time
from dataclasses import dataclass

# Ordered from least to most severe.
SEVERITIES = ("info", "warning", "critical")
SEVERITY_RANK = {severity: rank for rank, severity in enumerate(SEVERITIES)}


class AlertError(ValueError):
    """An alert is malformed."""


@dataclass(frozen=True)
class Alert:
    source: str
    key: str
    severity: str
    text: str
    raised_at: float

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise AlertError(f"unknown severity {self.severity!r}")
        if not self.source or not self.key:
            raise AlertError("alerts need a source and a key")

    @property
    def critical(self) -> bool:
        return self.severity == "critical"

    def to_record(self) -> dict[str, object]:
        return {
            "source": self.source,
            "key": self.key,
            "severity": self.severity,
            "text": self.text,
            "raised_at": self.raised_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, object]) -> Alert:
        return cls(
            source=str(record["source"]),
            key=str(record["key"]),
            severity=str(record["severity"]),
            text=str(record["text"]),
            raised_at=float(record["raised_at"]),
        )


def format_alert(alert: Alert) -> str:
    stamp = time.strftime("%H:%M:%S", time.gmtime(alert.raised_at))
    return f"[{alert.severity}] {alert.source}/{alert.key} {stamp}Z: {alert.text}"
