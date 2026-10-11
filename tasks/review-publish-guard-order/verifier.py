# Source PR/review: none; authored synthetic review fixture for RFD-04.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "publisher.py",
                "line_start": 2,
                "line_end": 2,
                "expected_category": "correctness",
            }
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["publisher.py"],
    },
)
gv.passed()
