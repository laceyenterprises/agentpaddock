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
                    {"file": "dispatcher.py", "line_start": 121, "line_end": 124},
                    {"file": "sender.py", "line_start": 20, "line_end": 20},
                ],
                "expected_category": "error-handling",
            },
            {
                "file": "store.py",
                "line_start": 90,
                "line_end": 104,
                "expected_category": "concurrency",
            },
            {
                "file": "store.py",
                "line_start": 148,
                "line_end": 154,
                "expected_category": "correctness",
            },
            {
                "file": "store.py",
                "line_start": 208,
                "line_end": 214,
                "expected_category": "data-loss",
            },
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["backoff.py", "cli.py", "db.py", "dispatcher.py", "sender.py", "store.py"],
        "min_recall": 1.0,
        "decoys": [
            {"file": "dispatcher.py", "line_start": 145, "line_end": 147},
            {"file": "dispatcher.py", "line_start": 148, "line_end": 148},
            {"file": "dispatcher.py", "line_start": 108, "line_end": 112},
            {"file": "store.py", "line_start": 175, "line_end": 185},
            {"file": "store.py", "line_start": 188, "line_end": 193},
        ],
        "max_decoy_blocking": 0,
    },
)
gv.passed()
