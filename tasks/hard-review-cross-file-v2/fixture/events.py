"""An in-process event bus for registry changes."""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)

Handler = Callable[[str, object], None]


class EventBus:
    """Calls each handler subscribed to a matching pattern, in subscription order.

    A handler that raises is logged and skipped, so one broken subscriber cannot
    stop the others or fail the change that published the event.
    """

    def __init__(self) -> None:
        self._subscriptions: list[tuple[str, Handler]] = []

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subscriptions.append((pattern, handler))

    def publish(self, kind: str, payload: object) -> int:
        """Deliver ``payload`` to every matching handler; return how many succeeded."""
        delivered = 0
        for pattern, handler in self._subscriptions:
            if not fnmatch.fnmatchcase(kind, pattern):
                continue
            try:
                handler(kind, payload)
            except Exception:
                LOGGER.exception("subscriber %r failed on %s", handler, kind)
                continue
            delivered += 1
        return delivered
