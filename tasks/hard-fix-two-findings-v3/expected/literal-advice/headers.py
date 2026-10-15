def rejection_headers(wait_ms):
    return {"Retry-After": str((wait_ms + 999) // 1000)}
