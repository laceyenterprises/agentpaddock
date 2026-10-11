"""Hermetic diagnosis verifier; constants are pinned to staged replay by corpus tests.

No agent code is executed. Each counterexample is required, so naming a plausible
patch without understanding its behavior cannot pass.
"""

import gym_verifier as gv

EXPECTED = {
    "profile": "tail",
    "template_limit": 34,
    "fix": "copy-tags-list",
    "original_first_tags": 2,
    "fixed_template_tags": 0,
}

result = gv.result()
for name, expected in EXPECTED.items():
    gv.check(name, result.get(name) == expected, f"{name}: incorrect diagnosis or replay")
gv.passed()
