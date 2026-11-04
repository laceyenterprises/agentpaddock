"""Canonical JSON and the strict JSON reader for signed manifests.

:func:`canonical_json` is byte-for-byte the encoding of ``canonical_json`` in
``scripts/leaderboard.py``, which CONTRIBUTING names as the signing encoding.
The Lambda ships without the rest of the repository, so it carries this copy;
``tests/test_submission_api.py`` pins the two together.
"""

from __future__ import annotations

import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 canonical JSON; raises ``UnicodeEncodeError`` on lone surrogates."""
    return canonical_json(value).encode("utf-8")


class StrictJSONError(ValueError):
    """The text is not JSON the signing contract accepts."""


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise StrictJSONError("duplicate object key")
        result[key] = value
    return result


def _no_float(_text: str) -> Any:
    raise StrictJSONError("floats are not allowed")


def _no_constant(_text: str) -> Any:
    raise StrictJSONError("non-finite numbers are not allowed")


def strict_loads(data: bytes) -> Any:
    """Parse UTF-8 JSON with no duplicate keys, floats or NaN/Infinity.

    The result must also re-encode as UTF-8 (no lone surrogates), so that its
    canonical form, which the signature covers, is well defined.
    """
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
        canonical_bytes(value)
    except StrictJSONError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise StrictJSONError(type(exc).__name__) from None
    return value
