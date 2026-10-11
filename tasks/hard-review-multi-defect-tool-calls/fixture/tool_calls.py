"""Non-streaming chat tool calls for the local model bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from .chat_shapes import chat_completion_response

LOGGER = logging.getLogger(__name__)


def normalize_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare OpenAI history for Qwen templates without changing the input."""
    normalized = []
    for message in messages:
        item = dict(message)
        if item.get("content") is None:
            item["content"] = ""
        if item.get("role") == "assistant" and item.get("tool_calls"):
            calls = []
            for call in item["tool_calls"]:
                copied = dict(call)
                function = dict(call["function"])
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        function["arguments"] = json.loads(arguments)
                    except json.JSONDecodeError:
                        # Preserve malformed history for the template to handle.
                        pass
                copied["function"] = function
                calls.append(copied)
            item["tool_calls"] = calls
        normalized.append(item)
    return normalized


def parse_tool_text(
    text: str, tokenizer: Any, tools: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Parse complete blocks; retain the entire answer as text on a bad block."""
    start, end = tokenizer.tool_call_start, tokenizer.tool_call_end
    if not start or not end or start not in text:
        return text, []
    content = []
    calls = []
    offset = 0
    try:
        while (begin := text.find(start, offset)) >= 0:
            content.append(text[offset:begin])
            close = text.find(end, begin + len(start))
            if close < 0:
                return text, []
            parsed = tokenizer.tool_parser(text[begin + len(start) : close], tools)
            for call in parsed if isinstance(parsed, list) else [parsed]:
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    return text, []
                arguments = call.get("arguments")
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                calls.append(
                    {
                        "id": call.get("id") or f"call_{uuid.uuid4().hex}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                )
            offset = close + len(end)
        content.append(text[offset:])
    except (ValueError, TypeError, KeyError, AttributeError):
        LOGGER.warning("tool-call-parse-failed")
        return text, []
    return "".join(content), calls


def _generate_with_tools(
    backend: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int
) -> tuple[str, Any, str]:
    from mlx_lm import stream_generate  # type: ignore

    prompt = backend._tokenizer.apply_chat_template(
        normalize_history(messages), add_generation_prompt=True, tools=tools
    )
    text = ""
    finish_reason = "stop"
    for response in stream_generate(
        backend._model, backend._tokenizer, prompt=prompt, max_tokens=max_tokens
    ):
        text += response.text
        if response.finish_reason is not None:
            finish_reason = response.finish_reason
    return text, prompt, finish_reason


async def chat_completion_with_tools(
    backend: Any, payload: dict[str, Any], messages: list[dict[str, Any]], max_tokens: int
) -> dict[str, Any]:
    """Use the installed tokenizer's own parser, or preserve legacy text output."""
    tools = payload["tools"]
    tokenizer = backend._tokenizer
    supported = bool(
        tokenizer is not None
        and getattr(tokenizer, "has_tool_calling", False)
        and getattr(tokenizer, "tool_parser", None)
        and getattr(tokenizer, "tool_call_start", None)
        and getattr(tokenizer, "tool_call_end", None)
    )
    loop = asyncio.get_running_loop()
    if not supported or payload.get("tool_choice") == "none":
        if not supported:
            LOGGER.warning(
                "tools-ignored model=%s tool_count=%d",
                backend.current_model_name or "unknown",
                len(tools),
            )
        text, prompt = await loop.run_in_executor(
            backend._inference_executor, backend._generate_sync, messages, max_tokens
        )
        return backend._counted(
            chat_completion_response(backend.current_model_name or "unknown", text), prompt, text
        )

    text, prompt, finish_reason = await loop.run_in_executor(
        backend._inference_executor, _generate_with_tools, backend, messages, tools, max_tokens
    )
    content, calls = parse_tool_text(text, tokenizer, tools)
    response = chat_completion_response(backend.current_model_name or "unknown", text)
    choice = response["choices"][0]
    if calls:
        choice["message"] = {"role": "assistant", "content": content or None, "tool_calls": calls}
        choice["finish_reason"] = "length" if finish_reason == "length" else "tool_calls"
    else:
        choice["finish_reason"] = finish_reason
    return backend._counted(response, prompt, text)
