import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "limit": [
            "--limit\\b(?:[ `\"']*\\s*\\|\\s*(?:[ `\"']*(?:string|str|integer|int|number|enum)[ `\"']*\\s*\\|\\s*)?[ `\"']*|[^.;]{0,100}\\bdefault(?:s)?\\s*(?:is|to|:|=)?\\s*[ `\"']*|[ `\"']*\\s*(?:=|:)\\s*[ `\"']*)20\\b(?!\\.\\d)(?!\\.\\d)"
        ],
        "json": ["--json\\b[^.;]{0,100}\\bJSON\\b"],
        "order": [
            "created_at\\b[^.;]{0,100}(?:descending|newest[ -]first)|(?:descending|newest[ -]first)[^.;]{0,100}created_at\\b"
        ],
        "readonly": [
            "read[ -]only|(?:never|does not|doesn.t)\\s+(?:mutate|modify|update|write)\\w*\\s+(?:the )?rows"
        ],
        "success": [
            "(?:success(?:ful(?: inspection)?)?(?:(?!\\b(?:returns?|exits?|success|invalid|exhaustion)\\b)[^|]){0,100}?(?:\\b(?:returns?|exits?)(?:\\s+(?:with|exit))?(?:\\s+(?:code|status))?\\s*(?:is|:|=)?\\s*[ `\"']*0\\b(?!\\.\\d)|[ `\"']*\\s*\\|\\s*[ `\"']*0\\b(?!\\.\\d))|\\b0\\b(?!\\.\\d)[ `\"']*\\s*(?:on|for)\\s+success(?:ful(?: inspection)?)?)"
        ],
        "invalid": [
            "(?:invalid(?: arguments)?(?:(?!\\b(?:returns?|exits?|success|invalid|exhaustion)\\b)[^|]){0,100}?(?:\\b(?:returns?|exits?)(?:\\s+(?:with|exit))?(?:\\s+(?:code|status))?\\s*(?:is|:|=)?\\s*[ `\"']*2\\b(?!\\.\\d)|[ `\"']*\\s*\\|\\s*[ `\"']*2\\b(?!\\.\\d))|\\b2\\b(?!\\.\\d)[ `\"']*\\s*(?:on|for)\\s+invalid(?: arguments)?)"
        ],
    },
    "forbidden_claims": {"write": ["inspection (?:deletes|updates) rows"]},
    "known_flags": ["--limit", "--json"],
    "known_config_keys": [],
    "preserved_headings": [],
    "non_config_literals": ["argparse.ArgumentParser", "p.add_argument"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
