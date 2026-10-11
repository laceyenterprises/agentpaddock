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
                    {"file": "tool_calls.py", "line_start": 154, "line_end": 158},
                    {"file": "tool_calls.py", "line_start": 174, "line_end": 174},
                    {"file": "tool_calls.py", "line_start": 199, "line_end": 199},
                ],
                "expected_category": "security",
            },
            {
                "any_of": [
                    {"file": "tool_calls.py", "line_start": 68, "line_end": 70},
                    {"file": "tool_calls.py", "line_start": 189, "line_end": 190},
                    {"file": "tool_calls.py", "line_start": 200, "line_end": 201},
                ],
                "expected_category": "api-contract",
            },
            {
                "file": "bridge.py",
                "line_start": 158,
                "line_end": 159,
                "expected_category": "api-contract",
            },
        ],
        "tolerated": [],
        "slack": 3,
        "max_false_blocking": 1,
        "clean_control": False,
        "read_files": ["bridge.py", "parsers.py", "shapes.py", "tool_calls.py"],
        "min_recall": 1.0,
        "decoys": [
            {"file": "tool_calls.py", "line_start": 79, "line_end": 80},
            {"file": "tool_calls.py", "line_start": 98, "line_end": 99},
            {"file": "tool_calls.py", "line_start": 167, "line_end": 167},
            {"file": "tool_calls.py", "line_start": 208, "line_end": 209},
        ],
        "max_decoy_blocking": 0,
        "enforce_category": True,
    },
)
gv.passed()
