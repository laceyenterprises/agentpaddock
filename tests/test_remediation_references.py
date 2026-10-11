"""Apply reference patches and exercise the contracts their findings require."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

TASKS = Path(__file__).resolve().parents[1] / "tasks"


def apply_reference(task_id, directory):
    task = TASKS / task_id
    shutil.copytree(task / "fixture", directory, dirs_exist_ok=True)
    subprocess.run(
        ["git", "apply", str(task / "expected/source-remediation.diff")],
        cwd=directory,
        check=True,
        capture_output=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "damaged",
    [
        '{"version":',
        b"\xff",
        [],
        None,
        True,
        7,
        {"version": 2, "path_key": [], "checkpoint": {}},
        {"version": 2, "path_key": {}, "checkpoint": {}},
        {"version": 2, "path_key": None, "checkpoint": {}},
        {"version": 2, "path_key": 7, "checkpoint": {}},
        {"version": 2, "checkpoint": {}},
        {"version": 99, "path_key": "bad", "checkpoint": {}},
        {"version": [], "path_key": "bad", "checkpoint": {}},
        {"version": {}, "path_key": "bad", "checkpoint": {}},
        {"path_key": "bad", "checkpoint": {}},
        {"version": 2, "path_key": "bad", "checkpoint": []},
    ],
)
def test_checkpoint_reference_quarantines_invalid_payloads(tmp_path, caplog, damaged):
    apply_reference("review-corrupt-refresh-checkpoint", tmp_path)
    namespace = {}
    exec(compile((tmp_path / "progress_journal.py").read_text(), "reference", "exec"), namespace)
    files = tmp_path / "progress/files"
    files.mkdir(parents=True)
    valid = {"version": 2, "path_key": "good", "checkpoint": {"identity": "id", "offset": 5}}
    (files / "good.json").write_text(json.dumps(valid))
    bad = files / "bad.json"
    bad.write_bytes(
        damaged
        if isinstance(damaged, bytes)
        else (damaged if isinstance(damaged, str) else json.dumps(damaged)).encode()
    )
    identity = files.parent / "identities.json"
    identity.write_text("{")

    journal = namespace["ProgressJournal"](tmp_path)

    assert journal.files == {"good": valid["checkpoint"]}
    assert journal.identities == {"id": "good"}
    assert not bad.exists() and not identity.exists()
    assert len(journal.quarantined) == 2
    assert all(path.is_file() and ".corrupt-" in path.name for path in journal.quarantined)
    assert sum(record.levelname == "ERROR" for record in caplog.records) == 2


class SupervisorError(Exception):
    def __init__(self, message, *, status, kind):
        super().__init__(message)
        self.status = status
        self.kind = kind


def load_tool_reference(directory):
    apply_reference("hard-review-multi-defect-tool-calls", directory)
    # These dependencies are outside the extracted fixture. Stub only the response
    # envelope and error class; selection, parsing and generation routing run as patched.
    namespace = {
        "__name__": "tool_reference",
        "SupervisorError": SupervisorError,
        "chat_completion_response": lambda model, text: {
            "choices": [{"message": {"content": text}}]
        },
    }
    code = (directory / "tool_calls.py").read_text()
    for line in (
        "from .chat_shapes import chat_completion_response\n",
        "from .errors import SupervisorError\n",
    ):
        code = code.replace(line, "")
    exec(compile(code, "tool_reference", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("name", ["auto", "none", "required"])
@pytest.mark.parametrize("answer", ["selected", "other", "text", "unsupported"])
def test_named_tool_reference_enforces_reserved_function_names(tmp_path, name, answer):
    namespace = load_tool_reference(tmp_path)
    tools = [{"type": "function", "function": {"name": n}} for n in (name, "other")]
    payload = {"tools": tools, "tool_choice": {"type": "function", "function": {"name": name}}}
    selected = name if answer == "selected" else "other"
    text = (
        "plain answer"
        if answer == "text"
        else ("<call>" + json.dumps({"name": selected, "arguments": {}}) + "</call>")
    )
    tokenizer = SimpleNamespace(
        has_tool_calling=answer != "unsupported",
        tool_parser=lambda text, tools: json.loads(text),
        tool_call_start="<call>",
        tool_call_end="</call>",
    )
    namespace["_generate_with_tools"] = lambda *args: (text, "prompt", "stop")
    backend = SimpleNamespace(
        _tokenizer=tokenizer,
        _inference_executor=None,
        _generate_sync=lambda *args: ("plain answer", "prompt"),
        _counted=lambda response, *args: response,
        current_model_name="test",
    )
    if answer == "selected":
        result = asyncio.run(namespace["chat_completion_with_tools"](backend, payload, [], 10))
        calls = result["choices"][0]["message"]["tool_calls"]
        assert [call["function"]["name"] for call in calls] == [name]
    else:
        with pytest.raises(SupervisorError) as caught:
            asyncio.run(namespace["chat_completion_with_tools"](backend, payload, [], 10))
        assert caught.value.kind == (
            "TOOL_CHOICE_UNSUPPORTED" if answer == "unsupported" else "TOOL_CHOICE_VIOLATED"
        )


@pytest.mark.parametrize("mode", ["auto", "none", "required"])
def test_tool_reference_preserves_mode_choices(tmp_path, mode):
    namespace = load_tool_reference(tmp_path)
    text = '<call>{"name": "offered", "arguments": {}}</call>'
    namespace["_generate_with_tools"] = lambda *args: (text, "prompt", "stop")
    backend = SimpleNamespace(
        _tokenizer=SimpleNamespace(
            has_tool_calling=True,
            tool_parser=lambda text, tools: json.loads(text),
            tool_call_start="<call>",
            tool_call_end="</call>",
        ),
        _inference_executor=None,
        _generate_sync=lambda *args: ("plain answer", "prompt"),
        _counted=lambda response, *args: response,
        current_model_name="test",
    )
    payload = {"tools": [{"function": {"name": "offered"}}], "tool_choice": mode}
    result = asyncio.run(namespace["chat_completion_with_tools"](backend, payload, [], 10))
    message = result["choices"][0]["message"]
    if mode == "none":
        assert message == {"content": "plain answer"}
    else:
        assert [call["function"]["name"] for call in message["tool_calls"]] == ["offered"]


def test_stale_lock_guidance_rejects_rename_only_recovery():
    review = TASKS / "review-stale-lock-recovery/expected/source-review.txt"
    paired = TASKS / "fix-stale-lock-recovery/expected/source-review.txt"
    finding = TASKS / "fix-stale-lock-recovery/fixture/finding.md"
    assert review.read_bytes() == paired.read_bytes()
    for path in (review, paired, finding):
        text = path.read_text()
        assert "permanent inode that is never unlinked" in text
        assert "a second stat or rename alone cannot establish" in text
        assert "claim the stale file with an atomic rename" not in text
