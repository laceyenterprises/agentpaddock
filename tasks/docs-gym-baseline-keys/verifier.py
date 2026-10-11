import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "bare": [
            "\\A(?=.*(?:unmodel(?:l)?ed|no model|without (?:a "
            ")?model))(?=.*(?:bare task (?:id|identifier)))"
        ],
        "pinned": [
            "\\A(?=.*(?:model[ -]pinned|task[ -](?:pinned|level)))(?=.*(?:task(?:_id)?@model))"
        ],
        "migration": [
            "\\A(?=.*(?:old|legacy))(?=.*(?:baseline))(?=.*(?:updat\\w*|migrat\\w*))(?=.*(?:model[ "
            "-]pinned|task(?:_id)?@model))"
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
