import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "limit": {
            "patterns": [
                "--limit\\b|\\blimit\\s*=",
                "(?<![\\w.+-])20\\b(?!\\.\\d)",
                "\\bdefaults?\\b|[=:|]",
            ],
            "scope": "unit",
            "reject": [
                "\\bdefaults?\\s*(?:is|to|:|=)?\\s*[ `\"\\']*(?!20\\b)\\d+",
                "\\blimit\\s*=\\s*(?!20\\b)\\d+",
            ],
            "default_column": True,
        },
        "json": {"patterns": ["--json\\b", "(?<![-\\w])JSON\\b"], "scope": "unit"},
        "order": {
            "patterns": ["created_at\\b", "descending|newest[ -]first"],
            "scope": "unit",
        },
        "readonly": {
            "patterns": [
                "inspect\\w*|\\brows\\b|\\bfunction\\b|\\bcommand\\b",
                "read[ -]only|never "
                "(?:mutat|modif|updat|writ)\\w*|never mutated|does "
                "not mutat\\w*|doesn.t mutat\\w*",
            ],
            "scope": "unit",
        },
        "success": {
            "patterns": ["success\\w*|succeed\\w*", "(?<![\\w.+-])0\\b(?!\\.\\d)"],
            "scope": "unit",
            "reject": [
                "\\b(?:returns?|exits?)(?:\\s+(?:with|exit|code|status)){0,3}\\s*[ "
                "`\"\\']*(?!0\\b)\\d+\\b"
            ],
        },
        "invalid": {
            "patterns": [
                "invalid arguments?|argument error",
                "(?<![\\w.+-])2\\b(?!\\.\\d)",
            ],
            "scope": "unit",
            "reject": [
                "\\b(?:returns?|exits?)(?:\\s+(?:with|exit|code|status)){0,3}\\s*[ "
                "`\"\\']*(?!2\\b)\\d+\\b"
            ],
        },
    },
    "forbidden_claims": {"write": ["inspection (?:deletes|updates) rows"]},
    "known_flags": ["--limit", "--json"],
    "known_config_keys": [],
    "preserved_headings": [],
    "non_config_literals": ["argparse.ArgumentParser", "p.add_argument"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
