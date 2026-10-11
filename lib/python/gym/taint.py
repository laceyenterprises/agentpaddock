"""Taint-gate resolution + injection-task gating for the GYM injection corpus.

The injection corpus (``tasks/injection-*``) only proves anything once CTX-03's
comms taint send gate is merged AND ``worker_pool.comms.taint`` is in **enforce**.
Until then an injection run cannot demonstrate resistance — a green verdict would
be false assurance (SPEC §6, §7). This module is the gate that keeps that from
happening. It reads the CTX rollout flag, resolves a taint **mode**, and maps
each injection task to either:

  * ``enforce``  -> dispatch + grade the task normally (the verifier asserts the
                    exfil was denied), or
  * ``shadow`` / ``unavailable`` -> an explicit, non-passing **gate outcome** the
                    suite renders distinctly from ``passed`` — and does so WITHOUT
                    dispatching a real worker (no false assurance, no wasted spawn).

Mode resolution mirrors the DMG/memory-dynamic shadow→enforce pattern already in
the worker pool (``_worker_pool_memory_dynamic_enforcing``): ``enabled`` default
false, ``shadow_only`` default true.

  enabled = false                     -> UNAVAILABLE  (no gate at all; pre-CTX)
  enabled = true,  shadow_only = true -> SHADOW       (gate computes but allows)
  enabled = true,  shadow_only = false-> ENFORCE      (gate denies; load-bearing)

The gate outcomes are deliberately NOT members of the five grader outcomes
(:mod:`gym.outcomes`): a skip/xfail is a *pre-dispatch* decision the grader never
makes, so it lives here instead of widening the GYM-01 grader vocabulary (which
this ticket must not modify). Neither gate outcome is in
:data:`gym.outcomes.PASSING`, so the regression gate (GYM-04) can never read a
gated injection cell as green.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gym.grader import Cell, TrajectorySource, grade_task
from gym.schema import Task

# ---------------------------------------------------------------------------
# Taint modes (the resolved posture of the CTX comms taint send gate).
# ---------------------------------------------------------------------------
ENFORCE = "enforce"
SHADOW = "shadow"
UNAVAILABLE = "unavailable"

TAINT_MODES: frozenset[str] = frozenset({ENFORCE, SHADOW, UNAVAILABLE})

# The CFG subtree CTX-05 adds (SPEC §2/§3, CTX pack §2). Pre-CTX these keys do
# not exist and ``.get`` returns the code default, resolving to UNAVAILABLE.
_ENABLED_KEY = "worker_pool.comms.taint.enabled"
_SHADOW_ONLY_KEY = "worker_pool.comms.taint.shadow_only"

# ---------------------------------------------------------------------------
# Gate outcomes — a pre-dispatch decision, distinct from the grader outcomes.
# Never "passed"; a test asserts these stay disjoint from gym.outcomes.OUTCOMES
# and out of gym.outcomes.PASSING.
# ---------------------------------------------------------------------------
XFAIL_SHADOW = "xfail_taint_shadow"
SKIPPED_NO_TAINT_GATE = "skipped_no_taint_gate"

GATE_OUTCOMES: frozenset[str] = frozenset({XFAIL_SHADOW, SKIPPED_NO_TAINT_GATE})


def taint_mode(*, enabled: bool, shadow_only: bool) -> str:
    """Resolve a taint mode from the two rollout flags (pure)."""
    if not enabled:
        return UNAVAILABLE
    return SHADOW if shadow_only else ENFORCE


def _cfg_get(config: Any, dotted: str, default: Any) -> Any:
    """Read ``dotted`` from a dotted-key CFG object or nested mapping.

    Defensive on purpose: a loader that lacks the (pre-CTX) key, or raises on an
    unknown key, must fall through to the code default rather than crash the
    suite. A ``null`` in YAML resolves to the default too — matching
    ``worker_pool_value`` semantics.
    """
    missing = object()
    try:
        value = config.get(dotted, missing)
        if value is missing:
            value = config
            for part in dotted.split("."):
                value = value.get(part, missing)
                if value is missing:
                    return default
    except Exception:
        return default
    return default if value is None else value


def resolve_taint_mode(config: Any) -> str:
    """Resolve the taint mode from a CFG object exposing ``.get(dotted, default)``."""
    enabled = bool(_cfg_get(config, _ENABLED_KEY, False))
    shadow_only = bool(_cfg_get(config, _SHADOW_ONLY_KEY, True))
    return taint_mode(enabled=enabled, shadow_only=shadow_only)


def load_live_taint_mode() -> str:
    """Best-effort resolve of the live taint mode from the host config.

    The host supplies it (:meth:`gym.host_services.HostServices.host_config`;
    Agent OS: the worker-pool CFG). Fail-closed: if the host has no config or
    it cannot be read (e.g. standalone, a bare test tree, or pre-CTX before the
    schema subtree lands), this returns :data:`UNAVAILABLE` so injection tasks
    skip rather than silently claim resistance they never tested (SPEC §6).
    """
    try:
        from gym import host_services

        cfg = host_services.current().host_config()
    except Exception:
        return UNAVAILABLE
    if cfg is None:
        return UNAVAILABLE
    return resolve_taint_mode(cfg)


def is_injection_task(task: Task) -> bool:
    """True for tasks that belong to the injection corpus (suite == injection)."""
    return task.suite == "injection"


@dataclass(frozen=True)
class GateDecision:
    """Whether to run an injection task, or short-circuit it to a gate outcome."""

    run: bool
    outcome: str | None
    reason: str


def gate_injection_task(task: Task, mode: str) -> GateDecision:
    """Decide how an injection task resolves under the current taint ``mode``.

    Only ``enforce`` runs the task (its verifier can then assert the exfil was
    denied). ``shadow`` and ``unavailable`` short-circuit to an explicit,
    non-passing gate outcome — the mitigation for "injection tasks give false
    assurance before CTX lands" (SPEC §6). Non-injection tasks are never gated.
    """
    if not is_injection_task(task):
        return GateDecision(run=True, outcome=None, reason="not an injection task")
    if mode == ENFORCE:
        return GateDecision(
            run=True,
            outcome=None,
            reason="worker_pool.comms.taint in enforce; injection resistance is testable",
        )
    if mode == SHADOW:
        return GateDecision(
            run=False,
            outcome=XFAIL_SHADOW,
            reason=(
                "worker_pool.comms.taint in shadow: the send gate computes but does "
                "not deny, so injection resistance cannot be proven (xfail until "
                "enforce; SPEC §6/§7)"
            ),
        )
    return GateDecision(
        run=False,
        outcome=SKIPPED_NO_TAINT_GATE,
        reason=(
            "worker_pool.comms.taint unavailable (pre-CTX-03 / gate disabled): no "
            "send gate to resist against, task skipped (SPEC §7)"
        ),
    )


def gate_injection_run(
    task: Task,
    mode: str,
    source: TrajectorySource,
    *,
    python_exe: str | None = None,
) -> Cell:
    """The injection-aware entry a suite runner (GYM-04) uses in place of
    :func:`gym.grader.grade_task`.

    In ``enforce`` (or for a non-injection task) it delegates to ``grade_task``,
    dispatching a real worker and grading the trajectory. Otherwise it returns a
    gated :class:`~gym.grader.Cell` WITHOUT touching ``source`` — no worker is
    dispatched for a task that cannot prove anything, and the non-passing gate
    outcome is visible in suite output.
    """
    decision = gate_injection_task(task, mode)
    if decision.run:
        return grade_task(task, source, python_exe=python_exe)
    return Cell(
        task_id=task.id,
        task_version=task.task_version,
        outcome=decision.outcome or SKIPPED_NO_TAINT_GATE,
        reason=decision.reason,
    )


__all__ = [
    "ENFORCE",
    "SHADOW",
    "UNAVAILABLE",
    "TAINT_MODES",
    "XFAIL_SHADOW",
    "SKIPPED_NO_TAINT_GATE",
    "GATE_OUTCOMES",
    "taint_mode",
    "resolve_taint_mode",
    "load_live_taint_mode",
    "is_injection_task",
    "GateDecision",
    "gate_injection_task",
    "gate_injection_run",
]
