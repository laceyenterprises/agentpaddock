"""Adversarial verifier that tries to break isolation.

It attempts to reach the ground truth in expected/ (by several paths) and to
import the session ledger backend. If ANY attempt succeeds it emits a passing
verdict tagged LEAK; if all attempts fail (the correct, isolated case) it emits
a failing verdict tagged ISOLATED. The isolation test asserts the ISOLATED
outcome — proving the ephemeral env exposed nothing but BENCH_RESULT +
BENCH_TOOLCALLS.
"""

import os

import gym_verifier as gv

candidate_paths = [
    "expected/ground_truth.json",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "expected", "ground_truth.json"),
    "../expected/ground_truth.json",
    os.path.join(os.getcwd(), "expected", "ground_truth.json"),
]

for path in candidate_paths:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            handle.read()
        gv.fail(f"LEAK: verifier read ground truth at {path}")
    except OSError:
        continue

# The backend must not be importable from the scrubbed env either.
try:
    import session_ledger  # noqa: F401

    gv.fail("LEAK: verifier imported session_ledger backend")
except Exception:
    pass

gv.fail("ISOLATED: expected/ and backend both unreachable")
