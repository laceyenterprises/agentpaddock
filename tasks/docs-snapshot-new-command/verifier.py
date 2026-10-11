import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "limit": ["\\A(?=.*(?:--limit\\b))(?=.*(?:\\b20\\b))"],
        "json": ["\\A(?=.*(?:--json\\b))(?=.*(?:\\bJSON\\b))"],
        "order": [
            "\\A(?=.*(?:created_at\\b))(?=.*(?:descending|newest[ -]first))",
            "read[ -]only",
        ],
        "exits": [
            r"\b0\b[ `|():=-]*(?:(?:on|for)\s+)?success(?:ful(?: inspection)?)?\b"
            r"|success(?:ful(?: inspection)?)?[ `|():=-]*(?:(?:returns?|exit(?: code)?(?: is)?)\s+)?0\b",
            r"\b2\b[ `|():=-]*(?:(?:on|for)\s+)?invalid\b"
            r"|invalid(?: arguments)?[ `|():=-]*(?:(?:returns?|exit(?: code)?(?: is)?)\s+)?2\b",
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
