# Source PR/review: synthetic; authored hard-review fixture for HRF-04. Decoys port disputed findings from PR 7695, PR 7704, PR 7674, PR 7864, PR 7546 and PR 7615 (see expected/PROVENANCE.md).
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "replies.py",
                "line_start": 71,
                "line_end": 75,
                "expected_category": "concurrency",
            },
            {
                "file": "reconcile.py",
                "line_start": 62,
                "line_end": 63,
                "expected_category": "correctness",
            },
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["decisions.py", "reconcile.py", "replies.py"],
        "decoys": [
            {"file": "reconcile.py", "line_start": 28, "line_end": 30},
            {"file": "replies.py", "line_start": 100, "line_end": 105},
            {"file": "replies.py", "line_start": 123, "line_end": 125},
            {"file": "decisions.py", "line_start": 59, "line_end": 60},
            {"file": "decisions.py", "line_start": 12, "line_end": 13},
            {"file": "decisions.py", "line_start": 120, "line_end": 125},
            {"file": "decisions.py", "line_start": 166, "line_end": 182},
        ],
        "max_decoy_blocking": 0,
    },
)
gv.passed()
