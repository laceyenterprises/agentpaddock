# Source PR: none public; derived from a private review.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "file-lock.mjs",
                "line_start": 29,
                "line_end": 29,
                "expected_category": "concurrency",
            }
        ],
        "tolerated": [{"file": "file-lock.mjs", "line_start": 23, "line_end": 27}],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["file-lock.mjs"],
    },
)
gv.passed()
