"""Exercise captured docs through the real isolated verifier, without dispatch."""

import ast
import json
import subprocess
from pathlib import Path

import pytest
from gym.doc_checks import _KEY
from gym.grader import Trajectory, grade
from gym.schema import load_task
from gym.suite import discover_tasks

TASKS = Path(__file__).resolve().parents[1] / "tasks"
DOCS = sorted(TASKS.glob("docs-*"))

ALTERNATE_DOCS = {
    "docs-cache-config": """## Cache
| Key | Default |
| cache.max_entries | 128 |
| cache.ttl_seconds | 60 |
Setting `cache.ttl_seconds`: 0 disables caching.
Negative TTL values are invalid (exit code `2`).
The cache uses least-recently-used eviction. --no-cache skips writes and reads.
""",
    "docs-config-loader-keys": """## Config keys
| Key | Default | Allowed values |
| `roles.build_pack_default_worker_class` | `plan` | `claude-code`, `codex`, `plan` |
""",
    "docs-gym-baseline-keys": """## Baseline keys
Unmodeled tasks use the bare task identifier.
Task-level model pins use `task@model`, including when no run override is supplied.
Migrate legacy baseline entries for model-pinned tasks to their qualified keys.
""",
    "docs-gym-run-flags": """## Run
| Flag | Values |
| --reasoning-level | xhigh, high, medium, low |
`max` is an alias for `xhigh`.
Without --reasoning-level, existing keys and slices remain unchanged.
Insert @effort:high before the `skill_digest` and after the model.
--compression labels the cohort; worker_pool.tool_output_compression selects the backend.
""",
    "docs-retry-behavior-diff": """## Delivery
Delivery makes up to three attempts. Delivery only retries TimeoutError.
Non-timeout exceptions are raised to the caller.
Success returns `0`. When exhausted, delivery returns `4`.
Delivery no longer retries indefinitely.
Delivery no longer retries every exception.
""",
    "docs-snapshot-new-command": """## Inspect
| Flag | Default / Output |
| `--json` | JSON |
The default value for --limit is 20.
Inspection is read only, ordered newest first by created_at.
Exit codes: 0 on success, 2 for invalid arguments.
""",
}


def contract_for(task_dir):
    tree = ast.parse((task_dir / "verifier.py").read_text())
    return next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
    )


def run_doc(task_dir, text, tmp_path):
    task = load_task(task_dir)
    if text is not None:
        (tmp_path / "guide.md").write_text(text)
    return grade(
        task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"done": True},
            sandbox_root=tmp_path,
        ),
    )


def test_exactly_six_docs_three_real_and_three_synthetic():
    assert len(discover_tasks(TASKS, suite="docs")) == 6
    assert sum((d / "expected/provenance.json").exists() for d in DOCS) == 3
    for d in DOCS:
        if (d / "expected/provenance.json").exists():
            assert (
                json.loads((d / "expected/provenance.json").read_text())["kind"]
                == "real-derived"
            )


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_reference_doc_passes(task_dir, tmp_path):
    cell = run_doc(task_dir, (task_dir / "expected/reference.md").read_text(), tmp_path)
    assert cell.outcome == "passed", cell.reason
    assert cell.score == 1


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_alternate_format_doc_passes(task_dir, tmp_path):
    cell = run_doc(task_dir, ALTERNATE_DOCS[task_dir.name], tmp_path)
    assert cell.outcome == "passed", cell.reason


@pytest.mark.parametrize(
    "task_id,old,new",
    [
        (
            "docs-config-loader-keys",
            "| `roles.build_pack_default_worker_class` | `plan` | `claude-code`, `codex`, `plan` |",
            "`roles.build_pack_default_worker_class` selects the worker.\n"
            "Allowed values:\n- plan.\n- codex.\n- claude-code.\nThe default is plan.",
        ),
        (
            "docs-cache-config",
            "Setting `cache.ttl_seconds`: 0 disables caching.",
            "To disable caching, set cache.ttl_seconds to 0.",
        ),
    ],
)
def test_punctuated_values_and_outcome_first_instructions_pass(
    task_id, old, new, tmp_path
):
    text = ALTERNATE_DOCS[task_id].replace(old, new)
    cell = run_doc(TASKS / task_id, text, tmp_path)
    assert cell.outcome == "passed", cell.reason


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_correct_doc_with_prose_and_filenames_passes(task_dir, tmp_path):
    text = (task_dir / "expected/reference.md").read_text()
    text += "\nSee surface.py and guide.md, i.e. the task inputs; e.g. submit bench-result.json.\n"
    assert run_doc(task_dir, text, tmp_path).outcome == "passed"


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_all_staged_source_identifiers_are_declared(task_dir, tmp_path):
    subprocess.run(
        ["bash", str(task_dir / "stages/stage-input.sh"), str(tmp_path)], check=True
    )
    literals = {
        key
        for name in ("surface.py", "change.diff")
        if (tmp_path / name).exists()
        for key in _KEY.findall((tmp_path / name).read_text())
        if not all(len(segment) == 1 for segment in key.split("."))
    }
    contract = contract_for(task_dir)
    declared = set(contract["known_config_keys"]) | set(
        contract.get("non_config_literals", [])
    )
    assert not literals - declared
    # Citing any source identifier must also pass the real verifier.
    text = (task_dir / "expected/reference.md").read_text()
    text += "\nSource identifiers: " + ", ".join(sorted(literals)) + "\n"
    assert run_doc(task_dir, text, tmp_path).outcome == "passed"


@pytest.mark.parametrize(
    "task_id, table",
    [
        ("docs-cache-config", "| Condition | Exit code |\n| Negative TTL | `2` |\n"),
        (
            "docs-retry-behavior-diff",
            "| Outcome | Code |\n| Success | `0` |\n| Exhaustion | `4` |\n",
        ),
    ],
)
def test_exit_codes_in_markdown_tables_pass(task_id, table, tmp_path):
    text = ALTERNATE_DOCS[task_id]
    line = (
        "Negative TTL values" if task_id == "docs-cache-config" else "Success returns"
    )
    text = "\n".join(part for part in text.splitlines() if not part.startswith(line))
    assert run_doc(TASKS / task_id, text + "\n" + table, tmp_path).outcome == "passed"
    assert (
        run_doc(
            TASKS / task_id,
            text + "\n" + table.replace("`2`", "`3`").replace("`4`", "`5`"),
            tmp_path,
        ).outcome
        == "failed"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "`max` normalizes to `xhigh`, which means high effort.",
        "maximum reasoning means high effort.",
    ],
)
def test_alias_check_accepts_unrelated_high_claims(claim, tmp_path):
    text = ALTERNATE_DOCS["docs-gym-run-flags"] + "\n" + claim
    assert run_doc(TASKS / "docs-gym-run-flags", text, tmp_path).outcome == "passed"


@pytest.mark.parametrize("verb", ["normalizes to", "means", "aliases"])
def test_alias_check_rejects_direct_high_claims(verb, tmp_path):
    text = ALTERNATE_DOCS["docs-gym-run-flags"] + f"\n`max` {verb} `high`.\n"
    cell = run_doc(TASKS / "docs-gym-run-flags", text, tmp_path)
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == "stale:bad_alias")


@pytest.mark.parametrize("prefix", ["no longer", "never", "not"])
def test_retry_accepts_only_documented_adjacent_negations(prefix, tmp_path):
    text = ALTERNATE_DOCS["docs-retry-behavior-diff"]
    text += f"\nDelivery {prefix} retries every exception indefinitely.\n"
    text += f"Delivery {prefix} exits 0 on exhaustion.\n"
    assert (
        run_doc(TASKS / "docs-retry-behavior-diff", text, tmp_path).outcome == "passed"
    )


@pytest.mark.parametrize(
    "task_id, claim",
    [
        (
            "docs-retry-behavior-diff",
            "Previously, delivery retries every exception indefinitely.",
        ),
        (
            "docs-retry-behavior-diff",
            "Before: Delivery retries every exception indefinitely.",
        ),
        ("docs-cache-config", "The cache no longer defaults to 256."),
        ("docs-cache-config", "Eviction is FIFO no longer."),
    ],
)
def test_unsupported_changelog_phrasings_remain_stale(task_id, claim, tmp_path):
    cell = run_doc(TASKS / task_id, ALTERNATE_DOCS[task_id] + "\n" + claim, tmp_path)
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == "stale:old")


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_alternate_format_still_rejects_wrong_values(task_dir, tmp_path):
    replacements = {
        "docs-cache-config": ("128", "1280"),
        "docs-config-loader-keys": ("`plan` |", "`codex` |"),
        "docs-gym-baseline-keys": ("task@model", "task/model"),
        "docs-gym-run-flags": ("alias for `xhigh`", "alias for `high`"),
        "docs-retry-behavior-diff": ("three attempts", "four attempts"),
        "docs-snapshot-new-command": ("is 20.", "is 200."),
    }
    old, new = replacements[task_dir.name]
    text = ALTERNATE_DOCS[task_dir.name].replace(old, new)
    assert run_doc(task_dir, text, tmp_path).outcome == "failed"


def test_snapshot_exit_values_cannot_be_swapped(tmp_path):
    text = ALTERNATE_DOCS["docs-snapshot-new-command"].replace(
        "0 on success, 2 for invalid", "2 on success, 0 for invalid"
    )
    cell = run_doc(TASKS / "docs-snapshot-new-command", text, tmp_path)
    assert cell.outcome == "failed"
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == "fact:success")


@pytest.mark.parametrize("repeats", [100, 400, 4000])
def test_stuffed_reasoning_line_fails_quickly(repeats, tmp_path):
    import time

    started = time.monotonic()
    cell = run_doc(
        TASKS / "docs-gym-run-flags",
        "--reasoning-level " + "low medium high " * repeats,
        tmp_path,
    )
    assert time.monotonic() - started < 5
    assert cell.outcome == "failed"
    check = "document_shape" if repeats >= 400 else "fact:levels"
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == check)


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_staged_headings_cannot_be_deleted(task_dir, tmp_path):
    contract = contract_for(task_dir)
    if not contract["preserved_headings"]:
        assert task_dir.name == "docs-snapshot-new-command"  # No staged guide.
        return
    text = (task_dir / "expected/reference.md").read_text()
    text = "\n".join(line for line in text.splitlines() if not line.startswith("## "))
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "failed"
    assert any(c["name"].startswith("heading:") and not c["ok"] for c in cell.subchecks)


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_stale_doc_fails(task_dir, tmp_path):
    assert (
        run_doc(
            task_dir, (task_dir / "expected/stale.md").read_text(), tmp_path
        ).outcome
        == "failed"
    )


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_one_invented_flag_fails(task_dir, tmp_path):
    text = (
        task_dir / "expected/reference.md"
    ).read_text() + "\nUse --invented-rfd-option.\n"
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "failed"
    assert [c for c in cell.subchecks if not c["ok"]] == [
        next(c for c in cell.subchecks if c["name"] == "known_flags")
    ]


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_one_missing_required_fact_fails(task_dir, tmp_path):
    contract = contract_for(task_dir)
    name, patterns = next(iter(contract["required_facts"].items()))
    text = (task_dir / "expected/reference.md").read_text()
    # Remove the line carrying the first required fact. Other independent
    # assertions may share that line; the named fact must specifically fail.
    import re

    text = "\n".join(
        line for line in text.splitlines() if not re.search(patterns[0], line, re.I)
    )
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "failed"
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == "fact:" + name)
    assert all(c["name"].startswith("fact:") for c in cell.subchecks if not c["ok"])


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_invented_key_and_missing_capture_fail(task_dir, tmp_path):
    assert run_doc(task_dir, None, tmp_path).outcome == "failed"
    contract = contract_for(task_dir)
    prefixes = [key.split(".", 1)[0] for key in contract["known_config_keys"]]
    if not prefixes:
        assert (
            run_doc(
                task_dir,
                (task_dir / "expected/reference.md").read_text()
                + "\nself.models obj.attr\n",
                tmp_path,
            ).outcome
            == "passed"
        )
        return
    text = (
        task_dir / "expected/reference.md"
    ).read_text() + f"\n{prefixes[0]}.invented_rfd_key=3\n"
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "failed"
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == "known_config_keys")


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_staging_contains_only_inputs(task_dir, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    tmp_path = sandbox
    subprocess.run(
        ["bash", str(task_dir / "stages/stage-input.sh"), str(tmp_path)], check=True
    )
    assert set(p.name for p in tmp_path.iterdir()) <= {
        "surface.py",
        "guide.md",
        "change.diff",
    }
    assert (tmp_path / "surface.py").is_file()
    assert (
        run_doc(
            task_dir,
            (tmp_path / "guide.md").read_text()
            if (tmp_path / "guide.md").exists()
            else None,
            tmp_path,
        ).outcome
        == "failed"
    )


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_stale_claim_cannot_be_padded_with_correct_facts(task_dir, tmp_path):
    text = (task_dir / "expected/reference.md").read_text()
    stale_claims = {
        "docs-cache-config": "Eviction is FIFO",
        "docs-config-loader-keys": "default codex",
        "docs-gym-baseline-keys": "wire-compatible with every baseline",
        "docs-gym-run-flags": "max normalizes to high",
        "docs-retry-behavior-diff": "Delivery retries every exception indefinitely",
        "docs-snapshot-new-command": "inspection deletes rows",
    }
    cell = run_doc(task_dir, text + "\n" + stale_claims[task_dir.name], tmp_path)
    assert cell.outcome == "failed"
    assert any(c["name"].startswith("stale:") and not c["ok"] for c in cell.subchecks)


def test_doc_helper_is_snapshotted_before_disk_changes(tmp_path, monkeypatch):
    from gym import grader
    import shutil

    support = tmp_path / "support"
    support.mkdir()
    for name in (
        "verifier_runtime.py",
        "harness_toolcalls.py",
        "doc_checks.py",
        "review_checks.py",
    ):
        shutil.copyfile(Path(grader.__file__).with_name(name), support / name)
    monkeypatch.setattr(
        grader, "_SUPPORT_FILES", grader.snapshot_support_files(support)
    )
    (support / "doc_checks.py").write_text("raise RuntimeError('deployed mid-run')\n")
    task_dir = TASKS / "docs-cache-config"
    cell = run_doc(task_dir, (task_dir / "expected/reference.md").read_text(), tmp_path)
    assert cell.outcome == "passed", cell.reason


def test_numeric_default_is_a_value_not_a_prefix(tmp_path):
    task_dir = TASKS / "docs-cache-config"
    text = (task_dir / "expected/reference.md").read_text().replace("128", "1280")
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "failed"
    assert not next(c["ok"] for c in cell.subchecks if c["name"] == "fact:entries")


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_facts_wrapped_across_bullets_and_lines_pass(task_dir, tmp_path):
    import textwrap

    reference = (task_dir / "expected/reference.md").read_text()
    text = "\n\n".join(
        line
        if line.startswith("#")
        else "- "
        + "\n  ".join(
            textwrap.wrap(
                line, width=24, break_long_words=False, break_on_hyphens=False
            )
        )
        for line in reference.splitlines()
    )
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "passed", cell.reason


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_help_and_source_attributes_pass(task_dir, tmp_path):
    text = (task_dir / "expected/reference.md").read_text()
    text += "\nUse --help or -h. Source reads `self.models` and obj.attr.\n"
    cell = run_doc(task_dir, text, tmp_path)
    assert cell.outcome == "passed", cell.reason


# Each tuple holds equivalent assertions, followed by a contradictory assertion.
FACT_FORMS = {
    "docs-cache-config": {
        "entries": (
            "cache.max_entries defaults to 128.",
            'cache.max_entries = "128"',
            "| cache.max_entries | string | `128` |",
            "cache.max_entries defaults to 129.",
        ),
        "ttl": (
            "cache.ttl_seconds defaults to 60.",
            'cache.ttl_seconds = "60"',
            "| cache.ttl_seconds | string | `60` |",
            "cache.ttl_seconds defaults to 61.",
        ),
        "disabled": (
            "cache.ttl_seconds=0 disables caching.",
            'Setting `cache.ttl_seconds` to "0" disables caching.',
            "| cache.ttl_seconds | 0 | no caching |",
            "cache.ttl_seconds: 0 disables caching.",
            "- cache.ttl_seconds: 0 (disables caching)",
            "To disable caching, set cache.ttl_seconds to 0.",
            "Disable caching with ttl_seconds = 0.",
            'For no caching, set `cache.ttl_seconds` to "0".',
            "cache.ttl_seconds=1 disables caching.",
        ),
        "invalid": (
            "Negative `cache.ttl_seconds` values are invalid and exit with code `2`.",
            "Negative values are invalid and cause the process to exit with status `2`.",
            "Negative ttl_seconds values are invalid; the surface exits with status 2.",
            "Negative values exit with status 3.",
        ),
        "eviction": (
            "Eviction is least-recently-used.",
            "Evictions use LRU.",
            "| Eviction | LRU |",
            "The cache uses least-recently-used eviction.",
            "Least-recently-used eviction is enforced.",
            "Least recently used replacement is enforced.",
            "Eviction uses FIFO.",
        ),
        "bypass": (
            "--no-cache bypasses reads and writes.",
            "--no-cache skips writes and reads.",
            "--no-cache disables read and write operations.",
            "--no-cache enables reads and writes.",
        ),
    },
    "docs-config-loader-keys": {
        "values": (
            "roles.build_pack_default_worker_class accepts plan, codex, claude-code.",
            "| roles.build_pack_default_worker_class | plan, codex, claude-code |",
            'roles.build_pack_default_worker_class allows "claude-code", `plan`, and "codex".',
            "roles.build_pack_default_worker_class selects the worker. Allowed values: plan; codex; claude-code.",
            "roles.build_pack_default_worker_class selects the worker.\n- plan.\n- codex.\n- claude-code.",
            "roles.build_pack_default_worker_class accepts plan and codex.",
        ),
        "default": (
            "| Key | Type | Default | Allowed values |\n| `roles.build_pack_default_worker_class` | string | `plan` | `plan`, `codex`, `claude-code` |",
            '| Key | Type | Default | Allowed values |\n| `roles.build_pack_default_worker_class` | string | `"plan"` | `plan`, `codex`, `claude-code` |',
            '| `roles.build_pack_default_worker_class` | `"plan"` |',
            "| `roles.build_pack_default_worker_class` | `codex` | plan, codex, claude-code |",
        ),
    },
    "docs-gym-baseline-keys": {
        "bare": (
            "Unmodelled tasks keep the bare task id.",
            "Without a model, use just `task_id`.",
            "No model: the task identifier alone.",
            "Unmodelled tasks use task@model.",
        ),
        "pinned": (
            "**Model-pinned task**: the key is `<task_id>@<model>`, e.g. `my-task@some-model`.",
            "| Model-pinned | `<task_id>@<model>` | `my-task@some-model` |",
            "Task-level model pins use task@model.",
            "Model-pinned tasks use model@task.",
        ),
        "migration": (
            "Old baselines for model-pinned tasks need a one-time baseline update.",
            "Migrate legacy baseline entries for model-pinned tasks.",
            "Refresh existing baselines to use `<task_id>@<model>`.",
            "Old baselines for model-pinned tasks need no changes.",
        ),
    },
    "docs-gym-run-flags": {
        "levels": (
            "--reasoning-level accepts low, medium, high, xhigh.",
            "| --reasoning-level | `low`, `medium`, `high`, `xhigh` |",
            '--reasoning-level supports "xhigh", "high", "medium", "low".',
            "--reasoning-level accepts low, medium, high, extreme.",
        ),
        "alias": (
            "max normalizes to xhigh.",
            "`max` is an alias for `xhigh`.",
            'xhigh has the alias "max".',
            "max normalizes to high.",
        ),
        "omitted": (
            "Omitting --reasoning-level preserves existing keys.",
            "Without --reasoning-level, keys remain unchanged.",
            "--reasoning-level is unspecified: keep the same key.",
            "Omitting --reasoning-level changes existing keys.",
        ),
        "cohort": (
            "`task@model@effort:<level>[@<skill-digest>][@compression=<arm>][@attempt=<n>]`",
            "@effort:<level> is immediately after `task_id@<model>` and ahead of the skill digest.",
            "@effort:<level> is placed immediately after the model component, preceding any `skill_digest`.",
            "@effort:<level> is placed before the skill_digest and after the model.",
            "@effort:high is placed before the skill digest and after the model.",
            "@effort:low is placed ahead of any `skill-digest` and after the `model`.",
            '@effort:medium is placed preceding the "skill_digest" and after the "model".',
            "@effort:xhigh is placed before the skill digest and after the model.",
            "`task@model@<skill-digest>@effort:<level>`",
        ),
    },
    "docs-retry-behavior-diff": {
        "attempts": (
            "Delivery makes at most 3 attempts.",
            'Up to "three" attempts are made.',
            "Maximum of `3` attempts.",
            "Delivery makes at most 4 attempts.",
        ),
        "timeout": (
            "Delivery retries only TimeoutError.",
            "Only `TimeoutError` is retried.",
            'Only "TimeoutError" triggers retries.',
            "Delivery only retries TimeoutError.",
            "The function only retries `TimeoutError`.",
            "Delivery retries only ValueError.",
        ),
        "other": (
            "Any other exception propagates to the caller immediately (not retried, not swallowed).",
            "Only `TimeoutError` is retried; any other exception propagates to the caller.",
            "Non-timeout exceptions are raised to the caller.",
            "Any other exception is swallowed.",
        ),
        "exit": (
            "If the third attempt also raises `TimeoutError`, it returns exit code `4`.",
            "After the third and final timed-out attempt, the function returns `4`.",
            "| Exhaustion | `4` |",
            "Exhaustion returns 0.",
        ),
        "success": (
            "Success returns 0.",
            'Successful delivery exits with status "0".',
            "| Success | `0` |",
            "Success returns 4.",
        ),
    },
    "docs-snapshot-new-command": {
        "limit": (
            "--limit defaults to 20.",
            '--limit = "20"',
            "| --limit | string | `20` |",
            "The default value for --limit is 20.",
            'The default of `--limit` is "20".',
            "--limit defaults to 21.",
        ),
        "json": (
            "--json emits JSON.",
            "| --json | JSON output |",
            "--json returns a JSON object.",
            "--json emits YAML.",
        ),
        "order": (
            "Rows are sorted by created_at descending.",
            "Newest first by `created_at`.",
            "| created_at | newest-first |",
            "Rows are sorted by created_at ascending.",
        ),
        "readonly": (
            "Inspection is read-only.",
            "Inspection is read only.",
            "Inspection never mutates rows.",
            "Inspection updates rows.",
        ),
        "success": (
            "Successful inspection exits with status `0`.",
            'Success returns "0".',
            "Exit 0 on success.",
            "Success exits with status 2.",
        ),
        "invalid": (
            "Invalid arguments exit with status `2`.",
            'Invalid arguments return "2".',
            "Exit 2 for invalid arguments.",
            "Invalid arguments exit with status 0.",
        ),
    },
}

FACT_CASES = [
    (task, fact, text, ok)
    for task, facts in FACT_FORMS.items()
    for fact, forms in facts.items()
    for text, ok in [
        *((text, True) for text in forms[:-1]),
        (forms[-1], False),
        ("Unrelated documentation.", False),
    ]
]


@pytest.mark.parametrize("task_id,fact,text,ok", FACT_CASES)
def test_required_fact_wording(task_id, fact, text, ok):
    from gym.doc_checks import checks

    contract = contract_for(TASKS / task_id)
    records = checks(
        text,
        required_facts={fact: contract["required_facts"][fact]},
        forbidden_claims={},
    )
    assert next(c["ok"] for c in records if c["name"] == "fact:" + fact) is ok


def test_wording_table_covers_every_required_fact():
    assert set(FACT_FORMS) == {d.name for d in DOCS}
    for task_id, facts in FACT_FORMS.items():
        assert set(facts) == set(contract_for(TASKS / task_id)["required_facts"])


@pytest.mark.parametrize("task_dir", DOCS, ids=lambda d: d.name)
def test_independent_facts_can_use_separate_headings(task_dir):
    from gym.doc_checks import checks

    contract = contract_for(task_dir)
    text = "\n".join(
        f"## {fact}\n{forms[0]}" for fact, forms in FACT_FORMS[task_dir.name].items()
    )
    assert all(
        c["ok"]
        for c in checks(
            text,
            required_facts=contract["required_facts"],
            forbidden_claims={},
            known_flags=contract["known_flags"],
            known_config_keys=contract["known_config_keys"],
        )
    )


@pytest.mark.parametrize(
    "task_id,fact,text",
    [
        (
            "docs-retry-behavior-diff",
            "timeout",
            "Delivery only retries ValueError. TimeoutError propagates.",
        ),
        (
            "docs-cache-config",
            "eviction",
            "Least-recently-used tracks access. Eviction uses FIFO.",
        ),
        (
            "docs-cache-config",
            "disabled",
            "cache.ttl_seconds: 0. Another option disables caching.",
        ),
        (
            "docs-gym-run-flags",
            "cohort",
            "@effort:high is before the skill_digest. Another tag is after the model.",
        ),
        (
            "docs-gym-run-flags",
            "cohort",
            "@effort:high is after the skill_digest and before the model.",
        ),
        (
            "docs-snapshot-new-command",
            "limit",
            "The default value for --limit is 21. Another default is 20.",
        ),
        (
            "docs-cache-config",
            "entries",
            "cache.max_entries defaults to 129. Another default is 128.",
        ),
        (
            "docs-cache-config",
            "ttl",
            "cache.ttl_seconds defaults to 61. Another default is 60.",
        ),
        ("docs-cache-config", "invalid", "Negative values exit 3; success exits 2."),
        (
            "docs-retry-behavior-diff",
            "exit",
            "Exhaustion returns 0; success returns 4.",
        ),
        (
            "docs-retry-behavior-diff",
            "exit",
            "The third attempt succeeds and returns 4.",
        ),
        (
            "docs-retry-behavior-diff",
            "success",
            "Success returns 4; exhaustion returns 0.",
        ),
        (
            "docs-retry-behavior-diff",
            "other",
            "Other exceptions are not retried but are swallowed.",
        ),
        (
            "docs-snapshot-new-command",
            "success",
            "Success exits 2; invalid arguments exit 0.",
        ),
        (
            "docs-snapshot-new-command",
            "invalid",
            "Invalid arguments exit 0; success exits 2.",
        ),
        (
            "docs-config-loader-keys",
            "default",
            "| roles.build_pack_default_worker_class | string | codex | plan, codex, claude-code |",
        ),
        (
            "docs-config-loader-keys",
            "values",
            "## Key\nroles.build_pack_default_worker_class\n## Other\nplan, codex, claude-code.",
        ),
        (
            "docs-config-loader-keys",
            "values",
            "roles.build_pack_default_worker_class "
            + "x" * 101
            + " plan, codex, claude-code.",
        ),
        (
            "docs-cache-config",
            "disabled",
            "Disable caching. Another option sets ttl_seconds to 0.",
        ),
        (
            "docs-cache-config",
            "disabled",
            "Disable caching " + "x" * 101 + " ttl_seconds = 0.",
        ),
    ],
)
def test_unrelated_correct_values_do_not_satisfy_fact(task_id, fact, text):
    from gym.doc_checks import checks

    contract = contract_for(TASKS / task_id)
    records = checks(
        text,
        required_facts={fact: contract["required_facts"][fact]},
        forbidden_claims={},
    )
    assert not next(c["ok"] for c in records if c["name"] == "fact:" + fact)


@pytest.mark.parametrize(
    "task_id,fact,text,ok",
    [
        (
            "docs-cache-config",
            "entries",
            '| cache.max_entries | integer | "128" |',
            True,
        ),
        ("docs-cache-config", "ttl", "| cache.ttl_seconds | int | `60` |", True),
        (
            "docs-cache-config",
            "entries",
            'The default value for `cache.max_entries` is "128".',
            True,
        ),
        ("docs-cache-config", "ttl", "The default of cache.ttl_seconds is 60.", True),
        (
            "docs-config-loader-keys",
            "default",
            'The default value for roles.build_pack_default_worker_class is "plan".',
            True,
        ),
        (
            "docs-config-loader-keys",
            "default",
            '| Key | Type | Default | Allowed values |\n| `roles.build_pack_default_worker_class` | string | "plan" | plan, codex, claude-code |',
            True,
        ),
        (
            "docs-gym-run-flags",
            "alias",
            '| Alias | Canonical level |\n| `max` | "xhigh" |',
            True,
        ),
        ("docs-retry-behavior-diff", "attempts", "| Attempts | `3` |", True),
        ("docs-gym-baseline-keys", "bare", "| Unmodelled | `<task_id>` |", True),
        (
            "docs-cache-config",
            "invalid",
            "Negative values exit with status 2.5.",
            False,
        ),
        ("docs-snapshot-new-command", "limit", "--limit defaults to 20.5.", False),
        (
            "docs-snapshot-new-command",
            "limit",
            "The default value for --limit is 20.5.",
            False,
        ),
        (
            "docs-cache-config",
            "disabled",
            "cache.ttl_seconds: 0.5 disables caching.",
            False,
        ),
        (
            "docs-cache-config",
            "disabled",
            "Disable caching with ttl_seconds = 0.5.",
            False,
        ),
        (
            "docs-cache-config",
            "disabled",
            "Disable caching with ttl_seconds = 1.",
            False,
        ),
        ("docs-cache-config", "entries", "cache.max_entries defaults to 128.5.", False),
        ("docs-cache-config", "ttl", "cache.ttl_seconds defaults to 60.5.", False),
        ("docs-retry-behavior-diff", "exit", "Exhaustion returns 4.5.", False),
    ],
)
def test_additional_table_and_numeric_forms(task_id, fact, text, ok):
    from gym.doc_checks import checks

    contract = contract_for(TASKS / task_id)
    records = checks(
        text,
        required_facts={fact: contract["required_facts"][fact]},
        forbidden_claims={},
    )
    assert next(c["ok"] for c in records if c["name"] == "fact:" + fact) is ok
