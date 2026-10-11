"""GYM eval outcome vocabulary.

Single source of truth for the per-cell outcome set. One task run resolves to
exactly one of these (SPEC §10). Kept tiny and closed so the regression gate
(GYM-04) can diff verdicts without free-text ambiguity.
"""

from __future__ import annotations

# The terminal outcomes a graded cell can resolve to.
PASSED = "passed"
FAILED = "failed"
FORMAT_FAILED = "format_failed"
NO_SUBMISSION = "no_submission"
AGENT_ERROR = "agent_error"
# Harness failures are distinct from an agent failing to complete.
ERROR = "error"
INTEGRITY_HOLD = "integrity_hold"

OUTCOMES: frozenset[str] = frozenset(
    {
        PASSED,
        FAILED,
        FORMAT_FAILED,
        NO_SUBMISSION,
        AGENT_ERROR,
        ERROR,
        INTEGRITY_HOLD,
    }
)

# Outcomes that count as a "pass" for baseline/regression comparison. Only a
# clean verifier pass is a pass; everything else (including format_failed and
# agent_error) is a non-pass so the gate never treats a broken run as green.
PASSING: frozenset[str] = frozenset({PASSED})


def is_outcome(value: str) -> bool:
    return value in OUTCOMES


def is_passing(outcome: str) -> bool:
    return outcome in PASSING


__all__ = [
    "PASSED",
    "FAILED",
    "FORMAT_FAILED",
    "NO_SUBMISSION",
    "AGENT_ERROR",
    "ERROR",
    "INTEGRITY_HOLD",
    "OUTCOMES",
    "PASSING",
    "is_outcome",
    "is_passing",
]
