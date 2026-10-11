"""FRC-07: final publication uses joined usage, never admission snapshots."""

import json

import pytest

pytest.importorskip("gym.toc_eval", reason="host integration is not shipped")

from gym import toc_eval


class LateLedger:
    def cost_for_launch(self, lrq):
        return {"costUsd": 0.1}

    def compression_arm_for_launch(self, lrq):
        return {"requested": "off", "recorded": ["off"], "effective": ["off"]}

    def toc_counts_for_launch(self, lrq):
        return {"rows": 0, "okRows": 0}

    def turn_usage_for_launch(self, lrq):
        return {"usageComplete": True, "totalTokens": {"a": 282590, "b": 323213}[lrq]}


def document():
    return {
        "schema": toc_eval.SCHEMA,
        "attempts": [
            {
                "workerClass": "codex",
                "model": "m",
                "arm": "off",
                "verdict": "passed",
                "launchRequestId": lrq,
                "invocationAt": "pilot",
                "reservationTokens": 300000,
            }
            for lrq in ("a", "b")
        ],
        "lastInvocation": {"at": "pilot", "tokensSpent": 560302, "maxTotalTokens": 650000},
        "result": {
            "status": "complete",
            "reason": "every planned attempt dispatched",
            "tokensSpent": 560302,
            "maxTotalTokens": 650000,
        },
    }


def test_final_delayed_join_republishes_both_summaries(monkeypatch, tmp_path):
    monkeypatch.setattr(toc_eval, "_open_reader", LateLedger)
    path = tmp_path / "result.json"
    doc = document()
    assert toc_eval._finish(path, doc) == 0
    saved = json.loads(path.read_text())
    assert saved["result"]["tokensSpent"] == 605803
    assert saved["lastInvocation"]["tokensSpent"] == 605803


def test_late_usage_recovers_without_dispatch_or_double_count(monkeypatch, tmp_path):
    doc = document()
    reader = LateLedger()
    usage = reader.turn_usage_for_launch
    monkeypatch.setattr(reader, "turn_usage_for_launch", lambda _: None)
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: reader)
    monkeypatch.setattr(
        toc_eval, "_run_attempt", lambda *_: (_ for _ in ()).throw(AssertionError())
    )
    path = tmp_path / "result.json"
    assert toc_eval._finish(path, doc) == 1
    assert doc["result"]["tokensSpent"] is None
    assert doc["result"]["admissionTokensSpent"] == 560302
    monkeypatch.setattr(reader, "turn_usage_for_launch", usage)
    for _ in range(3):
        doc = json.loads(path.read_text())
        assert toc_eval._finish(path, doc) == 0
        assert doc["result"]["tokensSpent"] == 605803
        assert doc["result"]["status"] == "complete"
        assert doc["result"]["admissionTokensSpent"] == 560302


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"usageComplete": False, "totalTokens": 10},
        {"usageComplete": True, "totalTokens": None},
        *[{"usageComplete": True, "totalTokens": value} for value in (-1, True, "42", 3.5)],
    ],
)
def test_bad_usage_cannot_publish_complete_or_reuse_old_total(monkeypatch, tmp_path, usage):
    doc = document()
    for record in doc["attempts"]:
        record.update(usageComplete=True, totalTokens=123)
    reader = LateLedger()
    monkeypatch.setattr(reader, "turn_usage_for_launch", lambda _: usage)
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: reader)
    assert toc_eval._finish(tmp_path / "result.json", doc) == 1
    assert doc["result"]["tokensSpent"] is None
    assert doc["lastInvocation"]["tokenAccountingStatus"] == "unresolved"


@pytest.mark.parametrize(
    "change", ["alias", "missing_scope", "foreign_invocation", "excluded", "crash"]
)
def test_accounting_scope_and_interrupted_publication(monkeypatch, tmp_path, change):
    doc = document()
    if change == "alias":
        doc["attempts"].append(dict(doc["attempts"][0]))
    elif change == "missing_scope":
        doc["attempts"][0].pop("invocationAt")
    elif change == "foreign_invocation":
        doc["attempts"][0]["invocationAt"] = "earlier"
    elif change == "excluded":
        doc["attempts"][0].update(excluded=True, excludedReason="invalid grade")
    elif change == "crash":
        doc.pop("result")
    monkeypatch.setattr(toc_eval, "_open_reader", LateLedger)
    path = tmp_path / "result.json"
    code = toc_eval._finish(path, doc)
    if change in ("alias", "missing_scope"):
        assert code == 1 and doc["result"]["tokensSpent"] is None
    else:
        assert code == 0
        assert doc["result"]["tokensSpent"] == (
            323213 if change == "foreign_invocation" else 605803
        )
    if change == "crash":
        assert doc["result"]["status"] == "partial"
        assert "interrupted" in doc["result"]["reason"]


def test_final_cap_uses_measured_total_not_admission_snapshot(monkeypatch, tmp_path):
    doc = document()
    doc["lastInvocation"]["maxTotalTokens"] = doc["result"]["maxTotalTokens"] = 600000
    monkeypatch.setattr(toc_eval, "_open_reader", LateLedger)
    assert toc_eval._finish(tmp_path / "result.json", doc) == 0
    assert doc["result"]["status"] == "partial"
    assert doc["result"]["finalTokenCapComparison"] == "exceeded"
    assert doc["result"]["tokensSpent"] == 605803


def test_failed_join_publishes_null_and_retains_retry_evidence(monkeypatch, tmp_path):
    doc = document()
    original = json.loads(json.dumps(doc["attempts"]))
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert toc_eval._finish(tmp_path / "result.json", doc) == 1
    assert doc["attempts"] == original
    assert doc["result"]["tokensSpent"] is None
    assert doc["result"]["admissionTokensSpent"] == 560302
    assert "offline" in doc["result"]["reason"]


def test_crash_during_publication_retries_join_without_new_launch(monkeypatch, tmp_path):
    doc = document()
    path = tmp_path / "result.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(toc_eval, "_open_reader", LateLedger)
    write = toc_eval._write
    monkeypatch.setattr(toc_eval, "_write", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError, match="crash"):
        toc_eval._finish(path, doc)
    assert json.loads(path.read_text())["result"]["tokensSpent"] == 560302
    monkeypatch.setattr(toc_eval, "_write", write)
    monkeypatch.setattr(toc_eval, "_run_attempt", lambda *_: pytest.fail("retry dispatched"))
    assert toc_eval.main(["--out", str(path), "--rejoin-only"]) == 0
    assert json.loads(path.read_text())["result"]["tokensSpent"] == 605803


@pytest.mark.parametrize("fail", [False, True])
def test_owned_reader_closes_on_success_and_failure(monkeypatch, tmp_path, fail):
    reader = LateLedger()
    closed = []
    reader.close = lambda: closed.append(True)
    if fail:
        monkeypatch.setattr(
            reader, "turn_usage_for_launch", lambda _: (_ for _ in ()).throw(OSError())
        )
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: reader)
    assert toc_eval._finish(tmp_path / "result.json", document()) == int(fail)
    assert closed == [True]


def test_provider_failure_exclusion_survives_arm_revalidation(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        toc_eval.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "cells": [
                        {
                            "launchRequestId": "a",
                            "verdict": "agent_error",
                            "terminalStatus": "provider_unavailable",
                        }
                    ]
                }
            ),
        ),
    )
    [record] = toc_eval._run_attempt("codex", "m", "off", "coding-toc-commands", 1, "low")
    assert record["excluded"]
    assert record["excludedReason"] == "provider unavailable: infrastructure failure"
    # A transient arm mismatch must not replace the lasting infra exclusion.
    record["excludedReason"] = "arm mismatch: stale telemetry"
    toc_eval._join_record(record, LateLedger())
    assert record["excluded"]
    assert record["excludedReason"] == "provider unavailable: infrastructure failure"
    assert record["costUsd"] == 0.1
    assert record["totalTokens"] == 282590


@pytest.mark.parametrize("verdict", ["agent_error", "passed", "failed"])
def test_terminal_provider_annotation_excludes_only_ungraded_work(monkeypatch, verdict):
    from types import SimpleNamespace

    monkeypatch.setattr(
        toc_eval.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "cells": [
                        {
                            "launchRequestId": "a",
                            "verdict": verdict,
                            "infra_attempts": [{"reason": "provider_unavailable: litellm.Timeout"}],
                        }
                    ]
                }
            ),
        ),
    )
    [record] = toc_eval._run_attempt("codex", "m", "off", "coding-toc-commands", 1, "low")
    assert bool(record.get("excluded")) == (verdict == "agent_error")
