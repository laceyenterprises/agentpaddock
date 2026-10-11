import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "attempts": [
            "\\A(?=.*(?:at most|up to|maximum))(?=.*(?:\\b(?:3|three)\\b))(?=.*(?:attempts))",
            "\\A(?=.*(?:\\bTimeoutError\\b))(?=.*(?:only))(?=.*(?:retr))",
        ],
        "other": ["\\A(?=.*(?:other exceptions|non[ -]timeout exceptions))(?=.*(?:propagat|rais))"],
        "exit": [
            r"\A(?=.*(?:exhaustion|exhausted))(?=.*(?:"
            r"(?:returns?(?: exit code)?|exit code)[ `]+4\b"
            r"|\b(?:exhaustion|exhausted)[ `|():=-]*4\b))",
            r"\A(?=.*(?:success))(?=.*(?:"
            r"(?:returns?(?: exit code)?|exit code)[ `]+0\b"
            r"|\bsuccess[ `|():=-]*0\b))",
        ],
    },
    "forbidden_claims": {
        "old": [
            "(?<!no longer )(?<!never )(?<!not )retries (?:every exception|indefinitely)",
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
