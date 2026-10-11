"""Retry delays shared by the outbox dispatcher and the webhook client."""

from __future__ import annotations

BASE_SECONDS = 30.0
CAP_SECONDS = 3600.0


def delay(attempt: int) -> float:
    """Seconds to wait after failed attempt number ``attempt``.

    ``attempt`` is 1-based: the first attempt that fails is attempt 1 and waits
    BASE_SECONDS. Each later attempt doubles the wait, up to CAP_SECONDS.
    """
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt is a 1-based attempt number")
    return min(CAP_SECONDS, BASE_SECONDS * 2 ** (attempt - 1))
