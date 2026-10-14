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
                'pinned to a model|tasks? that resolve to a model|model[ -]pinned|task[ -](?:pinned|level|pin)|pinned model|(?:tasks?|cells?|runs?)\\s+with a model|with a model,?\\s+(?:the\\s+)?(?:baseline\\s+)?key|model (?:resolved|selected|specified|provided)|non[ -]empty model|\\bmodel\\s+(?:is\\s+)?(?:pinned|supplied|resolved|selected|specified|provided)\\b|\\bpinned\\s+to\\s+[ `"\\x27*]*[\\w-]{1,64}\\b|\\bpinned\\s+tasks?\\b',
                '[<{]?task(?:_id)?[>}]*@[<{]?model[>}]*|task[ _]id[ `"\\\']*(?:\\s*\\+|\\s*,|\\s+followed by)?\\s*[ `"\\\']*@[ `"\\\']*(?:\\s*\\+|\\s*,)?\\s*(?:then|and|followed by)?\\s*(?:the\\s+)?[ `"\\\']*model|separated by\\s*[`\\\']*@|\\btask[ _]id\\b[^@\\r\\n.;]{0,100}@[^\\r\\n.;]{0,100}?\\bmodel\\b|@model\\b[ `"\\x27*]*\\s+suffix\\b|\\b[\\w-]{1,64}@([\\w-]{1,64})[ `"\\x27*]*\\s+when\\s+pinned\\s+to\\s+[ `"\\x27*]*\\1(?![\\w-])|\\b(?:model[ -]pinned\\s+(?:tasks?|cells?|runs?)|pinned\\s+tasks?)\\b[^@.\\r\\n]{0,100}\\b[\\w-]{1,64}@[\\w-]{1,64}(?![\\w-])|\\bpinned\\s+to\\s+[ `"\\x27*]*(?P<pinned_model>[\\w-]{1,64})(?![\\w-])[^@.\\r\\n]{0,100}\\b[\\w-]{1,64}@(?P=pinned_model)(?![\\w-])',
            ],
            "scope": "unit",
            "reject": [
                "\\bmodel[>}]*@[<{]?task(?:_id)?\\b",
                "\\b(?:key (?:is|remains)|keys? (?:use|keep)|keyed by)\\s+(?:the\\s+)?bare\\s+(?:task\\s+)?id",
                "task(?:_id)?[>}]*(?:\\s*[:/]\\s*|\\|)[<{]?model",
                "\\bseparator\\s+(?:is|=)\\s*[ `\"\\']*[:/|]",
                '\\b(?:uses?|key (?:is|remains)|keyed by)\\s+[ `"\\x27*]*[\\w-]{1,64}[:/|][\\w-]{1,64}(?![\\w-])',
            ],
        },
        "migration": {
            "patterns": [
                "old|legacy|existing|before models|prior to model",
                "baseline",
                "model[ -]pinned|pinned to a model|tasks? that resolve to a model|task(?:_id)?[>}]*@[<{]?model",
                "matching model-qualified keys|updat\\w*|migrat\\w*|re-record|regenerat\\w*|rebuild\\w*|refresh\\w*|task(?:_id)?[>}]*@[<{]?model",
            ],
            "scope": "section",
            "reject_subject": [
                "model[ -]pinned|pinned to a model|tasks? that resolve to a model"
            ],
            "reject": [
                "no (?:changes|matching model-qualified keys|updat\\w*|migration)|unchanged|without requiring|do not require"
            ],
        },
    },
    "forbidden_claims": {
        "old": [
            "wire-compatible with\\s*every baseline(?!\\s+(?:written|created)\\s+(?:before|prior to)\\s+models)"
        ]
    },
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
