import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "levels": {
            "patterns": [
                "--reasoning-level\\b",
                "\\blow\\b",
                "\\bmedium\\b",
                "\\bhigh\\b",
                "\\bxhigh\\b",
            ],
            "scope": "unit",
        },
        "alias": {
            "patterns": [
                "\\bmax\\b",
                "\\bxhigh\\b",
                "alias\\w*|normaliz\\w*|map\\w*|[|=→]|->",
            ],
            "scope": "unit",
        },
        "omitted": {
            "patterns": [
                "--reasoning-level\\b",
                "omit\\w*|without|unset|not (?:set|supplied|specified)|unspecified",
                "preserv\\w*|unchanged|retain\\w*|same|keep\\w*",
                "\\bkeys?\\b",
            ],
            "scope": "unit",
        },
        "cohort": {
            "patterns": [
                "@effort:",
                "(?:model[^@]{0,20}@effort:|after\\s+(?:the\\s+)?[`\"\\']*(?:model|<?task(?:_id)?>?@<?model>?))",
                "(?:@effort:[^@]{0,70}@(?:[<{]?skill[-_]digest)|(?:before|ahead "
                "of|preceding)\\s+(?:any |the )?[`\"\\']*skill[-_ "
                "]digest)",
            ],
            "scope": "unit",
        },
    },
    "forbidden_claims": {
        "bad_alias": ["\\bmax\\b[ `]*(?:normalizes to|means|aliases)[ `]*high\\b"]
    },
    "known_flags": [
        "--task",
        "--results-dir",
        "--compression",
        "--model",
        "--confirm",
        "--suite",
        "--json",
        "--baseline",
        "--worker-class",
        "--locks-dir",
        "--tasks-dir",
        "--agent",
        "--attempt",
        "--taint-mode",
        "--no-compare",
        "--reasoning-level",
        "--priority",
    ],
    "known_config_keys": ["worker_pool.tool_output_compression"],
    "preserved_headings": ["## Run"],
    "non_config_literals": [
        "VERDICT_KEY_SEPARATOR.join",
        "argparse.ArgumentParser",
        "argparse.ArgumentTypeError",
        "argparse.SUPPRESS",
        "baseline.add_subparsers",
        "baseline.py",
        "baseline_sub.add_parser",
        "cwp_dispatch.reasoning_level",
        "gym-baseline.json",
        "lock.add_subparsers",
        "lock_check.add_argument",
        "lock_sub.add_parser",
        "lock_update.add_argument",
        "parser.add_subparsers",
        "run.add_argument",
        "sub.add_parser",
        "suite_mod.SELECTABLE_SUITES",
        "tail.append",
        "update.add_argument",
    ],
}

check_document(gv, gv.workspace_text("guide.md"), **CONTRACT)
gv.passed()
