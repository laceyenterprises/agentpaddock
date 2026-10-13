import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "bare": {
            "patterns": [
                "unmodel(?:l)?ed|no model|without (?:a )?model|falsy",
                "bare|\\b(?:only|just)\\b|alone|\\|\\s*[`<]*task(?:_id)?[>`]*\\s*\\|",
            ],
            "scope": "unit",
        },
        "pinned": {
            "patterns": [
                "model[ -]pinned|task[ -](?:pinned|level)|model "
                "(?:selected|specified|provided)",
                "(?:[<{]?task(?:_id)?[>}]*@[<{]?model[>}]*|task ID "
                "followed by\\s*[`\\']*@|separated by\\s*[`\\']*@)",
            ],
            "scope": "unit",
        },
        "migration": {
            "patterns": [
                "\\b(?:old|legacy|existing)\\b",
                "baseline\\w*",
                "updat\\w*|migrat\\w*|regenerat\\w*|rebuild\\w*|refresh\\w*",
                "model[ -]pinned|[<{]?task(?:_id)?[>}]*@[<{]?model",
            ],
            "scope": "unit",
            "reject": ["no (?:changes|updat\\w*)|without requiring|do not require"],
        },
    },
    "forbidden_claims": {"old": ["wire-compatible with\\s*every baseline"]},
    "known_flags": [],
    "known_config_keys": [],
    "preserved_headings": ["## Baseline keys"],
    "non_config_literals": [
        "baseline.verdict_key",
        "g.cell.outcome",
        "g.cell.task_id",
        "self.graded",
        "self.models.get",
        "t.dispatch.get",
        "t.id",
    ],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
