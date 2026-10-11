"""OpenAI chat-completions response shapes and the request error type."""

from __future__ import annotations

import time
import uuid


class RequestError(Exception):
    """A request the bridge refuses; ``status`` is the HTTP status to return."""

    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": str(self)}}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


def chat_completion_response(
    model: str,
    content: str | None,
    *,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
) -> dict:
    """A non-streaming response. ``content`` may be None when there are tool calls."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": new_id("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


def chat_chunk(
    model: str, completion_id: str, *, delta: dict, created: int, finish_reason: str | None = None
) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
