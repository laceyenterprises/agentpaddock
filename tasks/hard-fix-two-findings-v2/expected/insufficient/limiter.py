class Limiter:
    """Sliding-window rate limit: at most `limit` admitted requests per client
    in any `window_ms` milliseconds.

    allow(client, now_ms) returns 0 when the request is admitted. Otherwise it
    returns the milliseconds until the client's oldest admitted request leaves
    the window, which is when its next request will be admitted. Only admitted
    requests count against the limit.
    """

    def __init__(self, limit, window_ms):
        self.limit = limit
        self.window_ms = window_ms
        self.seen = {}

    def allow(self, client, now_ms):
        recent = [t for t in self.seen.get(client, []) if now_ms - t < self.window_ms]
        self.seen[client] = recent
        if len(recent) >= self.limit:
            return recent[0] + self.window_ms - now_ms
        recent.append(now_ms)
        return 0
