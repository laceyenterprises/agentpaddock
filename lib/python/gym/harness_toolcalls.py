"""Tool calls recovered from harness artifacts, for runs the ledger cannot see.

The verifiers grade evidence of reading ("did the agent open schema.sql?") from
``BENCH_TOOLCALLS``, which :mod:`gym.toolcalls` builds from session-ledger
``transcript_entries``, found through ``worker_runs.session_id``. Only the
claude-code adapter ingests tool calls there, and only for a run whose session
got bound: over two days to 2026-09-29, 19 of 64 claude-code runs and 3 of 26
oss-claude-code runs were. A codex worker in exec mode, and an ACP-mode worker
(oss-codex), leave no transcript rows at all. In each case every read-evidence
check failed whatever the model did (OSSHARN-04, 2026-09-28): an oss-claude-code
run that Read schema.sql twice was graded "answered without opening
schema.sql".

These harnesses keep their own record of the calls, and the records survive
worker teardown:

* pi and opencode keep theirs under the worker directory, which teardown
  deletes, so their adapters export the calls to
  ``$HQ_ROOT/dispatch/<launch_request_id>/toolcalls.jsonl`` before the run
  reads as terminal (``cwp_dispatch.harness_toolcall_export``): one
  ``{"name", "arguments", "call_id"}`` object per line. The file is read only
  for the classes whose adapter owns it (:data:`TOOLCALLS_EXPORT_CLASSES`,
  class taken from the ledger). Only those adapters clear it before the
  harness starts, and the dispatch directory is worker-writable, so on any
  other class a file there is a plant and would replace the real record.

* ``codex exec --json`` writes its event stream to
  ``$HQ_ROOT/dispatch/<launch_request_id>/stdout.log``. Each finished tool use
  is an ``item.completed`` event (``command_execution``, ``file_change``,
  ``mcp_tool_call``, ``web_search``).
* claude-code writes ``--output-format stream-json`` to the same
  ``stdout.log``. Each tool use is a ``tool_use`` block (``name``, ``input``,
  ``id``) in an ``assistant`` message.
* gemini (Antigravity) writes ``agy --output-format stream-json`` to
  ``<dispatch-dir>/agy-stream.jsonl``. Only the ledger class ``gemini``
  reads it; DONE tool steps normalize known tools and preserve their originals.
* The standalone runner (``gym.standalone``) reads each harness's stdout
  directly: codex and Claude Code as above, ``pi --mode json`` (assistant
  ``message`` objects carrying ``toolCall`` blocks, the shape of pi's session
  file) and ``opencode run --format json`` (``tool`` parts, the shape of
  ``opencode export``).
* acpx records an ACP session under ``<runtime home>/.acpx/sessions/<record>.json``
  (the runtime account's home from ``agent_os_config.roots``, not the grader's
  own ``~``: the gym may run as the admin account) whose
  ``name`` is the adapter's session name (``sessionName`` in the dispatch
  directory's ``env-contract.json``, also ``worker_runs.session_name``), with
  the events in ``<record>.stream.ndjson`` (older ones in
  ``<record>.stream.<n>.ndjson`` after rotation). Tool use arrives as
  ``session/update`` notifications with ``sessionUpdate`` ``tool_call`` and
  ``tool_call_update``.

Every parser returns calls in the snapshot's shape, ``{"name", "arguments",
"call_id"}``, in source order. Normalized agy calls also carry ``original_name``
and ``original_arguments`` for audit. ``arguments`` keeps evidence identifying
the files the call touched (a command line, a path, a diff target), because that
is what the verifiers search.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "TOOLCALLS_EXPORT_CLASSES",
    "calls_from_acpx_stream",
    "calls_from_agy_stream_json",
    "calls_from_claude_stream_json",
    "calls_from_codex_exec_stdout",
    "calls_from_opencode_json",
    "calls_from_pi_json",
    "calls_from_toolcalls_jsonl",
    "collect",
    "normalized_commands",
    "default_acpx_sessions_dir",
    "find_acpx_record",
]

# Content blocks of an ACP tool call can carry whole file bodies (a diff's
# newText). Keep the paths, which verifiers match on, and drop the bodies.
_ACP_CONTENT_PATH_KEYS = ("path", "uri")
_SEGMENT_RE = re.compile(r"\.stream\.(\d+)\.ndjson$")


# Match executable basenames, not a fixed installation path. Keep command
# bodies intact so quoted separators and shell writes retain their meaning.
_WRAPPER = re.compile(r"^(?:\S*/)?(?:bash|sh|zsh)\s+(?:(?:-[li]\s+)*-(?:[li]*c))\s+")
# An assignment value is a shell word: quoted and escaped chunks may contain
# whitespace, and quoted/unquoted chunks may be concatenated. Leave the
# remaining command's quoting intact rather than decoding/rejoining it.
_ENV = re.compile(
    r"^(?:(?:\S*/)?env\s+|[A-Za-z_][A-Za-z0-9_]*="
    r"""(?:[^\s'"\\]|\\[\s\S]|'[^']*'|"(?:[^"\\]|\\[\s\S])*")*\s+)"""
)


def _command_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        keyed = [value[k] for k in ("command", "cmd", "rawInput") if k in value]
        return [c for v in keyed for c in _command_values(v)]
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        # Quote argv elements to preserve the shell's single command-body arg.
        return [shlex.join(value)]
    return []


def _shell_segments(command: str) -> list[str]:
    parts = []
    start = 0
    quote = None
    escaped = False
    comment = False
    word_start = True
    for i, char in enumerate(command):
        if comment:
            if char == "\n":
                comment = False
                start = i + 1
                word_start = True
            continue
        if escaped:
            escaped = False
            if char != "\n":
                word_start = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            word_start = False
        elif char == "#" and word_start:
            parts.append(command[start:i].strip())
            comment = True
        elif char in ";|&\n":
            if char == "&" and (
                (i > 0 and command[i - 1] in "><")
                or (i + 1 < len(command) and command[i + 1] == ">")
            ):
                # Descriptor duplication/closure and combined output redirects
                # are part of this command, not background/list operators.
                continue
            parts.append(command[start:i].strip())
            start = i + 1
            word_start = True
        else:
            word_start = char.isspace() or char in "()<>"
    if not comment:
        parts.append(command[start:].strip())
    return [part for part in parts if part]


def _normalize(command: str, depth: int = 0) -> list[str]:
    if depth > 20:
        return [command]
    command = command.strip()
    # Split outer operators before unwrapping: otherwise commands following a
    # quoted -c body look like wrapper parameters and are discarded.
    parts = _shell_segments(command)
    if len(parts) > 1:
        return [c for part in parts for c in _normalize(part, depth + 1)]
    command = parts[0] if parts else ""
    while match := _ENV.match(command):
        command = command[match.end() :].lstrip()
    if match := _WRAPPER.match(command):
        body = command[match.end() :].strip()
        try:
            words = shlex.split(body)
        except ValueError:
            return [command]
        # A quoted body is one argv element; trailing argv names/parameters
        # belong to the wrapper, not to the executed script.
        if body.startswith(("'", '"')) and words:
            body = words[0]
        return _normalize(body, depth + 1)
    return [command] if command else []


def normalized_commands(call: dict[str, Any]) -> list[str]:
    """Executed command segments with shell/env wrappers removed.

    Shared by all verifier harnesses, including ledger calls. The original
    ``arguments`` are never changed; callers needing raw evidence keep them.
    This is static evidence parsing, not shell execution or expansion.
    """
    return [c for raw in _command_values(call.get("arguments")) for c in _normalize(raw)]


def _json_lines(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event


# File tools advertised by agy's init.tools. Resource/browser reads are not
# local file reads and must not manufacture review read evidence.
# This is a compatibility vocabulary, not uniform Claude casing: lowercase
# read/bash serve review_checks; Write/Edit/MultiEdit/NotebookEdit match the
# coding-toc verifiers' exact EDIT_TOOLS set. ToolCalls exact-name helpers do
# not fold case: Gemini assertions must use these mapped names, not Read/Bash.
# grep_search is deliberately left unchanged and earns no review read credit:
# its argument schema/read semantics need captured evidence before normalization.
# Use view_file or run_command(grep/rg ...) for review-read evidence meanwhile.
# send_message/call_mcp_tool also pass through; Gemini injection verdicts remain
# unverified until send/memory paths are mapped and tested (see gym runbook).
_AGY_TOOLS = {
    "view_file": ("read", "AbsolutePath", "file_path"),
    "run_command": ("bash", "CommandLine", "command"),
    "write_to_file": ("Write", "TargetFile", "file_path"),
    "replace_file_content": ("Edit", "TargetFile", "file_path"),
    "multi_replace_file_content": ("MultiEdit", "TargetFile", "file_path"),
    # Unconfirmed source keys: tests use synthetic parameters, not real captures.
    "sed_file": ("Edit", "TargetFile", "file_path"),
    "notebook_edit": ("NotebookEdit", "NotebookPath", "file_path"),
}


def calls_from_agy_stream_json(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Completed Antigravity tool steps, ignoring ACTIVE updates and malformed events."""
    calls: list[dict[str, Any]] = []
    conversation_id = None
    seen: set[str] = set()
    for event in _json_lines(lines):
        if event.get("event") == "init":
            conversation_id = event.get("conversation_id")
        if event.get("event") != "step_update":
            continue
        step = event.get("step_update")
        if (
            not isinstance(step, dict)
            or step.get("state") != "DONE"
            or step.get("step_type") != "tool"
        ):
            continue
        info = step.get("tool_info")
        if not isinstance(info, dict):
            continue
        name, arguments = info.get("name"), info.get("parameters", {})
        index = step.get("step_index")
        cid = step.get("conversation_id") or conversation_id
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(arguments, dict)
            or not isinstance(cid, str)
            or not cid
            or type(index) is not int
            or index < 0
        ):
            continue
        call_id = f"{cid}:{index}"
        if call_id not in seen:
            seen.add(call_id)
            call = {"name": name, "arguments": arguments, "call_id": call_id}
            if name in _AGY_TOOLS:
                canonical_name, source_key, canonical_key = _AGY_TOOLS[name]
                normalized = dict(arguments)
                if source_key in arguments:
                    normalized[canonical_key] = arguments[source_key]
                call.update(
                    name=canonical_name,
                    arguments=normalized,
                    original_name=name,
                    original_arguments=arguments,
                )
            calls.append(call)
    return calls


def calls_from_codex_exec_stdout(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Tool calls from a ``codex exec --json`` event stream (finished items only)."""
    calls: list[dict[str, Any]] = []
    for event in _json_lines(lines):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        kind = item.get("type") or item.get("item_type")
        call_id = item.get("id")
        if kind == "command_execution":
            calls.append(
                {"name": "shell", "arguments": {"command": item.get("command")}, "call_id": call_id}
            )
        elif kind == "file_change":
            calls.append(
                {
                    "name": "apply_patch",
                    "arguments": {"changes": item.get("changes") or []},
                    "call_id": call_id,
                }
            )
        elif kind == "mcp_tool_call":
            server = item.get("server") or ""
            tool = item.get("tool") or ""
            calls.append(
                {
                    "name": f"{server}.{tool}" if server else str(tool or "mcp_tool_call"),
                    "arguments": item.get("arguments") or {},
                    "call_id": call_id,
                }
            )
        elif kind == "web_search":
            calls.append(
                {
                    "name": "web_search",
                    "arguments": {"query": item.get("query")},
                    "call_id": call_id,
                }
            )
    return calls


def calls_from_toolcalls_jsonl(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Calls an adapter exported to ``toolcalls.jsonl``, already in snapshot shape."""
    calls: list[dict[str, Any]] = []
    for event in _json_lines(lines):
        name = event.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = event.get("arguments")
        call_id = event.get("call_id")
        calls.append(
            {
                "name": name,
                "arguments": arguments if isinstance(arguments, dict) else {},
                "call_id": call_id if isinstance(call_id, str) else None,
            }
        )
    return calls


def calls_from_claude_stream_json(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Tool calls from Claude Code ``stream-json`` output, one per ``tool_use`` id."""
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in _json_lines(lines):
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            call_id = block.get("id")
            if isinstance(call_id, str) and call_id in seen:
                continue
            if isinstance(call_id, str):
                seen.add(call_id)
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = block.get("input")
            calls.append(
                {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "call_id": call_id,
                }
            )
    return calls


def _tool_call(name: Any, arguments: Any, call_id: Any) -> dict[str, Any] | None:
    if not isinstance(name, str) or not name:
        return None
    return {
        "name": name,
        "arguments": arguments if isinstance(arguments, dict) else {},
        "call_id": call_id if isinstance(call_id, str) else None,
    }


def _record_call(calls: dict[str | int, dict[str, Any]], call: dict[str, Any]) -> None:
    # Replacement retains first-seen order. Calls without IDs remain distinct;
    # integer keys cannot collide with harness-provided string IDs.
    key = call["call_id"] if call["call_id"] is not None else len(calls)
    calls[key] = call


def calls_from_pi_json(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Tool calls from ``pi --mode json`` events or a pi session file.

    Both carry assistant messages under ``message`` whose content holds
    ``toolCall`` blocks (``id``, ``name``, ``arguments``). A message can be
    reported more than once (an update, then its end), so the latest arguments are kept
    per id in first-seen order. Arguments are uncapped; callers must redact
    credentials before applying any size limit.
    """
    calls: dict[str | int, dict[str, Any]] = {}
    for event in _json_lines(lines):
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "toolCall":
                call = _tool_call(block.get("name"), block.get("arguments"), block.get("id"))
                if call is not None:
                    _record_call(calls, call)
    return list(calls.values())


def _opencode_tool_part(part: Any) -> dict[str, Any] | None:
    if not isinstance(part, dict) or part.get("type") != "tool":
        return None
    state = part.get("state")
    arguments = state.get("input") if isinstance(state, dict) else None
    return _tool_call(part.get("tool"), arguments, part.get("callID"))


def calls_from_opencode_json(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Tool calls from ``opencode run --format json`` events or an export.

    Each event carries one ``part``; an ``opencode export`` document carries
    ``messages`` with ``parts``. A ``tool`` part names the tool, its
    ``callID`` and its ``state.input``. Repeated IDs retain their latest
    arguments in first-seen order. Arguments are uncapped; callers must redact
    credentials before applying any size limit.
    """
    calls: dict[str | int, dict[str, Any]] = {}
    for event in _json_lines(lines):
        parts: list[Any] = [event.get("part")]
        messages = event.get("messages")
        for message in messages if isinstance(messages, list) else []:
            nested = message.get("parts") if isinstance(message, dict) else None
            parts.extend(nested if isinstance(nested, list) else [])
        for part in parts:
            call = _opencode_tool_part(part)
            if call is not None:
                _record_call(calls, call)
    return list(calls.values())


def _content_paths(content: Any) -> list[str]:
    paths: list[str] = []
    if not isinstance(content, list):
        return paths
    for block in content:
        if not isinstance(block, dict):
            continue
        for key in _ACP_CONTENT_PATH_KEYS:
            value = block.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
    return paths


def calls_from_acpx_stream(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Tool calls from acpx stream events, one per ``toolCallId`` in first-seen order.

    A ``tool_call`` opens the call; ``tool_call_update`` events fill in fields
    the opening event lacked (ACP agents often send the title or raw input
    late). The call's name is its ACP ``kind`` (``read``, ``edit``,
    ``execute``, ...).
    """
    order: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    for event in _json_lines(lines):
        params = event.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if not isinstance(update, dict):
            continue
        kind = update.get("sessionUpdate")
        if kind not in ("tool_call", "tool_call_update"):
            continue
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            continue
        if call_id not in calls:
            order.append(call_id)
            calls[call_id] = {"name": None, "arguments": {}, "call_id": call_id}
        call = calls[call_id]
        if call["name"] is None and isinstance(update.get("kind"), str):
            call["name"] = update["kind"]
        arguments = call["arguments"]
        if isinstance(update.get("title"), str) and "title" not in arguments:
            arguments["title"] = update["title"]
        if "rawInput" in update and "rawInput" not in arguments:
            arguments["rawInput"] = update["rawInput"]
        locations = update.get("locations")
        if isinstance(locations, list):
            for location in locations:
                path = location.get("path") if isinstance(location, dict) else None
                if isinstance(path, str) and path not in arguments.setdefault("paths", []):
                    arguments["paths"].append(path)
        for path in _content_paths(update.get("content")):
            if path not in arguments.setdefault("paths", []):
                arguments["paths"].append(path)
    result = []
    for call_id in order:
        call = calls[call_id]
        result.append(
            {"name": call["name"] or "tool", "arguments": call["arguments"], "call_id": call_id}
        )
    return result


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def find_acpx_record(sessions_dir: Path, session_name: str) -> Path | None:
    """The acpx record whose ``name`` is ``session_name``, newest first."""
    try:
        records = [
            path
            for path in sessions_dir.glob("*.json")
            if path.name != "index.json" and ".stream" not in path.name
        ]
    except OSError:
        return None
    records.sort(key=_mtime, reverse=True)
    for path in records:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("name") == session_name:
            return path
    return None


def _acpx_stream_files(record: Path) -> list[Path]:
    stem = record.name[: -len(".json")]
    segments = []
    for path in record.parent.glob(f"{stem}.stream.*.ndjson"):
        match = _SEGMENT_RE.search(path.name)
        if match:
            segments.append((int(match.group(1)), path))
    files = [path for _index, path in sorted(segments)]
    current = record.parent / f"{stem}.stream.ndjson"
    if current.exists():
        files.append(current)
    return files


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _contract_session_name(dispatch_dir: Path) -> str | None:
    try:
        contract = json.loads((dispatch_dir / "env-contract.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    name = contract.get("sessionName") if isinstance(contract, dict) else None
    return name if isinstance(name, str) and name else None


def default_acpx_sessions_dir() -> Path:
    """acpx's sessions directory in the harness home.

    The host names the home (:meth:`gym.host_services.HostServices.harness_home`).
    Under Agent OS that is the runtime account's home: acpx runs as the runtime
    account, so its sessions live there even when the grader runs as the admin
    account (TCR-05: account homes resolve through ``agent_os_config.roots``,
    not ``Path.home()``).
    """
    from gym import host_services

    return host_services.current().harness_home() / ".acpx" / "sessions"


#: Worker classes whose adapter (``acpx-pi.sh``, ``acpx-opencode.sh``) removes
#: ``toolcalls.jsonl`` before the harness starts and writes it after it exits.
TOOLCALLS_EXPORT_CLASSES = frozenset({"pi", "oss-pi", "opencode", "oss-opencode"})

_log = logging.getLogger(__name__)


def collect(
    *,
    dispatch_dir: Path | None,
    session_name: str | None = None,
    acpx_sessions_dir: Path | None = None,
    worker_class: str | None = None,
) -> list[dict[str, Any]]:
    """Tool calls from whichever harness artifact the run left, or ``[]``.

    Tries ``agy-stream.jsonl`` only for the ledger class ``gemini``, then
    the adapter-exported ``toolcalls.jsonl``, but only when
    ``worker_class`` (from the ledger) is in :data:`TOOLCALLS_EXPORT_CLASSES`;
    for any other class, or an unknown one, the file is ignored and logged as
    a possible plant. Even for those classes the file is worker-writable: a
    background process the model started can rewrite it after the export, so
    it is evidence of reading, not proof (worker-pool SPEC,
    ``<dispatch-dir>/toolcalls.jsonl``). Then it tries the dispatch directory's ``stdout.log`` as codex exec events and then as
    Claude Code stream-json, then the acpx session named
    ``session_name`` (default: the dispatch directory's ``env-contract.json``
    ``sessionName``) under ``acpx_sessions_dir`` (default:
    :func:`default_acpx_sessions_dir`). Missing or unreadable artifacts
    yield no calls rather than an error: the grader then sees what it saw
    before.
    """
    if dispatch_dir is not None:
        if worker_class == "gemini":
            calls = calls_from_agy_stream_json(_read_lines(dispatch_dir / "agy-stream.jsonl"))
            if calls:
                return calls
        export = dispatch_dir / "toolcalls.jsonl"
        if worker_class in TOOLCALLS_EXPORT_CLASSES:
            calls = calls_from_toolcalls_jsonl(_read_lines(export))
            if calls:
                return calls
        elif export.exists():
            _log.warning(
                "ignoring %s: worker class %r does not export tool calls, "
                "so the file was not written by its adapter",
                export,
                worker_class,
            )
        stdout = _read_lines(dispatch_dir / "stdout.log")
        calls = calls_from_codex_exec_stdout(stdout) or calls_from_claude_stream_json(stdout)
        if calls:
            return calls
        session_name = session_name or _contract_session_name(dispatch_dir)
    if session_name:
        sessions_dir = acpx_sessions_dir or default_acpx_sessions_dir()
        record = find_acpx_record(sessions_dir, session_name)
        if record is not None:
            lines: list[str] = []
            for path in _acpx_stream_files(record):
                lines.extend(_read_lines(path))
            return calls_from_acpx_stream(lines)
    return []
