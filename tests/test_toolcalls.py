"""Tool-call extraction: ordered names+args, present AND absent, controls."""

from __future__ import annotations

import json

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
