def rejection_headers(wait_ms):
    seconds, remainder = divmod(wait_ms, 1000)
    return {"Retry-After": str(seconds + bool(remainder))}
