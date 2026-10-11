# Source PR/review: synthetic; authored hard-review clean control for HRF-04. No defects; decoys only (see expected/PROVENANCE.md).
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 0,
        "clean_control": True,
        "read_files": ["alerts.py", "digest.py", "notify.py", "notifyctl.py", "quiet_hours.py"],
        "decoys": [
            {"file": "notify.py", "line_start": 83, "line_end": 86},
            {"file": "notify.py", "line_start": 79, "line_end": 82},
            {"file": "notify.py", "line_start": 27, "line_end": 30},
            {"file": "quiet_hours.py", "line_start": 56, "line_end": 58},
            {"file": "digest.py", "line_start": 43, "line_end": 43},
            {"file": "digest.py", "line_start": 67, "line_end": 70},
            {"file": "digest.py", "line_start": 94, "line_end": 97},
        ],
        "max_decoy_blocking": 0,
    },
)
gv.passed()
