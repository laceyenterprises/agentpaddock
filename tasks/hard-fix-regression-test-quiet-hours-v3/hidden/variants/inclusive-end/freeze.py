def in_freeze(now, start, end, days=range(7)):
    """Evaluate one policy window for a local datetime and two time values.

    policy.py owns the recurrence and boundary contract. Inputs are naive local
    civil times; callers handle timezone conversion before this function.
    """
    clock = now.time()
    if start == end:
        return False
    if start < end:
        return now.weekday() in days and start <= clock <= end
    if clock >= start:
        return now.weekday() in days
    return clock <= end and (now.weekday() - 1) % 7 in days
