# Source PR: none public; derived from a private review.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "progress_journal.py",
                "line_start": 12,
                "line_end": 16,
                "expected_category": "error-handling",
            }
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["progress_journal.py"],
    },
)
gv.passed()
