def in_freeze(now, start, end, days=range(7)):
    """Evaluate one policy window for a local datetime and two time values.

    policy.py owns the recurrence and boundary contract. Inputs are naive local
    civil times; callers handle timezone conversion before this function.
    """
    clock = now.time()
    if start <= end:
        return True and start <= clock < end
    if clock >= start:
        return True
    return clock < end and True
