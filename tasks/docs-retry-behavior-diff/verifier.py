import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "attempts": [
            "(?:at most|up to|maximum(?: of)?|no more than)\\s*[ `\"']*(?:3|three)\\b[ `\"']*\\s*attempts?|\\b(?:maximum attempts?|attempt limit|attempts?)\\b[ `\"']*\\s*(?:\\||:|=|is)\\s*[ `\"']*(?:3|three)\\b"
        ],
        "timeout": [
            "(?:only\\s*[ `\"']*TimeoutError\\b[^.;]{0,100}retr\\w*|retr\\w*\\s+only\\s*[ `\"']*TimeoutError\\b)"
        ],
        "other": [
            "(?:other exceptions?|non[ -]timeout exceptions?)[^.;]{0,100}(?:propagat\\w*|rais\\w*)"
        ],
        "exit": [
            "(?:(?:exhaustion|exhausted|third(?: and final)? timed-out attempt|third attempt[^.;|]{0,60}\\bTimeoutError\\b)(?:(?!\\b(?:returns?|exits?|success|invalid|exhaustion)\\b)[^|]){0,100}?(?:\\b(?:returns?|exits?)(?:\\s+(?:with|exit))?(?:\\s+(?:code|status))?\\s*(?:is|:|=)?\\s*[ `\"']*4\\b(?!\\.\\d)|[ `\"']*\\s*\\|\\s*[ `\"']*4\\b(?!\\.\\d))|\\b4\\b(?!\\.\\d)[ `\"']*\\s*(?:on|for)\\s+(?:exhaustion|exhausted|third(?: and final)? timed-out attempt|third attempt[^.;|]{0,60}\\bTimeoutError\\b))"
        ],
        "success": [
            "(?:success(?:ful(?: delivery)?)?(?:(?!\\b(?:returns?|exits?|success|invalid|exhaustion)\\b)[^|]){0,100}?(?:\\b(?:returns?|exits?)(?:\\s+(?:with|exit))?(?:\\s+(?:code|status))?\\s*(?:is|:|=)?\\s*[ `\"']*0\\b(?!\\.\\d)|[ `\"']*\\s*\\|\\s*[ `\"']*0\\b(?!\\.\\d))|\\b0\\b(?!\\.\\d)[ `\"']*\\s*(?:on|for)\\s+success(?:ful(?: delivery)?)?)"
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
