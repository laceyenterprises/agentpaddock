#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/surface.py" <<'RFD_INPUT'
# Historical CLI excerpts (documentation input, not a standalone runner).
def _reasoning_level(value: str) -> str:
    from cwp_dispatch.reasoning_level import normalize_reasoning_level

    try:
        return normalize_reasoning_level(value, field="--reasoning-level")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hq gym", description="Adversarial Eval Gym runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a suite and diff it against the committed baseline.")
    run.add_argument("--suite", choices=sorted(suite_mod.SELECTABLE_SUITES), default=None)
    run.add_argument("--task", default=None, help="Run a single task id (across any suite).")
    run.add_argument("--json", action="store_true", help="Emit the SPEC §1 summary JSON.")
    run.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    run.add_argument("--baseline", default=None, help=argparse.SUPPRESS)
    run.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)
    run.add_argument("--results-dir", default=None, help=argparse.SUPPRESS)
    run.add_argument("--no-compare", action="store_true", help="Skip the baseline diff.")
    run.add_argument(
        "--model",
        default=None,
        help=(
            "Grade the suite against this model instead of the worker-class default. "
            "Verdicts are recorded per (task, model), so a sweep does not overwrite "
            "another model's results. Tasks that pin their own model keep it."
        ),
    )
    run.add_argument(
        "--worker-class",
        dest="worker_class",
        default=None,
        help="Dispatch through this worker class (e.g. oss-local) where a task does not pin one.",
    )
    run.add_argument(
        "--reasoning-level",
        type=_reasoning_level,
        default=None,
        help="Requested dispatch reasoning level (low, medium, high, xhigh; max aliases xhigh).",
    )
    run.add_argument(
        "--priority",
        choices=PRIORITIES,
        default=None,
        help="Dispatch priority forwarded to hq dispatch (TOC-05 runs its matrix at low).",
    )
    run.add_argument(
        "--compression",
        choices=COMPRESSION_ARMS,
        default=None,
        help=(
            "TOC-05 arm label for the cohort key. It does not select the backend: "
            "the worker reads worker_pool.tool_output_compression from host config."
        ),
    )
    run.add_argument(
        "--attempt",
        type=_positive_int,
        default=None,
        help="TOC-05 repeat number, so repeats of one cell never overwrite each other.",
    )
    run.add_argument("--taint-mode", default=None, help=argparse.SUPPRESS)

    baseline = sub.add_parser("baseline", help="Inspect or update the committed baseline.")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    update = baseline_sub.add_parser(
        "update", help="Operator-gated: update this model's gym-baseline.json slice."
    )
    update.add_argument("--confirm", action="store_true", help="Required to rewrite the baseline.")
    update.add_argument("--json", action="store_true")
    update.add_argument("--model", default=None, help="Record verdicts for this model.")
    update.add_argument(
        "--worker-class", dest="worker_class", default=None, help="Dispatch through this class."
    )
    update.add_argument("--priority", choices=PRIORITIES, default=None, help="Dispatch priority.")
    update.add_argument(
        "--reasoning-level", type=_reasoning_level, default=None,
        help="Requested dispatch reasoning level.",
    )
    update.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    update.add_argument("--baseline", default=None, help=argparse.SUPPRESS)
    update.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)
    update.add_argument("--taint-mode", default=None, help=argparse.SUPPRESS)

    lock = sub.add_parser("lock", help="Inspect or refresh the eval-agent tool-surface locks.")
    lock_sub = lock.add_subparsers(dest="lock_command", required=True)

    lock_update = lock_sub.add_parser(
        "update", help="Rewrite a lock from the live tool surface (the only refresh path)."
    )
    lock_update.add_argument(
        "--agent", default=None, help="Refresh one agent (default: every corpus persona)."
    )
    lock_update.add_argument("--json", action="store_true")
    lock_update.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    lock_update.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)

    lock_check = lock_sub.add_parser(
        "check", help="Verify in-scope agent surfaces match their locks (no dispatch)."
    )
    lock_check.add_argument("--suite", choices=sorted(suite_mod.SELECTABLE_SUITES), default=None)
    lock_check.add_argument("--task", default=None, help="Check a single task id's persona.")
    lock_check.add_argument(
        "--model", default=None, help="Check the class selected for this model."
    )
    lock_check.add_argument(
        "--worker-class",
        dest="worker_class",
        default=None,
        help="Check this override class where tasks do not pin one.",
    )
    lock_check.add_argument(
        "--reasoning-level", type=_reasoning_level, default=None,
        help="Validate the requested reasoning level for this cohort.",
    )
    lock_check.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    lock_check.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)
    lock_check.add_argument("--taint-mode", default=None, help=argparse.SUPPRESS)
    return parser



# Historical baseline.py cohort excerpts:
VERDICT_KEY_SEPARATOR = "@"
EFFORT_KEY_MARKER = "effort:"
COMPRESSION_KEY_MARKER = "compression="
ATTEMPT_KEY_MARKER = "attempt="
COMPRESSION_OFF = "off"
def cohort_key(model: str | None, reasoning_level: str | None = None) -> str | None:
    """CLI sweep identity; an unlevelled sweep retains its original slice."""
    if reasoning_level is None:
        return model
    return f"{model or ''}@{EFFORT_KEY_MARKER}{reasoning_level}"


def verdict_key(
    task_id: str,
    model: str | None = None,
    skill_digest: str | None = None,
    compression: str | None = None,
    attempt: int | None = None,
    reasoning_level: str | None = None,
) -> str:
    """Baseline key for one (task, model, skill digest, compression, attempt) cohort cell.

    A run with no model override keeps the bare task id, so every baseline
    written before models were selectable still reads and compares unchanged.
    A run with no skill digest, no compression (or ``off``), and no attempt
    likewise preserves the old key.
    """
    tail: list[str] = []
    if reasoning_level:
        tail.append(f"{EFFORT_KEY_MARKER}{reasoning_level}")
    if skill_digest:
        tail.append(skill_digest)
    if compression and compression != COMPRESSION_OFF:
        tail.append(f"{COMPRESSION_KEY_MARKER}{compression}")
    if attempt is not None:
        tail.append(f"{ATTEMPT_KEY_MARKER}{int(attempt)}")
    if not tail:
        if not model:
            return task_id
        return f"{task_id}{VERDICT_KEY_SEPARATOR}{model}"
    return VERDICT_KEY_SEPARATOR.join((task_id, model or "", *tail))


RFD_INPUT
cat >"$sandbox_root/guide.md" <<'RFD_INPUT'
## Run
```bash
hq gym run --suite injection --json        # SPEC §1 summary shape
hq gym run --suite all                      # what the scheduled one-shot runs
hq gym run --suite coding --worker-class oss-codex --model litellm-local-openai/glm-5.2
hq gym baseline update --confirm            # operator-gated; updates the full current model slice
```

A model run stores a JSON record under `$HQ_ROOT/gym/results/<model-hash>/`,
including the exact model ID and model-qualified verdict keys. The command
prints the result path; it does not update the established baseline. Before
dispatch, the lock check uses the worker class the task will actually run on,
including `--worker-class` overrides. The four OSS harness classes and
`oss-local` each have a committed lock in `locks/`; a missing or drifted class
lock aborts the suite.

`gym-baseline.json` now ships with the AMF-08 first-green baseline established.
The operator runs `hq gym baseline update --confirm` whenever the last-known-good
verdict set is deliberately accepted. The scheduled runner never rewrites the
baseline — `baseline update` requires `--confirm`, and the plist only ever calls
`run`.
Baseline updates always sweep `--suite all`; partial-suite updates are rejected
so a narrowed run cannot garbage-collect another suite's last-known-good verdicts.
The baseline stores flat verdict keys plus a `slices` map. A `null` slice is the
default suite, including task-level model pins that use `task@model` keys; a
model string is only a CLI-level `--model` sweep slice. `compare` and
`baseline update` use `slices` as the authority, so a default run does not trip
over old sweep keys and a default update garbage-collects deleted pinned tasks.
When `--model` is used for a sweep, baseline update replaces only keys whose
slice is that model: deleted tasks for the swept model are garbage-collected,
task-pinned cells are not refreshed by the sweep, and other model slices remain
intact.
RFD_INPUT
