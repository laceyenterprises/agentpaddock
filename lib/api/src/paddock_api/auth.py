"""GitHub-token authentication, resolved per request and never stored.

A request carries ``Authorization: Bearer <GitHub token>``. The token is sent
once to the GitHub user API to learn the account login, then dropped: it is
never cached, persisted, logged or echoed, and no exception raised here
carries it. Any token GitHub accepts for ``GET /user`` works; it needs no
scopes.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Protocol

GITHUB_USER_API = "https://api.github.com/user"
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
# Token characters GitHub issues; anything else cannot be a token, so it is
# refused before any network call and never reaches a header.
_TOKEN = re.compile(r"[A-Za-z0-9_.\-]{1,255}")


class AuthUnavailable(RuntimeError):
    """GitHub could not answer (outage, rate limit, network); not the caller's fault."""


class AccountResolver(Protocol):
    def resolve(self, token: str) -> str | None:
        """The GitHub login the token belongs to, or None when GitHub rejects it."""


def bearer_token(authorization: str | None) -> str | None:
    """The token of a well-formed ``Bearer`` header value, else None."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not _TOKEN.fullmatch(token):
        return None
    return token


def valid_login(login: object) -> bool:
    return isinstance(login, str) and bool(_LOGIN.fullmatch(login))


class GitHubUserResolver:
    """Resolve a token with one ``GET https://api.github.com/user`` call."""

    def __init__(self, timeout: float = 5.0, opener=urllib.request.urlopen) -> None:
        self._timeout = timeout
        self._open = opener

    def resolve(self, token: str) -> str | None:
        request = urllib.request.Request(
            GITHUB_USER_API,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "agentpaddock-submission-api",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                payload = json.loads(response.read(65536).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            remaining = (
                exc.headers.get("x-ratelimit-remaining") if exc.headers else None
            )
            exc.close()
            if status == 401 or (status == 403 and remaining != "0"):
                return None
            raise AuthUnavailable(f"github user api status {status}") from None
        except (OSError, ValueError):
            raise AuthUnavailable("github user api unreachable") from None
        login = payload.get("login") if isinstance(payload, dict) else None
        if not valid_login(login):
            raise AuthUnavailable("github user api returned no login")
        return login
