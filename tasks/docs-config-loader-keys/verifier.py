import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "values": [
            "roles\\.build_pack_default_worker_class\\b.{0,100}\\bplan\\b",
            "roles\\.build_pack_default_worker_class\\b.{0,100}\\bcodex\\b",
            "roles\\.build_pack_default_worker_class\\b.{0,100}\\bclaude-code\\b",
        ],
        "default": [
            "roles\\.build_pack_default_worker_class\\b(?:[ `\"']*\\s*\\|\\s*(?:[ `\"']*(?:string|str|integer|int|number|enum)[ `\"']*\\s*\\|\\s*)?[ `\"']*|[^.;]{0,100}\\bdefault(?:s)?\\s*(?:is|to|:|=)?\\s*[ `\"']*|[ `\"']*\\s*(?:=|:)\\s*[ `\"']*)plan\\b|\\bdefault\\b\\s*(?:is|to|:|=)?\\s*[ `\"']*plan\\b|\\bdefault(?: value)?\\s+(?:for|of)\\s+[ `\"']*roles\\.build_pack_default_worker_class\\b[ `\"']*\\s*(?:is|:|=)\\s*[ `\"']*plan\\b"
        ],
    },
    "forbidden_claims": {"old_default": ["default [ `]*(?:codex|claude-code)"]},
    "known_flags": [],
    "known_config_keys": ["roles.build_pack_default_worker_class"],
    "preserved_headings": ["## Config keys"],
    "non_config_literals": ["roles.__keys"],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
