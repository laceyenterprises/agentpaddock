def wraps_midnight(start, end):
    """True when [start, end) runs past midnight; an empty window never does."""
    return end < start


def in_freeze(now, start, end):
    """True when the wall-clock time `now` falls in the freeze window [start, end).

    All three are datetime.time values. A window may span midnight:
    start=22:00, end=06:00 freezes deploys from 22:00 through 05:59.
    """
    if wraps_midnight(start, end):
        return not end <= now < start
    return start <= now < end
