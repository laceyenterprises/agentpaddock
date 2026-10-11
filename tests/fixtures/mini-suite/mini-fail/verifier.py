"""Fixture verifier: pass iff the submitted answer is "ok"."""

import gym_verifier as gv

result = gv.result()
if result.get("answer") == "ok":
    gv.passed()
gv.fail("answer was not ok")
