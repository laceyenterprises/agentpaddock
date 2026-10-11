"""Hermetic diagnosis verifier; constants are pinned to staged replay by corpus tests.

No agent code is executed. Each counterexample is required, so naming a plausible
patch without understanding its behavior cannot pass.
"""

import gym_verifier as gv

EXPECTED = {
    "fix": "generation-equals-current",
    "original_wrong_schedules": 4,
    "original_trace": "null,null,20",
    "fixed_trace": "30,30,30",
    "fixed_wrong_schedules": 0,
}

result = gv.result()
for name, expected in EXPECTED.items():
    gv.check(name, result.get(name) == expected, f"{name}: incorrect diagnosis or replay")
gv.passed()
