"""Hermetic diagnosis verifier; constants are pinned to staged replay by corpus tests.

No agent code is executed. Each counterexample is required, so naming a plausible
patch without understanding its behavior cannot pass.
"""

import gym_verifier as gv

EXPECTED = {
    "fix": "tenant-nfc-revision",
    "original": "17,17,17,17",
    "tenant_only": "17,29,17,17",
    "fixed_third": 41,
    "fixed_entries": 3,
}

result = gv.result()
for name, expected in EXPECTED.items():
    gv.check(name, result.get(name) == expected, f"{name}: incorrect diagnosis or replay")
gv.passed()
