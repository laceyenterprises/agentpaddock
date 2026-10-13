import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "values": {
            "patterns": [
                "build_pack_default_worker_class\\b|allowed values|__enum",
                "\\bplan\\b",
                "\\bcodex\\b",
                "\\bclaude-code\\b",
            ],
            "scope": "unit",
        },
        "default": {
            "patterns": [
                "default\\w*|__default|build_pack_default_worker_class\\b",
                "\\bplan\\b",
                "\\bdefaults?\\b|__default|[=:|]",
            ],
            "scope": "unit",
            "reject": [
                "default\\w*\\s*[ `\"\\']*(?:is|to|:|=)?\\s*[ "
                "`\"\\']*(?:codex|claude-code)\\b",
                "\\|[^|]*\\b(?:codex|none)\\b[^|]*\\|",
            ],
            "default_column": True,
        },
    },
    "forbidden_claims": {"old_default": ["default [ `]*(?:codex|claude-code)"]},
    "known_flags": [],
    "known_config_keys": ["roles.build_pack_default_worker_class"],
    "preserved_headings": ["## Config keys"],
    "non_config_literals": ["roles.__keys"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
