import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "levels": [
            "--reasoning-level\\b",
            "\\blow\\b",
            "\\bmedium\\b",
            "\\bhigh\\b",
            "\\bxhigh\\b",
        ],
        "alias": [
            "\\bmax\\b[^.;]{0,100}(?:alias|normaliz|map)\\w*[^.;]{0,100}\\bxhigh\\b|\\bxhigh\\b[^.;]{0,100}alias\\w*[^.;]{0,100}\\bmax\\b|\\bmax\\b[ `\"']*\\s*(?:\\||->|→|=)\\s*[ `\"']*xhigh\\b"
        ],
        "omitted": [
            "\\A(?=[\\s\\S]{0,8192}(?:omit\\w*|without|unset|not (?:set|supplied|specified)|unspecified))(?=[\\s\\S]{0,8192}--reasoning-level\\b)(?=[\\s\\S]{0,8192}(?:preserv\\w*|unchanged|retain\\w*|same|keep\\w*))(?=[\\s\\S]{0,8192}\\bkeys?\\b)"
        ],
        "cohort": [
            "(?:<?task(?:_id)?>?@<?model>?@effort:<?(?:level|low|medium|high|xhigh)>?(?:\\[?@<?skill[-_]digest>?\\]?)|@effort:<?(?:level|low|medium|high|xhigh)>?[^.;]{0,100}(?:after (?:the )?[ `\"']*model|after [ `\"']*<?task(?:_id)?>?@<?model>?)[^.;]{0,100}(?:before|ahead of|preceding)\\s+(?:any |the )?[ `\"']*skill[-_ ]digest|@effort:<?(?:level|low|medium|high|xhigh)>?[^.;]{0,100}(?:before|ahead of|preceding)\\s+(?:any |the )?[ `\"']*skill[-_ ]digest[^.;]{0,100}after (?:the )?[ `\"']*model)"
        ],
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
