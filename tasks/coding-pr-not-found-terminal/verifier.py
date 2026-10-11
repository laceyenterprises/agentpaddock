"""Out-of-band verifier for coding-pr-not-found-terminal."""

import json

import gym_verifier as gv

EXPECTED_LINE = 14
EXPECTED_STATUS = "skipped"
EXPECTED_RETRYABLE = False

result = gv.result()
calls = gv.toolcalls()

gv.check(
    "defect_line",
    result.get("defect_line") in {12, 14},
    "defect_line must identify the exact-match guard (12) or failed fallback (14)",
)
gv.check(
    "terminal_status",
    result.get("terminal_status") == EXPECTED_STATUS,
    "a no-PullRequest response is terminal for this reference and should be skipped",
)
gv.check(
    "retryable",
    result.get("retryable") is EXPECTED_RETRYABLE,
    "retryable must be false: retry cannot turn an issue into a pull request",
)
gv.check(
    "read_evidence",
    any("batch_activation.py" in json.dumps(call.get("arguments") or {}) for call in calls.calls),
    "agent answered without opening batch_activation.py",
)

gv.passed()
