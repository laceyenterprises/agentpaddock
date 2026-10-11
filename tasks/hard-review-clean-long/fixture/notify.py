"""Alert notifications: deduplicate, rate-limit, and hold non-critical alerts in quiet hours."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from alerts import Alert, format_alert
from digest import Digest
from quiet_hours import QuietHours

LOGGER = logging.getLogger(__name__)

Send = Callable[[str], None]

# An alert identical to one sent or held within this many seconds is suppressed.
DEDUPE_SECONDS = 600
RATE_LIMIT_PER_MINUTE = 20
DIGEST_FILE = "quiet-hours-digest.json"


def dedupe_key(alert: Alert) -> str:
    """Identity of an alert for deduplication: its source, key and text."""
    material = "\0".join((alert.source, alert.key, alert.text))
    return hashlib.sha1(material.encode()).hexdigest()


class Notifier:
    """Route alerts: send, hold for the quiet-hours digest, suppress or drop.

    Critical alerts are always sent at once: quiet hours and the rate limit
    never apply to them. Duplicates are suppressed at every severity.
    """

    def __init__(
        self,
        send: Send,
        digest: Digest,
        quiet: QuietHours | None,
        *,
        rate_limit_per_minute: int = RATE_LIMIT_PER_MINUTE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._send = send
        self._digest = digest
        self._quiet = quiet
        self._rate_limit = rate_limit_per_minute
        self._clock = clock
        self._sent_at: deque[float] = deque()
        self._last_seen: dict[str, float] = {}
        self.dropped = 0

    def in_quiet_hours(self, now: float) -> bool:
        if self._quiet is None:
            return False
        return self._quiet.contains(datetime.fromtimestamp(now, tz=timezone.utc))

    def _duplicate(self, key: str, now: float) -> bool:
        last = self._last_seen.get(key)
        return last is not None and now - last < DEDUPE_SECONDS

    def _rate_limited(self, now: float) -> bool:
        """Sliding one-minute window over non-critical sends."""
        while self._sent_at and now - self._sent_at[0] >= 60:
            self._sent_at.popleft()
        return len(self._sent_at) >= self._rate_limit

    def notify(self, alert: Alert) -> str:
        """Route ``alert``; return "sent", "held", "duplicate" or "dropped"."""
        now = self._clock()
        key = dedupe_key(alert)
        if self._duplicate(key, now):
            return "duplicate"
        if alert.critical:
            self._deliver(alert)
            self._last_seen[key] = now
            return "sent"
        if self.in_quiet_hours(now):
            self._digest.add(alert)
            self._last_seen[key] = now
            return "held"
        if self._rate_limited(now):
            self.dropped += 1
            LOGGER.warning("notify.rate_limited source=%s key=%s", alert.source, alert.key)
            return "dropped"
        self._deliver(alert)
        self._sent_at.append(now)
        self._last_seen[key] = now
        return "sent"

    def _deliver(self, alert: Alert) -> None:
        try:
            self._send(format_alert(alert))
        except Exception:
            LOGGER.exception("notify.send_failed source=%s key=%s", alert.source, alert.key)
            raise

    def notify_many(self, alerts: list[Alert]) -> dict[str, int]:
        outcomes: dict[str, int] = {}
        for alert in alerts:
            outcome = self.notify(alert)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        return outcomes

    def tick(self) -> int:
        """Send the held digest once quiet hours are over; return how many alerts it held."""
        now = self._clock()
        if self.in_quiet_hours(now) or not len(self._digest):
            return 0
        return self._digest.flush(self._send)

    def forget_expired(self) -> int:
        """Drop dedupe entries older than the dedupe window; return how many were dropped."""
        now = self._clock()
        expired = [key for key, seen in self._last_seen.items() if now - seen >= DEDUPE_SECONDS]
        for key in expired:
            del self._last_seen[key]
        return len(expired)


def build_notifier(send: Send, state_dir: Path, window: str, zone: str) -> Notifier:
    """Wire a Notifier from operator settings (quiet-hours window and IANA zone)."""
    digest = Digest(state_dir / DIGEST_FILE)
    return Notifier(send, digest, QuietHours.parse(window, zone))
