def rejection_headers(wait_ms):
    """Headers for a 429 response; `wait_ms` is the limiter's wait, always positive."""
    return {"Retry-After": str(-(-wait_ms // 1000))}
