from headers import rejection_headers


def handle(limiter, client, now_ms):
    """Admit or reject one request; returns (status, headers).

    Our SDK clients obey Retry-After: on a 429 they sleep exactly that many
    seconds, retry once, and surface the error to the user if the retry is
    rejected too. So Retry-After must be the smallest whole number of seconds
    after which the retry is admitted: shorter and the retry is rejected as
    well, longer and the user waits for nothing.
    """
    wait_ms = limiter.allow(client, now_ms)
    if wait_ms:
        return 429, rejection_headers(wait_ms)
    return 200, {}
