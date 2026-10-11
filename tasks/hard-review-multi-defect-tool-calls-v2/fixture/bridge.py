"""OpenAI-compatible chat completions over a local model backend.

A backend exposes ``model_name``, ``tokenizer``, ``generate(messages,
max_tokens, tools=None) -> (text, finish_reason)`` and ``stream(messages,
max_tokens)``, which yields ``(text_delta, finish_reason_or_None)`` pairs.
``generate`` renders ``tools`` into the prompt through the chat template.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from shapes import RequestError, chat_chunk, chat_completion_response, new_id
from tool_calls import complete_with_tools, offered_names, tool_family

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
MAX_TOKENS_LIMIT = 8192
ROLES = ("system", "user", "assistant", "tool")


def invalid(message: str) -> RequestError:
    return RequestError(400, "invalid_request_error", message)


def text_content(content) -> str:
    """Flatten OpenAI message content (a string or text parts) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                raise invalid("only text content parts are supported")
            parts.append(str(part.get("text", "")))
        return "".join(parts)
    raise invalid("content must be a string or a list of parts")


def check_message(message, index: int) -> dict:
    """One validated message: role, flattened content and any tool-call fields."""
    if not isinstance(message, dict) or message.get("role") not in ROLES:
        raise invalid(f"messages[{index}] has an unsupported role")
    role = message["role"]
    checked = {"role": role, "content": text_content(message.get("content"))}
    if role == "tool":
        call_id = message.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise invalid(f"messages[{index}] is a tool result without a tool_call_id")
        checked["tool_call_id"] = call_id
    elif role == "assistant" and message.get("tool_calls") is not None:
        calls = message["tool_calls"]
        if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
            raise invalid(f"messages[{index}].tool_calls must be a list of objects")
        checked["tool_calls"] = validate_history_calls(calls, index)
    return checked


def check_answers(messages: list[dict]) -> None:
    """Every tool result must answer a tool call made earlier in the history."""
    made = set()
    for index, message in enumerate(messages):
        made.update(call.get("id") for call in message.get("tool_calls", ()))
        if message["role"] == "tool" and message["tool_call_id"] not in made:
            raise invalid(f"messages[{index}] answers an unknown tool call")


@dataclass(frozen=True)
class ChatRequest:
    """A validated chat-completions request body."""

    payload: dict
    messages: list[dict]
    max_tokens: int
    stream: bool

    @classmethod
    def parse(cls, payload) -> ChatRequest:
        if not isinstance(payload, dict):
            raise invalid("the request body must be a JSON object")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise invalid("messages must be a non-empty list")
        checked = [check_message(message, index) for index, message in enumerate(messages)]
        check_answers(checked)
        max_tokens = payload.get("max_tokens", DEFAULT_MAX_TOKENS)
        if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_TOKENS_LIMIT:
            raise invalid(f"max_tokens must be an integer from 1 to {MAX_TOKENS_LIMIT}")
        stream = payload.get("stream", False)
        if not isinstance(stream, bool):
            raise invalid("stream must be a boolean")
        return cls(payload, checked, max_tokens, stream)

    @property
    def uses_tools(self) -> bool:
        return self.payload.get("tools") is not None or self.payload.get("tool_choice") is not None


class Bridge:
    """Routes OpenAI chat-completions requests to one backend."""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._routes: dict[tuple[str, str], Callable] = {
            ("GET", "/v1/models"): self.list_models,
            ("POST", "/v1/chat/completions"): self.chat,
        }

    def route(self, method: str, path: str, payload=None) -> tuple[int, dict | Iterator[dict]]:
        """Dispatch one HTTP request; streaming responses are chunk iterators."""
        handler = self._routes.get((method, path))
        if handler is None:
            return 404, {"error": {"type": "not_found", "message": f"{method} {path}"}}
        try:
            return 200, handler(payload)
        except RequestError as exc:
            LOGGER.info("%s %s refused with %d: %s", method, path, exc.status, exc)
            return exc.status, exc.body()

    def list_models(self, payload=None) -> dict:
        model = {
            "id": self._backend.model_name,
            "object": "model",
            "owned_by": "local",
            "capabilities": {
                "tool_calls": tool_family(self._backend.tokenizer) is not None,
                "streaming_tool_calls": False,
            },
        }
        return {"object": "list", "data": [model]}

    def chat(self, payload) -> dict | Iterator[dict]:
        request = ChatRequest.parse(payload)
        if request.stream:
            return self.stream_chat(request)
        return self.complete(request)

    def complete(self, request: ChatRequest) -> dict:
        backend = self._backend
        if request.uses_tools:
            return complete_with_tools(
                backend, request.payload, request.messages, request.max_tokens
            )
        text, finish = backend.generate(request.messages, request.max_tokens)
        return chat_completion_response(backend.model_name, text, finish_reason=finish)

    def stream_chat(self, request: ChatRequest) -> Iterator[dict]:
        """Check the request now, so a refusal comes before any chunk is sent.

        Streaming does not parse tool calls: a request with tools streams text.
        """
        if request.uses_tools:
            offered_names(request.payload.get("tools"))
        return self._chunks(request.messages, request.max_tokens)

    def _chunks(self, messages: list[dict], max_tokens: int) -> Iterator[dict]:
        model = self._backend.model_name
        completion_id, created = new_id("chatcmpl"), int(time.time())
        yield chat_chunk(model, completion_id, delta={"role": "assistant"}, created=created)
        for delta, finish in self._backend.stream(messages, max_tokens):
            yield chat_chunk(
                model,
                completion_id,
                delta={"content": delta} if delta else {},
                created=created,
                finish_reason=finish,
            )


def validate_history_calls(calls: list[dict], index: int) -> list[dict]:
    """Reject malformed assistant calls before history consumers inspect them."""
    for call in calls:
        if not isinstance(call.get("id"), str) or not call["id"]:
            raise invalid(f"messages[{index}].tool_calls IDs must be non-empty strings")
        function = call.get("function")
        if call.get("type") != "function" or not isinstance(function, dict):
            raise invalid(f"messages[{index}].tool_calls must contain function objects")
        if not isinstance(function.get("name"), str) or not function["name"]:
            raise invalid(f"messages[{index}].tool_calls function names must be non-empty strings")
        if not isinstance(function.get("arguments"), str):
            raise invalid(f"messages[{index}].tool_calls function arguments must be strings")
    return calls
