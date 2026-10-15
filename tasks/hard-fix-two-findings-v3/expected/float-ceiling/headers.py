import math


def rejection_headers(wait_ms):
    return {"Retry-After": str(math.ceil(wait_ms / 1000))}
