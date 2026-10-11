"""Quiet hours: a daily local-time window in which only critical alerts are sent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

_WINDOW = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


class QuietHoursError(ValueError):
    """The quiet-hours setting is malformed."""


def _clock(hour: str, minute: str) -> time:
    h, m = int(hour), int(minute)
    if h > 23 or m > 59:
        raise QuietHoursError(f"invalid clock time {hour}:{minute}")
    return time(h, m)


@dataclass(frozen=True)
class QuietHours:
    """A half-open local-time window [start, end) in ``zone``."""

    start: time
    end: time
    zone: ZoneInfo

    @classmethod
    def parse(cls, window: str, zone: str) -> QuietHours | None:
        """Parse ``HH:MM-HH:MM`` in the IANA ``zone``; an empty window disables quiet hours."""
        window = window.strip()
        if not window:
            return None
        match = _WINDOW.match(window)
        if match is None:
            raise QuietHoursError(f"quiet hours must be HH:MM-HH:MM, got {window!r}")
        start = _clock(*match.group(1, 2))
        end = _clock(*match.group(3, 4))
        if start == end:
            raise QuietHoursError("quiet hours start and end must differ")
        return cls(start, end, ZoneInfo(zone))

    def contains(self, moment: datetime) -> bool:
        """True when the timezone-aware ``moment`` falls inside the window.

        ``moment`` may be in any zone; it is converted to the window's zone
        first. A window whose start is later than its end wraps midnight.
        """
        if moment.tzinfo is None:
            raise QuietHoursError("contains() needs a timezone-aware datetime")
        local = moment.astimezone(self.zone).time()
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end

    def describe(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M} {self.zone.key}"
