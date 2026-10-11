"""Out-of-band verifier for coding-unknown-change-kind-sentinel."""

import json

import gym_verifier as gv

EXPECTED_LINE = 3
EXPECTED_HANDLING = "fall-through"

result = gv.result()
calls = gv.toolcalls()

# Line 5 correctly rejects invalid explicit values; bypass the sentinel before normalization.
gv.check(
    "defect_line",
    result.get("defect_line") in {2, 3},
    "defect_line must identify the explicit guard (2) or normalization call (3)",
)
gv.check(
    "sentinel_handling",
    result.get("sentinel_handling") == EXPECTED_HANDLING,
    "the terminal value should fall through to inference, not return as a declared kind",
)
gv.check(
    "invalid_explicit_still_errors",
    result.get("invalid_explicit_still_errors") is True,
    "truly invalid explicit values must still error",
)
gv.check(
    "read_evidence",
    any("change_kind.py" in json.dumps(call.get("arguments") or {}) for call in calls.calls),
    "agent answered without opening change_kind.py",
)

gv.passed()
