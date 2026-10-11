"""Hermetic diagnosis verifier; constants are pinned to staged replay by corpus tests.

No agent code is executed. Each counterexample is required, so naming a plausible
patch without understanding its behavior cannot pass.
"""

import gym_verifier as gv

EXPECTED = {
    "fix": "atomic-receipt-and-apply",
    "original_balance": 39,
    "reorder_balance": 39,
    "fixed_balance": 13,
    "fixed_receipts": 1,
}

result = gv.result()
for name, expected in EXPECTED.items():
    gv.check(name, result.get(name) == expected, f"{name}: incorrect diagnosis or replay")
gv.passed()
