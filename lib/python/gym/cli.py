"""``hq gym`` CLI: run a suite, diff a baseline, and pin the action space.

Operator surfaces plus the scheduled entrypoint:

  * ``hq gym run [--suite S] [--task T] [--json]`` — grade the selected tasks
    through the real worker pool and print the SPEC §1 summary. The run's setup
    phase first pins the action space: every in-scope eval agent's live tool
    surface must match its committed ``locks/<agent>.lock.json`` or the run
    aborts fail-closed with a per-tool diff, before any task dispatches (GYM-05).
    When a committed baseline exists it also diffs HEAD against it and emits a
    ``gym_regression`` alert for every baseline-pass -> HEAD-fail transition (a
    newly-passing task is logged, never alerted). This is exactly what the
    scheduled launchd one-shot runs (``--suite all``).
  * ``hq gym baseline update`` — **operator-gated** (requires ``--confirm``):
    re-grade the suite and rewrite ``gym-baseline.json`` from the fresh
    verdicts. The scheduled runner never calls this, so it can never rewrite the
    baseline itself (SPEC §7, ticket Don't).
  * ``hq gym lock check`` / ``hq gym lock update`` — inspect or refresh the
    tool-surface locks. ``update`` is the ONLY refresh path; the setup check
    never rewrites a lock (SPEC §3 GYM-05, ticket Don't).

The heavy seams (dispatch source, alerter, surface resolver, clock) are injected
so the unit suite drives the whole CLI with a fake source, a recording alerter,
and a fake resolver — no real worker, no real page, no worker-class registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from gym import (
    alerting,
    grader,
    baseline as baseline_mod,
    host_services,
    hosts,
    library,
    locks as locks_mod,
    outcomes,
    standalone,
    suite as suite_mod,
    taint,
    toc_matrix,
)
from gym.grader import TrajectorySource

# Exit codes: 0 clean, 1 usage/refusal/error, 2 regressions detected+alerted.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REGRESSION = 2

#: `hq dispatch --priority` values; forwarded unchanged to the gym's dispatch.
PRIORITIES = ("low", "normal", "high", "critical")
#: TOC-05 compression arms. `off` is the control and keys like an unlabelled run.
COMPRESSION_ARMS = toc_matrix.ARMS


def _reasoning_level(value: str) -> str:
    try:
        return host_services.current().normalize_reasoning_level(value, field="--reasoning-level")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _model_id(value: str) -> str:
    try:
        return baseline_mod.validate_model_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}")
    return value


def _module_root() -> Path:
    # lib/python/gym/cli.py -> gym -> python -> lib -> module root.
    return Path(__file__).resolve().parents[3]


def shipped_baseline_path() -> Path:
    """The baseline committed to the repo.

    Read-only seed. `hq gym baseline update` must never write here -- see
    `default_baseline_path`.
    """
    return _module_root() / "gym-baseline.json"


def default_baseline_path() -> Path:
    """Where the ESTABLISHED baseline lives: host state, not the repo.

    An established baseline is per-host by design. The repo copy is a read-only
    seed, and AMF-08 established its first-green baseline. Future
    `hq gym baseline update` invocations still write to host state, not back to
    the checkout.

    But the old default wrote back into
    `modules/adversarial-eval-gym/gym-baseline.json`, which is git-tracked and
    not ignored, inside the deploy checkout. Establishing a baseline therefore
    left the checkout dirty and main-catchup refused every subsequent deploy
    with `freezeClass: non-absorbable-dirt`. Observed twice on 2026-08-24: the
    suite went green 3/3, the baseline was written, deploys stopped, and the
    only way to unfreeze was to throw the baseline away.

    Before the host-state split, you could have an established baseline or a
    working deploy pipeline, not both. Host state does not belong in the repo
    working tree: it lives in the host's data directory (Agent OS:
    ``$HQ_ROOT/gym``; standalone: the paddock data directory).
    """
    return host_services.current().data_dir() / "gym-baseline.json"


def default_locks_dir() -> Path:
    return locks_mod.default_locks_dir()


def default_run_records() -> host_services.RunRecordSink:
    """The host's run-record sink (Agent OS: ``$HQ_ROOT/gym/results``)."""
    return host_services.current().run_records()


def _store_model_result(
    result: suite_mod.SuiteResult,
    model: str | None,
    worker_class: str | None,
    results: Path | host_services.RunRecordSink,
    reasoning_level: str | None = None,
    target: Path | None = None,
    concurrency: int = 1,
) -> Path:
    """Keep each model run in host state, separate from the established baseline.

    ``results`` is a run-record sink, or a directory for a local one
    (``--results-dir``). Each cohort gets its own bucket.
    """
    sink = host_services.LocalRunRecords(results) if isinstance(results, Path) else results
    model = _effective_result_model(result, model)
    cohort = baseline_mod.cohort_key(model, reasoning_level)
    bucket = hashlib.sha256(cohort.encode("utf-8")).hexdigest() if cohort is not None else "default"
    document = {
        "model": model,
        "worker_class": worker_class,
        "concurrency": concurrency,
        "summary": result.summary_dict(),
        "verdicts": result.verdicts(),
        "task_versions": result.verdict_versions(),
    }
    if reasoning_level is not None:
        document["reasoning_level"] = reasoning_level
    return sink.write(document, bucket=bucket, target=target)


def _default_source(
    *,
    model: str | None = None,
    worker_class: str | None = None,
    priority: str | None = None,
    reasoning_level: str | None = None,
    compression: str | None = None,
    runner: str | None = None,
    harness: str | None = None,
    image: str | None = None,
) -> TrajectorySource:
    # Mode resolution (PDK-01): --runner, else PADDOCK_RUNNER, else the user
    # config, else an available host plugin (Agent OS: the worker pool), else
    # the standalone Docker runner. bin/hq-gym pins PADDOCK_RUNNER=agent-os.
    return hosts.resolve_source(
        runner,
        model=model,
        worker_class=worker_class,
        priority=priority,
        reasoning_level=reasoning_level,
        compression=compression,
        harness=harness,
        image=image,
    )


def _add_runner_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runner",
        default=None,
        help=(
            "Where cells run: 'standalone' (a harness CLI in Docker) or a host plugin "
            "such as 'agent-os' (the worker pool). Default: PADDOCK_RUNNER, then the "
            "user config's runner, then an available host, then standalone."
        ),
    )
    parser.add_argument(
        "--harness",
        default=None,
        help="Standalone runner only: claude-code, codex, opencode or pi.",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Standalone runner only: a local Docker image that has the harness CLI.",
    )


#: A runner that cannot be resolved or configured is a usage error, not a crash.
_RUNNER_ERRORS = (hosts.RunnerResolutionError, standalone.StandaloneConfigError)


def _runner_options(args: argparse.Namespace) -> dict[str, Any]:
    """The runner flags that were given; absent ones keep each source's defaults."""
    options = {key: getattr(args, key, None) for key in ("runner", "harness", "image")}
    return {key: value for key, value in options.items() if value is not None}


def _default_resolver() -> locks_mod.ToolSurfaceResolver:
    # The host's lock provider (Agent OS: the worker-class registry, read lazily
    # inside the resolver so the core stays importable without the worker pool).
    return host_services.current().tool_surfaces()


def _default_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _baseline_date(generated_at: str | None) -> str | None:
    if not generated_at:
        return None
    return generated_at.split("T", 1)[0]


def cmd_run(
    args: argparse.Namespace,
    *,
    source: TrajectorySource,
    alerter: alerting.Alerter,
    resolver: locks_mod.ToolSurfaceResolver,
    out: TextIO,
) -> int:
    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else suite_mod.default_tasks_dir()
    locks_dir = Path(args.locks_dir) if args.locks_dir else default_locks_dir()
    suite_selector = args.suite or suite_mod.ALL_SUITE
    try:
        tasks = suite_mod.discover_tasks(tasks_dir, suite=suite_selector, task=args.task)
    except suite_mod.SuiteError as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR

    result_path: Path | None = None
    results: Path | host_services.RunRecordSink = (
        Path(args.results_dir) if args.results_dir else default_run_records()
    )

    def checkpoint(result: suite_mod.SuiteResult) -> None:
        nonlocal result_path
        result_path = _store_model_result(
            result,
            args.model,
            args.worker_class,
            results,
            getattr(args, "reasoning_level", None),
            target=result_path,
            concurrency=getattr(args, "concurrency", 1),
        )

    # Setup-phase action-space pinning: a drifted or unlocked eval-agent surface
    # aborts here, before run_suite dispatches anything (fail-closed, SPEC §6).
    try:
        result = suite_mod.run_suite(
            tasks,
            source,
            suite=suite_selector,
            taint_mode=args.taint_mode,
            resolver=resolver,
            locks_dir=locks_dir,
            model=getattr(args, "model", None),
            worker_class=getattr(args, "worker_class", None),
            reasoning_level=getattr(args, "reasoning_level", None),
            compression=getattr(args, "compression", None),
            attempt=getattr(args, "attempt", None),
            on_progress=checkpoint,
            concurrency=getattr(args, "concurrency", 1),
        )
    except (locks_mod.LockError, OSError) as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR

    alerting.emit_level_unresolved(alerter, suite=result.suite, count=result.level_unresolved)

    baseline_path = Path(args.baseline) if args.baseline else default_baseline_path()
    # A host that has never established a baseline reads the shipped
    # unestablished one, so the run still reports "unestablished; run
    # `hq gym baseline update`" instead of silently skipping the comparison.
    read_path = baseline_path
    if not args.baseline and not read_path.exists():
        read_path = shipped_baseline_path()
    report: baseline_mod.RegressionReport | None = None
    baseline_date: str | None = None
    if not args.no_compare and read_path.exists():
        try:
            loaded = baseline_mod.load_baseline(read_path)
            baseline_date = _baseline_date(loaded.generated_at)
            report = baseline_mod.compare(
                loaded,
                result.verdicts(include_held_failures=True),
                slice_model=baseline_mod.cohort_key(
                    args.model, getattr(args, "reasoning_level", None)
                ),
                head_versions=result.verdict_versions(),
            )
            if report.established:
                alerting.emit_regressions(
                    report,
                    alerter,
                    suite=result.suite,
                    baseline_ref=baseline_path.name,
                    baseline_date=baseline_date,
                )
        except baseline_mod.BaselineError as exc:
            print(f"[hq gym] {exc}", file=sys.stderr)
            return EXIT_ERROR

    if args.json:
        payload = result.summary_dict()
        if result_path is not None:
            payload["model"] = args.model
            payload["worker_class"] = args.worker_class
            payload["result_path"] = str(result_path)
        if report is not None:
            payload["regression"] = {
                "baseline": baseline_path.name,
                "baseline_date": baseline_date,
                "established": report.established,
                "regressions": [
                    {
                        "task": r.task,
                        "baseline_verdict": r.baseline_verdict,
                        "head_verdict": r.head_verdict,
                    }
                    for r in report.regressions
                ],
                "new_passes": [
                    {"task": np.task, "baseline_verdict": np.baseline_verdict}
                    for np in report.new_passes
                ],
                "version_changed": list(report.version_changed),
            }
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    else:
        _print_scheduled_report(result, report, baseline_path, baseline_date, out=out)
        if result_path is not None:
            print(f"  results stored under model={args.model}: {result_path}", file=out)

    if report is not None and report.has_regressions:
        return EXIT_REGRESSION
    if result.has_agent_errors:
        return EXIT_ERROR
    return EXIT_OK


def _print_scheduled_report(
    result: suite_mod.SuiteResult,
    report: baseline_mod.RegressionReport | None,
    baseline_path: Path,
    baseline_date: str | None,
    *,
    out: TextIO,
) -> None:
    print(
        f"[gym-scheduled] suite={result.suite}  {len(result.graded)} tasks  "
        f"{result.passed} passed  {result.failed} failed  {result.errored} errors  "
        f"{result.refused} refused (not measured)  "
        f"{result.level_mismatches} level mismatches  "
        f"{result.level_unresolved} unresolved levels",
        file=out,
    )
    for graded in result.graded:
        cell = graded.cell
        if cell.outcome == outcomes.AGENT_ERROR:
            reason = cell.reason or "agent error without diagnostic"
            session = f" session={cell.runtime_session_id}" if cell.runtime_session_id else ""
            print(f"  AGENT ERROR: {cell.task_id}{session}: {reason}", file=out)
    if report is None:
        print(f"  baseline: {baseline_path.name} (not compared)", file=out)
        return
    if not report.established:
        print(
            f"  baseline: {baseline_path.name} (unestablished; run "
            "`hq gym baseline update` after the first green suite)",
            file=out,
        )
        return
    for regression in report.regressions:
        print(
            f"  REGRESSION: {regression.task} "
            f"{regression.baseline_verdict}@baseline -> {regression.head_verdict}@HEAD",
            file=out,
        )
    for new_pass in report.new_passes:
        print(
            f"  NEW PASS: {new_pass.task} "
            f"{new_pass.baseline_verdict}@baseline -> passed@HEAD (logged, not alerted)",
            file=out,
        )
    for task in report.version_changed:
        print(
            f"  VERSION CHANGED: {task} task_version differs from baseline (not compared)",
            file=out,
        )
    dated = f" ({baseline_date})" if baseline_date else ""
    alert_note = (
        f"  ->  alert: {alerting.GYM_REGRESSION_ALERT_KEY}"
        if report.has_regressions
        else ("  ->  non-clean: agent error" if result.has_agent_errors else "  ->  no regression")
    )
    print(f"  baseline: {baseline_path.name}{dated}{alert_note}", file=out)


def cmd_baseline_update(
    args: argparse.Namespace,
    *,
    source: TrajectorySource,
    resolver: locks_mod.ToolSurfaceResolver,
    now: str,
    out: TextIO,
) -> int:
    if not args.confirm:
        print(
            "[hq gym] refusing to rewrite the baseline without --confirm.\n"
            "         `hq gym baseline update` is operator-gated; the scheduled "
            "runner never rewrites the baseline (SPEC §7).",
            file=sys.stderr,
        )
        return EXIT_ERROR

    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else suite_mod.default_tasks_dir()
    locks_dir = Path(args.locks_dir) if args.locks_dir else default_locks_dir()
    suite_selector = suite_mod.ALL_SUITE
    try:
        tasks = suite_mod.discover_tasks(tasks_dir, suite=suite_selector)
    except suite_mod.SuiteError as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR

    # A baseline must never be re-cut against a drifted action space, so the same
    # setup-phase lock check gates the update too.
    try:
        result = suite_mod.run_suite(
            tasks,
            source,
            suite=suite_selector,
            taint_mode=args.taint_mode,
            resolver=resolver,
            locks_dir=locks_dir,
            model=getattr(args, "model", None),
            worker_class=getattr(args, "worker_class", None),
            reasoning_level=getattr(args, "reasoning_level", None),
        )
    except locks_mod.LockError as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR
    if result.failed or result.errored:
        print(
            "[hq gym] refusing to establish baseline from a non-green suite: "
            f"{result.passed} passed, {result.failed} failed, {result.errored} errors",
            file=sys.stderr,
        )
        return EXIT_ERROR
    baseline_path = Path(args.baseline) if args.baseline else default_baseline_path()
    existing: baseline_mod.Baseline | None = None
    if baseline_path.exists():
        try:
            existing = baseline_mod.load_baseline(baseline_path)
        except baseline_mod.BaselineError as exc:
            print(f"[hq gym] {exc}", file=sys.stderr)
            return EXIT_ERROR
    document = baseline_mod.merge_model_slice(
        existing,
        result.verdicts(),
        model=baseline_mod.cohort_key(args.model, getattr(args, "reasoning_level", None)),
        generated_at=now,
        verdict_slices=result.verdict_slices(),
        verdict_versions=result.verdict_versions(),
    )
    baseline_mod.write_baseline(baseline_path, document)

    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True), file=out)
    else:
        print(
            f"[hq gym] baseline updated: {baseline_path} "
            f"({len(document['verdicts'])} tasks, generated_at {now})",
            file=out,
        )
        for task_id, verdict in document["verdicts"].items():
            print(f"  {task_id}: {verdict}", file=out)
        print(
            "  Host-state baseline is now the last-known-good baseline for this machine.",
            file=out,
        )
    return EXIT_OK


def _lockable_agents(args: argparse.Namespace, tasks_dir: Path) -> list[str]:
    """Agents to (re)lock: an explicit ``--agent``, else every task persona."""
    if args.agent:
        return [args.agent]
    tasks = suite_mod.discover_tasks(tasks_dir, suite=suite_mod.ALL_SUITE)
    return sorted({t.persona for t in tasks if t.persona})


def cmd_lock_update(
    args: argparse.Namespace,
    *,
    resolver: locks_mod.ToolSurfaceResolver,
    out: TextIO,
) -> int:
    """Rewrite ``locks/<agent>.lock.json`` from the live surface (operator-run).

    The ONLY refresh path: the setup check never rewrites a lock. With ``--agent``
    it refreshes one agent; otherwise it refreshes every persona in the corpus,
    skipping (with a note) any whose live surface is not yet resolvable (e.g. a
    pre-CTX ``resident`` that is not a real worker class).
    """
    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else suite_mod.default_tasks_dir()
    locks_dir = Path(args.locks_dir) if args.locks_dir else default_locks_dir()
    try:
        agents = _lockable_agents(args, tasks_dir)
    except suite_mod.SuiteError as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR

    written: list[tuple[str, list[str]]] = []
    skipped: list[tuple[str, str]] = []
    for agent in agents:
        try:
            tools = locks_mod.update_lock(agent, resolver, locks_dir)
        except locks_mod.ToolSurfaceUnavailable as exc:
            skipped.append((agent, str(exc)))
            continue
        written.append((agent, tools))

    if args.json:
        print(
            json.dumps(
                {
                    "locks_dir": str(locks_dir),
                    "written": {agent: tools for agent, tools in written},
                    "skipped": {agent: reason for agent, reason in skipped},
                },
                indent=2,
                sort_keys=True,
            ),
            file=out,
        )
    else:
        for agent, tools in written:
            print(
                f"[hq gym] locked {agent}: {locks_mod.lock_path(locks_dir, agent).name} "
                f"({len(tools)} tools: {', '.join(tools)})",
                file=out,
            )
        for agent, reason in skipped:
            print(f"[hq gym] skipped {agent}: {reason}", file=out)
        if written:
            print("  Commit the updated lock file(s) to pin the new action space.", file=out)
    # An explicit --agent that could not be resolved is a hard error, not a note.
    if args.agent and not written:
        return EXIT_ERROR
    return EXIT_OK


def cmd_lock_check(
    args: argparse.Namespace,
    *,
    resolver: locks_mod.ToolSurfaceResolver,
    out: TextIO,
) -> int:
    """Diagnostic: run the setup-phase lock check without dispatching anything."""
    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else suite_mod.default_tasks_dir()
    locks_dir = Path(args.locks_dir) if args.locks_dir else default_locks_dir()
    suite_selector = args.suite or suite_mod.ALL_SUITE
    try:
        tasks = suite_mod.discover_tasks(tasks_dir, suite=suite_selector, task=args.task)
    except suite_mod.SuiteError as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR

    mode = args.taint_mode if args.taint_mode is not None else taint.load_live_taint_mode()
    agents = suite_mod.in_scope_agents(
        tasks, mode, model=args.model, worker_class=args.worker_class
    )
    try:
        locks_mod.check_surface_locks(agents, resolver, locks_dir)
    except locks_mod.LockError as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return EXIT_ERROR

    scope = ", ".join(agents) if agents else "(no in-scope agents)"
    print(f"[hq gym] tool-surface locks OK for {scope}", file=out)
    return EXIT_OK


def cmd_show(args, *, out: TextIO) -> int:
    from gym import fix_audit

    try:
        run = Path(args.run)
        if not run.is_file():
            if run.name != args.run or run.name in {".", ".."}:
                raise ValueError("run must be a result file or run id")
            matches = default_run_records().find(run.stem)
            if len(matches) != 1:
                raise ValueError("run id missing or ambiguous; supply result path")
            run = matches[0]
        document = json.loads(run.read_text())
        cells = document["summary"]["cells"]
        selected = [
            cell
            for index, cell in enumerate(cells)
            if args.cell in {str(index), cell.get("launchRequestId"), cell.get("task")}
            and (args.task is None or cell.get("task") == args.task)
        ]
        if len(selected) != 1 or "fix_audit" not in selected[0]:
            raise ValueError("select exactly one cell with a fix audit artifact")
        certificate = args.certificate or os.environ.get("GYM_FIX_AUDIT_CERT")
        key = args.key or os.environ.get("GYM_FIX_AUDIT_KEY")
        if not certificate or not key:
            raise ValueError("operator certificate and private key required")
        payload = fix_audit.decrypt(selected[0]["fix_audit"], certificate, key)
        print(json.dumps({k: v for k, v in payload.items() if k != "files"}, indent=2), file=out)
        for entry in payload["files"]:
            print(f"File: {entry['file']} (truncated={entry.get('truncated', False)})", file=out)
            print(entry.get("diff", "submission missing"), file=out)
        return EXIT_OK
    except (OSError, ValueError, KeyError, grader.GraderError) as exc:
        print(f"[hq gym] audit unavailable: {exc}", file=out)
        return EXIT_ERROR


def build_parser(prog: str = "hq gym") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Adversarial Eval Gym runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="Decrypt a fix-cell audit using operator-only keys.")
    show.add_argument("run")
    show.add_argument("--cell", required=True, help="Zero-based index, launch id, or task id.")
    show.add_argument("--task")
    show.add_argument("--certificate")
    show.add_argument("--key", help="Operator-only private key path; never available to workers.")

    run = sub.add_parser("run", help="Run a suite and diff it against the committed baseline.")
    run.add_argument("--suite", choices=sorted(suite_mod.SELECTABLE_SUITES), default=None)
    run.add_argument(
        "--concurrency",
        type=_positive_int,
        default=1,
        help="Maximum simultaneous tasks; use for per-hour endpoints (default: 1).",
    )
    run.add_argument("--task", default=None, help="Run a single task id (across any suite).")
    run.add_argument("--json", action="store_true", help="Emit the SPEC §1 summary JSON.")
    run.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    run.add_argument("--baseline", default=None, help=argparse.SUPPRESS)
    run.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)
    run.add_argument("--results-dir", default=None, help=argparse.SUPPRESS)
    run.add_argument("--no-compare", action="store_true", help="Skip the baseline diff.")
    run.add_argument(
        "--model",
        type=_model_id,
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
            "TOC-05 arm for this launch only: forwarded as hq dispatch "
            "--tool-output-compression, so the worker runs this arm instead of the "
            "host worker_pool.tool_output_compression config, and keys the cohort. "
            "rtk is refused (RTK-ELIGIBILITY.md)."
        ),
    )
    run.add_argument(
        "--attempt",
        type=_positive_int,
        default=None,
        help="TOC-05 repeat number, so repeats of one cell never overwrite each other.",
    )
    run.add_argument("--taint-mode", default=None, help=argparse.SUPPRESS)
    _add_runner_arguments(run)

    baseline = sub.add_parser("baseline", help="Inspect or update the committed baseline.")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    update = baseline_sub.add_parser(
        "update", help="Operator-gated: update this model's gym-baseline.json slice."
    )
    update.add_argument("--confirm", action="store_true", help="Required to rewrite the baseline.")
    update.add_argument("--json", action="store_true")
    update.add_argument(
        "--model", type=_model_id, default=None, help="Record verdicts for this model."
    )
    update.add_argument(
        "--worker-class", dest="worker_class", default=None, help="Dispatch through this class."
    )
    update.add_argument("--priority", choices=PRIORITIES, default=None, help="Dispatch priority.")
    update.add_argument(
        "--reasoning-level",
        type=_reasoning_level,
        default=None,
        help="Requested dispatch reasoning level.",
    )
    update.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    update.add_argument("--baseline", default=None, help=argparse.SUPPRESS)
    update.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)
    update.add_argument("--taint-mode", default=None, help=argparse.SUPPRESS)
    _add_runner_arguments(update)

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
        "--model", type=_model_id, default=None, help="Check the class selected for this model."
    )
    lock_check.add_argument(
        "--worker-class",
        dest="worker_class",
        default=None,
        help="Check this override class where tasks do not pin one.",
    )
    lock_check.add_argument(
        "--reasoning-level",
        type=_reasoning_level,
        default=None,
        help=(
            "Validate the level's spelling (as run and baseline update do). Harness "
            "support is not checked here; a run at an unsupported level fails its cells."
        ),
    )
    lock_check.add_argument("--tasks-dir", default=None, help=argparse.SUPPRESS)
    lock_check.add_argument("--locks-dir", default=None, help=argparse.SUPPRESS)
    lock_check.add_argument("--taint-mode", default=None, help=argparse.SUPPRESS)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    source: TrajectorySource | None = None,
    alerter: alerting.Alerter | None = None,
    resolver: locks_mod.ToolSurfaceResolver | None = None,
    now: str | None = None,
    out: TextIO | None = None,
    prog: str = "hq gym",
) -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    out = out or sys.stdout

    if args.command == "show":
        return cmd_show(args, out=out)

    if args.command == "run" or (args.command == "baseline" and args.baseline_command == "update"):
        # Pin one gym-library version before any cell dispatches (GYMSKEW-02).
        try:
            library.preload_library()
        except library.GymLibraryChanged as exc:
            print(f"[hq gym] {exc}", file=sys.stderr)
            return EXIT_ERROR

    if args.command == "run":
        omitted = toc_matrix.OMITTED_ARM_REASONS.get(getattr(args, "compression", None) or "")
        if omitted:
            print(f"[hq gym] --compression {args.compression} refused: {omitted}", file=sys.stderr)
            return EXIT_ERROR
        try:
            resolved_source = (
                source
                if source is not None
                else _default_source(
                    model=getattr(args, "model", None),
                    worker_class=getattr(args, "worker_class", None),
                    priority=getattr(args, "priority", None),
                    reasoning_level=getattr(args, "reasoning_level", None),
                    compression=getattr(args, "compression", None),
                    **_runner_options(args),
                )
            )
        except _RUNNER_ERRORS as exc:
            print(f"[hq gym] {exc}", file=sys.stderr)
            return EXIT_ERROR
        resolved_alerter = alerter if alerter is not None else host_services.current().alerter()
        resolved_resolver = resolver if resolver is not None else _default_resolver()
        return cmd_run(
            args,
            source=resolved_source,
            alerter=resolved_alerter,
            resolver=resolved_resolver,
            out=out,
        )

    if args.command == "baseline" and args.baseline_command == "update":
        try:
            resolved_source = (
                source
                if source is not None
                else _default_source(
                    model=getattr(args, "model", None),
                    worker_class=getattr(args, "worker_class", None),
                    priority=getattr(args, "priority", None),
                    reasoning_level=getattr(args, "reasoning_level", None),
                    **_runner_options(args),
                )
            )
        except _RUNNER_ERRORS as exc:
            print(f"[hq gym] {exc}", file=sys.stderr)
            return EXIT_ERROR
        resolved_resolver = resolver if resolver is not None else _default_resolver()
        resolved_now = now if now is not None else _default_now()
        return cmd_baseline_update(
            args,
            source=resolved_source,
            resolver=resolved_resolver,
            now=resolved_now,
            out=out,
        )

    if args.command == "lock":
        resolved_resolver = resolver if resolver is not None else _default_resolver()
        if args.lock_command == "update":
            return cmd_lock_update(args, resolver=resolved_resolver, out=out)
        if args.lock_command == "check":
            return cmd_lock_check(args, resolver=resolved_resolver, out=out)

    parser.error(f"unknown command {args.command!r}")
    return EXIT_ERROR  # pragma: no cover - argparse exits first


def _effective_result_model(result: suite_mod.SuiteResult, model: str | None) -> str | None:
    effective_models = {
        c.execution_provenance.get("model") for c in result.cells if c.execution_provenance
    }
    if model is None and len(effective_models) == 1:
        return effective_models.pop()
    return model


def console_main() -> int:
    """The ``paddock`` console script of the agentpaddock distribution (PDK-05)."""
    return main(prog="paddock")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
