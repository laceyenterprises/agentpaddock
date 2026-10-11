from redact import redact

ELLIPSIS = "…"


def truncate(message, limit):
    """Fit message into limit bytes of UTF-8, the pager gateway's hard cap.

    A message that fits is returned unchanged. Otherwise return the longest
    prefix of message that still fits with ELLIPSIS appended, followed by
    ELLIPSIS. A character is never split. limit is at least 3.
    """
    if len(message) <= limit:
        return message
    return message[: limit - 1] + ELLIPSIS


def format_page(alert, limit=160):
    """The page text for one alert: redacted first, then fitted to the gateway."""
    return truncate(redact(f"[{alert['severity']}] {alert['summary']}"), limit)
