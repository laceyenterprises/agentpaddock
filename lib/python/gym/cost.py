"""Dollars and subscription-quota share per cell (MEG-03).

Prices a cell's MEG-02 ledger metrics (:mod:`gym.metrics`) at the published
rates in the one SEW price table (``sew.cost_model.load_price_table``); the gym
keeps no rate of its own. The ledger's input column is total input, cache reads
included, so it is split into disjoint buckets first:

  * fresh input  = ``token_usage_input - token_usage_cache_read``, input rate;
  * cache reads  = ``token_usage_cache_read``, cached-input rate;
  * output       = ``token_usage_output``, output rate.

Reasoning tokens are priced exactly once: :data:`REASONING_INSIDE_OUTPUT` says,
per harness, whether the ledger's output column already contains them.

Nothing here is zero-filled. A null column, a cache-read count above total
input, an unknown harness or token source, or a model with no published rate
yields ``cost_usd`` null with ``cost_status`` ``unknown``, never 0.

``quota_share`` is the fraction of the auth path's weekly subscription window
the cell used. It reuses the per-model calibration that
``cwp_dispatch.subscription_quota_reconcile`` stores in the ledger (tokens per
percent of a window, fixed at the first calibrating window), read through
:meth:`gym.ledger.LedgerReader.subscription_quota_proofs`. With no calibration
for the model and auth path it is null with ``quota_status`` ``unknown``.
Mixed-model cells also read unknown: metrics retain only output per model,
so their input + output cannot be attributed to each model's calibration.

The calibration arithmetic (:func:`_number`, :func:`_previous_baseline`) is
the reconcile's, copied here so this module imports no Agent OS package
(PDK-02); a parity test pins the copies to ``cwp_dispatch``. The SEW import is
lazy, so the gym core still imports without it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping

from gym.metrics import model_matches

UNKNOWN = "unknown"

#: Whether ``token_usage_output`` already contains ``token_usage_reasoning``,
#: per harness, as the ledger capture code writes it.
#:
#: * ``codex``: ``cwp_dispatch.teardown_rollup._usage_from_mapping`` stores the
#:   OpenAI ``output_tokens``, which includes ``reasoning_output_tokens``, and
#:   keeps reasoning only as a breakout.
#: * ``claude-code``: the transcript adapter
#:   (``session_ledger.adapters.claude_code``) stores Anthropic
#:   ``output_tokens``, which already counts thinking; no reasoning breakout is
#:   derived.
#:
#: A harness absent here is priced ``unknown``: its rule has not been read.
REASONING_INSIDE_OUTPUT: dict[str, bool] = {"codex": True, "claude-code": True}

#: ``worker_runs.token_usage_source`` values whose ``token_usage_input`` is
#: total input (fresh + cache reads, and cache writes where the harness reports
#: them), the contract the fresh-input split relies on. Anything else is priced
#: ``unknown``: e.g. ``claude-code-exec-json`` stores Anthropic's fresh-only
#: ``input_tokens``, ``live-floor`` is a running total, and a partial rollout
#: undercounts.
PRICEABLE_TOKEN_SOURCES = frozenset(
    {
        "codex-exec-json",
        "codex-exec-json+pricing-table-synthetic",
        "session-ledger",
        "session-ledger+pricing-table-synthetic",
    }
)

#: ``windowMinutes`` of the weekly subscription window in the quota snapshots.
WEEKLY_WINDOW_MINUTES = 10080.0

# ``_previous_baseline`` takes calibrations from windows that reset before a
# given reset; a cell reads the fixed first one, so every window qualifies.
_ANY_RESET = "￿"


def _number(value: object) -> float | None:
    """``cwp_dispatch.codex_quota_snapshots._number``: a finite float, else None."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _previous_baseline(
    proofs: list[dict[str, Any]], limit_id: str, model: str, window: str, reset: str
) -> float | None:
    """``cwp_dispatch.subscription_quota_reconcile._previous_baseline``."""
    candidates: list[tuple[str, float]] = []
    for proof in proofs:
        if (
            proof.get("provider") == "openai"
            and proof.get("limitId") == limit_id
            and proof.get("model") == model
            and proof.get("window") == window
            and str(proof.get("resetsAt") or "") < reset
            and proof.get("status") == "calibrating"
        ):
            baseline = _number(proof.get("tokensPerPercent"))
            if baseline and baseline > 0:
                candidates.append((str(proof["resetsAt"]), baseline))
    # The first usable calibration is fixed; later drift cannot move the bar.
    return min(candidates)[1] if candidates else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def price_cell(metrics: Mapping[str, Any], table: Any) -> dict[str, Any]:
    """Price one cell's metrics at its majority model's published rates.

    ``table`` is a ``sew.cost_model.PriceTable``. The result always carries
    ``cost_usd``, ``cost_status`` (the SEW basis: ``inferred``, ``estimated`` or
    ``unknown``) and ``cost_reason``.
    """
    from sew.cost_model import model_cost  # lazy: SEW dep

    model = metrics.get("majority_model")
    tokens: dict[str, int | None] = {"fresh_input": None, "cache_read": None, "output": None}

    def unknown(reason: str | None) -> dict[str, Any]:
        return {
            "cost_usd": None,
            "cost_status": UNKNOWN,
            "cost_reason": reason or UNKNOWN,
            "priced_model": model,
            "tokens": tokens,
            "price_source": None,
            "price_as_of": None,
        }

    harness = metrics.get("worker_class")
    reasoning_inside = REASONING_INSIDE_OUTPUT.get(str(harness)) if harness else None
    if reasoning_inside is None:
        return unknown("reasoning_rule_unknown_for_harness")
    sources = metrics.get("token_usage_sources")
    if not sources or any(source is None for source in sources):
        return unknown("token_source_unrecorded")
    if any(source not in PRICEABLE_TOKEN_SOURCES for source in sources):
        return unknown("token_source_input_contract_unknown")

    total_input = _count(metrics.get("token_usage_input"))
    cache_read = _count(metrics.get("token_usage_cache_read"))
    output = _count(metrics.get("token_usage_output"))
    reasoning = _count(metrics.get("token_usage_reasoning"))
    if total_input is None or cache_read is None or output is None:
        return unknown("token_usage_incomplete")
    if min(total_input, cache_read, output) < 0:
        return unknown("invalid_negative_token_count")
    if cache_read > total_input:
        # Invalid telemetry. Clamping would price a number nobody measured.
        return unknown("invalid_cache_read_exceeds_input")
    if not reasoning_inside:
        if reasoning is None:
            return unknown("token_usage_incomplete")
        output += reasoning
    tokens = {"fresh_input": total_input - cache_read, "cache_read": cache_read, "output": output}

    component = model_cost(
        model,
        {
            "accounting_source": "worker_runs",
            "input": tokens["fresh_input"],
            "cached_input": cache_read,
            "output": output,
        },
        table,
    )
    amount = component.get("amount_usd")
    if component.get("basis") == UNKNOWN or amount is None:
        return unknown(component.get("reason"))

    status = str(component["basis"])
    notes = [component["pricing_note"]] if component.get("pricing_note") else []
    observed: dict[str, int] = metrics.get("observed_models") or {}
    total_output = sum(observed.values())
    side_output = sum(n for m, n in observed.items() if not model_matches(m, str(model)))
    if any(not model_matches(m, str(model)) for m in observed):
        # Presence includes input-consuming models with zero output. The ledger
        # splits only output by model, so a side model's tokens are
        # priced at the majority model's rates.
        status = "estimated"
        notes.append("side_model_tokens_priced_at_majority_model")
    return {
        "cost_usd": round(float(amount), 6),
        "cost_status": status,
        "cost_reason": None,
        "priced_model": model,
        "tokens": tokens,
        "price_source": component.get("price_source"),
        "price_as_of": component.get("price_as_of"),
        "assumed_rate": component.get("assumed_rate"),
        "pricing_notes": notes,
        "side_model_output_share": round(side_output / total_output, 6) if side_output else 0.0,
    }


def weekly_windows(snapshots: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """``(limitId, window)`` pairs the quota snapshots show to be weekly.

    Proofs name a window (``primary``/``secondary``) but not its length; the
    snapshots they were computed from carry ``windowMinutes``. A pair seen with
    any other length is not treated as weekly.
    """
    minutes: dict[tuple[str, str], set[float]] = defaultdict(set)
    for row in snapshots:
        value = _number(row.get("windowMinutes"))
        if value is None:
            continue
        # Same defaults as the reconcile's grouping key.
        key = (str(row.get("limitId") or "codex"), str(row.get("window") or "unknown"))
        minutes[key].add(value)
    return {key for key, seen in minutes.items() if seen == {WEEKLY_WINDOW_MINUTES}}


def quota_share(
    metrics: Mapping[str, Any],
    *,
    proofs: list[dict[str, Any]],
    weekly: set[tuple[str, str]],
) -> dict[str, Any]:
    """The cell's share of its auth path's weekly window, from the calibration.

    ``proofs`` are the stored ``subscription-quota-proof`` records and
    ``weekly`` comes from :func:`weekly_windows`. The calibration measures a
    model's tokens as input + output (``_captured_tokens``), so the cell's
    tokens are measured the same way.
    """
    model = metrics.get("majority_model")

    def unknown(reason: str) -> dict[str, Any]:
        return {
            "quota_share": None,
            "quota_status": UNKNOWN,
            "quota_reason": reason,
            "quota_auth_path": None,
            "quota_calibration": None,
        }

    if not model:
        return unknown("model_unknown")
    total_input = _count(metrics.get("token_usage_input"))
    output = _count(metrics.get("token_usage_output"))
    if total_input is None or output is None:
        return unknown("token_usage_incomplete")
    observed = metrics.get("observed_models") or {}
    if any(not model_matches(str(observed_model), str(model)) for observed_model in observed):
        # Even a side model with zero output may have spent input. Until the
        # metrics retain each model's total, never apply the majority rate to
        # that contribution, whether or not the side model has a calibration.
        return unknown("mixed_model_token_totals_unavailable")

    keys = sorted(
        {
            (str(p.get("limitId")), str(p.get("model")), str(p.get("window")))
            for p in proofs
            if p.get("provider") == "openai"
            and model_matches(str(p.get("model") or ""), str(model))
            and (str(p.get("limitId")), str(p.get("window"))) in weekly
        }
    )
    calibrations = []
    for limit_id, proof_model, window in keys:
        baseline = _previous_baseline(proofs, limit_id, proof_model, window, _ANY_RESET)
        if baseline:
            calibrations.append((limit_id, proof_model, window, baseline))
    if not calibrations:
        return unknown("no_weekly_calibration")
    if len(calibrations) > 1:
        # Two auth paths (or two weekly windows) calibrate this model; the
        # ledger does not say which one the cell drew on.
        return unknown("auth_path_ambiguous")
    limit_id, proof_model, window, tokens_per_percent = calibrations[0]
    tokens = total_input + output
    return {
        "quota_share": round(tokens / tokens_per_percent / 100, 9),
        "quota_status": "calibrated",
        "quota_reason": None,
        "quota_auth_path": limit_id,
        "quota_calibration": {
            "model": proof_model,
            "window": window,
            "tokens_per_percent": tokens_per_percent,
            "cell_tokens": tokens,
        },
    }


def cell_economics(
    metrics: Mapping[str, Any],
    *,
    table: Any = None,
    proofs: list[dict[str, Any]] | None = None,
    weekly: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """``cost_*`` and ``quota_*`` fields for one cell.

    ``table`` None means the price table could not be loaded, and ``proofs`` or
    ``weekly`` None that the calibration could not be read; either reports
    that half unknown rather than failing the cell.
    """
    if metrics.get("cost_basis") == "gpu_hours":
        return {
            key: metrics[key]
            for key in ("cost_basis", "cost_usd", "cost_status", "endpoint_lease", "gpu_hours")
        }
    if table is None:
        cost = {"cost_usd": None, "cost_status": UNKNOWN, "cost_reason": "price_table_unavailable"}
    else:
        cost = price_cell(metrics, table)
    if proofs is None or weekly is None:
        quota = {
            "quota_share": None,
            "quota_status": UNKNOWN,
            "quota_reason": "calibration_unavailable",
        }
    else:
        quota = quota_share(metrics, proofs=proofs, weekly=weekly)
    return {**cost, **quota}


__all__ = [
    "PRICEABLE_TOKEN_SOURCES",
    "REASONING_INSIDE_OUTPUT",
    "WEEKLY_WINDOW_MINUTES",
    "cell_economics",
    "price_cell",
    "quota_share",
    "weekly_windows",
]
