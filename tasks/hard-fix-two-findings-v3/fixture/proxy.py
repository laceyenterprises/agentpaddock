from headers import rejection_headers


def handle(limiter, client, now_ms, cost=1):
    """Process a request costing an integer in [1, limiter.limit].

    Only admitted costs consume capacity. allow returns zero on admission,
    otherwise the SMALLEST positive wait in milliseconds after which this
    SAME cost fits, assuming no intervening admissions. Expirations at the
    exact window boundary free capacity; clients have independent histories.
    Retry-After is the smallest integer number of seconds covering that wait.
    Timestamps and durations are arbitrary Python integers: no float precision
    bound is part of this protocol. Rejected attempts consume no capacity.
    """
    wait = limiter.allow(client, now_ms, cost)
    if wait:
        return 429, rejection_headers(wait)
    return 200, {}
