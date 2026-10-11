def in_quiet_hours(now, start, end):
    """True when the wall-clock time `now` is inside the quiet window [start, end).

    All three are datetime.time values. A window may span midnight:
    start=22:00, end=06:00 is quiet from 22:00 through 05:59. start == end
    means the policy has no quiet hours.
    """
    if start <= end:
        return start <= now < end
    return now >= start or now < end
