import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "entries": {
            "patterns": [
                "cache\\.max_entries\\b",
                "(?<![\\w.+-])128\\b(?!\\.\\d)",
                "\\bdefaults?\\b|[=:|]",
            ],
            "scope": "unit",
            "reject": ["\\bdefaults?\\s*(?:is|to|:|=)?\\s*[ `\"\\']*(?!128\\b)\\d+"],
            "default_column": True,
        },
        "ttl": {
            "patterns": [
                "cache\\.ttl_seconds\\b",
                "(?<![\\w.+-])60\\b(?!\\.\\d)",
                "\\bdefaults?\\b|[=:|]",
            ],
            "scope": "unit",
            "reject": ["\\bdefaults?\\s*(?:is|to|:|=)?\\s*[ `\"\\']*(?!60\\b)\\d+"],
            "default_column": True,
        },
        "disabled": {
            "patterns": [
                "(?:cache\\.)?ttl_seconds\\b",
                "(?<![\\w.+-])0\\b(?!\\.\\d)",
                "disabl\\w*|(?:no|without)\\s+cach\\w*",
            ],
            "scope": "unit",
            "reject": [
                "ttl_seconds\\b[ `\"\\']*\\s*(?:=|:|to|is)\\s*[ `\"\\']*(?!0\\b)\\d+"
            ],
        },
        "invalid": {
            "patterns": ["\\bnegative\\b", "(?<![\\w.+-])2\\b(?!\\.\\d)"],
            "scope": "unit",
            "reject": [
                "\\b(?:returns?|exits?)(?:\\s+(?:with|exit|code|status)){0,3}\\s*[ "
                "`\"\\']*(?!2\\b)\\d+\\b"
            ],
        },
        "eviction": {
            "patterns": [
                "evict\\w*|replacement",
                "least[ -]recently[ -]used|\\bLRU\\b",
            ],
            "scope": "unit",
        },
        "bypass": {
            "patterns": [
                "--no-cache\\b",
                "bypass\\w*|skip\\w*|disabl\\w*",
                "\\bread\\w*",
                "\\bwrite\\w*",
            ],
            "scope": "unit",
        },
    },
    "forbidden_claims": {
        "old": ["defaults to 256", "defaults to 300", "Eviction is FIFO"]
    },
    "known_flags": ["--no-cache"],
    "known_config_keys": ["cache.max_entries", "cache.ttl_seconds"],
    "preserved_headings": ["## Cache"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
