from redact import redact

ELLIPSIS = "…"


def truncate(message, limit):
    """Fit message into limit bytes of UTF-8, the pager gateway's hard cap.

    A message that fits is returned unchanged. Otherwise return the longest
    prefix of message that still fits with ELLIPSIS appended, followed by
    ELLIPSIS. A character is never split. limit is at least 3.
    """
    encoded = message.encode("utf-8")
    if len(encoded) <= limit:
        return message
    budget = limit - len(ELLIPSIS.encode("utf-8"))
    # A cut inside a multi-byte character leaves an incomplete tail; drop it.
    return encoded[:budget].decode("utf-8", errors="ignore") + ELLIPSIS


def format_page(alert, limit=160):
    """The page text for one alert: redacted first, then fitted to the gateway."""
    return truncate(redact(f"[{alert['severity']}] {alert['summary']}"), limit)
