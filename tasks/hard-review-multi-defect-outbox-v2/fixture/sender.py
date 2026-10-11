"""HTTP delivery for outbox messages."""

from __future__ import annotations

import http.client


class DeliveryError(Exception):
    """A delivery attempt failed; a later attempt may succeed."""


class RetryLater(DeliveryError):
    """The endpoint asked the client to wait (429 or 503 with Retry-After)."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentFailure(DeliveryError):
    """The endpoint rejected the message itself; retrying cannot succeed."""


# 408 and 429 are the client-error statuses that a retry can clear.
RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})


def retry_after_seconds(value: str | None) -> float | None:
    """Parse a delta-seconds Retry-After header; None when absent or an HTTP date."""
    if value is None or not value.strip().isdigit():
        return None
    return float(value.strip())


class HttpSender:
    def __init__(self, host: str, port: int, *, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    def send(self, topic: str, body: str) -> None:
        """POST one message; raise a DeliveryError (or a subclass) on failure."""
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            conn.request(
                "POST",
                f"/topics/{topic}",
                body=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DeliveryError(f"{topic}: {exc}") from exc
        finally:
            conn.close()
        status = response.status
        if status in (429, 503):
            wait = retry_after_seconds(response.getheader("Retry-After"))
            if wait is not None:
                raise RetryLater(f"{topic}: HTTP {status}", wait)
        if 400 <= status < 500 and status not in RETRYABLE_CLIENT_STATUSES:
            raise PermanentFailure(f"{topic}: HTTP {status}")
        if status >= 300:
            raise DeliveryError(f"{topic}: HTTP {status}")
