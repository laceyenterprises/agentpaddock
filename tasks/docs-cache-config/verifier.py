import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "entries": [
            "cache\\.max_entries\\b(?:[ `\"']*\\s*\\|\\s*(?:[ `\"']*(?:string|str|integer|int|number|enum)[ `\"']*\\s*\\|\\s*)?[ `\"']*|[^.;]{0,100}\\bdefault(?:s)?\\s*(?:is|to|:|=)?\\s*[ `\"']*|[ `\"']*\\s*(?:=|:)\\s*[ `\"']*)128\\b(?!\\.\\d)|\\bdefault(?: value)?\\s+(?:for|of)\\s+[ `\"']*cache\\.max_entries\\b[ `\"']*\\s*(?:is|:|=)\\s*[ `\"']*128\\b(?!\\.\\d)"
        ],
        "ttl": [
            "cache\\.ttl_seconds\\b(?:[ `\"']*\\s*\\|\\s*(?:[ `\"']*(?:string|str|integer|int|number|enum)[ `\"']*\\s*\\|\\s*)?[ `\"']*|[^.;]{0,100}\\bdefault(?:s)?\\s*(?:is|to|:|=)?\\s*[ `\"']*|[ `\"']*\\s*(?:=|:)\\s*[ `\"']*)60\\b(?!\\.\\d)|\\bdefault(?: value)?\\s+(?:for|of)\\s+[ `\"']*cache\\.ttl_seconds\\b[ `\"']*\\s*(?:is|:|=)\\s*[ `\"']*60\\b(?!\\.\\d)"
        ],
        "disabled": [
            "(?:cache\\.)?ttl_seconds\\b[ `\"']*\\s*(?:=|:|to|is|\\|)\\s*[ `\"']*0\\b(?!\\.\\d)[^.;]{0,100}(?:disabl\\w*\\s+cach\\w*|(?:no|without)\\s+cach\\w*)|(?:disabl\\w*\\s+cach\\w*|(?:no|without)\\s+cach\\w*)[^.;]{0,100}(?:cache\\.)?ttl_seconds\\b[ `\"']*\\s*(?:=|:|to|is|\\|)\\s*[ `\"']*0\\b(?!\\.\\d)"
        ],
        "invalid": [
            "(?:negative\\b(?:(?!\\b(?:returns?|exits?|success)\\b)[^|]){0,100}?(?:\\b(?:returns?|exits?)(?:\\s+(?:with|exit))?(?:\\s+(?:code|status))?\\s*(?:is|:|=)?\\s*[ `\"']*2\\b(?!\\.\\d)|[ `\"']*\\s*\\|\\s*[ `\"']*2\\b(?!\\.\\d))|\\b2\\b(?!\\.\\d)[ `\"']*\\s*(?:on|for)\\s+negative\\b)"
        ],
        "eviction": [
            "(?:evict\\w*|replacement)[^.;]{0,100}(?:least[ -]recently[ -]used|\\bLRU\\b)|(?:least[ -]recently[ -]used|\\bLRU\\b)[^.;]{0,100}(?:evict\\w*|replacement)"
        ],
        "bypass": [
            "--no-cache\\b[^.;]{0,100}(?:bypass\\w*|skip\\w*|disabl\\w*)[^.;]{0,100}(?:read\\w*\\s+(?:and|or|/)\\s*write\\w*|write\\w*\\s+(?:and|or|/)\\s*read\\w*)"
        ],
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
