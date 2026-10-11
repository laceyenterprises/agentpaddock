"""TOC-05 compression A/B matrix: quota gate, arm gate, and arm aggregation.

Pure functions over plain data; the driver that dispatches cells and joins
cost is ``gym/toc_eval.py`` (``hq gym toc-matrix``). Kept here so the decision logic TOC-06
reads is unit-tested with the rest of the gym.

Four rules carry the experiment's honesty:

* Quota is read from ``hq fleet quota status --json``. A status that cannot be
  read is ``unknown``, never ``healthy``: the matrix does not spend quota it
  cannot see.
* The arm is pinned per launch (TOCMEASURE-01). ``hq gym run --compression A``
  forwards ``hq dispatch --tool-output-compression A``, which the claude-code
  hook and codex shims honour instead of the host config for that launch only.
  ``rtk`` is never dispatched (RTK-ELIGIBILITY.md) and is carried as an omitted
  arm.
* An attempt counts only when the ledger shows it ran the arm it was labelled
  with: the worker-run dispatch receipts record the requested arm, and the TOC-02
  rows attributed to the launch match it (spill rows come from the launch arm;
  an off launch writes none). A mismatch is excluded, not averaged in.
* Cost-per-success is total cost of *every* attempt in the arm divided by the
  gym-graded passes. A worker run that succeeded with a wrong answer is a
  failure and still costs; zero passes reports the ratio undefined, never zero.
  An attempt the driver marks ``excluded`` (no graded cell, a poll deadline
  that includes low-priority queue time, or an arm mismatch) says nothing about
  the arm, so it is counted separately and left out of the sample, the passes,
  and the cost. Byte reduction is recorded but is not the decision metric.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from gym import outcomes

#: The uncompressed control arm plus the backends TOC-05 compares.
ARMS = ("off", "spill", "rtk")
CONTROL_ARM = "off"
#: Arms a gym launch can pin (``tool_output_compression_arm.LAUNCH_ARMS``).
LAUNCH_ARMS = ("off", "spill")
#: Arms the matrix records as omitted instead of dispatching. Restated from
#: ``cwp_dispatch.tool_output_compression_arm`` so this module stays stdlib-only.
OMITTED_ARM_REASONS = {
    "rtk": (
        "rtk is ineligible (projects/tool-output-compression/RTK-ELIGIBILITY.md): "
        "RTK v0.50.0 is filter-shaped, no network-denied sidecar exists, and the "
        "wrappers lack first-execution raw capture"
    )
}

QUOTA_HEALTHY = "healthy"
QUOTA_SUSPENDED = "suspended"
QUOTA_UNKNOWN = "unknown"

#: cwp_quota_probe.STATE_EXHAUSTED, restated so this module stays stdlib-only.
_STATE_EXHAUSTED = "exhausted"

#: The off arm installs no seam (no hook, no shims), so nothing observes its
#: eligible invocations; its verification is that it wrote no TOC-02 rows.
OFF_ARM_ELIGIBLE_REASON = "off arm installs no compression seam; eligible calls are not observed"


@dataclass(frozen=True)
class QuotaGate:
    state: str
    reason: str


def quota_gate(returncode: int, stdout: str, *, requirements=None, worker_class=None) -> QuotaGate:
    """Classify ``hq fleet quota status --json`` output.

    With requirements, only the cell's provider/auth paths and same-class
    suspended launches gate spending; each required path must explicitly be ok.
    Without requirements, report fleet-wide suspensions for the plan.
    Anything unparseable is unknown.
    """
    if returncode != 0:
        return QuotaGate(QUOTA_UNKNOWN, f"hq fleet quota status exited {returncode}")
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        return QuotaGate(QUOTA_UNKNOWN, f"quota status is not JSON: {exc}")
    if not isinstance(payload, dict):
        return QuotaGate(QUOTA_UNKNOWN, "quota status JSON is not an object")
    statuses = payload.get("providerStatuses")
    suspended_lrqs = payload.get("suspendedLaunchRequests")
    if not isinstance(statuses, list) or not isinstance(suspended_lrqs, list):
        return QuotaGate(
            QUOTA_UNKNOWN,
            "quota status lacks providerStatuses/suspendedLaunchRequests lists",
        )
    if requirements is not None:
        selected = [
            row
            for row in statuses
            if isinstance(row, dict) and (row.get("provider"), row.get("authPath")) in requirements
        ]
        for provider, auth_path in requirements:
            rows = [
                r
                for r in selected
                if (r.get("provider"), r.get("authPath")) == (provider, auth_path)
            ]
            if not rows:
                return QuotaGate(QUOTA_UNKNOWN, f"missing quota for {provider}/{auth_path}")
            for row in rows:
                state = str(row.get("state") or "").lower()
                if row.get("operatorSuspendUntil") or state in ("exhausted", "suspended"):
                    return QuotaGate(QUOTA_SUSPENDED, f"provider suspended: {provider}/{auth_path}")
                if state != "ok":
                    return QuotaGate(
                        QUOTA_UNKNOWN, f"quota {state or 'unknown'} for {provider}/{auth_path}"
                    )
        suspended_lrqs = [
            r
            for r in suspended_lrqs
            if isinstance(r, dict) and r.get("workerClass") == worker_class
        ]
        statuses = selected
    blocked = sorted(
        f"{row.get('provider')}/{row.get('authPath')}"
        for row in statuses
        if isinstance(row, dict)
        and (
            str(row.get("state") or "").lower() == _STATE_EXHAUSTED
            or row.get("operatorSuspendUntil")
        )
    )
    if blocked:
        return QuotaGate(QUOTA_SUSPENDED, "provider suspended: " + ", ".join(blocked))
    if suspended_lrqs:
        return QuotaGate(
            QUOTA_SUSPENDED, f"{len(suspended_lrqs)} launch request(s) are quota-suspended"
        )
    return QuotaGate(QUOTA_HEALTHY, "no provider exhausted and no quota-suspended launches")


def arm_omission_reason(arm: str, task_commands: Iterable[str] = ()) -> str | None:
    """Why ``arm`` cannot be measured on a task, or None to run it.

    ``task_commands`` are the eligible command classes the task's correct path
    runs. The launch arm compresses every eligible class, so a backend arm only
    needs the task to run at least one; without one it would measure the off
    arm under a backend label.
    """
    if arm not in ARMS:
        return f"unknown arm {arm!r}"
    if arm in OMITTED_ARM_REASONS:
        return OMITTED_ARM_REASONS[arm]
    if arm != CONTROL_ARM and not set(task_commands):
        return f"task declares no eligible command class; backend {arm!r} would compress nothing"
    return None


def fidelity_note(arm: str, fidelity_passed: Iterable[str]) -> dict[str, Any] | None:
    """TOC-04's verdict on a backend arm, recorded next to the A/B numbers.

    A launch arm runs ``spill`` whatever the verdict (the gym measures it); the
    host config still refuses a backend TOC-04 did not admit.
    """
    if arm == CONTROL_ARM or arm not in LAUNCH_ARMS:
        return None
    admitted = arm in set(fidelity_passed)
    note = (
        "fidelity-admitted for host config"
        if admitted
        else "not fidelity-admitted (projects/tool-output-compression/fidelity-result.json); "
        "run as a gym launch arm for measurement only"
    )
    return {"admitted": admitted, "note": note}


def arm_mismatch_reason(
    arm: str, launch_arm: Mapping[str, Any] | None, toc_counts: Mapping[str, Any] | None
) -> str | None:
    """Why the ledger says a launch did not run ``arm``; None when it did.

    ``launch_arm`` is ``{"requested", "recorded": [...], "effective": [...]}``
    and ``toc_counts`` the TOC-02 rows attributed to the launch. ``effective``
    is the arm the spawning adapter reported applying; a launch whose adapter
    reported none (one that predates TOCMEASURE-01 ignores the arm) is a
    mismatch, not a pass. Callers only pass a launch the ledger knows; an
    unknown launch stays unverified, not mismatched.
    """
    requested = (launch_arm or {}).get("requested")
    recorded = list((launch_arm or {}).get("recorded") or [])
    effective = list((launch_arm or {}).get("effective") or [])
    if requested != arm:
        return f"dispatch receipts request arm {requested!r}, not {arm!r}"
    if arm not in recorded or any(r != arm for r in recorded):
        return f"worker runs record arm(s) {recorded!r}, not {arm!r}"
    counts = toc_counts or {}
    rows = int(counts.get("rows") or 0)
    if arm == CONTROL_ARM and rows:
        return f"off launch wrote {rows} TOC-02 row(s)"
    if arm != CONTROL_ARM and rows:
        if set(counts.get("backends") or []) != {arm}:
            return f"TOC-02 rows ran backend(s) {counts.get('backends')!r}, not {arm!r}"
        if set(counts.get("armSources") or []) != {"launch"}:
            return f"TOC-02 rows came from arm source(s) {counts.get('armSources')!r}, not launch"
    if arm not in effective or any(e != arm for e in effective):
        return f"adapters report applying arm(s) {effective!r}, not {arm!r}"
    return None


def coverage(arm: str, toc_counts: Mapping[str, Any] | None) -> dict[str, Any]:
    """Per-attempt eligible/compressed invocation counts from attributed rows."""
    counts = toc_counts or {}
    rows = int(counts.get("rows") or 0)
    base = {
        "seamCallsByCommand": dict(counts.get("callsByCommand") or {}),
        "compressedInvocations": int(counts.get("okRows") or 0),
        "bytesIn": int(counts.get("bytesIn") or 0),
        "bytesOut": int(counts.get("bytesOut") or 0),
    }
    if arm == CONTROL_ARM:
        return {**base, "eligibleInvocations": None, "eligibleReason": OFF_ARM_ELIGIBLE_REASON}
    return {**base, "eligibleInvocations": rows}


def attempt_tokens(
    worker_class: str, input_tokens: int, cache_read_tokens: int, output_tokens: int
) -> int:
    """Sum the ledger's normalized inclusive input and output.

    The ledger applies consumption_events input-basis rules per run before
    aggregation. Cache reads are a subset of input, regardless of harness.
    """
    return int(input_tokens) + int(output_tokens)


def format_budget(input_tokens: float, cache_read_tokens: float, output_tokens: float) -> str:
    """``Input(CacheRead):Output`` with thousands separators."""
    return f"{round(input_tokens):,}({round(cache_read_tokens):,}):{round(output_tokens):,}"


def estimate_attempt_budget(
    worker_class: str, model: str, samples: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Mean per-attempt token vector from comparable recent gym runs.

    Same-model runs are preferred; with none, every run of the class is the
    basis (a new model has no history yet). ``basis`` says which was used.
    """
    samples = [
        s
        for s in samples
        if all(s.get(key) is not None for key in ("inputTokens", "cacheReadTokens", "outputTokens"))
    ]
    same = [s for s in samples if s.get("model") == model]
    chosen, basis = (same, "model") if same else (list(samples), "class")
    if not chosen:
        return {"basis": "none", "sampleSize": 0, "totalTokens": None}
    mean = {
        key: statistics.fmean(int(s.get(key) or 0) for s in chosen)
        for key in ("inputTokens", "cacheReadTokens", "outputTokens")
    }
    return {
        "basis": basis,
        "sampleSize": len(chosen),
        **{k: round(v) for k, v in mean.items()},
        "totalTokens": attempt_tokens(
            worker_class,
            round(mean["inputTokens"]),
            round(mean["cacheReadTokens"]),
            round(mean["outputTokens"]),
        ),
    }


def _stats(values: Sequence[float]) -> dict[str, Any]:
    n = len(values)
    return {
        "n": n,
        "mean": round(statistics.fmean(values), 6) if n else None,
        "variance": round(statistics.variance(values), 6) if n >= 2 else None,
    }


def _number(record: Mapping[str, Any], key: str) -> float | None:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _cost(record: Mapping[str, Any]) -> float | None:
    return _number(record, "costUsd")


def _cost_per_success_variance(costs: Sequence[float], successes: Sequence[float]) -> float | None:
    """Delta-method variance of mean(cost) / mean(success), ignoring covariance.

    An approximation for a handful of repetitions: it says how far apart two
    arms' cost-per-success would have to be before the difference means much.
    """
    n = len(costs)
    p = statistics.fmean(successes) if successes else 0.0
    if n < 2 or p == 0:
        return None
    mean_cost = statistics.fmean(costs)
    var_mean_cost = statistics.variance(costs) / n
    var_p = p * (1 - p) / n
    return round(var_mean_cost / p**2 + mean_cost**2 * var_p / p**4, 6)


def _informative(arm: str, records: Sequence[Mapping[str, Any]]) -> str | None:
    if not records:
        return "no counted attempts"
    unverified = sum(1 for r in records if r.get("armVerified") is not True)
    if unverified:
        return f"arm not verified against the ledger for {unverified} attempt(s); rejoin later"
    pending_coverage = sum(r.get("seamCoverage") == "pending" for r in records)
    if pending_coverage:
        return f"required seam coverage pending for {pending_coverage} attempt(s); rejoin later"
    compressed = sum(int(r.get("compressedInvocations") or 0) for r in records)
    if arm != CONTROL_ARM and not compressed:
        return (
            "no compressed invocation is attributed to this arm's launches (TOC-02 "
            "journals may not be ingested yet; rejoin later)"
        )
    return None


def aggregate(attempts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Roll attempt records up to one summary per (worker class, model, arm).

    Each record carries ``workerClass``, ``model``, ``arm``, ``verdict`` (the
    gym-graded outcome) and ``costUsd`` (None while the ledger has no run, an
    uncosted run, or a run still in flight for the launch), plus the joined
    ``turns``, token vector, ``armVerified`` and coverage counts. A record with
    a truthy ``excluded`` is reported in ``excludedAttempts`` only.
    """
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for record in attempts:
        key = (str(record["workerClass"]), str(record["model"]), str(record["arm"]))
        groups.setdefault(key, []).append(record)

    summaries: list[dict[str, Any]] = []
    for (worker_class, model, arm), group in sorted(groups.items()):
        records = [r for r in group if not r.get("excluded")]
        excluded = [r for r in group if r.get("excluded")]
        sample_size = len(records)
        successes = [1.0 if r.get("verdict") == outcomes.PASSED else 0.0 for r in records]
        passes = int(sum(successes))
        costs = [c for c in (_cost(r) for r in records) if c is not None]
        missing_cost = sample_size - len(costs)
        total_cost = round(sum(costs), 6)
        cost_per_success: float | None = None
        undefined_reason: str | None = None
        if not sample_size:
            undefined_reason = "no counted attempts"
        elif missing_cost:
            undefined_reason = f"cost missing for {missing_cost} attempt(s)"
        elif passes == 0:
            undefined_reason = "no gym-graded passes"
        else:
            cost_per_success = round(total_cost / passes, 6)
        turns_all = [t for t in (_number(r, "turns") for r in records) if t is not None]
        turns_passed = [
            t
            for r in records
            if r.get("verdict") == outcomes.PASSED
            for t in [_number(r, "turns")]
            if t is not None
        ]
        bytes_in = sum(int(r.get("bytesIn") or 0) for r in records)
        bytes_out = sum(int(r.get("bytesOut") or 0) for r in records)
        eligible = [r.get("eligibleInvocations") for r in records]
        summary: dict[str, Any] = {
            "workerClass": worker_class,
            "model": model,
            "arm": arm,
            "sampleSize": sample_size,
            "passes": passes,
            "successRate": round(passes / sample_size, 6) if sample_size else None,
            "successRateVariance": _stats(successes)["variance"],
            "totalCostUsd": total_cost,
            "costPerSuccessUsd": cost_per_success,
            "costPerSuccessUndefinedReason": undefined_reason,
            "costPerSuccessVarianceUsd": (
                _cost_per_success_variance(costs, successes) if not missing_cost else None
            ),
            "costPerSuccessVarianceMethod": "delta (mean cost / success rate, no covariance)",
            "meanAttemptCostUsd": round(statistics.fmean(costs), 6) if costs else None,
            "attemptCostStdevUsd": round(statistics.stdev(costs), 6) if len(costs) >= 2 else None,
            "attemptCostVarianceUsd": _stats(costs)["variance"],
            "turnsToCompletion": _stats(turns_passed),
            "turnsAllAttempts": _stats(turns_all),
            "tokensPerAttempt": {
                key: _stats([v for v in (_number(r, key) for r in records) if v is not None])
                for key in ("inputTokens", "cacheReadTokens", "outputTokens")
            },
            "eligibleInvocations": (
                None if any(e is None for e in eligible) else sum(int(e) for e in eligible)
            ),
            "compressedInvocations": sum(int(r.get("compressedInvocations") or 0) for r in records),
            "bytesIn": bytes_in,
            "bytesOut": bytes_out,
            "byteReduction": round(1 - bytes_out / bytes_in, 6) if bytes_in else None,
            "byteReductionIsDecisionMetric": False,
            "launchRequestIds": [r.get("launchRequestId") for r in records],
            "excludedAttempts": len(excluded),
            "excludedLaunchRequestIds": [
                r.get("launchRequestId") for r in excluded if r.get("launchRequestId")
            ],
        }
        reason = _informative(arm, records)
        summary["informative"] = reason is None
        if reason:
            summary["uninformativeReason"] = reason
        summaries.append(summary)
    return summaries


__all__ = [
    "ARMS",
    "CONTROL_ARM",
    "LAUNCH_ARMS",
    "OFF_ARM_ELIGIBLE_REASON",
    "OMITTED_ARM_REASONS",
    "QUOTA_HEALTHY",
    "QUOTA_SUSPENDED",
    "QUOTA_UNKNOWN",
    "QuotaGate",
    "aggregate",
    "arm_mismatch_reason",
    "arm_omission_reason",
    "attempt_tokens",
    "coverage",
    "estimate_attempt_budget",
    "fidelity_note",
    "format_budget",
    "quota_gate",
]


def expand_axes(axes, repetitions, seed=None):
    """Shared Cartesian expansion; a seed shuffles once, None preserves order."""
    import itertools
    import random

    cells = [
        (*values, repetition)
        for values in itertools.product(*axes)
        for repetition in range(1, repetitions + 1)
    ]
    if seed is not None:
        random.Random(seed).shuffle(cells)
    return cells
