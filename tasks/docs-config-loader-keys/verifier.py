import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "values": [
            "\\A(?=.*(?:roles\\.build_pack_default_worker_class\\b))(?=.*(?:\\bplan\\b))",
            "\\A(?=.*(?:roles\\.build_pack_default_worker_class\\b))(?=.*(?:\\bcodex\\b))",
            "\\A(?=.*(?:roles\\.build_pack_default_worker_class\\b))(?=.*(?:\\bclaude-code\\b))",
        ],
        "default": [
            "\\bdefault\\b\\s*(?:is\\s+|:\\s*|=\\s*)?[ "
            "`]*plan\\b|roles\\.build_pack_default_worker_class`?\\s*\\|\\s*`?plan`?\\s*\\|"
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
