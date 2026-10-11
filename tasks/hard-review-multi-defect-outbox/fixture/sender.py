"""HTTP delivery for outbox messages."""

from __future__ import annotations

import http.client


class DeliveryError(Exception):
    """A delivery attempt failed; a later attempt may succeed."""


class PermanentFailure(DeliveryError):
    """The endpoint rejected the message itself; retrying cannot succeed."""


# 408 and 429 are the client-error statuses that a retry can clear.
RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})


class HttpSender:
    def __init__(self, host: str, port: int, *, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    def send(self, topic: str, body: str) -> None:
        """POST one message; raise DeliveryError or PermanentFailure on failure."""
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
        if 400 <= status < 500 and status not in RETRYABLE_CLIENT_STATUSES:
            raise PermanentFailure(f"{topic}: HTTP {status}")
        if status >= 300:
            raise DeliveryError(f"{topic}: HTTP {status}")
