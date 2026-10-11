import math


def rejection_headers(wait_ms):
    """Headers for a 429 response; `wait_ms` is the limiter's wait, always positive."""
    seconds = math.ceil(wait_ms / 1000)
    return {"Retry-After": f"{seconds}"}
