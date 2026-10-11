"""Suite discovery + run: many tasks -> cells + a §1-shaped summary.

GYM-01 grades a single cell; GYM-04 needs to run a *suite* (``injection`` /
``rca`` / ``all``) and roll the cells into the summary shape from SPEC §1. This
module is that runner. It reuses the GYM-01 grader and the GYM-03 taint gate
verbatim, so it dispatches nothing itself — an injected
:class:`~gym.grader.TrajectorySource` (real ``WorkerPoolTrajectorySource`` in
production, a fake in tests) is what decides whether a real worker spawns.

Injection tasks go through :func:`gym.taint.gate_injection_run`, so a task that
cannot prove resistance (pre-CTX / shadow) short-circuits to a non-passing gate
outcome *without* dispatching a worker — the SPEC §6 "no false assurance"
mitigation carries straight into the scheduled run.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from contextvars import copy_context
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable

from gym import baseline, grader, library, locks, outcomes, taint
from gym import metrics as metrics_mod
from gym.grader import Cell, TrajectorySource, economics_view
from gym.schema import SUITES, Task, is_fix_family, load_task

# The run-time suite selector. "all" is not a task-level membership value — it
# selects every task regardless of its declared suite (SPEC §10).
ALL_SUITE = "all"

# Suites a caller may select at run time: every corpus suite plus "all".
SELECTABLE_SUITES: frozenset[str] = SUITES | {ALL_SUITE}

#: ``WorkerPoolTrajectorySource`` terminal status when the poll deadline passed.
TIMEOUT_TERMINAL_STATUS = "timeout"

_GRADER_NOTE = "out-of-band; verifiers never entered the agent sandbox"


class SuiteError(ValueError):
    """Raised when a suite selection resolves to no runnable tasks."""


def default_tasks_dir() -> Path:
    """The shipped corpus root: ``modules/adversarial-eval-gym/tasks``."""
    # lib/python/gym/suite.py -> gym -> python -> lib -> module root.
    return Path(__file__).resolve().parents[3] / "tasks"


def discover_tasks(
    tasks_dir: str | Path,
    *,
    suite: str | None = None,
    task: str | None = None,
) -> list[Task]:
    """Load every ``tasks/*/task.toml`` matching the selection, sorted by id.

    ``suite`` filters by declared membership (``"all"`` / ``None`` keeps all).
    ``task`` selects a single task id across every suite. Ordering is by id so a
    suite run — and therefore the baseline it produces — is deterministic.
    """
    tasks_dir = Path(tasks_dir)
    if suite is not None and suite not in SELECTABLE_SUITES:
        raise SuiteError(f"unknown suite {suite!r}; choose one of {sorted(SELECTABLE_SUITES)}")

    found: list[Task] = []
    for toml_path in sorted(tasks_dir.glob("*/task.toml")):
        loaded = load_task(toml_path.parent)
        if task is not None and loaded.id != task:
            continue
        if suite is not None and suite != ALL_SUITE and loaded.suite != suite:
            continue
        found.append(loaded)
    found.sort(key=lambda t: t.id)

    if task is not None and not found:
        raise SuiteError(f"no task with id {task!r} under {tasks_dir}")
    if task is None and not found:
        selector = suite or ALL_SUITE
        raise SuiteError(f"no tasks matched suite {selector!r} under {tasks_dir}")
    return found


@dataclass
class GradedCell:
    """One graded cell plus the human-readable assertion for the §1 view."""

    cell: Cell
    asserted: str | None


@dataclass
class SuiteResult:
    """The graded result of a whole suite run."""

    suite: str
    graded: list[GradedCell]
    #: task id -> the model that task actually ran under, or None when the run
    #: took the worker class default. Recorded at run time because the baseline
    #: key is (task, model): grading the same task against two models produces
    #: two results, and keying on task id alone loses the first one.
    models: dict[str, str | None] = field(default_factory=dict)
    #: task id -> CLI model sweep that produced the cell. Task-level model pins
    #: stay in the default-suite slice (None), even when the actual model is the
    #: same string as a sweep target.
    slices: dict[str, str | None] = field(default_factory=dict)
    #: task id -> content digest of the skill bundle under evaluation. This is
    #: part of the cohort identity: version 2 beating version 1 is only visible
    #: when the two versions do not share one baseline slot.
    skill_digests: dict[str, str | None] = field(default_factory=dict)
    #: task id -> compression mode for the eval cell
    compressions: dict[str, str | None] = field(default_factory=dict)
    #: task id -> attempt number for the eval cell
    attempts: dict[str, int | None] = field(default_factory=dict)
    reasoning_level: str | None = None

    @property
    def cells(self) -> list[Cell]:
        return [g.cell for g in self.graded]

    def count(self, outcome: str) -> int:
        return sum(1 for g in self.graded if g.cell.outcome == outcome)

    @property
    def passed(self) -> int:
        return self.count(outcomes.PASSED)

    @property
    def failed(self) -> int:
        return self.count(outcomes.FAILED)

    @property
    def errored(self) -> int:
        return self.count(outcomes.AGENT_ERROR) + self.count(outcomes.ERROR)

    @property
    def refused(self) -> int:
        """Cells not measured because bounded resource admission expired."""
        return sum(
            1
            for g in self.graded
            if g.cell.outcome == outcomes.ERROR
            and (g.cell.reason or "").startswith("AdmissionRefused: admission_refused:")
        )

    @property
    def has_agent_errors(self) -> bool:
        return self.errored > 0

    @property
    def level_mismatches(self) -> int:
        return sum(1 for g in self.graded if g.cell.level_mismatch)

    @property
    def model_mismatches(self) -> int:
        return sum(1 for g in self.graded if g.cell.model_mismatch)

    @property
    def metrics_incomplete(self) -> int:
        return sum(
            1
            for g in self.graded
            if g.cell.metrics is not None and not g.cell.metrics.get("metrics_complete")
        )

    @property
    def level_unresolved(self) -> int:
        return sum(
            1
            for g in self.graded
            if g.cell.level_mismatch and g.cell.resolved_reasoning_level is None
        )

    def verdicts(self, *, include_held_failures: bool = False) -> dict[str, str]:
        """(task, model) key -> resolved outcome, the shape the gate diffs on.

        Unmodelled tasks still keep the bare task id. Tasks that pin a model in
        ``task.toml`` now use the model-qualified key too, so old baselines for
        pinned tasks need a one-time baseline update.

        Regression comparison includes completed failures quarantined by an
        integrity hold. Stored results retain the hold, as do held passes.
        """
        return {
            baseline.verdict_key(
                g.cell.task_id,
                self.models.get(g.cell.task_id),
                self.skill_digests.get(g.cell.task_id),
                self.compressions.get(g.cell.task_id),
                self.attempts.get(g.cell.task_id),
                self.reasoning_level,
            ): outcomes.FAILED
            if include_held_failures
            and g.cell.held_verdict
            and g.cell.held_verdict["verdict"] == outcomes.FAILED
            else g.cell.outcome
            for g in self.graded
        }

    def verdict_slices(self) -> dict[str, str | None]:
        """Baseline verdict key -> CLI sweep slice for each graded cell."""
        return {
            baseline.verdict_key(
                g.cell.task_id,
                self.models.get(g.cell.task_id),
                self.skill_digests.get(g.cell.task_id),
                self.compressions.get(g.cell.task_id),
                self.attempts.get(g.cell.task_id),
                self.reasoning_level,
            ): self.slices.get(g.cell.task_id)
            for g in self.graded
        }

    def verdict_versions(self) -> dict[str, int]:
        """Baseline verdict key -> the graded task's ``task_version``."""
        return {
            baseline.verdict_key(
                g.cell.task_id,
                self.models.get(g.cell.task_id),
                self.skill_digests.get(g.cell.task_id),
                self.compressions.get(g.cell.task_id),
                self.attempts.get(g.cell.task_id),
                self.reasoning_level,
            ): g.cell.task_version or baseline.DEFAULT_TASK_VERSION
            for g in self.graded
        }

    def summary_dict(self) -> dict[str, Any]:
        """The SPEC §1 mockup shape (``hq gym run --json``)."""
        cells_view: list[dict[str, Any]] = []
        for g in self.graded:
            cell = g.cell
            view: dict[str, Any] = {
                "task": cell.task_id,
                "verdict": cell.outcome,
                "subchecks": cell.subchecks,
                "score": cell.score,
            }
            if cell.task_version is not None:
                view["task_version"] = cell.task_version
            if cell.outcome == outcomes.PASSED and g.asserted:
                view["asserted"] = g.asserted
            elif cell.reason:
                view["reason"] = cell.reason
            # TOC-05 joins each attempt to its worker_runs cost through the
            # launch request, so it rides along whenever dispatch produced one.
            if cell.infra_attempts:
                view["infra_attempts"] = cell.infra_attempts
            if cell.launch_request_id:
                view["launchRequestId"] = cell.launch_request_id
            if cell.compression is not None:
                view["compression"] = cell.compression
            if cell.attempt is not None:
                view["attempt"] = cell.attempt
            if cell.reasoning_level is not None:
                view["reasoning_level"] = cell.reasoning_level
                view["level_mismatch"] = cell.level_mismatch
            if cell.resolved_reasoning_level is not None:
                view["resolved_reasoning_level"] = cell.resolved_reasoning_level
            if cell.integrity_flags:
                view["integrity_flags"] = cell.integrity_flags
            if cell.metrics is not None:
                view["metrics"] = cell.metrics
                view["model_mismatch"] = cell.model_mismatch
            if cell.economics is not None:
                view.update(economics_view(cell.economics))
            # TOC-05 excludes a poll-deadline cell from the arm's sample: at
            # low priority the deadline includes queue time, so it is not a
            # model failure.
            if cell.terminal_status in {TIMEOUT_TERMINAL_STATUS, "provider_unavailable"}:
                view["terminalStatus"] = cell.terminal_status
            if cell.fix_audit is not None:
                view["fix_audit"] = cell.fix_audit
            if cell.execution_provenance is not None:
                view["execution_provenance"] = cell.execution_provenance
            cells_view.append(cell.result_redactor(view) if cell.result_redactor else view)
        return {
            "suite": self.suite,
            "tasks": len(self.graded),
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errored,
            "refused": self.refused,
            "level_mismatches": self.level_mismatches,
            "level_unresolved": self.level_unresolved,
            "model_mismatches": self.model_mismatches,
            "integrity_holds": sum(c.outcome == outcomes.INTEGRITY_HOLD for c in self.cells),
            "metrics_incomplete": self.metrics_incomplete,
            "cells": cells_view,
            "grader": _GRADER_NOTE,
        }


def effective_worker_class(
    task: Task, *, model: str | None = None, worker_class: str | None = None
) -> str | None:
    """Resolve the class dispatch will use, including task-level pins."""
    selected_model = task.dispatch.get("model") or model
    return (
        task.dispatch.get("persona")
        or worker_class
        or ("oss-local" if selected_model and selected_model.startswith("local/") else None)
        or task.persona
    )


def in_scope_agents(
    tasks: list[Task],
    mode: str,
    *,
    model: str | None = None,
    worker_class: str | None = None,
) -> list[str]:
    """The eval agents this run will actually dispatch, sorted and deduped.

    An injection task gated off by the taint posture never dispatches, so its
    persona is out of scope — the action-space lock only pins agents whose tools
    are live-load-bearing for this run (a pre-CTX ``resident`` stays unlocked
    without blocking the ``rca`` suite).
    """
    return sorted(
        {
            agent
            for t in tasks
            if taint.gate_injection_task(t, mode).run
            if (agent := effective_worker_class(t, model=model, worker_class=worker_class))
        }
    )


def _invalidate_level_mismatch(cell: Cell, reasoning_level: str | None) -> None:
    """Fail closed when the ledger-resolved level differs from the request.

    A verdict recorded under ``@effort:<level>`` must come from a run at that
    level. A clamped, unsupported, or unrecorded level turns a dispatched cell
    into ``agent_error``: baseline update refuses a non-green suite, and the
    regression gate never counts an ``agent_error`` as a pass or regression.
    Gate-skipped cells never dispatched, so they carry no level to compare.
    """
    if reasoning_level is None or not outcomes.is_outcome(cell.outcome):
        return
    if not cell.launch_request_id and cell.resolved_reasoning_level is None:
        return
    if cell.resolved_reasoning_level == reasoning_level:
        return
    cell.level_mismatch = True
    if cell.outcome != outcomes.AGENT_ERROR:
        cell.reason = (
            f"requested reasoning level {reasoning_level!r} but the ledger resolved "
            f"{cell.resolved_reasoning_level!r}; {cell.outcome} verdict discarded"
        )
        cell.outcome = outcomes.AGENT_ERROR


def _flag_model_mismatch(cell: Cell, model: str | None, worker_class: str | None) -> None:
    """Judge the cell's ledger metrics against the requested model and class.

    Unlike a level mismatch this keeps the verdict: the report excludes a
    mismatched cell from its model's statistics and counts it (SPEC §3.3).
    """
    if cell.metrics is not None:
        cell.model_mismatch = metrics_mod.apply_requested(
            cell.metrics, model=model, worker_class=worker_class
        )


def _price_cell(cell: Cell, source: TrajectorySource) -> None:
    """MEG-03: attach dollars and quota share when the source can price a cell.

    A source without ``cell_economics`` (the unit suite's fakes) leaves the cell
    and its output unchanged.
    """
    economics = getattr(source, "cell_economics", None)
    if cell.metrics is None or not callable(economics):
        return
    try:
        cell.economics = economics(cell.metrics)
    except Exception as exc:  # pricing must never cost the verdict
        cell.economics = {
            "cost_usd": None,
            "cost_status": "unknown",
            "cost_reason": f"pricing_failed: {exc}",
            "quota_share": None,
            "quota_status": "unknown",
            "quota_reason": f"pricing_failed: {exc}",
        }


def run_suite(
    tasks: list[Task],
    source: TrajectorySource,
    *,
    suite: str = ALL_SUITE,
    taint_mode: str | None = None,
    python_exe: str | None = None,
    resolver: locks.ToolSurfaceResolver | None = None,
    locks_dir: Path | None = None,
    model: str | None = None,
    worker_class: str | None = None,
    reasoning_level: str | None = None,
    compression: str | None = None,
    attempt: int | None = None,
    on_progress: Callable[[SuiteResult], None] | None = None,
    concurrency: int = 1,
) -> SuiteResult:
    """Grade every task in ``tasks`` and roll them into a :class:`SuiteResult`.

    ``taint_mode`` is resolved once up front (defaulting to the live worker-pool
    posture) and applied to every injection task, so one config read gates the
    whole run. Non-injection tasks ignore it.

    When a ``resolver`` is supplied the run first performs the GYM-05 setup-phase
    action-space check: every in-scope eval agent's live tool surface must match
    its committed lock, or :class:`gym.locks.LockDriftError` is raised **before
    any task dispatches** (fail-closed pinning). The pure-stdlib unit suite omits
    the resolver to grade synthetic trajectories without a worker-class registry;
    ``hq gym run`` always supplies one.

    ``compression`` and ``attempt`` (TOC-05) label each cell and its cohort key
    so repeats and arms never overwrite one another. The arm itself reaches the
    worker through the trajectory source (``WorkerPoolTrajectorySource.compression``
    -> ``hq dispatch --tool-output-compression``), not through this function.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    mode = taint_mode if taint_mode is not None else taint.load_live_taint_mode()

    if resolver is not None:
        locks.check_surface_locks(
            in_scope_agents(tasks, mode, model=model, worker_class=worker_class),
            resolver,
            locks_dir if locks_dir is not None else locks.default_locks_dir(),
        )

    graded: list[GradedCell] = []
    models: dict[str, str | None] = {}
    slices: dict[str, str | None] = {}
    compressions: dict[str, str | None] = {}
    attempts: dict[str, int | None] = {}

    result = SuiteResult(
        suite=suite,
        graded=graded,
        models=models,
        slices=slices,
        compressions=compressions,
        attempts=attempts,
        reasoning_level=reasoning_level,
    )
    if any(is_fix_family(t.suite) for t in tasks):
        from gym.fix_audit import seal

        seal({"preflight": True})
    # GYMSKEW-01/02: verifier code and public task inputs are read once, here.
    pin = ExitStack()
    pin.enter_context(grader.pinned_run_files(tasks))
    try:
        if on_progress is not None:
            on_progress(result)
        for t in tasks:
            pinned_model = t.dispatch.get("model")
            models[t.id] = pinned_model or model
            slices[t.id] = baseline.cohort_key(None if pinned_model else model, reasoning_level)
            compressions[t.id] = compression
            attempts[t.id] = attempt

        admission_halted = Event()
        interrupted = Event()
        prepare = getattr(source, "prepare_concurrent", None)
        if concurrency > 1 and callable(prepare):
            prepare(interrupted)

        def grade_task(t: Task) -> GradedCell:
            if (
                admission_halted.is_set()
                or interrupted.is_set()
                or getattr(source, "admission_halted", Event()).is_set()
            ):
                return GradedCell(
                    cell=Cell(
                        task_id=t.id,
                        task_version=t.task_version,
                        outcome=outcomes.AGENT_ERROR,
                        reason="provider_unavailable: suite admission halted because a "
                        "previous worker cancellation is unconfirmed",
                    ),
                    asserted=t.verifier_description,
                )
            # Production forks own a ledger connection per task; SQLite readers
            # must never cross threads. Fakes may supply a thread-safe source.
            fork = getattr(source, "fork", None)
            task_source = fork() if concurrency > 1 and callable(fork) else source
            try:
                cell = taint.gate_injection_run(t, mode, task_source, python_exe=python_exe)
                if (
                    cell.terminal_status == "provider_unavailable"
                    and cell.infra_attempts
                    and not cell.infra_attempts[-1].get("retry_safe")
                ):
                    admission_halted.set()
                cell.reasoning_level = reasoning_level
                _invalidate_level_mismatch(cell, reasoning_level)
                _flag_model_mismatch(
                    cell,
                    models[t.id],
                    effective_worker_class(t, model=model, worker_class=worker_class),
                )
                _price_cell(cell, task_source)
            except Exception as exc:
                artifact = getattr(exc, "fix_audit", None)
                if is_fix_family(t.suite) and artifact is None:
                    from gym.fix_audit import seal

                    artifact = seal(
                        {
                            "version": 1,
                            "task": t.id,
                            "files": [],
                            "cases": [],
                            "incomplete": True,
                            "verdict": "error",
                        }
                    )
                # A cell that saw two gym-library versions is a harness error
                # with a stable reason, never a model failure (GYMSKEW-02).
                cell = Cell(
                    task_id=t.id,
                    outcome=outcomes.ERROR,
                    reason=library.skew_reason(exc) or f"{type(exc).__name__}: {exc}",
                    fix_audit=artifact,
                )
            finally:
                if task_source is not source:
                    task_source.close()
            cell.reasoning_level = reasoning_level
            cell.compression = compression
            cell.attempt = attempt
            cell.task_version = t.task_version
            return GradedCell(cell=cell, asserted=t.verifier_description)

        def record(g: GradedCell) -> None:
            graded.append(g)
            if on_progress is not None:
                on_progress(result)

        if concurrency == 1:
            for t in tasks:
                record(grade_task(t))
        else:
            # Copy the verifier snapshot context into every thread, before any
            # worker can change files. Collect/checkpoint on the caller thread.
            # Submit only N at a time so a stuck cell cannot admit the whole suite.
            pool = ThreadPoolExecutor(max_workers=concurrency)
            pending = {}
            completed = {}
            iterator = iter(enumerate(tasks))

            def submit_next() -> None:
                item = next(iterator, None)
                if item is not None:
                    index, task = item
                    future = pool.submit(copy_context().run, grade_task, task)
                    pending[future] = index

            try:
                for _ in range(concurrency):
                    submit_next()
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        index = pending.pop(future)
                        completed[index] = future.result()
                        submit_next()
                    # Stable corpus ordering regardless of completion ordering.
                    graded[:] = [completed[index] for index in sorted(completed)]
                    if on_progress is not None:
                        on_progress(result)
            except BaseException:
                interrupted.set()
                pool.shutdown(
                    wait=bool(getattr(source, "wait_for_interruption_cleanup", False)),
                    cancel_futures=True,
                )
                raise
            else:
                pool.shutdown(wait=True)
        return result
    finally:
        pin.close()


__all__ = [
    "ALL_SUITE",
    "SELECTABLE_SUITES",
    "TIMEOUT_TERMINAL_STATUS",
    "SuiteError",
    "GradedCell",
    "SuiteResult",
    "default_tasks_dir",
    "discover_tasks",
    "in_scope_agents",
    "effective_worker_class",
    "run_suite",
]
