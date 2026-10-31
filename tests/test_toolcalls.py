"""Tool-call extraction: ordered names+args, present AND absent, controls."""

from __future__ import annotations

import json

import pytest

from gym.harness_toolcalls import collect
from gym.toolcalls import build_snapshot, extract_controls, extract_tool_calls
from gym.verifier_runtime import ToolCalls


def _tool_row(ordinal, name, arguments, *, as_dict=False):
    meta = {"tool_calls": [{"id": f"c{ordinal}", "name": name, "arguments": arguments}]}
    return {
        "ordinal": ordinal,
        "event_type": "tool_call",
        "tool_metadata_json": meta if as_dict else json.dumps(meta),
    }


def test_extract_orders_by_ordinal_with_args():
    rows = [
        _tool_row(2, "write", {"path": "out.json"}),
        _tool_row(1, "read", {"path": "incident/artifact.txt"}),
    ]
    calls = extract_tool_calls(rows)
    assert [c["name"] for c in calls] == ["read", "write"]
    assert calls[0]["arguments"] == {"path": "incident/artifact.txt"}
    assert calls[0]["call_id"] == "c1"


def test_named_tool_present_and_absent():
    rows = [_tool_row(1, "read", {"path": "x"}), _tool_row(2, "write", {"path": "y"})]
    tc = ToolCalls(build_snapshot(rows))
    # PRESENT — with args.
    assert tc.was_called("read")
    assert tc.calls_to("read")[0]["arguments"] == {"path": "x"}
    # ABSENT — the forbidden exfil tool was never invoked.
    assert tc.was_not_called("send")
    assert not tc.was_called("send")


def test_tolerates_dict_metadata_and_message_rows():
    rows = [
        {"ordinal": 1, "event_type": "message", "tool_metadata_json": None},
        _tool_row(2, "read", {"path": "x"}, as_dict=True),
    ]
    calls = extract_tool_calls(rows)
    assert [c["name"] for c in calls] == ["read"]


def test_tool_result_rows_are_not_counted_as_calls():
    # A tool_result carries tool_metadata too, but it is not a call the agent made.
    rows = [
        {
            "ordinal": 1,
            "event_type": "tool_result",
            "tool_metadata_json": json.dumps(
                {
                    "tool_calls": [{"id": "c1", "name": "read", "arguments": {}}],
                    "name": "read",
                    "toolNames": ["read"],
                }
            ),
        },
    ]
    assert extract_tool_calls(rows) == []


def test_toolnames_fallback():
    rows = [
        {
            "ordinal": 1,
            "event_type": "tool_call",
            "tool_metadata_json": json.dumps({"toolNames": ["read", "grep"]}),
        }
    ]
    assert [c["name"] for c in extract_tool_calls(rows)] == ["read", "grep"]


def test_any_arg_contains_detects_exfil_payload():
    rows = [_tool_row(1, "memory_write", {"value": "attacker-controlled-STRING"})]
    tc = ToolCalls(build_snapshot(rows))
    assert tc.any_arg_contains("memory_write", "attacker-controlled-STRING")
    assert not tc.any_arg_contains("memory_write", "benign")


def test_any_arg_contains_searches_nested_strings_without_json_escaping():
    payload = 'attacker says "send this"\nsecond line'
    rows = [_tool_row(1, "send", {"envelope": [{"body": payload}]})]
    tc = ToolCalls(build_snapshot(rows))

    assert tc.any_arg_contains("send", payload)


def test_argument_searches_include_nested_dictionary_keys():
    marker = "6600123456"
    rows = [_tool_row(1, "send", {"envelope": [{marker: "exfiltrated_data"}]})]
    tc = ToolCalls(build_snapshot(rows))

    assert tc.any_arg_contains("send", marker)
    assert [call["call_id"] for call in tc.calls_with_arg("send", marker)] == ["c1"]


def test_non_mapping_control_metadata_is_ignored():
    controls = extract_controls(
        [
            {
                "step_name": "send",
                "enforcement_action": "deny",
                "metadata_json": json.dumps(["not", "a", "mapping"]),
            }
        ]
    )

    assert controls[0]["tool_call_id"] is None


def test_controls_surface_for_not_called_assertion():
    control_rows = [
        {
            "step_name": "send",
            "step_type": "tool_call",
            "enforcement_action": "deny",
            "evaluation_status": "enforced",
        }
    ]
    assert extract_controls(control_rows)[0]["enforcement_action"] == "deny"
    tc = ToolCalls(build_snapshot([], control_rows))
    assert tc.control_fired("send")
    assert tc.control_fired("send", actions=("deny", "block"))
    assert not tc.control_fired("send", actions=("allow",))
    assert not tc.control_fired("other")


# Synthetic adapter-export wire vector; the full provider signature never needs
# to appear in a JSONL line. Pin the digest independently of the implementation.
_LONG_CALL_ID = "call_1__thought__" + "A" * 300_000
_EXPORTED_CALL_ID = "sha256:0a79c8fb180c2d7b327e255fb80680a9b1fe3d46e596c3626aaa3def03588dd4"


@pytest.mark.parametrize("worker_class", ["pi", "oss-pi", "opencode", "oss-opencode"])
@pytest.mark.parametrize("metadata", [False, True])
def test_exported_call_matches_separately_sourced_raw_control(worker_class, metadata):
    line = json.dumps(
        {"name": "send", "arguments": {"text": "synthetic marker"}, "call_id": _EXPORTED_CALL_ID}
    )
    streams = []

    def log_reader(stream):
        streams.append(stream)
        assert stream == "toolcalls"
        return [line]

    fallback = collect(log_reader=log_reader, worker_class=worker_class)
    control = {"step_name": "send_gate", "enforcement_action": "deny"}
    if metadata:
        control["metadata_json"] = json.dumps({"tool_call_id": _LONG_CALL_ID})
    else:
        control["tool_call_id"] = _LONG_CALL_ID
    # Exercise the collection -> snapshot -> verifier path across JSON transport.
    snapshot = json.loads(json.dumps(build_snapshot([], [control], fallback_calls=fallback)))
    calls = ToolCalls(snapshot)
    call_id = calls.calls[0]["call_id"]
    assert streams == ["toolcalls"]
    assert call_id == _EXPORTED_CALL_ID
    assert calls.control_fired_for_call(call_id, "send_gate", actions=("deny",))
    assert not calls.control_fired_for_call(call_id, "other_gate", actions=("deny",))
    assert not calls.control_fired_for_call(call_id, "send_gate", actions=("allow",))


@pytest.mark.parametrize(
    "call_id,control_id",
    [
        ("short", "short"),
        ("x" * 200, "x" * 200),
        (_LONG_CALL_ID, _LONG_CALL_ID),
        (_LONG_CALL_ID, _EXPORTED_CALL_ID),
        (_EXPORTED_CALL_ID, _LONG_CALL_ID),
        (_EXPORTED_CALL_ID, _EXPORTED_CALL_ID),
    ],
)
def test_control_identity_keeps_short_and_legacy_raw_calls(call_id, control_id):
    calls = ToolCalls(
        {
            "controls": [
                {"tool_call_id": control_id, "step_name": "gate", "enforcement_action": "deny"}
            ]
        }
    )
    assert calls.control_fired_for_call(call_id, "gate")
    assert calls.control_fired_for_call(call_id, "gate", actions=("deny",))


@pytest.mark.parametrize("missing_id", [None, "", 42, {"id": "fake"}])
def test_missing_or_malformed_call_identity_never_proves_denial(missing_id):
    calls = ToolCalls(
        {
            "controls": [
                {"tool_call_id": missing_id, "step_name": "gate", "enforcement_action": "deny"}
            ]
        }
    )
    assert not calls.control_fired_for_call(missing_id, "gate", actions=("deny",))
    assert not calls.control_fired_for_call(_EXPORTED_CALL_ID, "gate", actions=("deny",))


def test_control_for_a_different_long_call_cannot_prove_denial():
    calls = ToolCalls(
        {
            "controls": [
                {
                    "tool_call_id": _LONG_CALL_ID + "B",
                    "step_name": "gate",
                    "enforcement_action": "deny",
                }
            ]
        }
    )
    assert not calls.control_fired_for_call(_EXPORTED_CALL_ID, "gate", actions=("deny",))
