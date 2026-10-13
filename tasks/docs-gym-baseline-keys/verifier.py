import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "bare": [
            "(?:unmodel(?:l)?ed|no model|without (?:a )?model)[^.;]{0,100}(?:bare task (?:id|identifier)|(?:only|just)\\s+[ `\"']*<?task(?:_id)?>?|task (?:id|identifier) alone)|(?:unmodel(?:l)?ed|no model)\\b[ `\"']*\\s*\\|\\s*[ `\"']*<?task(?:_id)?>?[ `\"']*\\s*\\|"
        ],
        "pinned": [
            "(?:model[ -]pinned|task[ -](?:pinned|level))[^.;]{0,100}<?task(?:_id)?>?@<?model>?"
        ],
        "migration": [
            "\\A(?=[\\s\\S]{0,8192}\\b(?:old|legacy|existing)\\b)(?=[\\s\\S]{0,8192}\\bbaseline\\w*\\b)(?=[\\s\\S]{0,8192}\\b(?:updat\\w*|migrat\\w*|regenerat\\w*|rebuild\\w*|refresh\\w*)\\b)(?=[\\s\\S]{0,8192}(?:model[ -]pinned|<?task(?:_id)?>?@<?model>?))"
        ],
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
