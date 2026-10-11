# Source PR: none public; derived from a private review.
# GYM-GROUND-TRUTH-NEVER-STAGE
import gym_verifier as gv
import gym_review_checks as review_checks

review_checks.record(
    gv,
    **{
        "defects": [
            {
                "file": "tool_calls.py",
                "line_start": 60,
                "line_end": 71,
                "expected_category": "security",
            },
            {
                "any_of": [
                    {"file": "tool_calls.py", "line_start": 116, "line_end": 116},
                    {"file": "tool_calls.py", "line_start": 133, "line_end": 140},
                ],
                "expected_category": "api-contract",
            },
            {
                "any_of": [
                    {"file": "server_supervisor.py", "line_start": 161, "line_end": 162},
                    {"file": "server_supervisor.py", "line_start": 80, "line_end": 81},
                ],
                "expected_category": "api-contract",
            },
        ],
        "tolerated": [
            {"file": "tool_calls.py", "line_start": 77, "line_end": 79},
            {"file": "tool_calls.py", "line_start": 29, "line_end": 34},
        ],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["server_supervisor.py", "tool_calls.py"],
        "min_recall": 0.66,
    },
)
gv.passed()
