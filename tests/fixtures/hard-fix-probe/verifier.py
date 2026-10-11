"""Fixture verifier: only the target changed and the hidden case passed."""

import gym_verifier as gv

STAGED = {"target.py", "staged-manifest.json"}

gv.check("edited_only_allowed_files", set(gv.workspace_files()) == STAGED, required=True)
outcome = gv.hidden_tests() or {}
complete = outcome.get("status") == "passed" and outcome.get("passed") == 1
gv.check("hidden_tests_pass", complete, required=True)
gv.passed()
