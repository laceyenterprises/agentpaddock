"""Tool-call block parsers for the model families the bridge serves.

A model writes each tool call between ``<tool_call>`` and ``</tool_call>``. The
block body depends on the family: JSON for ``hermes``, nested tags for
``qwen-xml``. A parser decodes the syntax of one body into ``{"name": str,
"arguments": dict}``.

Parsers decode syntax only. They receive the request's tool definitions solely
to coerce ``qwen-xml`` parameter values to their declared JSON types; they do not
know which functions a request may call, and they return whatever function name
the model wrote. A name with no definition gets no coercion.
"""

from __future__ import annotations

import json
import re

OPEN = "<tool_call>"
CLOSE = "</tool_call>"

FUNCTION = re.compile(r"\A\s*<function=([^>\s]+)>(.*)</function>\s*\Z", re.S)
PARAMETER = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)


class ParseError(ValueError):
    """A tool-call block is not valid syntax for its family."""


def _call(name, arguments) -> dict:
    """Build a parsed call; ``arguments`` is always returned as a dict."""
    if not isinstance(name, str) or not name:
        raise ParseError("tool call has no function name")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            raise ParseError(f"arguments for {name} are not JSON") from exc
    if not isinstance(arguments, dict):
        raise ParseError(f"arguments for {name} must be an object")
    return {"name": name, "arguments": arguments}


def _declared_types(tools, name: str) -> dict[str, str]:
    for tool in tools or ():
        function = tool.get("function", {})
        if function.get("name") == name:
            properties = function.get("parameters", {}).get("properties", {})
            return {key: spec.get("type", "string") for key, spec in properties.items()}
    return {}


def _coerce(value: str, kind: str):
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    if kind == "boolean":
        if value.strip() not in ("true", "false"):
            raise ValueError(f"not a boolean: {value!r}")
        return value.strip() == "true"
    if kind in ("object", "array"):
        return json.loads(value)
    return value


def parse_hermes(body: str, tools) -> dict:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ParseError("hermes tool call is not JSON") from exc
    if not isinstance(data, dict):
        raise ParseError("hermes tool call must be an object")
    return _call(data.get("name"), data.get("arguments", {}))


def parse_qwen_xml(body: str, tools) -> dict:
    match = FUNCTION.match(body)
    if match is None:
        raise ParseError("qwen-xml tool call has no <function=...> element")
    name, inner = match.groups()
    types = _declared_types(tools, name)
    arguments = {}
    for key, value in PARAMETER.findall(inner):
        try:
            arguments[key] = _coerce(value, types.get(key, "string"))
        except ValueError as exc:
            raise ParseError(f"parameter {key} of {name}: {exc}") from exc
    return _call(name, arguments)


PARSERS = {"hermes": parse_hermes, "qwen-xml": parse_qwen_xml}


def parse_block(family: str, body: str, tools) -> dict:
    """Decode one block body; raise ParseError when it is not valid syntax."""
    try:
        parser = PARSERS[family]
    except KeyError:
        raise ParseError(f"no tool-call parser for {family!r}") from None
    return parser(body, tools)
