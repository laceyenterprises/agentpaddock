"""PDK-04: the leaderboard results schema, pricing and aggregation (scripts/leaderboard.py)."""

from __future__ import annotations

import copy
import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import leaderboard as lb
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "leaderboard"
FIXTURE_DOC = "2026-01-03-fixture-run.json"


@pytest.fixture
def results(tmp_path: Path) -> Path:
    target = tmp_path / "results"
    shutil.copytree(FIXTURE, target)
    return target


def _edit(results: Path, mutate) -> None:
    path = results / FIXTURE_DOC
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    lb.write_document(path, document)


def _errors(results: Path) -> str:
    with pytest.raises(lb.ResultsError) as caught:
        lb.load_results(results)
    return str(caught.value)


def test_committed_results_validate():
    loaded = lb.load_results()
    assert loaded.documents and loaded.records
    assert all(
        record["cost"]["source"] in lb.PRICE_SOURCES for record in loaded.records
    )


def test_fixture_results_validate():
    loaded = lb.load_results(FIXTURE)
    assert len(loaded.records) == 14
    assert set(loaded.price_tables) == {"2026-01-02"}


def test_gzip_results_load_equal_to_plain_results(results):
    plain = lb.load_results(results)
    path = results / FIXTURE_DOC
    compressed = path.with_suffix(".json.gz")
    lb.write_document(compressed, plain.documents[path.name])
    path.unlink()
    loaded = lb.load_results(results)
    assert loaded.records == plain.records
    assert loaded.price_tables == plain.price_tables
    assert loaded.changelog == plain.changelog
    assert loaded.fairness == plain.fairness
    assert list(loaded.documents) == [compressed.name]
    assert loaded.documents[compressed.name] == plain.documents[path.name]


def test_both_results_storage_forms_are_rejected(results):
    path = results / FIXTURE_DOC
    path.with_suffix(".json.gz").write_bytes(gzip.compress(path.read_bytes()))
    message = _errors(results)
    assert "duplicate results document" in message
    assert FIXTURE_DOC in message and FIXTURE_DOC + ".gz" in message


@pytest.mark.parametrize(
    "content", [b"not gzip", b"\x1f\x8bcorrupt", gzip.compress(b"{}")[:-1]]
)
def test_corrupt_gzip_results_name_the_file(results, content):
    path = results / (FIXTURE_DOC + ".gz")
    (results / FIXTURE_DOC).unlink()
    path.write_bytes(content)
    assert f"{path.name}: cannot read:" in _errors(results)


def test_gzip_results_over_64_mib_are_rejected(results):
    assert lb.MAX_DECOMPRESSED_BYTES == 64 * 1024 * 1024
    path = results / (FIXTURE_DOC + ".gz")
    (results / FIXTURE_DOC).unlink()
    path.write_bytes(gzip.compress(b" " * (lb.MAX_DECOMPRESSED_BYTES + 1)))
    message = _errors(results)
    assert path.name in message and "64 MiB" in message


def test_gzip_writer_is_deterministic_and_preserves_plain_text(tmp_path):
    document = lb.load_results(FIXTURE).documents[FIXTURE_DOC]
    first, second = tmp_path / "first.json.gz", tmp_path / "second.json.gz"
    lb.write_document(first, document)
    lb.write_document(second, document)
    content = first.read_bytes()
    assert content == second.read_bytes()
    assert content[:3] == b"\x1f\x8b\x08"
    assert content[3] & 8 == 0  # No stored file name.
    assert content[4:8] == b"\0" * 4  # mtime zero.
    assert content[8] == 2  # Maximum compression level.
    plain = tmp_path / "plain.json"
    lb.write_document(plain, document)
    assert (
        gzip.decompress(content)
        == plain.read_bytes()
        == lb.render_document(document).encode("utf-8")
    )


def test_gzip_content_requires_gzip_results_extension(results):
    path = results / FIXTURE_DOC
    path.write_bytes(gzip.compress(path.read_bytes()))
    message = _errors(results)
    assert f"{path.name}: cannot read:" in message
    assert "require a .json.gz extension" in message


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
@pytest.mark.parametrize("existing", [False, True])
def test_failed_atomic_replace_preserves_destination(
    tmp_path, monkeypatch, suffix, existing
):
    path = tmp_path / ("document" + suffix)
    document = lb.load_results(FIXTURE).documents[FIXTURE_DOC]
    if existing:
        lb.write_document(path, document)
    previous = path.read_bytes() if existing else None

    def fail_replace(source, destination):
        # The staged document is complete before the destination is touched.
        content = source.read_bytes()
        if suffix.endswith(".gz"):
            content = gzip.decompress(content)
        assert content == lb.render_document(document).encode("utf-8")
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        lb.write_document(path, document)
    assert (path.read_bytes() if path.exists() else None) == previous
    assert list(tmp_path.iterdir()) == ([path] if existing else [])


def test_failed_gzip_stream_preserves_destination(tmp_path, monkeypatch):
    path = tmp_path / "document.json.gz"
    document = lb.load_results(FIXTURE).documents[FIXTURE_DOC]
    lb.write_document(path, document)
    previous = path.read_bytes()
    original_write = gzip.GzipFile.write

    def fail_write(stream, content):
        original_write(stream, content[:10])
        raise OSError("disk full")

    monkeypatch.setattr(gzip.GzipFile, "write", fail_write)
    with pytest.raises(OSError, match="disk full"):
        lb.write_document(path, document)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_duplicate_run_ids_span_plain_and_gzip_documents(results):
    document = lb.load_results(results).documents[FIXTURE_DOC]
    document["id"] = "fixture-copy"
    lb.write_document(results / "2026-01-03-fixture-copy.json.gz", document)
    assert "appears in 2 records" in _errors(results)


def test_results_storage_forms_load_in_combined_sorted_order(results):
    document = lb.load_results(results).documents[FIXTURE_DOC]
    expected = []
    for id, suffix in [("zzz", ".json"), ("aaa", ".json.gz")]:
        other = copy.deepcopy(document)
        other["id"] = id
        for record in other["records"]:
            record["run_id"] += ":" + id
        name = f"2026-01-03-{id}{suffix}"
        lb.write_document(results / name, other)
        expected.append(name)
    loaded = lb.load_results(results)
    assert list(loaded.documents) == sorted([FIXTURE_DOC, *expected])


def test_gzip_filename_must_match_the_document(results):
    path = results / (FIXTURE_DOC + ".gz")
    document = lb.load_results(results).documents[FIXTURE_DOC]
    document["id"] = "different-id"
    (results / FIXTURE_DOC).unlink()
    lb.write_document(path, document)
    message = _errors(results)
    assert path.name in message and "file name must be" in message


def _evidenced_fairness():
    raw = json.loads((FIXTURE / "meta" / "fairness.json").read_text(encoding="utf-8"))
    raw["audits"] = [raw["audits"][0]]
    raw["audits"][0].update(
        status="audited",
        sign_off=None,
        evidence=[
            {
                "id": "tools",
                "status": "pass",
                "notes": "Native tools match the documented default.",
                "sources": ["https://vendor.example/defaults", "run.tools"],
            }
        ],
    )
    return raw


def test_fairness_review_and_maintainer_sign_off_are_separate():
    raw = _evidenced_fairness()
    assert lb.validate_fairness(raw) == []
    audit = raw["audits"][0]
    audit.update(status="passed", sign_off="Fixture maintainer sign-off")
    assert lb.validate_fairness(raw) == []
    audit.update(status="pending", sign_off=None)
    audit["evidence"][0]["status"] = "not-verifiable"
    assert lb.validate_fairness(raw) == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda a: a.pop("evidence"), "evidence must cover every checklist item"),
        (lambda a: a.update(evidence=None), "evidence must cover every checklist item"),
        (lambda a: a.update(evidence=[]), "evidence must cover every checklist item"),
        (
            lambda a: a["evidence"].append(dict(a["evidence"][0])),
            "duplicate checklist evidence",
        ),
        (
            lambda a: a["evidence"][0].update(id="unknown"),
            "id must name a checklist item",
        ),
        (lambda a: a["evidence"][0].update(status="unknown"), "status must be one of"),
        (
            lambda a: a["evidence"][0].update(status="fail"),
            "requires every check to pass",
        ),
        (
            lambda a: a["evidence"][0].update(status="not-verifiable"),
            "requires every check to pass",
        ),
        (
            lambda a: a["evidence"][0].update(sources=[]),
            "sources must contain evidence",
        ),
        (
            lambda a: a["evidence"][0].update(sources=[""]),
            "sources must contain evidence",
        ),
        (lambda a: a["evidence"][0].update(notes=""), "notes must explain the finding"),
        (lambda a: a["evidence"][0].pop("sources"), "missing sources"),
        (lambda a: a.update(sign_off="Fixture sign-off"), "only a passed audit"),
        (lambda a: a.update(status="passed"), "requires maintainer sign_off"),
        (lambda a: a.update(sign_off=42), "sign_off must be non-empty text or null"),
    ],
)
def test_fairness_evidence_rejects_incomplete_or_contradictory_audits(
    mutation, message
):
    raw = _evidenced_fairness()
    mutation(raw["audits"][0])
    assert message in "\n".join(lb.validate_fairness(raw))


@pytest.mark.parametrize("status", ["pending", "audited", "passed", "failed"])
@pytest.mark.parametrize("date", [None, "", "unknown", 42, "2026-01-03"])
def test_fairness_audit_dates(status, date):
    raw = _evidenced_fairness()
    raw["audits"][0].update(
        status=status,
        date=date,
        sign_off="Fixture maintainer sign-off" if status == "passed" else None,
    )
    if date == "2026-01-03" or (status == "pending" and date is None):
        expected = []
    elif status == "pending":
        expected = ["meta/fairness.json: audits[0]: date must be an ISO date or null"]
    else:
        expected = ["meta/fairness.json: audits[0]: a completed audit is dated"]
    assert lb.validate_fairness(raw) == expected


def test_fairness_evidence_covers_all_unique_checklist_ids():
    raw = _evidenced_fairness()
    raw["checklist"].append({"id": "model", "check": "Native model identity."})
    assert "evidence must cover every checklist item" in "\n".join(
        lb.validate_fairness(raw)
    )
    raw["checklist"][-1]["id"] = "tools"
    assert "checklist ids must be unique" in "\n".join(lb.validate_fairness(raw))


def test_sign_off_cannot_bypass_evidence_and_pending_review_dates_are_validated():
    raw = _evidenced_fairness()
    audit = raw["audits"][0]
    audit.update(status="passed", sign_off="Fixture sign-off")
    audit.pop("evidence")
    assert "evidence must cover every checklist item" in "\n".join(
        lb.validate_fairness(raw)
    )
    audit.update(status="pending", sign_off=None, date="unknown")
    assert "date must be an ISO date or null" in "\n".join(lb.validate_fairness(raw))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["records"][0].update(extra=1), "unknown extra"),
        (lambda d: d["records"][0].pop("task_version"), "missing task_version"),
        (
            lambda d: d["records"][0].update(task_version=0),
            "task_version must be a positive",
        ),
        (
            lambda d: d["records"][5].update(excludedBecause=None),
            "infrastructure row carries",
        ),
        (
            lambda d: d["records"][0].update(excludedBecause="pre-NOPE-01"),
            "nor a changelog",
        ),
        (lambda d: d["records"][0].update(verdict="maybe"), "is not a gym outcome"),
        (lambda d: d["records"][0].update(score=1.5), "score must be in [0, 1]"),
        (
            lambda d: d["records"][0]["tokens"].update(input=-1),
            "token counts are non-negative",
        ),
        (
            lambda d: d["records"][0]["tokens"].update(input=1.5),
            "token counts are non-negative",
        ),
        (
            lambda d: d["records"][0].update(paddock_commit=None),
            "names the paddock commit",
        ),
        (
            lambda d: d["records"][0]["model"].update(provider_id="nope"),
            "provider/model",
        ),
        (
            lambda d: d["records"][0]["harness"].update(
                config_hash="sha256:" + "0" * 64
            ),
            "no descriptor in harness_configs",
        ),
        (
            lambda d: d["harness_configs"].update(
                {"sha256:" + "1" * 64: {"harness": "x"}}
            ),
            "does not hash to its key",
        ),
        (
            lambda d: d["records"][0]["cost"].update(usd=0.01),
            "differs from the price table",
        ),
        (
            lambda d: d["records"][6].update(
                cost={"usd": 0.5, "source": "unknown", "reason": None}
            ),
            "unknown source has no dollar figure",
        ),
        (lambda d: d.update(price_table="1999-01-01"), "is not in results/prices/"),
        (lambda d: d.update(id="renamed"), "file name must be <date>-<id>.json"),
        (lambda d: d["source"].update(kind="guess"), "source.kind must be one of"),
    ],
)
def test_schema_rejects(results: Path, mutate, message: str):
    _edit(results, mutate)
    assert message in _errors(results)


def test_duplicate_run_ids_across_documents_are_rejected(results: Path):
    document = json.loads((results / FIXTURE_DOC).read_text(encoding="utf-8"))
    document["id"] = "fixture-copy"
    lb.write_document(results / "2026-01-03-fixture-copy.json", document)
    assert "run_id fixture-001 appears in 2 records" in _errors(results)


def test_import_documents_may_omit_the_paddock_commit(results: Path):
    def mutate(document):
        document["source"]["kind"] = "import"
        document["records"][0]["paddock_commit"] = None

    _edit(results, mutate)
    assert lb.load_results(results).records[0]["paddock_commit"] is None


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            {"input": 1.0, "cache": None, "output": 1.0, "source": "unknown"},
            "carries no rate",
        ),
        (
            {"input": None, "cache": None, "output": None, "source": "published-rate"},
            "needs input",
        ),
        ({"source": "estimate"}, "source must be one of"),
        ({"as_of": "2026-02-01"}, "as_of is after the table's date"),
        ({"reference": ""}, "names its reference"),
    ],
)
def test_price_table_rejects(results: Path, entry: dict, message: str):
    path = results / "prices" / "2026-01-02.json"
    table = json.loads(path.read_text(encoding="utf-8"))
    table["models"]["acme/coder-large"].update(entry)
    path.write_text(json.dumps(table), encoding="utf-8")
    assert message in _errors(results)


def test_changelog_exclusion_tag_must_name_its_entry(results: Path):
    path = results / "meta" / "changelog.json"
    changelog = json.loads(path.read_text(encoding="utf-8"))
    changelog["entries"][0]["excludes"] = "pre-SOMETHING-ELSE"
    path.write_text(json.dumps(changelog), encoding="utf-8")
    assert "excludes must be null or 'pre-FIXGRADE-01'" in _errors(results)


def _table() -> lb.PriceTable:
    return lb.load_results(FIXTURE).price_tables["2026-01-02"]


MEASURED = {"input": 100_000, "cache": 400_000, "output": 20_000}


def test_price_cost_is_a_number_only_with_a_priced_source():
    table = _table()
    assert lb.price_cost(MEASURED, "acme/coder-large", table) == {
        "usd": 0.48,
        "source": "published-rate",
        "reason": None,
    }
    assert lb.price_cost(MEASURED, "bolt/coder-small", table)["source"] == lb.RECONCILED
    unmeasured = dict(MEASURED, cache=None)
    assert (
        lb.price_cost(unmeasured, "acme/coder-large", table)["reason"]
        == "tokens_not_measured"
    )
    assert lb.price_cost(MEASURED, "nova/coder-unpriced", table)["reason"] == (
        "model_not_in_price_table"
    )
    assert (
        lb.price_cost(MEASURED, "local/coder-tiny", table)["reason"]
        == "price_source_unknown"
    )
    assert (
        lb.price_cost(MEASURED, "acme/coder-large", None)["reason"]
        == "price_table_missing"
    )
    for model in ("nova/coder-unpriced", "local/coder-tiny"):
        assert lb.price_cost(MEASURED, model, table)["usd"] is None


@pytest.mark.parametrize(
    "cost",
    [
        {"usd": 0.48},
        {"usd": 0.48, "source": None},
        {"usd": 0.48, "source": "unknown"},
        {"usd": 0.48, "source": "estimate"},
        {"usd": None, "source": "published-rate"},
        None,
    ],
)
def test_a_cost_without_a_price_source_formats_as_unknown(cost):
    assert lb.format_cost(cost) == "unknown"


def test_a_priced_cost_formats_as_dollars():
    assert lb.format_cost({"usd": 0.48, "source": "published-rate"}) == "$0.48"
    assert lb.format_cost({"usd": 0.004, "source": lb.RECONCILED}) == "$0.0040"


def test_wilson_matches_the_published_intervals():
    low, high = lb.wilson(7, 18)
    assert (round(low * 100), round(high * 100)) == (20, 61)
    assert lb.wilson(0, 12)[0] == 0.0
    assert lb.wilson(0, 0) == (0.0, 0.0)


def _rows(records, suite="coding", version=1):
    board = next(
        b
        for b in lb.boards(records)
        if b["suite"] == suite and b["task_version"] == version
    )
    return {(row["key"][0], row["key"][3]): row for row in board["rows"]}


def test_boards_score_only_included_rows_and_price_per_pass():
    rows = _rows(lb.load_results(FIXTURE).records)
    acme = rows[("alpha-cli", "acme/coder-large")]
    assert (acme["passed"], acme["n"]) == (2, 3)
    assert acme["tokens_per_pass"] == 3 * 520_000 / 2
    assert acme["cost_per_pass"] == {
        "usd": 0.72,
        "source": "published-rate",
        "reason": None,
    }
    bolt = rows[("beta-cli", "bolt/coder-small")]
    assert (bolt["passed"], bolt["n"]) == (1, 2)
    assert bolt["excluded"] == {"infra:agent_error": 1}
    assert bolt["cost_per_pass"]["source"] == lb.RECONCILED
    unpriced = rows[("beta-cli", "nova/coder-unpriced")]
    assert unpriced["cost_per_pass"]["usd"] is None
    assert unpriced["cost_per_pass"]["source"] == lb.UNKNOWN
    assert (
        rows[("alpha-cli", "local/coder-tiny")]["cost_per_pass"]["source"] == lb.UNKNOWN
    )


def test_mixed_sources_report_the_weaker_one():
    records = copy.deepcopy(lb.load_results(FIXTURE).records[:5])
    for record in records:
        record["harness"] = records[0]["harness"]
        record["model"] = records[0]["model"]
    records[3]["cost"]["source"] = lb.RECONCILED
    summary = lb.summarize(records)
    assert summary["cost_per_pass"]["source"] == lb.PUBLISHED


def test_tokens_per_pass_needs_every_attempt_measured():
    review_v1 = _rows(lb.load_results(FIXTURE).records, "review", 1)
    row = review_v1[("alpha-cli", "acme/coder-large")]
    assert (row["passed"], row["n"]) == (1, 1)
    assert row["excluded"] == {"pre-FIXGRADE-01": 1}
    assert row["tokens_per_pass"] is None and row["tokens_measured"] == 0
    assert row["cost_per_pass"]["source"] == lb.UNKNOWN


def test_a_row_with_no_scored_attempts_ranks_last():
    records = copy.deepcopy(lb.load_results(FIXTURE).records)
    only_infra = copy.deepcopy(records[5])
    only_infra["run_id"] = "fixture-infra-only"
    only_infra["model"]["provider_id"] = "acme/coder-flaky"
    records.append(only_infra)
    board = next(b for b in lb.boards(records) if b["suite"] == "coding")
    assert board["rows"][-1]["key"][3] == "acme/coder-flaky"
    assert board["rows"][-1]["n"] == 0 and board["rows"][-1]["rate"] is None


def test_task_versions_form_separate_boards():
    boards = lb.boards(lb.load_results(FIXTURE).records)
    review = [b for b in boards if b["suite"] == "review"]
    assert [b["task_version"] for b in review] == [1, 2]
    assert all(b["split"] for b in review)
    assert not next(b for b in boards if b["suite"] == "coding")["split"]


def test_discrimination_rule():
    tasks = {
        (t["task"], t["task_version"]): t
        for t in lb.discrimination(lb.load_results(FIXTURE).records)
    }
    assert tasks[("coding-alpha", 1)]["discriminates"] is True
    assert tasks[("coding-alpha", 1)]["combos_passing"] == 3
    assert tasks[("review-delta", 1)] == {
        **tasks[("review-delta", 1)],
        "passed": 1,
        "n": 1,
        "discriminates": False,
    }
    assert tasks[("review-delta", 2)]["discriminates"] is True


def test_records_from_run_maps_a_suite_summary():
    summary = {
        "suite": "coding",
        "cells": [
            {
                "task": "coding-alpha",
                "task_version": 2,
                "verdict": "passed",
                "score": 1.0,
                "launchRequestId": "lrq_a",
                "attempt": 1,
                "metrics": {
                    "worker_class": "codex",
                    "majority_model": "acme/coder-large",
                    "observed_models": {"acme/coder-large": 20_000},
                    "token_usage_sources": ["codex-exec-json"],
                    "token_usage_input": 500_000,
                    "token_usage_cache_read": 400_000,
                    "token_usage_output": 20_000,
                    "wall_seconds": 61.5,
                },
            },
            {"task": "coding-beta", "verdict": "agent_error", "score": None},
        ],
    }
    harness = {"name": "codex", "version": "1.2.3", "config_hash": "sha256:" + "a" * 64}
    model = {"provider_id": "acme/coder-large", "reasoning": "high"}
    first, second = lb.records_from_run(
        summary,
        bundle_id="test-bundle",
        harness=harness,
        model=model,
        paddock_commit="abcdef1",
        table=_table(),
    )
    assert first["tokens"] == MEASURED
    assert first["task_version"] == 2 and first["wall_seconds"] == 61.5
    assert first["cost"] == {"usd": 0.48, "source": "published-rate", "reason": None}
    assert first["excludedBecause"] is None
    assert second["run_id"] == "test-bundle:coding:coding-beta:1"
    assert second["excludedBecause"] == "infra:agent_error"
    assert second["tokens"] == {"input": None, "cache": None, "output": None}
    assert second["cost"]["source"] == lb.UNKNOWN


def test_render_document_is_stable_json():
    document = json.loads((FIXTURE / FIXTURE_DOC).read_text(encoding="utf-8"))
    text = lb.render_document(document)
    assert json.loads(text) == document
    assert text == (FIXTURE / FIXTURE_DOC).read_text(encoding="utf-8")
    assert len(text.splitlines()) < 40 + len(document["records"])


def _convert(cell, suite="coding", **kwargs):
    return lb.records_from_run(
        {
            "suite": suite,
            "cells": [dict(task="coding-alpha", verdict="passed", **cell)],
        },
        bundle_id="test-bundle",
        harness={"name": "codex", "version": "1", "config_hash": "sha256:" + "a" * 64},
        model={"provider_id": "acme/coder-large", "reasoning": "high"},
        paddock_commit="abcdef1",
        table=_table(),
        **kwargs,
    )[0]


def _metrics(**updates):
    return dict(
        worker_class="codex",
        majority_model="acme/coder-large",
        observed_models={"acme/coder-large": 100},
        token_usage_sources=["codex-exec-json"],
        token_usage_input=1000,
        token_usage_cache_read=400,
        token_usage_output=100,
        **updates,
    )


@pytest.mark.parametrize(
    "cell,tag",
    [
        ({"model_mismatch": True}, "infra:model_mismatch"),
        (
            {
                "metrics": {
                    "worker_class": "claude-code",
                    "majority_model": "acme/coder-large",
                }
            },
            "infra:model_mismatch",
        ),
        (
            {
                "metrics": {
                    "worker_class": "codex",
                    "majority_model": "bolt/coder-small",
                }
            },
            "infra:model_mismatch",
        ),
        (
            {
                "metrics": {
                    "worker_class": "codex",
                    "observed_models": {"acme/coder-large": 50, "bolt/coder-small": 50},
                }
            },
            "infra:model_mismatch",
        ),
        (
            {
                "execution_provenance": {
                    "harness": "claude-code",
                    "model": "acme/coder-large",
                }
            },
            "infra:model_mismatch",
        ),
        (
            {"execution_provenance": {"harness": "codex", "model": "bolt/coder-small"}},
            "infra:model_mismatch",
        ),
        ({}, "infra:attribution_unverified"),
    ],
)
def test_conversion_excludes_attribution_failures(cell, tag):
    record = _convert(cell)
    assert record["verdict"] == "passed"
    assert record["excludedBecause"] == tag
    assert not lb.included(record)
    assert lb.summarize([record])["n"] == 0


def test_standalone_provenance_verifies_identity_without_pricing():
    record = _convert(
        {"execution_provenance": {"harness": "codex", "model": "acme/coder-large"}}
    )
    assert lb.included(record)
    assert record["cost"]["source"] == lb.UNKNOWN


@pytest.mark.parametrize(
    "updates",
    [
        {"token_usage_sources": ["claude-code-exec-json"]},
        {"token_usage_sources": ["codex-exec-json", "claude-code-exec-json"]},
        {"token_usage_sources": []},
        {"token_usage_sources": [None]},
        {"observed_models": {"acme/coder-large": 100, "bolt/coder-small": 0}},
        {"token_usage_cache_read": 1001},
        {"worker_class": "unverified-harness"},
    ],
)
def test_conversion_does_not_price_unsupported_or_mixed_usage(updates):
    metrics = _metrics()
    metrics.update(updates)
    record = _convert({"metrics": metrics})
    assert record["cost"]["usd"] is None
    assert record["cost"]["source"] == lb.UNKNOWN
    assert all(value is None for value in record["tokens"].values())


def test_conversion_rejects_mixed_suite_selector():
    with pytest.raises(lb.ResultsError, match="single suite"):
        _convert({}, suite="all")


@pytest.mark.parametrize("value", [[], {}])
@pytest.mark.parametrize(
    "field", ["verdict", "model", "harness", "changelog_id", "changelog_excludes"]
)
def test_unhashable_values_report_validation_errors(results, field, value):
    if field.startswith("changelog"):
        path = results / "meta" / "changelog.json"
        raw = json.loads(path.read_text())
        raw["entries"][0]["id" if field == "changelog_id" else "excludes"] = value
        path.write_text(json.dumps(raw))
    else:

        def mutate(raw):
            record = raw["records"][0]
            if field == "verdict":
                record["verdict"] = value
            elif field == "model":
                record["model"]["provider_id"] = value
            else:
                record["harness"]["config_hash"] = value

        _edit(results, mutate)
    assert _errors(results)


@pytest.mark.parametrize(
    "cell",
    [
        {"level_mismatch": True},
        {"reasoning_level": "low"},
        {"resolved_reasoning_level": "low"},
        {"metrics": {"resolved_reasoning_level": "low"}},
    ],
)
def test_conversion_excludes_conflicting_reasoning(cell):
    cell = {
        "execution_provenance": {"harness": "codex", "model": "acme/coder-large"},
        **cell,
    }
    assert _convert(cell)["excludedBecause"] == "infra:reasoning_mismatch"


@pytest.mark.parametrize(
    "source,observed,excluded,priced",
    [
        ("codex-exec-json", {"acme/coder-large": 100}, None, True),
        ("claude-code-exec-json", {"acme/coder-large": 100}, None, False),
        (
            "codex-exec-json",
            {"acme/coder-large": 100, "bolt/coder-small": 0},
            None,
            False,
        ),
        ("codex-exec-json", {"bolt/coder-small": 100}, "infra:model_mismatch", False),
    ],
)
def test_cli_conversion_round_trips_actual_ledger_metrics(
    results, tmp_path, source, observed, excluded, priced
):
    from gym.metrics import launch_metrics

    metrics = launch_metrics(
        [
            {
                "run_id": "worker-1",
                "started_at": "2026-01-03T00:00:00Z",
                "ended_at": "2026-01-03T00:01:00Z",
                "token_usage_source": source,
                "token_usage_input": 1000,
                "token_usage_cache_read": 400,
                "token_usage_output": 100,
                "token_usage_reasoning": 0,
                "token_usage_running_total": 1100,
                "token_usage_by_model_json": {
                    m: {"output": n} for m, n in observed.items()
                },
                "resolved_reasoning_level": "high",
            }
        ],
        worker_class="codex",
    )
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "suite": "coding",
                "cells": [
                    {
                        "task": "coding-alpha",
                        "task_version": 2,
                        "verdict": "passed",
                        "model_mismatch": False,
                        "metrics": metrics,
                        "launchRequestId": "lrq-converted",
                    }
                ],
            }
        )
    )
    descriptor = tmp_path / "harness.json"
    descriptor.write_text(json.dumps({"harness": "codex", "reasoning_level": "high"}))
    argv = [
        "--results",
        str(results),
        "from-run",
        str(run),
        "--id",
        "converted",
        "--date",
        "2026-01-03",
        "--harness",
        "codex",
        "--harness-version",
        "1",
        "--harness-config",
        str(descriptor),
        "--model",
        "acme/coder-large",
        "--reasoning",
        "high",
        "--paddock-commit",
        "abcdef1",
        "--description",
        "conversion regression",
        "--evidence",
        "synthetic ledger run",
    ]
    # Test the documented standalone script in a fresh, isolated interpreter:
    # pytest's gym bootstrap must not hide checkout-import or path-mutation bugs.
    converted = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy, sys; "
            "script = runpy.run_path(sys.argv[1]); "
            "original_path = sys.path.copy(); "
            "status = script['main'](sys.argv[2:]); "
            "assert sys.path == original_path, 'conversion changed sys.path'; "
            "sys.exit(status)",
            lb.__file__,
            *argv,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert converted.returncode == 0, converted.stdout + converted.stderr
    record = next(
        r for r in lb.load_results(results).records if r["run_id"] == "lrq-converted"
    )
    assert record["excludedBecause"] == excluded
    assert (record["cost"]["usd"] is not None) == priced
    if priced:
        assert record["tokens"] == {"input": 600, "cache": 400, "output": 100}
    # A contradictory descriptor must fail before replacing the stored document.
    target = results / "2026-01-03-converted.json"
    before = target.read_bytes()
    descriptor.write_text(json.dumps({"harness": "codex", "reasoning_level": "low"}))
    assert lb.main(argv) == 1
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "provider_id,observed",
    [
        ("anthropic/claude-sonnet-5-5", "claude-sonnet-5-5-20261001"),
        ("openai/gpt-6.1-sol", "gpt-6.1-sol"),
    ],
)
def test_conversion_accepts_native_provider_model_names(provider_id, observed):
    records = lb.records_from_run(
        {
            "suite": "review",
            "cells": [
                {
                    "task": "review-one",
                    "verdict": "passed",
                    "execution_provenance": {"harness": "codex", "model": observed},
                }
            ],
        },
        bundle_id="test-bundle",
        harness={"name": "codex", "version": "1", "config_hash": "sha256:" + "a" * 64},
        model={"provider_id": provider_id, "reasoning": None},
        paddock_commit="abcdef1",
        table=None,
    )
    assert lb.included(records[0])


def test_conversion_verifies_fleet_worker_class_distinct_from_harness():
    metrics = _metrics()
    metrics["worker_class"] = "oss-codex"
    record = _convert({"metrics": metrics}, worker_class="oss-codex")
    assert lb.included(record)
    assert (
        record["cost"]["source"] == lb.UNKNOWN
    )  # output contract not registered for this class


@pytest.mark.parametrize("worker_class", [None, ""])
def test_conversion_keeps_tokens_without_worker_class(worker_class):
    metrics = _metrics()
    metrics["worker_class"] = worker_class
    record = _convert(
        {"metrics": metrics, "execution_provenance": {"harness": "codex"}}
    )
    assert record["tokens"] == {"input": 600, "cache": 400, "output": 100}


def test_fallback_run_ids_are_namespaced_by_bundle():
    summary = {
        "suite": "coding",
        "cells": [{"task": "coding-alpha", "verdict": "passed"}],
    }
    kwargs = dict(
        harness={"name": "codex"},
        model={"provider_id": "acme/coder-large", "reasoning": None},
        paddock_commit="abcdef1",
        table=None,
    )
    first = lb.records_from_run(summary, bundle_id="first", **kwargs)[0]
    second = lb.records_from_run(summary, bundle_id="second", **kwargs)[0]
    assert first["run_id"] == "first:coding:coding-alpha:0"
    assert second["run_id"] == "second:coding:coding-alpha:0"


@pytest.mark.parametrize("sign_off", [None, "Fixture maintainer sign-off"])
def test_legacy_passed_audit_requires_evidence_with_or_without_sign_off(sign_off):
    raw = _evidenced_fairness()
    audit = raw["audits"][0]
    audit.update(status="passed")
    audit.pop("evidence")
    audit.pop("sign_off")
    if sign_off is not None:
        audit["sign_off"] = sign_off
    assert "evidence must cover every checklist item" in "\n".join(
        lb.validate_fairness(raw)
    )


def test_legacy_pending_audit_remains_readable():
    raw = _evidenced_fairness()
    audit = raw["audits"][0]
    audit.update(status="pending")
    audit.pop("evidence")
    audit.pop("sign_off")
    assert lb.validate_fairness(raw) == []


@pytest.mark.parametrize(
    "source",
    [
        "not a reference",
        "http://vendor.example/docs",
        "https://",
        "https://vendor.example/bad path",
        "https://[invalid",
        "scripts/../leaderboard.py:1",
        "scripts/./leaderboard.py:1",
        "scripts//leaderboard.py:1",
        "scripts/leaderboard.py:0",
        "scripts/leaderboard.py:1junk",
        "unknown.field",
    ],
)
def test_fairness_rejects_malformed_source_references(source):
    raw = _evidenced_fairness()
    raw["audits"][0]["evidence"][0]["sources"] = [source]
    assert "sources must use HTTPS URLs" in "\n".join(lb.validate_fairness(raw))


@pytest.mark.parametrize(
    "source",
    [
        "https://vendor.example/docs?a=1&b=2",
        "scripts/leaderboard.py:1",
        "results/fixture.json:45 records[harness.name=codex].model.provider_id (49 records)",
        "run.tools",
        "records[model.reasoning=<unknown>]",
        "harness_configs[sha256:abc]",
    ],
)
def test_fairness_accepts_supported_source_references(source):
    raw = _evidenced_fairness()
    raw["audits"][0]["evidence"][0]["sources"] = [source]
    assert lb.validate_fairness(raw) == []


# Community setup sheets, public track facts and fork result provenance.


def _community_entry():
    raw = lb.read_toml(lb.MODULE_ROOT / "results/templates/entry.toml")
    raw.update(id="fixture-entry", display_name="Fixture entry", team="agent-paddock")
    raw["driver"]["provider_id"] = "acme/coder-large"
    raw["car"].update(harness="codex", version="1")
    raw["car"]["descriptor"]["harness"] = "codex"
    raw["car"]["config_hash"] = lb.config_hash(raw["car"]["descriptor"])
    return raw


def test_entry_hash_round_trip_and_historical_examples():
    entry = _community_entry()
    assert lb.validate_entry(entry) == []
    assert entry["car"]["config_hash"] == lb.config_hash(entry["car"]["descriptor"])
    old_hash = entry["car"]["config_hash"]
    entry["car"]["descriptor"]["radio"]["skills"].append(
        {"name": "review", "version": "2"}
    )
    assert lb.config_hash(entry["car"]["descriptor"]) != old_hash
    assert "config_hash" in " ".join(lb.validate_entry(entry))
    loaded = lb.load_results()
    for path in (lb.DEFAULT_RESULTS / "entries").glob("*.toml"):
        example = lb.read_toml(path)
        assert not lb.validate_entry(example)
        assert any(
            lb.included(row) and lb.entry_matches(example, row)
            for row in loaded.records
        )
        assert (
            loaded.configs()[example["car"]["config_hash"]]
            == example["car"]["descriptor"]
        )


def test_entry_livery_uses_supplied_root(tmp_path):
    (tmp_path / "design").mkdir()
    (tmp_path / "design/tokens.json").write_text(
        json.dumps({"color": {"car-fixture": "#123456"}})
    )
    entry = _community_entry()
    entry["tires"] = json.loads((lb.MODULE_ROOT / "locks/codex.lock.json").read_text())
    entry["livery"]["colour"] = "car-fixture"
    assert lb.validate_entry(entry, root=tmp_path) == []
    entry["livery"]["colour"] = "car-codex"
    assert lb.validate_entry(entry, root=tmp_path) == [
        "entry.livery: colour must name a car-* livery token"
    ]


@pytest.mark.parametrize("field", ["briefing", "radio"])
def test_new_entry_must_hash_provenance(field):
    entry = _community_entry()
    entry["car"]["descriptor"].pop(field)
    entry["car"]["config_hash"] = lb.config_hash(entry["car"]["descriptor"])
    assert "exact briefing and radio" in " ".join(lb.validate_entry(entry))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: e["livery"].update(number=0),
        lambda e: e["livery"].update(number=100),
        lambda e: e["livery"].update(number=True),
        lambda e: e["livery"].update(colour="red"),
        lambda e: e["tires"].update(lock="../other.lock.json"),
        lambda e: e["driver"].update(engine_map="invented"),
        lambda e: e["briefing"].update(kind="unrecorded"),
    ],
)
def test_invalid_entry_fields(mutation):
    entry = _community_entry()
    mutation(entry)
    assert lb.validate_entry(entry)


def _reference_entry(results):
    entry = _community_entry()
    # JSON inline tables serialize directly as TOML inline tables for this
    # flat fixture descriptor; provenance uses regular nested TOML tables.
    path = results / "entries" / "fixture-entry.toml"
    path.parent.mkdir()
    template = (lb.MODULE_ROOT / "results/templates/entry.toml").read_text()
    template = template.replace('id = ""', 'id = "fixture-entry"', 1)
    template = template.replace(
        'display_name = ""', 'display_name = "Fixture entry"', 1
    )
    template = template.replace('team = ""', 'team = "agent-paddock"', 1)
    template = template.replace(
        'provider_id = ""', 'provider_id = "acme/coder-large"', 1
    )
    template = template.replace('harness = ""', 'harness = "codex"')
    template = template.replace(
        'config_hash = ""', f'config_hash = "{entry["car"]["config_hash"]}"', 1
    )
    path.write_text(template)
    (results.parent / "locks").mkdir()
    shutil.copy(
        lb.MODULE_ROOT / "locks/codex.lock.json",
        results.parent / "locks/codex.lock.json",
    )

    def attach(doc):
        descriptor = entry["car"]["descriptor"]
        for record in doc["records"]:
            if record["model"]["provider_id"] == "acme/coder-large":
                # Make a deliberately minimal document for this reference test.
                record["harness"] = {
                    "name": "codex",
                    "version": "1",
                    "config_hash": lb.config_hash(descriptor),
                }
                record["model"]["reasoning"] = None
                record["entry"] = "fixture-entry"
                doc["records"] = [record]
                doc["harness_configs"] = {lb.config_hash(descriptor): descriptor}
                break
        else:
            raise AssertionError("fixture has no matching model")

    _edit(results, attach)
    return path


@pytest.mark.parametrize("field", ["provider_id", "reasoning", "config_hash"])
def test_entry_reference_identity_mismatch_rejected(results, field):
    _reference_entry(results)
    assert lb.load_results(results)

    def mutate(doc):
        row = doc["records"][0]
        if field == "config_hash":
            descriptor = dict(doc["harness_configs"][row["harness"][field]])
            descriptor["different"] = True
            key = lb.config_hash(descriptor)
            doc["harness_configs"] = {key: descriptor}
            row["harness"][field] = key
        else:
            row["model"][field] = "high" if field == "reasoning" else "acme/other"
            row["cost"] = lb.price_cost(
                row["tokens"],
                row["model"]["provider_id"],
                lb.load_results(FIXTURE).price_tables["2026-01-02"],
            )

    _edit(results, mutate)
    assert "disagrees with record" in _errors(results)


def test_missing_entry_rejected(results):
    _edit(results, lambda doc: doc["records"][0].update(entry="missing"))
    assert "does not resolve" in _errors(results)


def test_checks_are_optional_and_reasons_rejected(results):
    _edit(
        results,
        lambda doc: doc["records"][0].update(
            checks=[{"name": "quality", "ok": False, "required": True}]
        ),
    )
    assert lb.load_results(results).records[0]["checks"][0]["ok"] is False
    _edit(
        results,
        lambda doc: doc["records"][0]["checks"][0].update(
            reason="private grading detail"
        ),
    )
    assert "unknown reason" in _errors(results)


@pytest.mark.parametrize(
    "checks",
    [
        None,
        {},
        [None],
        [{"name": "x", "ok": 1, "required": True}],
        [{"name": "x", "ok": True, "required": False}] * 2,
    ],
)
def test_invalid_checks_are_errors(results, checks):
    _edit(results, lambda doc: doc["records"][0].update(checks=checks))
    assert "check" in _errors(results)


def test_from_run_copies_checks_without_reasons():
    rows = lb.records_from_run(
        {
            "suite": "coding",
            "cells": [
                {
                    "task": "coding-alpha",
                    "verdict": "passed",
                    "subchecks": [
                        {
                            "name": "quality",
                            "ok": True,
                            "required": True,
                            "reason": "private detail",
                        }
                    ],
                }
            ],
        },
        bundle_id="fixture",
        harness={"name": "codex", "version": "1", "config_hash": "sha256:" + "a" * 64},
        model={"provider_id": "acme/coder-large", "reasoning": None},
        paddock_commit="abcdef1",
        table=_table(),
    )
    assert rows[0]["checks"] == [{"name": "quality", "ok": True, "required": True}]
    assert "reason" not in rows[0]["checks"][0]


@pytest.fixture
def community_track(tmp_path):
    task = tmp_path / "track"
    task.mkdir()
    (task / "task.toml").write_text("""id = "fixture-track"
suite = "coding"
task_version = 2
[dispatch]
prompt = "prompt.md"
[[stages]]
run = "stage.sh"
[result_schema]
answer = {type = "enum", enum = ["ANSWER_SENTINEL"], description = "ANSWER_SENTINEL"}
[verifier]
entrypoint = "verifier.py"
description = "ANSWER_SENTINEL"
""")
    (task / "prompt.md").write_text("Do the task.")
    (task / "stage.sh").write_text("""set -eu
# The copied task must not expose either grading-only directory.
test ! -e expected
test ! -e hidden
printf 'public input\\nsecond line\\n' > "$1/input.txt"
""")
    for directory in ("expected", "hidden"):
        (task / directory).mkdir()
        (task / directory / "secret.txt").write_text("ANSWER_SENTINEL")
    (task / "verifier.py").write_text("# ANSWER_SENTINEL\n")
    (task / "track.toml").write_text("""name = "Fixture circuit"
pitch = "Solve the supplied task."
challenge = "coding"
infographic = "coding"
[[checkpoints]]
check = "quality"
label = "Quality"
disclose = "label"
[[checkpoints]]
check = "ANSWER_SENTINEL"
label = "COUNT_LABEL_SENTINEL"
disclose = "count"
[[checkpoints]]
check = "secret"
label = "HIDDEN_LABEL_SENTINEL"
disclose = "hidden"
""")
    return task


def _track_records():
    rows = copy.deepcopy(lb.load_results(FIXTURE).records[:2])
    for i, row in enumerate(rows):
        row.update(
            task="fixture-track",
            suite="coding",
            task_version=2,
            verdict="passed" if i == 0 else "failed",
            excludedBecause=None,
            checks=[
                {"name": "quality", "ok": i == 0, "required": True},
                {"name": "ANSWER_SENTINEL", "ok": False, "required": True},
                {"name": "secret", "ok": False, "required": True},
                {"name": "unlisted", "ok": False, "required": False},
            ],
            tokens={"input": 10, "cache": 30, "output": 2},
        )
    return rows


def test_checkpoint_names_verified_by_exact_task_version(community_track):
    rows = _track_records()
    assert lb.validate_track(community_track, rows) == ([], [])
    rows[0]["checks"] = rows[1]["checks"] = []
    errors, warnings = lb.validate_track(community_track, rows)
    assert errors and "not recorded" in " ".join(errors)
    assert not warnings
    for row in rows:
        row["task_version"] = 1
    assert lb.validate_track(community_track, rows) == ([], ["checkpoints unverified"])
    assert lb.validate_track(community_track, []) == ([], ["checkpoints unverified"])


@pytest.mark.parametrize(
    "about", ['"Public checkpoint explanation."', "42", "false", "[]"]
)
def test_validate_track_optional_checkpoint_about(community_track, about):
    card = community_track / "track.toml"
    card.write_text(
        card.read_text().replace(
            'label = "Quality"', f'label = "Quality"\nabout = {about}'
        )
    )
    errors, warnings = lb.validate_track(community_track, _track_records())
    assert not warnings
    if about.startswith('"'):
        assert not errors
    else:
        assert errors == ["track: checkpoint about must be nonempty public text"]


def test_legacy_checkpoint_absence_cannot_verify_card(community_track):
    rows = _track_records()
    for row in rows:
        row.pop("checks")
    assert lb.validate_track(community_track, rows) == ([], ["checkpoints unverified"])
    checked = _track_records()[0]
    checked["checks"] = [{"name": "different", "ok": True, "required": True}]
    assert lb.validate_track(community_track, rows + [checked])[0]


def test_track_facts_disclosure_leaks_and_live_statistics(community_track):
    loaded = lb.load_results(FIXTURE)
    loaded.records = _track_records()
    # An old version and an external fork of the same task must never pool.
    old = copy.deepcopy(loaded.records[0])
    old["task_version"] = 1
    external = copy.deepcopy(loaded.records[0])
    external["track"] = {}
    loaded.records.extend([old, external])
    facts = lb.track_facts(community_track, loaded)
    text = json.dumps(facts)
    for secret in (
        "ANSWER_SENTINEL",
        "COUNT_LABEL_SENTINEL",
        "HIDDEN_LABEL_SENTINEL",
        "secret.txt",
        "unlisted",
    ):
        assert secret not in text
    assert facts["staged"] == {"files": 1, "lines": 2}
    assert facts["prompt_words"] == 3
    assert facts["result_shape"] == [{"name": "answer", "type": "enum"}]
    assert facts["conditions"]["network"] == "bridge"
    assert facts["conditions"]["read_only_root"] is True
    live = facts["live"]
    assert live["runs"] == 2 and live["passed"] == 1
    assert live["avg_pass_rate"] == 0.5
    assert live["wilson"] == list(lb.wilson(1, 2))
    assert live["checkpoints"] == [
        {"position": 1, "label": "Quality", "recorded": 2, "clear_rate": 0.5}
    ]
    assert live["more_checks"] == 2  # One count-only, one unlisted, no hidden.
    assert live["retirements"] == {"undisclosed": 1, "1": 1}
    assert live["fuel"] == {
        "input_per_pass": 80,
        "cached_percent": 75,
        "output_per_pass": 4,
    }


def test_track_facts_unknown_measurements_and_exclusions(community_track):
    loaded = lb.load_results(FIXTURE)
    loaded.records = _track_records()
    loaded.records[0]["excludedBecause"] = "infra:model_mismatch"
    loaded.records[1]["tokens"]["cache"] = None
    facts = lb.track_facts(community_track, loaded)
    assert facts["live"]["runs"] == 1
    assert facts["live"]["avg_pass_rate"] == 0
    assert all(value is None for value in facts["live"]["fuel"].values())
    loaded.records = []
    facts = lb.track_facts(community_track, loaded)
    assert facts["live"]["avg_pass_rate"] is None
    assert facts["live"]["wilson"] is None


@pytest.mark.parametrize(
    "edit",
    [
        lambda text: text + "\nexpected_tests = 5\n",
        lambda text: text.replace('challenge = "coding"', 'challenge = "docs"'),
        lambda text: text.replace('disclose = "label"', 'disclose = "invalid"'),
        lambda text: text + '\n[briefing]\nshow = "invalid"\n',
    ],
)
def test_invalid_track_authored_data(community_track, edit):
    path = community_track / "track.toml"
    path.write_text(edit(path.read_text()))
    assert lb.validate_track(community_track)[0]


def test_track_infographic_reuse(community_track):
    assert lb.validate_track(community_track, infographic_ids={"coding": "different"})[
        0
    ]


def test_track_cli_preserves_first_infographic_mapping(
    community_track, results, monkeypatch, capsys
):
    monkeypatch.setattr(lb, "MODULE_ROOT", results.parent)
    for name, infographic in (
        ("a-baseline", "coding"),
        ("b-conflict", "different"),
        ("c-baseline", "coding"),
        ("d-conflict", "different"),
    ):
        task = results.parent / "tasks" / name
        shutil.copytree(community_track, task)
        card = task / "track.toml"
        card.write_text(
            card.read_text().replace(
                'infographic = "coding"', f'infographic = "{infographic}"'
            )
        )
    assert lb.main(["--results", str(results), "validate-track"]) == 1
    errors = capsys.readouterr().err.splitlines()
    assert [error for error in errors if "conflicting infographic ids" in error] == [
        "b-conflict: challenge has conflicting infographic ids",
        "d-conflict: challenge has conflicting infographic ids",
    ]
    assert [error for error in errors if "must reuse its infographic id" in error] == [
        "b-conflict: track: existing challenge must reuse its infographic id",
        "d-conflict: track: existing challenge must reuse its infographic id",
    ]


@pytest.mark.parametrize("level", lb.TRUST_LEVELS[1:])
@pytest.mark.parametrize("missing", ["evidence", "sign_off"])
def test_elevated_trust_requires_evidence_and_sign_off(results, level, missing):
    trust = {
        "level": level,
        "evidence": ["https://example.com/evidence"],
        "sign_off": "maintainer",
    }
    trust[missing] = [] if missing == "evidence" else None
    (results / "meta/trust.json").write_text(
        json.dumps({"schema": "paddock-trust/v1", "documents": {FIXTURE_DOC: trust}})
    )
    assert "elevated trust requires" in _errors(results)


def test_trust_defaults_and_valid_evidence(results):
    assert lb.load_results(results).trust == {}
    for level in lb.TRUST_LEVELS:
        trust = {
            "level": level,
            "evidence": ["https://example.com/evidence"],
            "sign_off": "maintainer",
        }
        assert (
            lb.validate_trust(
                {"schema": "paddock-trust/v1", "documents": {FIXTURE_DOC: trust}},
                {FIXTURE_DOC: {}},
            )
            == []
        )
    assert lb.validate_trust(
        {"schema": "paddock-trust/v1", "documents": {"missing.json": trust}}, {}
    )


def _fork_pin():
    return {"repository": "https://example.com/community/paddock", "commit": "a" * 40}


def test_additive_fork_fields_validate(results):
    def mutate(doc):
        doc["source"]["fork"] = _fork_pin()
        doc["records"][0]["track"] = {
            **_fork_pin(),
            "path": "tasks/community/track.toml",
            "task_version": doc["records"][0]["task_version"],
            "sha256": "sha256:" + "b" * 64,
        }

    _edit(results, mutate)
    assert lb.load_results(results)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda pin: pin.update(repository="http://example.com/paddock"),
        lambda pin: pin.update(commit="abcdef1"),
        lambda pin: pin.update(path="../track.toml"),
        lambda pin: pin.update(path="tasks/hidden/track.toml"),
        lambda pin: pin.update(task_version=99),
        lambda pin: pin.update(sha256="bad"),
    ],
)
def test_fork_pin_validation(results, mutation):
    def mutate(doc):
        pin = {
            **_fork_pin(),
            "path": "tasks/community/track.toml",
            "task_version": doc["records"][0]["task_version"],
            "sha256": "sha256:" + "b" * 64,
        }
        mutation(pin)
        doc["records"][0]["track"] = pin

    _edit(results, mutate)
    assert "track" in _errors(results)


def test_templates_not_discovered_as_results():
    assert all(
        path.parent == lb.DEFAULT_RESULTS
        for path in lb.result_paths(lb.DEFAULT_RESULTS)
    )


def test_external_track_exhibition_never_scores():
    row = copy.deepcopy(lb.load_results(FIXTURE).records[0])
    row.update(verdict="passed", excludedBecause=None)
    assert lb.included(row)
    row["track"] = {
        **_fork_pin(),
        "path": "tasks/community/track.toml",
        "task_version": row["task_version"],
        "sha256": "sha256:" + "b" * 64,
    }
    assert not lb.included(row)
    assert lb.boards([row]) == []
    summary = lb.summarize([row])
    assert summary["n"] == 0 and summary["rate"] is None
    assert summary["excluded"] == {"exhibition:external-track": 1}
    assert lb.exclusion_counts([row]) == {"exhibition:external-track": 1}


@pytest.mark.parametrize("value", [[], {}, 1, None])
def test_malformed_entry_hash_is_a_validation_error(value):
    entry = _community_entry()
    entry["car"]["config_hash"] = value
    assert lb.validate_entry(entry)


def test_toml_only_descriptor_values_rejected():
    import datetime

    entry = _community_entry()
    entry["car"]["descriptor"]["date"] = datetime.date(2026, 1, 1)
    assert lb.validate_entry(entry)


def test_legacy_track_facts_remain_readable(community_track):
    loaded = lb.load_results(FIXTURE)
    loaded.records = _track_records()
    for row in loaded.records:
        row.pop("checks")
    facts = lb.track_facts(community_track, loaded)
    assert facts["warnings"] == ["checkpoints unverified"]
    assert facts["live"]["retirement_unrecorded"] == 2
    assert facts["live"]["checkpoints"][0]["clear_rate"] is None


def test_community_cli_commands(community_track):
    commands = [
        ["validate-entry"],
        ["validate-track", str(community_track)],
        ["track-facts", str(community_track)],
    ]
    for command in commands:
        proc = subprocess.run(
            [sys.executable, str(lb.MODULE_ROOT / "scripts/leaderboard.py"), *command],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        if command[0] == "track-facts":
            assert json.loads(proc.stdout)["conditions"]["network"] == "bridge"
        if command[0] == "validate-track":
            assert "checkpoints unverified" in proc.stderr


@pytest.mark.parametrize("entry_id", [None, "INVALID"])
def test_entry_cli_filename_check_requires_valid_entry(tmp_path, capsys, entry_id):
    source = next((lb.DEFAULT_RESULTS / "entries").glob("*.toml")).read_text()
    invalid = tmp_path / "invalid.toml"
    invalid.write_text(
        "\n".join(
            (f'id = "{entry_id}"' if entry_id is not None else "")
            if line.startswith("id = ")
            else line
            for line in source.splitlines()
        )
    )
    mismatch = tmp_path / "mismatch.toml"
    mismatch.write_text(source)
    problems = lb.validate_entry(lb.read_toml(invalid))
    assert problems
    assert lb.main(["validate-entry", str(invalid), str(mismatch)]) == 1
    assert capsys.readouterr().err.splitlines() == [
        *(f"invalid.toml: {problem}" for problem in problems),
        "mismatch.toml: filename must match id",
    ]


@pytest.mark.parametrize(
    "runs,passed,difficulty",
    [
        (100, 100, 1),
        (100, 90, 1),
        (100, 89, 2),
        (100, 75, 2),
        (100, 74, 3),
        (100, 60, 3),
        (100, 59, 4),
        (100, 40, 4),
        (100, 39, 5),
        (100, 0, 5),
        (9, 9, None),
        (10, 9, 1),
        (0, 0, None),
    ],
)
def test_track_difficulty_edges(community_track, runs, passed, difficulty):
    loaded = lb.load_results(FIXTURE)
    row = _track_records()[0]
    loaded.records = [
        dict(row, verdict="passed" if i < passed else "failed") for i in range(runs)
    ]
    live = lb.track_facts(community_track, loaded)["live"]
    assert live["difficulty"] == difficulty
    assert live["difficulty_reason"] == ("not enough runs" if runs < 10 else None)


@pytest.mark.parametrize(
    "bundle,valid",
    [
        ({"sha256": "a" * 64, "key_id": "submitter-key"}, True),
        ({"sha256": "A" * 64, "key_id": "submitter-key"}, True),
        ({"sha256": "sha256:" + "a" * 64, "key_id": "key"}, False),
        ({"sha256": "g" * 64, "key_id": "key"}, False),
        ({"sha256": "a" * 63, "key_id": "key"}, False),
        ({"sha256": "a" * 64, "key_id": " "}, False),
        ({"sha256": "a" * 64, "key_id": 1}, False),
        ({"sha256": "a" * 64}, False),
        (None, False),
    ],
)
def test_answers_bundle_shape_only(results, bundle, valid):
    _edit(results, lambda doc: doc.update(answers_bundle=bundle))
    if valid:
        assert lb.load_results(results)
    else:
        assert "answers_bundle" in _errors(results)


def test_track_difficulty_minimum_uses_only_counted_runs(community_track):
    loaded = lb.load_results(FIXTURE)
    row = _track_records()[0]
    loaded.records = [copy.deepcopy(row) for _ in range(10)]
    loaded.records[0]["excludedBecause"] = "infra:model_mismatch"
    assert lb.track_facts(community_track, loaded)["live"]["difficulty"] is None
    loaded.records[0]["excludedBecause"] = None
    assert lb.track_facts(community_track, loaded)["live"]["difficulty"] == 1


# ---------------------------------------------------------------- PDW-03 helpers


def _bench_document(**source):
    descriptor = {"harness": "alpha-cli", "runner": lb.PADDOCK_BENCH_RUNNER}
    key = lb.config_hash(descriptor)
    return {
        "source": {"kind": "import", **source},
        "harness_configs": {key: descriptor},
        "records": [{"task": "t"}],
    }


def test_paddock_bench_is_read_from_provenance_not_the_filename():
    assert lb.paddock_bench(_bench_document())
    assert not lb.paddock_bench(_bench_document(fork=_fork_pin()))
    other = _bench_document()
    for descriptor in other["harness_configs"].values():
        descriptor["runner"] = "somewhere-else"
    assert not lb.paddock_bench(other)
    pinned = _bench_document()
    pinned["records"].append({"track": {}})
    assert not lb.paddock_bench(pinned)
    assert not lb.paddock_bench({**_bench_document(), "harness_configs": {}})
    # The committed eval-bench export is the paddock's own bench.
    loaded = lb.load_results()
    bench = loaded.documents["2026-10-29-eval-bench.json.gz"]
    assert lb.paddock_bench(bench)
    assert lb.document_trust("anything.json", bench, {}) == (
        "paddock-run",
        "paddock-bench",
    )


def test_document_trust_defaults_and_trust_json_overrides():
    bench, fork = _bench_document(), _bench_document(fork=_fork_pin())
    assert lb.document_trust("b.json", bench, {}) == ("paddock-run", "paddock-bench")
    assert lb.document_trust("f.json", fork, {}) == ("self-reported", "default")
    signed = {
        "level": "reproduced",
        "evidence": ["https://example.com/evidence"],
        "sign_off": "maintainer",
    }
    trust = {
        "schema": "paddock-trust/v1",
        "documents": {
            "b.json": {"level": "self-reported", "evidence": [], "sign_off": None},
            "f.json": signed,
        },
    }
    assert lb.document_trust("b.json", bench, trust) == ("self-reported", "trust.json")
    assert lb.document_trust("f.json", fork, trust) == ("reproduced", "trust.json")
    assert lb.TRUSTED_LEVELS == ("reproduced", "paddock-run")


def _first_record(run_id, attempt, verdict="passed", **changes):
    record = copy.deepcopy(lb.load_results(FIXTURE).records[0])
    record.update(run_id=run_id, attempt=attempt, verdict=verdict, **changes)
    return record


def test_first_results_take_the_earliest_document_then_the_lowest_attempt():
    documents = {
        "2026-01-05-later.json": {
            "records": [_first_record("later-1", 1, "passed")],
        },
        "2026-01-04-earlier.json": {
            "records": [
                _first_record("earlier-3", 3, "passed"),
                _first_record("earlier-null", None, "passed"),
                _first_record(
                    "earlier-infra", 1, "error", excludedBecause="infra:error"
                ),
                _first_record("earlier-2", 2, "failed"),
                _first_record("other-task", 9, "passed", task="coding-beta"),
            ],
        },
    }
    firsts = lb.first_results(documents)
    assert [r["run_id"] for r in firsts] == ["earlier-2", "other-task"]
    # A change of engine map is a different identity.
    documents["2026-01-05-later.json"]["records"][0]["model"]["reasoning"] = "high"
    assert {r["run_id"] for r in lb.first_results(documents)} == {
        "earlier-2",
        "other-task",
        "later-1",
    }


def test_standing_key_is_the_board_order_and_places_share_ties():
    records = lb.load_results(FIXTURE).records
    for board in lb.boards(records):
        assert board["rows"] == sorted(board["rows"], key=lb.standing_key)
    rows = [
        {"key": (str(i),), "passed": k, "n": n, "rate": k / n if n else None}
        for i, (k, n) in enumerate([(2, 2), (4, 4), (3, 4), (6, 8), (1, 4), (0, 0)])
    ]
    for row in rows:
        row["ci"] = lb.wilson(row["passed"], row["n"])
    rows.sort(key=lb.standing_key)
    assert [(r["passed"], r["n"]) for r in rows] == [
        (4, 4),
        (2, 2),
        (6, 8),
        (3, 4),
        (1, 4),
        (0, 0),
    ]
    assert lb.places(rows) == [
        (1, True),
        (1, True),
        (3, True),
        (3, True),
        (5, False),
        (None, False),
    ]
    with pytest.raises(ValueError):
        lb.places(list(reversed(rows[:5])))


def test_cost_per_run_needs_every_counted_run_priced():
    records = copy.deepcopy(lb.load_results(FIXTURE).records[:3])
    assert lb.cost_per_run(records) == {
        "usd": 0.48,
        "source": "published-rate",
        "reason": None,
    }
    records[2]["cost"] = {"usd": None, "source": lb.UNKNOWN, "reason": "no_price"}
    assert lb.cost_per_run(records)["usd"] is None
    assert lb.cost_per_run([])["reason"] == "no_runs"


def test_difficulty_bands_match_track_facts_edges():
    assert lb.difficulty(9, 9) is None
    assert [lb.difficulty(k, 10) for k in (10, 9, 8, 7, 6, 4, 3, 0)] == [
        1,
        1,
        2,
        3,
        3,
        4,
        5,
        5,
    ]


def test_docs_fact_checks_are_never_disclosed_by_name():
    fact = {
        "check": "fact:FACT_SENTINEL",
        "label": "FACT_SENTINEL",
        "disclose": "label",
    }
    assert lb.disclosure(fact) == "count"
    assert lb.disclosure({**fact, "disclose": "hidden"}) == "hidden"
    assert (
        lb.disclosure({"check": "quality", "label": "Q", "disclose": "label"})
        == "label"
    )
    checkpoints = [
        {"check": "quality", "label": "Quality", "disclose": "label"},
        fact,
    ]
    record = {
        "checks": [
            {"name": "quality", "ok": True, "required": True},
            {"name": "fact:FACT_SENTINEL", "ok": False, "required": True},
        ]
    }
    assert lb.retirement(record, checkpoints) == 1
    assert lb.retirement({}, checkpoints) is None
    summary = lb.checkpoint_summary(checkpoints, [record])
    assert "FACT_SENTINEL" not in json.dumps(summary)
    assert summary["more_checks"] == 1
    assert summary["retirements"] == {"undisclosed": 1}


@pytest.mark.parametrize(
    ("card", "show"),
    [
        ({}, "full"),
        ({"briefing": {}}, "full"),
        ({"briefing": {"show": "excerpt"}}, "excerpt"),
        ({"briefing": {"show": "none"}}, "none"),
        ({"briefing": {"show": "everything"}}, "none"),
        ({"briefing": "full"}, "none"),
    ],
)
def test_briefing_show_defaults_to_full_and_refuses_unknown_values(card, show):
    assert lb.briefing_show(card) == show


def test_public_prompt_path_refuses_grading_and_outside_files(community_track):
    assert (
        lb.public_prompt_path(community_track)
        == (community_track / "prompt.md").resolve()
    )
    for prompt in ("expected/secret.txt", "hidden/secret.txt", "../outside.md"):
        with pytest.raises(lb.ResultsError, match="public task file"):
            lb.public_prompt_path(community_track, prompt)


def test_track_conditions_are_the_conditions_track_facts_reports(community_track):
    loaded = lb.load_results(FIXTURE)
    assert lb.track_facts(community_track, loaded)["conditions"] == (
        lb.track_conditions()
    )
    assert lb.track_conditions()["network"] == "bridge"
