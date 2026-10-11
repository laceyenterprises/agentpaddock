# Source PR/review: synthetic; authored hard-review fixture for HRF-04.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "dispatcher.py",
                "line_start": 24,
                "line_end": 24,
                "expected_category": "correctness",
            },
            {
                "any_of": [
                    {"file": "dispatcher.py", "line_start": 54, "line_end": 63},
                    {"file": "sender.py", "line_start": 12, "line_end": 12},
                ],
                "expected_category": "error-handling",
            },
            {
                "file": "store.py",
                "line_start": 48,
                "line_end": 58,
                "expected_category": "concurrency",
            },
            {
                "file": "store.py",
                "line_start": 74,
                "line_end": 76,
                "expected_category": "data-loss",
            },
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["dispatcher.py", "sender.py", "store.py"],
        "min_recall": 0.75,
    },
)
gv.passed()
