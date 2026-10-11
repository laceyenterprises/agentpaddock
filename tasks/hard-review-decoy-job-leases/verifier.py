# Source PR/review: synthetic; authored hard-review fixture for HRF-04. Decoys port disputed findings from PR 7516, PR 7522, PR 7663 and PR 7644 (see expected/PROVENANCE.md).
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "jobs.py",
                "line_start": 111,
                "line_end": 114,
                "expected_category": "concurrency",
            },
            {
                "file": "sweeper.py",
                "line_start": 33,
                "line_end": 33,
                "expected_category": "correctness",
            },
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["db.py", "jobs.py", "sweeper.py"],
        "decoys": [
            {"file": "jobs.py", "line_start": 150, "line_end": 157},
            {"file": "jobs.py", "line_start": 185, "line_end": 186},
            {"file": "jobs.py", "line_start": 91, "line_end": 93},
            {"file": "sweeper.py", "line_start": 38, "line_end": 40},
            {"file": "sweeper.py", "line_start": 43, "line_end": 45},
            {"file": "sweeper.py", "line_start": 50, "line_end": 55},
            {"file": "db.py", "line_start": 68, "line_end": 70},
        ],
        "max_decoy_blocking": 0,
    },
)
gv.passed()
