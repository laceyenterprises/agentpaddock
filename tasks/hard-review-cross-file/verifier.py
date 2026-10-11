# Source PR/review: synthetic; authored hard-review fixture for HRF-04.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "any_of": [
                    {"file": "registry.py", "line_start": 47, "line_end": 49},
                    {"file": "sync.py", "line_start": 26, "line_end": 32},
                ],
                "expected_category": "api-contract",
            }
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["registry.py", "sync.py"],
    },
)
gv.passed()
