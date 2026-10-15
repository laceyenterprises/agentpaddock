class Limiter:
    def __init__(self, limit, window_ms):
        self.limit, self.window_ms = limit, window_ms
        self.seen = {}

    def allow(self, client, now_ms, cost=1):
        recent = [
            (t, c) for t, c in self.seen.get(client, []) if t > now_ms - self.window_ms
        ]
        self.seen[client] = recent
        if sum(c for t, c in recent) + cost <= self.limit:
            recent.append((now_ms, cost))
            return 0
        waits = sorted({t + self.window_ms - now_ms for t, c in recent})
        return next(
            wait
            for wait in waits
            if sum(c for t, c in recent if t + self.window_ms > now_ms + wait) + cost
            <= self.limit
        )
