# Source PR/review: none; authored synthetic review fixture for RFD-04.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": True,
        "read_files": ["retention.py"],
    },
)
gv.passed()
