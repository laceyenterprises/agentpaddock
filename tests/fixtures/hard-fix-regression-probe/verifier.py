"""Fixture verifier: only editable files changed and the hidden cases passed.

The grader appends the four required regression_test_* subchecks itself.
"""

import json

import gym_verifier as gv

MANIFEST = (
    "{\n"
    '  "editable": [\n'
    '    "target.py",\n'
    '    "rules.py",\n'
    '    "test_regression.py"\n'
    "  ],\n"
    '  "files": [\n'
    '    "target.py",\n'
    '    "rules.py",\n'
    '    "test_regression.py"\n'
    "  ]\n"
    "}\n"
)
STAGED = {*json.loads(MANIFEST)["files"], "staged-manifest.json"}

allowed = set(gv.workspace_files()) == STAGED and (
    gv.workspace_text("staged-manifest.json") == MANIFEST
)
gv.check("edited_only_allowed_files", allowed, required=True)
outcome = gv.hidden_tests() or {}
complete = outcome.get("status") == "passed" and outcome.get("passed") == 3
gv.check("hidden_tests_pass", complete, required=True)
gv.passed()
