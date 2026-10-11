"""Per-cell ledger metrics (MEG-02): what a graded run actually spent.

Built from every ``worker_runs`` row of the cell's launch request, as returned
raw by ``session_ledger.api.fetch_launch_request_worker_runs``. A launch can
have several runs after a retry, and a retried run spends quota too, so tokens
are summed across all of them and wall time spans the first start to the last
end.

Nothing here is zero-filled. A column the ledger left NULL on any run makes that
sum ``None``, names the column in ``missing`` and sets ``metrics_complete`` to
false; a report excludes such a cell rather than reading it as free.

Pure stdlib, so the gym core and its unit suite need neither ledger nor pool.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

#: worker_runs token columns summed across a launch's runs.
TOKEN_COLUMNS: tuple[str, ...] = (
    "token_usage_input",
    "token_usage_cache_read",
    "token_usage_output",
    "token_usage_reasoning",
    "token_usage_running_total",
)

_BY_MODEL_COLUMN = "token_usage_by_model_json"
# Provider snapshot suffix: Anthropic ``claude-haiku-4-5-20251001``, OpenAI
# ``gpt-4o-2024-08-06``.
_SNAPSHOT_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")
# First-party provider prefixes name the vendor's own API, so
# ``anthropic/claude-opus-5-5`` is the model a native harness reports as
# ``claude-opus-5-5``. Hosted routes (``fireworks/``, ``together/``...) stay
# distinct: they serve the same weights through another provider.
_FIRST_PARTY_PREFIXES = ("anthropic/", "openai/")


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    # The ledger writes UTC; a naive stamp is read as UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _by_model(value: Any) -> dict[str, Any] | None:
    """Decode ``token_usage_by_model_json`` (TEXT on SQLite, JSON on Postgres)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _is_model_key(key: str) -> bool:
    """Ignore accounting buckets using the spend analytics model-key contract.

    Kept pure stdlib so hermetic gym aggregation needs no worker-pool install;
    a parity regression checks cwp_dispatch.budget_analytics_core.is_model_key.
    Token totals still come from run columns, including floor-only spending.
    """
    value = str(key).strip()
    return (
        bool(value)
        and value not in {"unknown", "live-floor"}
        and not (value.startswith("<") and value.endswith(">"))
    )


def _output_tokens(bucket: Any) -> int | None:
    if not isinstance(bucket, dict):
        return None
    value = bucket.get("output")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def model_matches(observed: str, requested: str) -> bool:
    """True when a by-model key names the requested model.

    Strips a known outer router prefix and a dated provider snapshot suffix,
    retaining the provider route itself.
    """
    return _model_key(observed) == _model_key(requested)


def _model_key(value: str) -> str:
    """Keep provider routes while removing the known outer router and snapshot."""
    key = value.strip().lower()
    if key.startswith("litellm-local-openai/"):
        key = key.removeprefix("litellm-local-openai/")
    elif key.startswith("litellm-local/"):
        key = key.removeprefix("litellm-local/")
    for prefix in _FIRST_PARTY_PREFIXES:
        key = key.removeprefix(prefix)
    return _SNAPSHOT_SUFFIX.sub("", key)


def launch_metrics(
    runs: list[dict[str, Any]], *, worker_class: str | None = None
) -> dict[str, Any]:
    """Aggregate a launch's worker runs into one cell's metrics.

    ``runs`` is ordered oldest first. ``worker_class`` is the class the launch
    actually ran as (``launch_requests.worker_class``, after any admission
    fallback). ``observed_models`` maps each model in the by-model breakdown to
    its output tokens; ``majority_model`` carries the most of them.
    """
    missing: set[str] = set()
    tokens: dict[str, int | None] = {}
    for column in TOKEN_COLUMNS:
        values = [run.get(column) for run in runs]
        if not runs or any(value is None for value in values):
            tokens[column] = None
            missing.add(column)
        else:
            tokens[column] = sum(int(value) for value in values)

    starts = [_parse_timestamp(run.get("started_at")) for run in runs]
    ends = [_parse_timestamp(run.get("ended_at")) for run in runs]
    wall_seconds: float | None = None
    if not runs or any(stamp is None for stamp in starts):
        missing.add("started_at")
    if not runs or any(stamp is None for stamp in ends):
        missing.add("ended_at")
    if runs and "started_at" not in missing and "ended_at" not in missing:
        wall_seconds = (max(ends) - min(starts)).total_seconds()  # type: ignore[type-var]

    observed: dict[str, int] = {}
    for run in runs:
        breakdown = _by_model(run.get(_BY_MODEL_COLUMN))
        if breakdown is None:
            missing.add(_BY_MODEL_COLUMN)
            continue
        for model, bucket in breakdown.items():
            if not _is_model_key(model):
                continue
            output = _output_tokens(bucket)
            if output is None:
                missing.add(_BY_MODEL_COLUMN)
                continue
            observed[str(model)] = observed.get(str(model), 0) + output
    if not runs or not any(observed.values()):
        # Nothing attributes the output to a model, so the model is unverifiable.
        missing.add(_BY_MODEL_COLUMN)
    majority_model = _majority_model(observed)

    if worker_class is None:
        missing.add("worker_class")
    resolved_levels = [run.get("resolved_reasoning_level") for run in runs]
    return {
        "run_ids": [str(run.get("run_id")) for run in runs],
        "attempts": len(runs),
        **tokens,
        # Per run, aligned with ``run_ids``: which capture path wrote the token
        # columns. MEG-03 prices only sources whose input column is total input.
        "token_usage_sources": [
            str(run["token_usage_source"]) if run.get("token_usage_source") else None
            for run in runs
        ],
        "wall_seconds": wall_seconds,
        "worker_class": worker_class,
        "resolved_reasoning_level": next(
            (str(level) for level in reversed(resolved_levels) if level), None
        ),
        "observed_models": dict(sorted(observed.items())),
        "majority_model": majority_model,
        "side_models": sorted(m for m in observed if _model_key(m) != majority_model),
        "missing": sorted(missing),
        "metrics_complete": not missing,
    }


def _majority_model(observed: dict[str, int]) -> str | None:
    grouped: dict[str, int] = {}
    for model, output in observed.items():
        key = _model_key(model)
        grouped[key] = grouped.get(key, 0) + output
    return max(grouped, key=lambda m: (grouped[m], m)) if grouped else None


def combine_launch_metrics(measurements: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Sum execution spend across gym-owned retries, retaining unknowns."""
    parts = [item if item is not None else launch_metrics([]) for item in measurements]
    result = dict(parts[-1])
    missing = {name for item in parts for name in item.get("missing", [])}
    for name in (*TOKEN_COLUMNS, "wall_seconds"):
        values = [item.get(name) for item in parts]
        result[name] = None if any(value is None for value in values) else sum(values)
        if result[name] is None:
            missing.add(name)
    for name in ("run_ids", "token_usage_sources"):
        result[name] = [value for item in parts for value in item.get(name, [])]
    result["attempts"] = sum(item.get("attempts", 0) for item in parts)
    observed: dict[str, int] = {}
    for item in parts:
        for model, count in item.get("observed_models", {}).items():
            observed[model] = observed.get(model, 0) + count
    result["observed_models"] = dict(sorted(observed.items()))
    majority = _majority_model(observed)
    result["majority_model"] = majority
    result["side_models"] = sorted(model for model in observed if _model_key(model) != majority)
    if len({item.get("worker_class") for item in parts}) != 1:
        result["worker_class"] = None
        missing.add("worker_class")
    result["missing"] = sorted(missing)
    result["metrics_complete"] = not missing and all(
        item.get("metrics_complete", False) for item in parts
    )
    return result


def apply_requested(
    metrics: dict[str, Any], *, model: str | None, worker_class: str | None
) -> bool:
    """Judge a cell's metrics against what was requested; True is a mismatch.

    The requested model must carry a strict majority of the cell's output
    tokens. Every other observed model is a side model (claude-code calls a
    small one) and does not invalidate the cell. A worker class other than the
    requested one is also a mismatch. Unverifiable evidence (no by-model
    breakdown, no resolved class) is not a mismatch; ``metrics_complete`` is
    already false for it, which excludes the cell on its own.
    """
    observed: dict[str, int] = metrics.get("observed_models") or {}
    mismatch = False
    if model:
        metrics["side_models"] = sorted(m for m in observed if not model_matches(m, model))
        total = sum(observed.values())
        if total > 0:
            requested = sum(n for m, n in observed.items() if model_matches(m, model))
            mismatch = requested * 2 <= total
    resolved_class = metrics.get("worker_class")
    if worker_class and resolved_class and resolved_class != worker_class:
        mismatch = True
    return mismatch


__all__ = ["TOKEN_COLUMNS", "apply_requested", "launch_metrics", "model_matches"]
