# Source PR: none public; derived from a private review.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "provision.sh",
                "line_start": 57,
                "line_end": 61,
                "expected_category": "data-loss",
            }
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["provision.sh"],
    },
)
gv.passed()
