"""Build-time settings for the site (PDW-04), with the defaults the public site uses.

A setting changes what the pages point at, never a number on them. Override a
setting with a small JSON object passed to ``scripts/build_site.py --config``;
unknown keys are refused so a typo cannot silently fall back to a default.

``live_feed_url``
    Where the home page's ticker reads what is on track: the submission API's
    public ``GET /live`` (``paddock-live/v1``, see ``design/web/data/``). An
    absolute ``https://`` URL or a site-relative path. An empty string turns
    the fetch off, so the ticker shows only its labelled sample runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

#: The submission API host proposed for the live feed.
DEFAULT_LIVE_FEED_URL = "https://api.agentpaddock.ai/live"

DEFAULTS: dict[str, Any] = {"live_feed_url": DEFAULT_LIVE_FEED_URL}


class ConfigError(ValueError):
    """A site config that the build refuses."""


def _check_feed_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("live_feed_url must be a string")
    if not value:
        return value
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or value.startswith("//"):
        if parts.scheme != "https" or not parts.netloc:
            raise ConfigError(
                "live_feed_url must be an https:// URL or a relative path"
            )
        if parts.username or parts.password:
            raise ConfigError("live_feed_url must not carry credentials")
    elif value.startswith("/"):
        raise ConfigError(
            "live_feed_url must be relative to the page (the site links relatively)"
        )
    if any(ch.isspace() or ch in "\"'<>`" for ch in value):
        raise ConfigError("live_feed_url holds a character a URL cannot")
    return value


def resolve(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The defaults with ``overrides`` applied, each value checked."""
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(DEFAULTS))
    if unknown:
        raise ConfigError(f"unknown site config keys: {unknown}")
    config = {**DEFAULTS, **overrides}
    config["live_feed_url"] = _check_feed_url(config["live_feed_url"])
    return config


def load(path: Path | str | None) -> dict[str, Any]:
    """The config from a JSON object at ``path``, or the defaults without one."""
    if path is None:
        return resolve()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read site config {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("the site config must be a JSON object")
    return resolve(raw)
