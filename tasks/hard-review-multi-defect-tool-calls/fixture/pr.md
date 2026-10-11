# Tool calling on non-streaming chat completions

Lets a locally served Qwen model answer harness tool requests through the local model bridge, using the OpenAI chat-completions tool-call shape.

`tool_calls.py` is new. It rewrites assistant and tool turns into the form the Qwen chat template expects, puts the request's `tools` into the prompt, runs the tokenizer's tool-call parser over the generated text, and returns `message.tool_calls` with finish reason `tool_calls`. `server_supervisor.py` hands any request that carries `tools` to it. Callers send `tools` on every turn, so a tokenizer with no tool support (the Llama entries) logs `tools-ignored` and returns its plain-text answer.

Contract the bridge follows (OpenAI chat completions):

- `tool_choice` is `"none"`, `"auto"` (also when absent), `"required"`, or `{"type": "function", "function": {"name": ...}}`. No request may complete successfully while violating its `tool_choice`; a choice the bridge cannot honour is a 4xx error.
- Every returned call names a function from the request's `tools`. Agent harnesses dispatch returned calls by name to their own functions.
- `tokenizer.tool_parser(text, tools)` is the installed mlx_lm parser. It decodes the JSON in a tool-call block and does not consult `tools`.

Streamed tool-call deltas are a separate later change; until then a streaming request with `tools` in auto mode keeps today's text stream.

This is a trimmed source excerpt. `tool_calls.py` is the reviewed source file. `server_supervisor.py` keeps the chat paths; configuration, the model swap gate, auth, timeout and disconnect handling, and the other handlers are omitted and unchanged. Line numbers refer to the staged files.
