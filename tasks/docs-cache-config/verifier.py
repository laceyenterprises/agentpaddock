import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "entries": ["\\A(?=.*(?:cache\\.max_entries\\b))(?=.*(?:\\b128\\b))"],
        "ttl": [
            "\\A(?=.*(?:cache\\.ttl_seconds\\b))(?=.*(?:\\b60\\b))",
            "\\A(?=.*(?:cache\\.ttl_seconds\\b))(?=.*(?:\\b0\\b))(?=.*(?:disabl\\w* cach))",
        ],
        "invalid": [r"\A(?=.*(?:negative))(?=.*(?:(?:exit(?: code)?[ `]*|\|[ `]*)2\b))"],
        "eviction": ["\\A(?=.*(?:eviction))(?=.*(?:least[ -]recently[ -]used|\\bLRU\\b))"],
        "bypass": [
            "\\A(?=.*(?:--no-cache\\b))(?=.*(?:\\breads\\b))(?=.*(?:\\bwrites\\b))(?=.*(?:bypass|skip))"
        ],
    },
    "forbidden_claims": {"old": ["defaults to 256", "defaults to 300", "Eviction is FIFO"]},
    "known_flags": ["--no-cache"],
    "known_config_keys": ["cache.max_entries", "cache.ttl_seconds"],
    "preserved_headings": ["## Cache"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
