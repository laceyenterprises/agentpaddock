import gym_verifier as gv
from gym_doc_checks import check_document

CONTRACT = {
    "required_facts": {
        "levels": [
            "\\A(?=.*(?:--reasoning-level\\b))(?=.*(?:\\blow\\b))(?=.*(?:\\bmedium\\b))(?=.*(?:\\bhigh\\b))(?=.*(?:\\bxhigh\\b))",
            r"\bmax\b.{0,80}?(?:alias|normaliz|map)\w*.{0,40}?\bxhigh\b|\bxhigh\b.{0,80}?alias\w*.{0,40}?\bmax\b",
        ],
        "omitted": [
            "\\A(?=.*(?:omit\\w*|without|not "
            "(?:set|supplied)))(?=.*(?:--reasoning-level\\b))(?=.*(?:preserv\\w*|unchanged|retain\\w*))(?=.*(?:keys))"
        ],
        "cohort": [
            "\\A(?=.*(?:@effort:<level>|@effort:(?:low|medium|high|xhigh)))(?=.*(?:after "
            "(?:the )?model))(?=.*(?:before (?:the )?skill))"
        ],
    },
    "forbidden_claims": {"bad_alias": [r"\bmax\b[ `]*(?:normalizes to|means|aliases)[ `]*high\b"]},
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
