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
        line
        for line in text.splitlines()
        if line.startswith("#") or not re.search(patterns["patterns"][0], line, re.I)
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


# Saved submissions: complete assertions pass; fragments and headings alone do
# not establish a fact. Wrapped continuations belong to their list item. A
# heading may identify the subject of its item, but sibling claims stay apart.
SAVED_PASS = {
    "docs-cache-config": {
        "entries": [
            "- `cache.max_entries` — maximum number of cached entries. Default: 128."
        ],
        "ttl": [
            "- `cache.ttl_seconds` — time-to-live for cached entries, in seconds. Default: 60."
        ],
        "disabled": [
            "| `cache.ttl_seconds` | `60` | Entry time-to-live in seconds. `0` disables caching. Negative values are invalid and cause exit code `2`. |",
            "- `cache.ttl_seconds` — defaults to `60`. Setting it to `0` disables caching. Negative values are invalid and cause the process to exit with code `2`.",
            "- `cache.ttl_seconds` — defaults to 60. Setting it to `0` disables caching. Negative values are invalid and exit with status 2.",
        ],
        "invalid": [
            "- Negative `cache.ttl_seconds` values are invalid; the process exits with code 2."
        ],
        "eviction": ["Eviction is least-recently-used (LRU)."],
        "bypass": [
            "The `--no-cache` flag bypasses both cache reads and cache writes.",
            "- `--no-cache` — bypasses both cache reads and cache writes.",
            "Eviction is least-recently-used (LRU). The `--no-cache` flag bypasses both cache reads and cache writes.",
            "- `--no-cache` bypasses cache reads and cache writes.",
            "--no-cache bypasses both cache reads and cache writes.",
            "- `--no-cache` bypasses both cache reads and cache writes.",
        ],
    },
    "docs-config-loader-keys": {
        "values": [
            "- Allowed values (`__enum`): `plan`, `codex`, `claude-code`; values outside this enum are not valid"
        ],
        "default": [
            "- Default (`__default`): `plan`, applied when the key is not set",
            "- `__default` — `plan`. This is the value applied when the key is unset; the\n  key does have a default, it is not absent.",
            "| Key | Type | Default | Allowed values |\n| `roles.build_pack_default_worker_class` | `_TYPE_STRING` (string) | `plan` | `plan`, `codex`, `claude-code` |",
            "| Key | Type | Default | Allowed values |\n| `roles.build_pack_default_worker_class` | string (`_TYPE_STRING`) | `plan` | `plan`, `codex`, `claude-code` |",
        ],
    },
    "docs-gym-baseline-keys": {
        "bare": [
            "- Unmodelled task: the key is the bare `task_id` — no separator, no suffix.",
            "- When both are falsy, the resolved value is falsy and the task keys bare.",
            "- Unmodelled / no model override: the key is the bare `task_id`.",
            "`verdict_key` treats any falsy model (`None` or empty string) as unmodelled and\nreturns the bare `task_id`. A non-empty model is appended after the separator.",
        ],
        "pinned": [
            "### Model-pinned tasks (model override set)\n<task_id>@<model>",
            "| Model selected by task dispatch or the run-level model | Task ID followed by `@` and the model | `task-a@model-x` |",
            "- **Model-pinned tasks**: When a `model` is provided, the baseline key combines the task ID and model name separated by `@`:",
            "- **Model-pinned additions**: Model-pinned cells introduce keys formatted as `{task_id}@{model}` without altering the structure or validity of unmodelled bare task ID entries in baseline comparisons.",
            '- **Model-pinned tasks**: When a model is specified, the baseline key uses the format `{task_id}@{model}`, joined by the separator `@` (`VERDICT_KEY_SEPARATOR = "@"`).',
        ],
        "migration": [
            "Old baselines for model-pinned tasks need a one-time baseline update."
        ],
    },
    "docs-gym-run-flags": {
        "cohort": [
            "`{task_id}@{model or ''}@effort:{reasoning_level}[@{skill_digest}][@compression={compression}][@attempt={attempt}]`"
        ],
    },
    "docs-retry-behavior-diff": {
        "attempts": [
            "`deliver(send)` attempts to call `send()` up to **3 times** (attempts are\nindexed 0, 1, 2 via `for attempt in range(3)`).",
            "`deliver(send)` calls `send()` up to 3 times.",
            "`deliver(send)` performs at most 3 delivery attempts and takes no configuration options.",
            "Delivery is bounded to a maximum of 3.",
        ],
        "timeout": [
            "- A `TimeoutError` is retried. After the third and final timed-out attempt, the function returns `4`.",
            "- **Error Handling**: Catches only `TimeoutError`. All other exceptions propagate to the caller.",
            "- Only **`TimeoutError`** is caught and retried. Any other exception propagates",
            "Retries are limited to `TimeoutError`:",
            "- On `TimeoutError`, the call is retried until 3 attempts have been made in",
            "- **Retries**: Up to 3 attempts on `TimeoutError` only.",
        ],
        "other": [
            "- Any exception other than `TimeoutError` is not caught: it propagates to the\n  caller.",
            "- Other exceptions: any exception other than `TimeoutError` is not caught. It propagates to the caller right away, and no further attempts are made.",
        ],
        "exit": [
            "- **4** — all 3 attempts raised `TimeoutError` (returned after the failure\n  on attempt index 2, the final attempt).",
            "- If all 3 attempts raise `TimeoutError`, the final (third) timeout returns `4`.",
            "- `4` — the third and final attempt (`attempt == 2`) raised `TimeoutError`.",
            "- Returns **`4`** when the third and final attempt (index 2) raises\n  `TimeoutError`.",
            "- Every path terminates: `0` on success, `4` after three timeouts, or a\n  propagated exception.",
        ],
        "success": [
            "- **0** — `send()` succeeded on some attempt.",
            "- Returns `0` as soon as a `send()` call succeeds.",
            "- `0` — `send` succeeded. Returned as soon as any attempt succeeds, including a\n  success on the second or third attempt after earlier timeouts.",
            "If `send()` succeeds on any attempt, `deliver` returns `0` immediately.",
            "`deliver(send)` calls `send()` up to 3 times and returns 0 on the first success.",
            "- Success: as soon as a call to `send()` returns without raising, `deliver` returns `0`.",
        ],
    },
    "docs-snapshot-new-command": {
        "limit": [
            "| Flag | Type | Default | Description |\n| `--limit N` | integer | `20` | Return at most N rows. |",
            "| Flag | Type | Default | Description |\n| `--limit N` | integer | `20` | Maximum number of rows returned. Rows are sliced after sorting, so this caps the result at the N newest rows. |",
            "- The `limit` parameter of `inspect` defaults to `20`, matching the `--limit` CLI default.",
            "- a function `inspect(rows, limit=20)`.",
            "inspect(rows, limit=20)",
            "| Flag | Type | Default | Description |\n| `--limit LIMIT` | `int` | `20` | Maximum number of rows returned. Parsed with `type=int`; a value that is not a valid integer is an argument error (exit 2). No range validation is performed. |",
        ],
        "json": [
            "| `--json` | boolean flag | off | Store-true flag; enables JSON output when set. |"
        ],
        "readonly": [
            "- The input rows are never mutated.",
            "- The input rows are never mutated; inspection returns a new sorted slice.",
            "- The input `rows` are never mutated; sorting produces a new list.",
            "- The function never mutates `rows`.",
            "- The command inspects snapshot rows; it never mutates them.",
        ],
        "success": [
            "- `0` — successful inspection.",
            "- `0`: inspection succeeded.",
            "| `0` | Successful inspection |",
            "| `0` | Inspection completed successfully. |",
            "- `0` — Inspection completed successfully.",
            "- `0`: Successful inspection.",
            "| `0` | Inspection succeeded. |",
        ],
        "invalid": [
            "- `2` — invalid arguments (for example, a non-integer `--limit` value).",
            "- `2`: invalid arguments.",
            "- `2` — invalid arguments (standard argparse failure).",
            "- `2` — invalid arguments (e.g. a non-integer `--limit`).",
            "| `2` | Invalid arguments |",
            "| `2` | Invalid arguments. |",
            "Exit code `2` is produced by argparse on any argument error, including an\ninvalid integer.",
            "- `2` — invalid arguments.",
            "- `2` — Invalid arguments (e.g., a non-integer value passed to `--limit`).",
            "| `2` | Invalid arguments were supplied (e.g. a non-integer `--limit`). |",
        ],
    },
}

SAVED_FAIL = [
    (
        "docs-cache-config",
        "entries",
        "cache.max_entries defaults to 256. cache.ttl_seconds defaults to 300. Eviction is FIFO.",
    ),
    (
        "docs-cache-config",
        "invalid",
        "- Negative values for `cache.ttl_seconds` are invalid and cause the program to",
    ),
    (
        "docs-config-loader-keys",
        "values",
        "| `roles.build_pack_default_worker_class` | `plan` |",
    ),
    (
        "docs-config-loader-keys",
        "default",
        "| `roles.build_pack_default_worker_class` | (none) |",
    ),
    (
        "docs-gym-baseline-keys",
        "bare",
        "Baselines written before models were selectable contain bare task ids.",
    ),
    ("docs-gym-baseline-keys", "migration", "### Upgrading old baselines"),
    (
        "docs-gym-baseline-keys",
        "migration",
        "- Existing baselines read and compare unchanged with unmodelled runs without requiring data migration or baseline format upgrades.",
    ),
    ("docs-gym-baseline-keys", "pinned", "### Model-pinned tasks"),
    (
        "docs-gym-run-flags",
        "cohort",
        "- With reasoning level and model: `task_id@model@effort:high`",
    ),
    (
        "docs-gym-run-flags",
        "cohort",
        "- Without model: `@effort:<reasoning_level>` (e.g. `@effort:high`).",
    ),
    (
        "docs-retry-behavior-diff",
        "attempts",
        "- **No sleep/backoff** between attempts; retries are immediate.",
    ),
    (
        "docs-retry-behavior-diff",
        "timeout",
        "- `4` — all 3 attempts failed with `TimeoutError`.",
    ),
    (
        "docs-retry-behavior-diff",
        "success",
        "indexed 0, 1, 2 via `for attempt in range(3)`).",
    ),
    (
        "docs-retry-behavior-diff",
        "exit",
        "Delivery retries every exception indefinitely and exits 0 on exhaustion.",
    ),
    ("docs-snapshot-new-command", "limit", "hq snapshot inspect [--limit N] [--json]"),
    (
        "docs-snapshot-new-command",
        "json",
        "| `--json` | flag (no value) | off | Boolean flag; takes no argument. |",
    ),
    ("docs-snapshot-new-command", "success", "| `0` | Empty list. |"),
    ("docs-snapshot-new-command", "success", "- `--limit 0` returns no rows."),
    (
        "docs-snapshot-new-command",
        "invalid",
        "2. The sorted result is truncated to `limit` rows.",
    ),
]


# Each tuple holds equivalent assertions, followed by a contradictory assertion.
FACT_FORMS = {
    "docs-cache-config": {
        "entries": (
            "cache.max_entries defaults to 128.",
            "cache.max_entries defaults to 129.",
        ),
        "ttl": (
            "cache.ttl_seconds defaults to 60.",
            "cache.ttl_seconds defaults to 61.",
        ),
        "disabled": (
            "cache.ttl_seconds=0 disables caching; negative values are "
            "invalid (exit 2).",
            "cache.ttl_seconds=1 disables caching.",
        ),
        "invalid": (
            "cache.ttl_seconds=0 disables caching; negative values are "
            "invalid (exit 2).",
            "Negative values exit with status 3.",
        ),
        "eviction": (
            "Eviction is least-recently-used. --no-cache bypasses reads and writes.",
            "Eviction uses FIFO.",
        ),
        "bypass": (
            "Eviction is least-recently-used. --no-cache bypasses reads and writes.",
            "--no-cache enables reads and writes.",
        ),
    },
    "docs-config-loader-keys": {
        "values": (
            "`roles.build_pack_default_worker_class` accepts `plan`, "
            "`codex`, and `claude-code`.",
            "roles.build_pack_default_worker_class accepts plan and codex.",
        ),
        "default": (
            "The default is `plan`.",
            "| `roles.build_pack_default_worker_class` | `codex` | "
            "plan, codex, claude-code |",
        ),
    },
    "docs-gym-baseline-keys": {
        "bare": (
            "Unmodelled tasks keep the bare task id.",
            "Unmodelled tasks use task@model.",
        ),
        "pinned": (
            "Model-pinned tasks use task@model even without a run override.",
            "Model-pinned tasks use model@task.",
        ),
        "migration": (
            "Old baselines for model-pinned tasks need a one-time baseline update.",
            "Old baselines for model-pinned tasks need no changes.",
        ),
    },
    "docs-gym-run-flags": {
        "levels": (
            "Use --reasoning-level low, medium, high, or xhigh; max "
            "normalizes to xhigh.",
            "--reasoning-level accepts low, medium, high, extreme.",
        ),
        "alias": (
            "Use --reasoning-level low, medium, high, or xhigh; max "
            "normalizes to xhigh.",
            "max normalizes to high.",
        ),
        "omitted": (
            "Omitting --reasoning-level preserves existing keys, slices, "
            "paths and dispatch arguments.",
            "Omitting --reasoning-level changes existing keys.",
        ),
        "cohort": (
            "A leveled verdict uses @effort:<level> after the model, before "
            "the skill digest.",
            "`task@model@<skill-digest>@effort:<level>`",
        ),
    },
    "docs-retry-behavior-diff": {
        "attempts": (
            "Delivery makes at most 3 attempts and retries only TimeoutError.",
            "Delivery makes at most 4 attempts.",
        ),
        "timeout": (
            "Delivery makes at most 3 attempts and retries only TimeoutError.",
            "Delivery retries only ValueError.",
        ),
        "other": (
            "Other exceptions propagate. Exhaustion returns 4; success returns 0.",
            "Any other exception is swallowed.",
        ),
        "exit": (
            "Other exceptions propagate. Exhaustion returns 4; success returns 0.",
            "Exhaustion returns 0.",
        ),
        "success": (
            "Other exceptions propagate. Exhaustion returns 4; success returns 0.",
            "Success returns 4.",
        ),
    },
    "docs-snapshot-new-command": {
        "limit": (
            "hq snapshot inspect --limit defaults to 20.",
            "--limit defaults to 21.",
        ),
        "json": ("--json emits JSON.", "--json emits YAML."),
        "order": (
            "Rows are sorted by created_at descending; inspection is read-only.",
            "Rows are sorted by created_at ascending.",
        ),
        "readonly": (
            "Rows are sorted by created_at descending; inspection is read-only.",
            "Inspection updates rows.",
        ),
        "success": (
            "Exit 0 on success and 2 for invalid arguments.",
            "Success exits with status 2.",
        ),
        "invalid": (
            "Exit 0 on success and 2 for invalid arguments.",
            "Invalid arguments exit with status 0.",
        ),
    },
}

FACT_FORMS = {
    task: {
        fact: (forms[0], *SAVED_PASS.get(task, {}).get(fact, []), forms[-1])
        for fact, forms in facts.items()
    }
    for task, facts in FACT_FORMS.items()
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
            "docs-cache-config",
            "disabled",
            "Disable caching. Another option sets ttl_seconds to 0.",
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


@pytest.mark.parametrize(
    "task_id,fact,text,ok",
    [
        (task, fact, text, True)
        for task, facts in SAVED_PASS.items()
        for fact, texts in facts.items()
        for text in texts
    ]
    + [(task, fact, text, False) for task, fact, text in SAVED_FAIL],
)
def test_saved_submission_assertions(task_id, fact, text, ok):
    from gym.doc_checks import checks

    spec = contract_for(TASKS / task_id)["required_facts"][fact]
    records = checks(text, required_facts={fact: spec}, forbidden_claims={})
    assert next(c["ok"] for c in records if c["name"] == "fact:" + fact) is ok


@pytest.mark.parametrize(
    "task_id,fact,text",
    [
        (
            "docs-cache-config",
            "entries",
            "- cache.max_entries defaults to 129; 128 is the old default.",
        ),
        (
            "docs-cache-config",
            "ttl",
            "- cache.ttl_seconds defaults to 61; 60 is the old default.",
        ),
        (
            "docs-cache-config",
            "disabled",
            "- cache.ttl_seconds=1 disables caching; 0 is another value.",
        ),
        ("docs-cache-config", "entries", "cache.max_entries defaults to -128."),
        (
            "docs-config-loader-keys",
            "default",
            "roles.build_pack_default_worker_class allows plan, codex and claude-code.",
        ),
        (
            "docs-snapshot-new-command",
            "limit",
            "- --limit defaults to 21; 20 is the old default.",
        ),
        ("docs-snapshot-new-command", "limit", "--limit defaults to -20."),
        (
            "docs-retry-behavior-diff",
            "attempts",
            "Delivery makes at most 3.5 attempts.",
        ),
        ("docs-retry-behavior-diff", "attempts", "Delivery makes at most 30 attempts."),
        (
            "docs-retry-behavior-diff",
            "success",
            "- Success returns 4; an earlier return code was 0.",
        ),
        (
            "docs-retry-behavior-diff",
            "exit",
            "- Exhaustion returns 0; an earlier return code was 4.",
        ),
    ],
)
def test_wrong_value_in_same_unit_fails(task_id, fact, text):
    from gym.doc_checks import checks

    spec = contract_for(TASKS / task_id)["required_facts"][fact]
    records = checks(text, required_facts={fact: spec}, forbidden_claims={})
    assert not next(c["ok"] for c in records if c["name"] == "fact:" + fact)
