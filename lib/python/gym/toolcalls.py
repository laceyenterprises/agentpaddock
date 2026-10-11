"""Ordered tool-call extraction from session-ledger transcript rows.

Today the session ledger only *counts* tool activity in
``transcript_entries.tool_metadata_json`` (worker_observability aggregates
tokens, never names). GYM needs the ordered NAMES and ARGS so a downstream
verifier can assert both that a tool WAS called (with which args) and that a
tool was NOT — so this extractor lives here in the gym module, not in the
ledger and not in Foundry (SPEC §3 GYM-01).

Input rows are session-ledger ``transcript_entries`` rows (sqlite3.Row or plain
dicts). ``tool_metadata_json`` may arrive as a JSON string (as stored) or as an
already-decoded dict. The canonical shape written by the claude_code adapter is:

    {"tool_calls": [{"id", "call_id", "name", "arguments"}, ...], "stop_reason": ...}

We also tolerate the codex/openclaw variants (a top-level name, or a bare
``toolNames`` list) so the snapshot survives across harnesses.

``control_evaluations`` rows (migration 014) are the richer surface for the
NOT-called assertion: a governance control that fired with a deny/block action
proves a tool was refused rather than merely absent.
"""

from __future__ import annotations

import json
from typing import Any


def _row_get(row: Any, key: str) -> Any:
    """Read a column from a sqlite3.Row or a plain dict, defaulting to None."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        keys = row.keys()
    except AttributeError:
        return getattr(row, key, None)
    if key in keys:
        return row[key]
    return None


def _decode_metadata(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _calls_from_metadata(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the individual tool calls out of one row's tool_metadata dict."""
    raw_calls = meta.get("tool_calls")
    if isinstance(raw_calls, list):
        out: list[dict[str, Any]] = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            name = call.get("name")
            if not isinstance(name, str) or not name:
                continue
            out.append(
                {
                    "name": name,
                    "arguments": call.get("arguments"),
                    "call_id": call.get("call_id") or call.get("id"),
                }
            )
        return out
    # Single top-level call (some codex rows).
    name = meta.get("name") or meta.get("tool_name")
    if isinstance(name, str) and name:
        return [
            {
                "name": name,
                "arguments": meta.get("arguments") or meta.get("input"),
                "call_id": meta.get("call_id") or meta.get("id"),
            }
        ]
    # Names-only fallback (openclaw toolNames).
    tool_names = meta.get("toolNames")
    if isinstance(tool_names, list):
        return [
            {"name": n, "arguments": None, "call_id": None}
            for n in tool_names
            if isinstance(n, str) and n
        ]
    return []


def extract_tool_calls(rows: list[Any]) -> list[dict[str, Any]]:
    """Return the ordered tool calls across transcript rows.

    Each entry: ``{"ordinal", "name", "arguments", "call_id"}``. Ordering is by
    the row ``ordinal`` (stable source order); ties keep input order.
    """
    indexed: list[tuple[int, int, dict[str, Any]]] = []
    for input_index, row in enumerate(rows):
        event_type = _row_get(row, "event_type")
        meta = _decode_metadata(_row_get(row, "tool_metadata_json"))
        if meta is None:
            continue
        # Only harvest from tool-call-bearing rows. A tool_result row carries
        # tool_metadata too, but it is not a call the agent made.
        if event_type not in (None, "tool_call"):
            continue
        ordinal_raw = _row_get(row, "ordinal")
        try:
            ordinal = int(ordinal_raw) if ordinal_raw is not None else input_index
        except (TypeError, ValueError):
            ordinal = input_index
        for call in _calls_from_metadata(meta):
            indexed.append((ordinal, input_index, call))

    indexed.sort(key=lambda t: (t[0], t[1]))
    return [
        {
            "ordinal": ordinal,
            "name": call["name"],
            "arguments": call["arguments"],
            "call_id": call["call_id"],
        }
        for ordinal, _input_index, call in indexed
    ]


def extract_controls(rows: list[Any]) -> list[dict[str, Any]]:
    """Project control_evaluations rows into the snapshot's control surface.

    Keeps just the fields a verifier needs to assert a control fired / a tool
    was refused: step name, the enforcement action, and the evaluation status.
    """
    controls: list[dict[str, Any]] = []
    for row in rows:
        step_name = _row_get(row, "step_name")
        if not isinstance(step_name, str) or not step_name:
            continue
        metadata = _decode_metadata(_row_get(row, "metadata_json")) or {}
        controls.append(
            {
                "step_name": step_name,
                "step_type": _row_get(row, "step_type"),
                "enforcement_action": _row_get(row, "enforcement_action"),
                "evaluation_status": _row_get(row, "evaluation_status"),
                "tool_call_id": _row_get(row, "tool_call_id") or metadata.get("tool_call_id"),
            }
        )
    return controls


def build_snapshot(
    transcript_rows: list[Any],
    control_rows: list[Any] | None = None,
    fallback_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the BENCH_TOOLCALLS snapshot handed to the out-of-band verifier.

    Shape:

        {"calls": [{"ordinal", "name", "arguments", "call_id"}, ...],
         "controls": [{"step_name", "step_type", "enforcement_action",
                       "evaluation_status", "tool_call_id"}, ...]}

    ``fallback_calls`` (``{"name", "arguments", "call_id"}`` in source order,
    from :mod:`gym.harness_toolcalls`) stand in only when the transcript rows
    yield no call, so a run the ledger does see is graded exactly as before.
    Calls may also carry optional ``original_name`` and ``original_arguments``
    audit fields, retained in the snapshot. For agy, ``name`` can be a mapped
    compatibility name (read/bash or Claude-cased edits); ``arguments`` retains
    source keys and adds file_path/command aliases. The audit fields preserve
    the unmodified harness name and arguments; unknown tools pass through.
    """
    calls = extract_tool_calls(transcript_rows)
    if not calls and fallback_calls:
        calls = [
            {
                **{
                    key: call[key] for key in ("original_name", "original_arguments") if key in call
                },
                "ordinal": index,
                "name": call.get("name"),
                "arguments": call.get("arguments") if call.get("arguments") is not None else {},
                "call_id": call.get("call_id"),
            }
            for index, call in enumerate(fallback_calls)
            if isinstance(call, dict) and call.get("name")
        ]
    return {
        "calls": calls,
        "controls": extract_controls(control_rows or []),
    }


__all__ = [
    "extract_tool_calls",
    "extract_controls",
    "build_snapshot",
]
