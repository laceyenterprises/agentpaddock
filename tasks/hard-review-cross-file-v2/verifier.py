# Source PR/review: synthetic; authored hard-review fixture for HRX-02.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "any_of": [
                    {"file": "registry.py", "line_start": 58, "line_end": 65},
                    {"file": "audit.py", "line_start": 12, "line_end": 20},
                ],
                "expected_category": "api-contract",
            }
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["audit.py", "events.py", "registry.py", "webhooks.py", "wiring.py"],
    },
)
gv.passed()
