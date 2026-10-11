"""The ``gym_regression`` alert seam.

A regression (baseline-pass -> HEAD-fail) is the one thing the scheduled gym
pages an operator about. This module is the thin, injectable boundary between
the regression report and the shared alert-delivery surface:

  * :class:`RecordingAlerter` — captures emitted alerts in memory. Tests assert
    against it; **no test ever touches a live channel** (SPEC §7, ticket Don't).
  * :class:`FileAlerter` — the standalone path: one JSON line per alert in
    the data directory.
  * the host's alerter (:meth:`gym.host_services.HostServices.alerter`) — the
    production path. Under Agent OS it is
    ``gym.agent_os_services.SentinelAlerter``, which defers to
    ``sentinel.notifications.enqueue_alert`` (the same shared alert path every
    other Agent OS producer rides), stamping ``alert_key=gym_regression`` into
    the metadata so alert-delivery routing keys on it.

The alert key is a committed constant so the regression path and the
alert-delivery wiring agree on exactly one string (SPEC §10).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from gym.baseline import RegressionReport

# The single operator alert key this pack emits (SPEC §10). Alert-delivery (ADW)
# routes on it; the recorder/verifier tests assert on it.
GYM_REGRESSION_ALERT_KEY = "gym_regression"
GYM_LEVEL_UNRESOLVED_ALERT_KEY = "gym_level_unresolved"

# Stable source identity so the alert-delivery ledger attributes gym pages.
ALERT_SOURCE = "gym-scheduled"


class Alerter(Protocol):
    """The seam a regression emitter writes to."""

    def emit(self, *, key: str, message: str, metadata: dict[str, Any]) -> None:  # pragma: no cover
        ...


class RecordingAlerter:
    """In-memory alerter. The only alerter any test is allowed to use."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    def emit(self, *, key: str, message: str, metadata: dict[str, Any]) -> None:
        self.alerts.append({"key": key, "message": message, "metadata": dict(metadata)})


class FileAlerter:
    """Append each alert as one JSON line to a local file (standalone)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def emit(self, *, key: str, message: str, metadata: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "message": message,
            "metadata": dict(metadata),
            "source": ALERT_SOURCE,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def regression_message(regression: Any, *, baseline_ref: str, baseline_date: str | None) -> str:
    """The operator-facing line, mirroring the SPEC §1 regression mockup."""
    dated = f" ({baseline_date})" if baseline_date else ""
    return (
        f"REGRESSION: {regression.task} "
        f"{regression.baseline_verdict}@baseline -> {regression.head_verdict}@HEAD  "
        f"(baseline: {baseline_ref}{dated})  ->  alert: {GYM_REGRESSION_ALERT_KEY}"
    )


def emit_level_unresolved(alerter: Alerter, *, suite: str, count: int) -> None:
    """Signal that leveled cells were excluded because their level is unknown."""
    if count <= 0:
        return
    alerter.emit(
        key=GYM_LEVEL_UNRESOLVED_ALERT_KEY,
        message=f"Gym suite {suite}: {count} dispatched cell(s) have an unresolved reasoning level",
        metadata={
            "suite": suite,
            "count": count,
            "idempotency_key": f"{GYM_LEVEL_UNRESOLVED_ALERT_KEY}:{suite}",
        },
    )


def emit_regressions(
    report: RegressionReport,
    alerter: Alerter,
    *,
    suite: str,
    baseline_ref: str,
    baseline_date: str | None = None,
) -> list[dict[str, Any]]:
    """Emit one ``gym_regression`` alert per regression; return what was sent.

    A newly-passing task is intentionally NOT emitted here — the caller logs it.
    The idempotency key is stable per (task, baseline date) so a re-run over the
    same baseline does not re-page.
    """
    emitted: list[dict[str, Any]] = []
    for regression in report.regressions:
        message = regression_message(
            regression, baseline_ref=baseline_ref, baseline_date=baseline_date
        )
        metadata = {
            "task": regression.task,
            "suite": suite,
            "baseline_verdict": regression.baseline_verdict,
            "head_verdict": regression.head_verdict,
            "baseline_ref": baseline_ref,
            "baseline_date": baseline_date,
            "idempotency_key": f"{GYM_REGRESSION_ALERT_KEY}:{regression.task}:{baseline_date}",
        }
        alerter.emit(key=GYM_REGRESSION_ALERT_KEY, message=message, metadata=metadata)
        emitted.append({"key": GYM_REGRESSION_ALERT_KEY, "message": message, "metadata": metadata})
    return emitted


__all__ = [
    "GYM_REGRESSION_ALERT_KEY",
    "GYM_LEVEL_UNRESOLVED_ALERT_KEY",
    "Alerter",
    "ALERT_SOURCE",
    "FileAlerter",
    "RecordingAlerter",
    "regression_message",
    "emit_regressions",
    "emit_level_unresolved",
]
