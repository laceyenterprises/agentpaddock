# Bridge: OpenAI tool calling for local models

BRIDGE-31. Agents that talk to the local bridge through an OpenAI client cannot use tools today: any request with `tools` or `tool_choice` gets a 400. This change adds non-streaming tool calling for tokenizers whose chat template has a tool-call format, and moves the request handling into a `Bridge` class with a validated `ChatRequest`.

## The contract (OpenAI chat completions)

- `tools` is a list of `{"type": "function", "function": {"name", "description", "parameters"}}` objects. Names are unique within a request.
- `tool_choice`:
  - omitted: `auto` when `tools` is non-empty, otherwise `none` (the OpenAI default);
  - `none`: the model must not call tools; tools are not rendered into the prompt;
  - `auto`: the model may answer with text, tool calls, or both;
  - `required`: the response must contain at least one tool call;
  - `{"type": "function", "function": {"name": N}}`: the response must call `N`. Only `N`'s definition is rendered into the prompt, so the model sees one function. `N` must be one of `tools`.
- A response may only call functions the request offered. The agents executing these calls treat the function name as authoritative, so a call to anything else must never reach them.
- When a `required` or named choice ends without a tool call, the bridge answers 422 `tool_call_required` instead of returning text the client did not ask for.
- Tool calls are returned as OpenAI `tool_calls` entries whose `function.arguments` is a JSON-encoded string, with `content` set to any text outside the calls (or null). `finish_reason` is `tool_calls`, except that it stays `length` when generation stopped at `max_tokens`, so clients can tell the text may be cut short.

## Model support

- The tokenizer's `tool_format` picks the parser family: `hermes`/`qwen2` use JSON blocks, `qwen3`/`qwen3-coder` use XML-style blocks. `parsers.py` is unchanged from BRIDGE-27 (it was written for the offline eval harness) and is included for context.
- A model whose tokenizer has no tool format answers `auto` requests as plain chat with `"warnings": ["tools-ignored"]` on the response; it refuses `required` and named choices with a 400, since it cannot satisfy them.
- If any tool-call block in the output is unclosed or unparsable, the bridge returns the whole output as text with no calls rather than a partial set.

## Streaming

Tool-call parsing is not supported while streaming yet (BRIDGE-33). A streaming request with tools under `auto` or `none` validates `tools` and streams plain text; a streaming request with a `required` or named choice is refused with 400 before any chunk is sent, since a text stream cannot satisfy it. `/v1/models` reports `streaming_tool_calls: false`.

## Other changes

- `tool` messages must carry the `tool_call_id` of an earlier assistant tool call; assistant `tool_calls` history is decoded for the chat template.
- `stream` must be a boolean.

`bridge.py` and `tool_calls.py` are the complete modules; `shapes.py` is unchanged.
