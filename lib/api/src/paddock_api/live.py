"""Strict public ticker document contract; no grading or answer fields."""

from __future__ import annotations

import datetime
import re
from typing import Any

FIELDS = {"schema", "updated", "updatedIso", "racing", "ticks"}
TICK_FIELDS = {"who", "what", "kind", "flagName", "flag", "when"}
KINDS = {"racing", "passed", "dnf", "rerun"}


def validate_document(document: Any) -> None:
    """Raise ValueError unless this is a bounded paddock-live/v1 document."""
    if not isinstance(document, dict) or set(document) != FIELDS:
        raise ValueError("document fields")
    if document["schema"] != "paddock-live/v1":
        raise ValueError("schema")
    stamp = document["updatedIso"]
    if not isinstance(stamp, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", stamp
    ):
        raise ValueError("timestamp")
    datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    if document["updated"] != stamp[11:16] + "Z":
        raise ValueError("display timestamp")
    racing = document["racing"]
    if type(racing) is not int or racing < 0:
        raise ValueError("racing")
    ticks = document["ticks"]
    if not isinstance(ticks, list) or len(ticks) > 12:
        raise ValueError("ticks")
    for tick in ticks:
        if not isinstance(tick, dict) or set(tick) != TICK_FIELDS:
            raise ValueError("tick fields")
        if any(
            not isinstance(value, str)
            or not 0 < len(value) <= 256
            or not value.isprintable()
            for value in tick.values()
        ):
            raise ValueError("tick text")
        if tick["kind"] not in KINDS or not re.fullmatch(
            r"#[0-9A-Fa-f]{6}", tick["flag"]
        ):
            raise ValueError("tick kind or colour")
