"""The local model supervisor daemon.

Owns the one loaded open-weight model on this host and serves the
bearer-gated OpenAI-compatible bridge, the loopback raw listener and the
admin listener.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from aiohttp import web

from .chat_shapes import (
    chat_completion_chunk,
    chat_completion_response,
    clamped_max_tokens,
    payload_with_clamped_max_tokens,
)
from .tool_calls import chat_completion_with_tools

# SupervisorError is re-exported: it used to be defined in this module.
from .errors import SupervisorError as SupervisorError
from .usage_accounting import TokenCounts, counts_from_tokenizer, terminal_stream_counts


class LocalModelBackend:
    def __init__(self, *, max_generation_tokens: int, max_concurrent_inference: int) -> None:
        self.current_model_name: str | None = None
        self._model: Any = None
        self._tokenizer: Any = None
        self.max_generation_tokens = max_generation_tokens
        self._inference_executor = ThreadPoolExecutor(
            max_workers=max(1, max_concurrent_inference),
            thread_name_prefix="model-inference",
        )

    def _render_chat_prompt(self, messages: list[dict[str, Any]]) -> Any:
        if self._tokenizer is None:
            raise RuntimeError("model not loaded")
        if getattr(self._tokenizer, "has_chat_template", False):
            return self._tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        lines = []
        for message in messages:
            lines.append(f"{message.get('role', 'user')}: {message.get('content', '')}")
        lines.append("assistant:")
        return "\n".join(lines)

    def _generate_sync(self, messages: list[dict[str, Any]], max_tokens: int) -> tuple[str, Any]:
        """Generate, returning the text AND the rendered prompt."""
        from mlx_lm import generate  # type: ignore

        prompt = self._render_chat_prompt(messages)
        text = generate(
            self._model, self._tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False
        )
        return text, prompt

    def _counted(self, response: dict[str, Any], prompt: Any, text: str) -> dict[str, Any]:
        """Attach token counts for the supervisor's meter to pick up."""
        response["_supervisor_token_counts"] = counts_from_tokenizer(
            self._tokenizer, prompt=prompt, text=text
        )
        return response

    async def chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise SupervisorError("messages must be a list", status=400, kind="INVALID_REQUEST")
        max_tokens = clamped_max_tokens(payload, ceiling=self.max_generation_tokens)
        if payload.get("tools"):
            return await chat_completion_with_tools(self, payload, messages, max_tokens)
        # Run blocking generation on the dedicated inference executor, not the
        # default pool, so it cannot starve admin operations.
        loop = asyncio.get_running_loop()
        text, prompt = await loop.run_in_executor(
            self._inference_executor, self._generate_sync, messages, max_tokens
        )
        model_name = self.current_model_name or "unknown"
        return self._counted(chat_completion_response(model_name, text), prompt, text)

    async def stream_chat_completions(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[str | TokenCounts]:
        """Yield incremental text deltas token-by-token via mlx_lm.stream_generate.

        The producer runs on the inference executor and bridges deltas to this
        consumer through a queue. The final item is a TokenCounts, not text.
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise SupervisorError("messages must be a list", status=400, kind="INVALID_REQUEST")
        max_tokens = clamped_max_tokens(payload, ceiling=self.max_generation_tokens)
        prompt = self._render_chat_prompt(messages)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        stop_event = threading.Event()

        def _produce() -> None:
            from mlx_lm import stream_generate  # type: ignore

            last: Any = None
            emitted = 0
            try:
                for response in stream_generate(
                    self._model, self._tokenizer, prompt=prompt, max_tokens=max_tokens
                ):
                    if stop_event.is_set():
                        break
                    last = response
                    emitted += 1
                    if response.text:
                        loop.call_soon_threadsafe(queue.put_nowait, ("delta", response.text))
            except Exception as exc:  # surfaced to the consumer for terminal handling
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                counts = terminal_stream_counts(last, emitted)
                if counts is not None:
                    loop.call_soon_threadsafe(queue.put_nowait, ("usage", counts))
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        producer = self._inference_executor.submit(_produce)
        try:
            while True:
                kind, value = await queue.get()
                if kind in ("delta", "usage"):
                    yield value
                elif kind == "error":
                    raise value
                else:
                    break
        finally:
            # Block until the producer exits so a swap cannot unload weights
            # under a live generation thread.
            stop_event.set()
            concurrent.futures.wait([producer])


class ServerSupervisor:
    # Configuration, model swap gate, auth and the other handlers are omitted.

    async def handle_chat(
        self, request: web.Request, *, require_auth: bool
    ) -> web.Response | web.StreamResponse:
        if require_auth:
            self._check_bridge_auth(request)
        payload = await self._read_json_object(request)
        payload = payload_with_clamped_max_tokens(
            payload, ceiling=self.config.max_generation_tokens
        )
        if payload.get("stream"):
            return await self._handle_chat_stream(request, payload, require_auth=require_auth)
        async with self._requested_model_inference(payload, surface="chat") as meter:
            response = await self._run_generation_with_timeout(
                self.backend.chat_completions(payload)
            )
            meter.record_response(response)
        return web.json_response(response)

    async def _handle_chat_stream(
        self, request: web.Request, payload: dict[str, Any], *, require_auth: bool
    ) -> web.StreamResponse:
        # Errors raised before any byte is written surface as a normal JSON
        # error. Once the SSE stream is prepared, errors are delivered as a
        # terminal chunk followed by [DONE]. Timeout and disconnect handling
        # are omitted from this excerpt.
        async with self._requested_model_inference(payload, surface="chat-stream") as meter:
            model_name = self.backend.current_model_name or "unknown"
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
            )
            await resp.prepare(request)
            chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
            created = int(time.time())
            first = True

            async def _write_chunk(chunk: dict[str, Any]) -> None:
                await resp.write(b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n")

            async with contextlib.aclosing(
                self.backend.stream_chat_completions(payload)
            ) as generator:
                async for item in generator:
                    delta_text = meter.observe_stream_item(item)
                    if delta_text is None:
                        continue
                    delta: dict[str, Any] = {"content": delta_text}
                    if first:
                        delta = {"role": "assistant", "content": delta_text}
                        first = False
                    await _write_chunk(
                        chat_completion_chunk(
                            chunk_id=chunk_id,
                            created=created,
                            model_name=model_name,
                            delta=delta,
                            finish_reason=None,
                        )
                    )
            await _write_chunk(
                chat_completion_chunk(
                    chunk_id=chunk_id,
                    created=created,
                    model_name=model_name,
                    delta={},
                    finish_reason="stop",
                    usage=meter.snapshot().usage_block(),
                )
            )
            await resp.write(b"data: [DONE]\n\n")
        return resp
