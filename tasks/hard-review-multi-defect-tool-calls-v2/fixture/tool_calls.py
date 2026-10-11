"""OpenAI tool calling for the chat bridge: validation, prompting and parsing.

The bridge renders the offered tools into the prompt through the tokenizer's
chat template, generates, then splits tool-call blocks out of the text with the
parser for the tokenizer's family (see ``parsers.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from parsers import CLOSE, OPEN, ParseError, parse_block
from shapes import RequestError, chat_completion_response, new_id

# Chat-template tool formats, by tokenizer ``tool_format``, mapped to parser families.
FAMILIES = {
    "hermes": "hermes",
    "qwen2": "hermes",
    "qwen3": "qwen-xml",
    "qwen3-coder": "qwen-xml",
}

CHOICE_MODES = ("none", "auto", "required")


def invalid(message: str) -> RequestError:
    return RequestError(400, "invalid_request_error", message)


def tool_family(tokenizer) -> str | None:
    """The parser family for this tokenizer, or None when it cannot call tools."""
    return FAMILIES.get(getattr(tokenizer, "tool_format", None))


def offered_names(tools) -> list[str]:
    """Validate the request's ``tools`` and return the offered function names."""
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise invalid("tools must be a list")
    names = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise invalid(f"tools[{index}] must be a function tool")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise invalid(f"tools[{index}].function must be an object")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise invalid(f"tools[{index}].function.name must be a non-empty string")
        if not isinstance(function.get("parameters", {}), dict):
            raise invalid(f"tools[{index}].function.parameters must be an object")
        if name in names:
            raise invalid(f"tools[{index}] repeats the function name {name!r}")
        names.append(name)
    return names


@dataclass(frozen=True)
class ToolChoice:
    """A resolved ``tool_choice``: none, auto, required, or one named function."""

    mode: str
    function: str | None = None

    @property
    def requires_call(self) -> bool:
        """True when the response must contain at least one tool call."""
        return self.mode == "required"

    @property
    def renders_tools(self) -> bool:
        return self.mode != "none"


def resolve_tool_choice(value, names: list[str]) -> ToolChoice:
    """Validate ``tool_choice`` against the offered names."""
    if value is None:
        return ToolChoice("auto" if names else "none")
    if value in CHOICE_MODES:
        if value != "none" and not names:
            raise invalid(f"tool_choice {value!r} needs at least one tool")
        return ToolChoice(value)
    if isinstance(value, dict) and value.get("type") == "function":
        function = value.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if name not in names:
            raise invalid(f"tool_choice names {name!r}, which is not in tools")
        return ToolChoice("function", name)
    raise invalid("tool_choice must be none, auto, required or a function object")


def tools_for_prompt(tools: list[dict], choice: ToolChoice) -> list[dict] | None:
    """The tool definitions the chat template renders for this choice."""
    if not choice.renders_tools:
        return None
    if choice.mode == "function":
        return [tool for tool in tools if tool["function"]["name"] == choice.function]
    return tools


def _arguments(call: dict, index: int) -> dict:
    arguments = call.get("function", {}).get("arguments", "{}")
    if isinstance(arguments, dict):
        return arguments
    try:
        decoded = json.loads(arguments) if arguments.strip() else {}
    except (AttributeError, json.JSONDecodeError):
        decoded = None
    if not isinstance(decoded, dict):
        raise invalid(f"tool_calls[{index}].function.arguments must be a JSON object")
    return decoded


def normalize_history(messages: list[dict]) -> list[dict]:
    """Convert OpenAI tool-call history into the shape chat templates expect.

    Assistant ``tool_calls`` carry their arguments as JSON strings; templates
    want them decoded. Tool results keep their ``tool_call_id``.
    """
    converted = []
    for message in messages:
        if message["role"] == "assistant" and message.get("tool_calls"):
            calls = [
                {
                    "id": call.get("id"),
                    "name": call.get("function", {}).get("name"),
                    "arguments": _arguments(call, index),
                }
                for index, call in enumerate(message["tool_calls"])
            ]
            converted.append({**message, "tool_calls": calls})
        else:
            converted.append(message)
    return converted


def split_tool_calls(text: str, family: str, tools: list[dict]) -> tuple[str, list[dict]]:
    """Split generated text into plain content and parsed tool calls.

    Text outside the blocks is kept as content. An unclosed or unparsable block
    voids every call in the text: the whole text is returned as content with no
    calls, because acting on part of what the model asked for is worse than
    acting on none of it.
    """
    content, calls, rest = [], [], text
    while OPEN in rest:
        before, _, after = rest.partition(OPEN)
        body, closed, rest = after.partition(CLOSE)
        if not closed:
            return text, []
        try:
            parsed = parse_block(family, body.strip(), tools)
        except ParseError:
            return text, []
        content.append(before)
        calls.append(parsed)
    content.append(rest)
    return "".join(content).strip(), calls


def openai_calls(calls: list[dict]) -> list[dict]:
    """OpenAI ``tool_calls`` entries; arguments are JSON-encoded strings."""
    result = []
    for call in calls:
        arguments = json.dumps(call["arguments"])

        result.append(
            {
                "id": new_id("call"),
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": arguments,
                },
            }
        )
    return result


def complete_with_tools(backend, payload: dict, messages: list[dict], max_tokens: int) -> dict:
    """Run one non-streaming completion for a request that sets tools or tool_choice."""
    tools = payload.get("tools") or []
    choice = resolve_tool_choice(payload.get("tool_choice"), offered_names(payload.get("tools")))
    family = tool_family(backend.tokenizer)
    history = normalize_history(messages)
    if family is None:
        if choice.requires_call:
            raise invalid(f"{backend.model_name} cannot make tool calls")
        text, finish = backend.generate(history, max_tokens)
        response = chat_completion_response(backend.model_name, text, finish_reason=finish)
        response["warnings"] = ["tools-ignored"]
        return response

    text, finish = backend.generate(history, max_tokens, tools=tools_for_prompt(tools, choice))
    if not choice.renders_tools:
        return chat_completion_response(backend.model_name, text, finish_reason=finish)
    content, calls = split_tool_calls(text, family, tools)
    if choice.requires_call and not calls:
        raise RequestError(422, "tool_call_required", "tool_choice requires a tool call")
    if not calls:
        return chat_completion_response(backend.model_name, content, finish_reason=finish)
    return chat_completion_response(
        backend.model_name,
        content or None,
        tool_calls=openai_calls(calls),
        # A completion cut off at max_tokens says so, even when it holds calls.
        finish_reason="length" if finish == "length" else "tool_calls",
    )
