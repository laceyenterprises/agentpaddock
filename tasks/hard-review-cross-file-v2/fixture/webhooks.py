"""Posts registry changes to the configured HTTP endpoints.

Changes are queued when they are published and posted by ``flush``, which the
single-threaded service calls from its main loop between requests, so a slow
endpoint never holds up a registry change. Each queued body is built from the change's snapshot.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections import deque

LOGGER = logging.getLogger(__name__)

# Set at startup from the deployment's webhook configuration.
ENDPOINTS: list[str] = []
# Bodies waiting to be posted, oldest first: (url, body).
PENDING: deque[tuple[str, dict]] = deque()


def post_json(url: str, body: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def deliver_change(kind: str, change: dict) -> None:
    """Queue one body per endpoint for the change snapshot ``change``."""
    body = {"event": kind, **change}
    for url in ENDPOINTS:
        PENDING.append((url, body))


def flush(*, post=post_json, limit: int = 100) -> int:
    """Post up to ``limit`` queued bodies, oldest first; return how many were sent.

    After a failure, that endpoint's remaining bodies stay queued in order for the
    next flush, so each endpoint receives changes in the order they happened and
    one unreachable endpoint does not hold up the others.
    """
    sent, kept, failed = 0, [], set()
    while PENDING and sent < limit:
        url, body = PENDING.popleft()
        if url in failed:
            kept.append((url, body))
            continue
        try:
            post(url, body)
        except OSError as exc:
            LOGGER.warning("webhook %s failed, will retry: %s", url, exc)
            failed.add(url)
            kept.append((url, body))
            continue
        sent += 1
    PENDING.extendleft(reversed(kept))
    return sent
