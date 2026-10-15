def in_freeze(now, start, end, days=range(7)):
    """Evaluate one policy window for a local datetime and two time values.

    policy.py owns the recurrence and boundary contract. Inputs are naive local
    civil times; callers handle timezone conversion before this function.
    """
    if start > end:
        return now.weekday() in days and (now.time() >= start or now.time() < end)
    return now.weekday() in days and start <= now.time() < end
