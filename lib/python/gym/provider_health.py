"""Incremental provider-failure evidence from a launch's harness output.

Only structured error/retry records count: quoted errors in ordinary model
text or tool output must never terminate a worker. No deployment API is used.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

_PROVIDER_ERROR = re.compile(
    r"litellm\.(?:Timeout|ServiceUnavailable|APIConnectionError)"
    r"|API Error:\s*(?:408|5\d\d)\b"
)
_MAX_LINE = 1024 * 1024
_log = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    path: Path | None
    consecutive_limit: int = 2
    timeout_seconds: float = 600.0
    consecutive: int = 0
    failed_since: float | None = None
    cause: str | None = None
    offset: int = 0
    pending: bytes = b""
    discarding: bool = False

    read_page: object = None
    generation: str | None = None
    warned_unreadable: bool = False

    def _lines(self):
        if self.read_page is not None:
            try:
                page = self.read_page(self.offset)
                if not isinstance(page, dict):
                    raise ValueError("invalid host log page")
                if page.get("status") != "found":
                    return
                next_offset = page["nextOffset"]
                text = page["text"]
                if type(next_offset) is not int or next_offset < 0 or not isinstance(text, str):
                    raise ValueError("invalid host log page fields")
                chunk = text.encode()
            except Exception:
                # Host plugins may use their own protocol exception classes.
                # An unreadable page must not advance the cursor or lose a fragment.
                if not self.warned_unreadable:
                    _log.warning("Provider monitoring evidence unavailable")
                    self.warned_unreadable = True
                return
            if (
                page.get("reset")
                or self.generation is not None
                and self.generation != page.get("generation")
            ):
                self.pending = b""
                self.discarding = False
            self.generation = page.get("generation")
            self.offset = next_offset
            yield from self._complete_lines(chunk)
            return
        if self.path is None:
            return
        try:
            with self.path.open("rb") as handle:
                # Drain a fixed file snapshot, not an unbounded growing stream.
                # Keep parser memory bounded while reaching final error records.
                end = os.fstat(handle.fileno()).st_size
                if self.offset > end:
                    self.offset = 0
                    self.pending = b""
                    self.discarding = False
                handle.seek(self.offset)
                while handle.tell() < end:
                    chunk = handle.read(min(_MAX_LINE, end - handle.tell()))
                    if not chunk:
                        break
                    self.offset = handle.tell()
                    yield from self._complete_lines(chunk)
        except FileNotFoundError:
            return
        except OSError as exc:
            if not self.warned_unreadable:
                _log.warning("Provider monitoring cannot read %s: %s", self.path, exc)
                self.warned_unreadable = True

    def _complete_lines(self, chunk: bytes):
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            if self.discarding:
                self.discarding = False
                continue
            if len(line) <= _MAX_LINE:
                yield line
        if len(self.pending) > _MAX_LINE:
            self.pending = b""
            self.discarding = True

    def check(self) -> str | None:
        for line in self._lines():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            retry = kind == "system" and event.get("subtype") == "api_retry"
            text = ""
            if retry:
                text = str(event.get("error", ""))
                status = str(event.get("error_status", ""))
                failed = status == "408" or bool(re.fullmatch(r"5\d\d", status))
                failed = failed or bool(_PROVIDER_ERROR.search(text))
                if failed:
                    self._failure(f"HTTP {status}: {text}" if status else text)
                else:
                    # A different error breaks the consecutive count, but only
                    # successful response progress may reset the elapsed budget.
                    self.consecutive = 0
            elif kind == "result" and event.get("is_error"):
                text = str(event.get("result", ""))
                if _PROVIDER_ERROR.search(text):
                    self._failure(text)
                    # A terminal provider error has already exhausted harness retries.
                    self.consecutive = self.consecutive_limit
            elif kind in ("error", "turn.failed"):
                # Codex JSONL transport errors, not arbitrary assistant content.
                error = event.get("error") or {}
                text = str(error.get("message", "")) if isinstance(error, dict) else str(error)
                text = text or str(event.get("message", ""))
                if _PROVIDER_ERROR.search(text):
                    self._failure(text)
            elif kind in ("turn.completed",):
                self._reset()
            elif kind == "assistant":
                if event.get("error"):
                    message = event.get("message") or {}
                    content = message.get("content", []) if isinstance(message, dict) else []
                    text = " ".join(
                        str(item.get("text", "")) for item in content if isinstance(item, dict)
                    )
                    if _PROVIDER_ERROR.search(text):
                        self._failure(text)
                elif (
                    isinstance(event.get("message"), dict)
                    and event["message"].get("model") != "<synthetic>"
                ):
                    self._reset()
            elif kind == "stream_event":
                stream = event.get("event") or {}
                if isinstance(stream, dict) and stream.get("type") in (
                    "message_start",
                    "content_block_delta",
                    "message_stop",
                ):
                    self._reset()
        expired = self.failed_since is not None and (
            time.monotonic() - self.failed_since >= self.timeout_seconds
        )
        if self.consecutive >= self.consecutive_limit or expired:
            # Do not copy arbitrary endpoint error bodies/fallback route maps.
            match = _PROVIDER_ERROR.search(self.cause or "")
            status = re.search(r"HTTP (408|5\d\d)\b", self.cause or "")
            cause = (
                match.group()
                if match
                else (status.group() if status else "provider transport failure")
            )
            timeout = re.search(r"Timeout passed=(\d+(?:\.\d+)?)", self.cause or "")
            if timeout:
                cause += f" (route timeout={timeout.group(1)}s)"
            return (
                f"provider_unavailable: {cause}; {self.consecutive} consecutive errors; "
                f"budget={self.consecutive_limit} errors or {self.timeout_seconds:g}s "
                "after first error; check route timeout/stream_timeout and endpoint readiness"
            )
        return None

    def _failure(self, cause: str) -> None:
        self.consecutive += 1
        if self.failed_since is None:
            self.failed_since = time.monotonic()
        self.cause = cause

    def _reset(self) -> None:
        self.consecutive = 0
        self.failed_since = None
        self.cause = None
