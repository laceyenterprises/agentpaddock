import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "attempts": {
            "patterns": [
                "attempt\\w*|deliver\\w*|send",
                "(?<![\\w.+-])(?:3|three|third)\\b(?!\\.\\d)",
                "at most|up to|maximum|no more than|bounded|all|final|\\|",
            ],
            "scope": "unit",
            "reject": ["\\b(?:4|four)\\s+(?:delivery\\s+)?attempts"],
        },
        "timeout": {
            "patterns": ["\\bTimeoutError\\b", "retr\\w*|caught|catches"],
            "scope": "unit",
            "reject": ["only\\s+(?:retr\\w*\\s+)?ValueError"],
        },
        "other": {
            "patterns": [
                "other\\s+(?:than\\s+[`]*TimeoutError|exceptions?)|non[ "
                "-]timeout exceptions?",
                "propagat\\w*|rais\\w*|not caught",
            ],
            "scope": "unit",
        },
        "exit": {
            "patterns": [
                "exhaust\\w*|(?:all|three|3|third|final)[^.;]{0,100}(?:TimeoutError|timeout|timed-out)",
                "(?<![\\w.+-])4\\b(?!\\.\\d)",
            ],
            "scope": "unit",
            "reject": [
                "\\b(?:returns?|exits?)(?:\\s+(?:with|exit|code|status)){0,3}\\s*[ "
                "`\"\\']*(?!4\\b)\\d+\\b"
            ],
        },
        "success": {
            "patterns": [
                "success\\w*|succeed\\w*|without raising",
                "(?<![\\w.+-])0\\b(?!\\.\\d)",
            ],
            "scope": "unit",
            "reject": [
                "\\b(?:returns?|exits?)(?:\\s+(?:with|exit|code|status)){0,3}\\s*[ "
                "`\"\\']*(?!0\\b)\\d+\\b"
            ],
        },
    },
    "forbidden_claims": {
        "old": [
            "(?<!no longer )(?<!never )(?<!not )retries (?:every "
            "exception|indefinitely)",
            "(?<!no longer )(?<!never )(?<!not )exits 0 on exhaustion",
        ]
    },
    "known_flags": [],
    "known_config_keys": [],
    "preserved_headings": ["## Delivery"],
    "non_config_literals": ["delivery.py"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
