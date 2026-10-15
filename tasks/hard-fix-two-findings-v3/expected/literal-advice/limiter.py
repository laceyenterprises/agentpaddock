class Limiter:
    """Per-client sliding window with integer millisecond timestamps.

    proxy.py defines admission costs and the wait contract. Times supplied to
    each client are nondecreasing. limit and window_ms are positive integers.
    """

    def __init__(self, limit, window_ms):
        self.limit, self.window_ms = limit, window_ms
        self.seen = {}

    def allow(self, client, now_ms, cost=1):
        recent = [
            (t, c) for t, c in self.seen.get(client, []) if now_ms - t < self.window_ms
        ]
        self.seen[client] = recent
        used = sum(c for _, c in recent)
        if used + cost <= self.limit:
            recent.append((now_ms, cost))
            return 0
        return recent[0][0] + self.window_ms - now_ms
