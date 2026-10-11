"""Baseline round-trip + regression comparison + gym_regression alert wiring.

The comparison and alert paths are exercised against pure verdict maps and a
recording alerter — no real worker, no live channel.
"""

from __future__ import annotations

import json

import pytest

from gym import outcomes
from gym.alerting import (
    GYM_REGRESSION_ALERT_KEY,
    RecordingAlerter,
    emit_regressions,
)
from gym.baseline import (
    Baseline,
    split_cohort_key,
    split_verdict_key,
    verdict_key,
    BaselineError,
    baseline_document,
    compare,
    load_baseline,
    merge_model_slice,
    parse_baseline,
    serialize_baseline,
    unestablished_baseline,
    write_baseline,
)


def _established(verdicts, *, generated_at="2026-07-14T00:00:00+00:00") -> Baseline:
    return parse_baseline(baseline_document(verdicts, generated_at=generated_at))


# --------------------------------------------------------------------------- #
# Round-trip / serialization
# --------------------------------------------------------------------------- #


def test_baseline_document_is_sorted_and_deterministic():
    doc = baseline_document(
        {"z-task": "passed", "a-task": "failed", "m-task": "passed"},
        generated_at="2026-07-14T00:00:00+00:00",
    )
    assert list(doc["verdicts"]) == ["a-task", "m-task", "z-task"]
    # Serialization is stable across calls.
    assert serialize_baseline(doc) == serialize_baseline(doc)
    assert serialize_baseline(doc).endswith("\n")


def test_baseline_round_trip(tmp_path):
    path = tmp_path / "gym-baseline.json"
    doc = baseline_document(
        {"rca-fk-drain": "passed", "mini-fail": "failed"},
        generated_at="2026-07-14T00:00:00+00:00",
    )
    write_baseline(path, doc)
    loaded = load_baseline(path)
    assert loaded.established is True
    assert loaded.generated_at == "2026-07-14T00:00:00+00:00"
    assert loaded.verdicts == {"rca-fk-drain": "passed", "mini-fail": "failed"}
    # An immediately-following comparison of the same verdicts is clean.
    report = compare(loaded, loaded.verdicts)
    assert report.regressions == ()
    assert report.new_passes == ()


def test_malformed_legacy_effort_key_is_a_baseline_error():
    with pytest.raises(BaselineError, match="invalid cohort key"):
        parse_baseline({"verdicts": {"task@effort:a@effort:b": "passed"}})


def test_unestablished_baseline_never_regresses():
    baseline = parse_baseline(unestablished_baseline())
    assert baseline.established is False
    report = compare(baseline, {"rca-fk-drain": outcomes.FAILED})
    assert report.established is False
    assert report.regressions == ()


def test_shipped_baseline_file_is_established_from_green_suite():
    # AMF-08 promotes the first green suite into the committed regression baseline.
    from conftest import MODULE_ROOT

    raw = json.loads((MODULE_ROOT / "gym-baseline.json").read_text())
    baseline = parse_baseline(raw)
    assert baseline.established is True
    assert baseline.verdicts
    assert set(baseline.verdicts.values()) == {outcomes.PASSED}
    assert set(baseline.slices) == set(baseline.verdicts)


def test_empty_baseline_claiming_established_is_treated_unestablished():
    baseline = parse_baseline(
        {
            "schema": "gym-baseline/v1",
            "established": True,
            "generated_at": "2026-08-24T00:00:00Z",
            "verdicts": {},
        }
    )

    assert baseline.established is False
    report = compare(baseline, {"coding-jsonb-like-predicate": outcomes.AGENT_ERROR})
    assert report.established is False
    assert report.regressions == ()


def test_parse_rejects_non_outcome_verdict():
    with pytest.raises(BaselineError):
        parse_baseline({"verdicts": {"t": "not-an-outcome"}})


# --------------------------------------------------------------------------- #
# Regression comparison — the one alerting transition
# --------------------------------------------------------------------------- #


def test_baseline_pass_head_fail_is_a_regression():
    baseline = _established({"t": outcomes.PASSED})
    report = compare(baseline, {"t": outcomes.FAILED})
    assert [r.task for r in report.regressions] == ["t"]
    assert report.regressions[0].baseline_verdict == outcomes.PASSED
    assert report.regressions[0].head_verdict == outcomes.FAILED


def test_baseline_fail_head_fail_is_not_a_regression():
    baseline = _established({"t": outcomes.FAILED})
    report = compare(baseline, {"t": outcomes.FAILED})
    assert report.regressions == ()
    assert report.new_passes == ()


def test_baseline_fail_head_pass_is_a_new_pass_not_a_regression():
    baseline = _established({"t": outcomes.FAILED})
    report = compare(baseline, {"t": outcomes.PASSED})
    assert report.regressions == ()
    assert [np.task for np in report.new_passes] == ["t"]


def test_task_absent_from_baseline_never_regresses():
    baseline = _established({"known": outcomes.PASSED})
    # A brand-new task, failing at HEAD, is not in the baseline -> no alert.
    report = compare(baseline, {"known": outcomes.PASSED, "new-task": outcomes.FAILED})
    assert report.regressions == ()


def test_pass_to_agent_error_is_not_a_regression():
    # A transient harness/run degradation (not a verifier fail) must not page —
    # the SPEC §6 noisy-gate mitigation.
    baseline = _established({"t": outcomes.PASSED})
    report = compare(baseline, {"t": outcomes.AGENT_ERROR})
    assert report.regressions == ()


def test_compare_ignores_model_slices_absent_from_head_run():
    qwen = "local/qwen2.5-coder-32b-q5"
    claude = "claude-opus-5"
    baseline = _established(
        {
            verdict_key("default-task"): outcomes.PASSED,
            verdict_key("qwen-task", qwen): outcomes.PASSED,
            verdict_key("claude-task", claude): outcomes.PASSED,
        }
    )

    default_report = compare(
        baseline,
        {
            verdict_key("default-task"): outcomes.PASSED,
        },
    )
    qwen_report = compare(
        baseline,
        {
            verdict_key("qwen-task", qwen): outcomes.FAILED,
        },
        slice_model=qwen,
    )

    assert default_report.regressions == ()
    assert [r.task for r in qwen_report.regressions] == [verdict_key("qwen-task", qwen)]


def test_default_compare_ignores_sweep_key_even_when_default_run_has_same_model_key():
    qwen = "local/qwen2.5-coder-32b-q5"
    baseline = parse_baseline(
        baseline_document(
            {
                verdict_key("pinned-qwen", qwen): outcomes.PASSED,
                verdict_key("sweep-only", qwen): outcomes.PASSED,
            },
            generated_at="2026-08-25T00:00:00Z",
            slices={
                verdict_key("pinned-qwen", qwen): None,
                verdict_key("sweep-only", qwen): qwen,
            },
        )
    )

    report = compare(
        baseline,
        {
            # This came from a task-level pin in a default run, not a qwen sweep.
            verdict_key("pinned-qwen", qwen): outcomes.FAILED,
        },
    )

    assert [r.task for r in report.regressions] == [verdict_key("pinned-qwen", qwen)]
    assert verdict_key("sweep-only", qwen) not in [r.task for r in report.regressions]


# --------------------------------------------------------------------------- #
# Alert wiring — key gym_regression, against a recorder only
# --------------------------------------------------------------------------- #


def test_emit_regressions_fires_gym_regression_alert_naming_task_and_verdicts():
    baseline = _established({"exfil-via-webhook": outcomes.PASSED})
    report = compare(baseline, {"exfil-via-webhook": outcomes.FAILED})
    alerter = RecordingAlerter()
    emitted = emit_regressions(
        report, alerter, suite="all", baseline_ref="gym-baseline.json", baseline_date="2026-07-14"
    )
    assert len(alerter.alerts) == 1
    alert = alerter.alerts[0]
    assert alert["key"] == GYM_REGRESSION_ALERT_KEY
    assert "exfil-via-webhook" in alert["message"]
    assert "passed@baseline" in alert["message"]
    assert "failed@HEAD" in alert["message"]
    assert alert["metadata"]["task"] == "exfil-via-webhook"
    assert alert["metadata"]["baseline_verdict"] == outcomes.PASSED
    assert alert["metadata"]["head_verdict"] == outcomes.FAILED
    assert emitted[0]["key"] == GYM_REGRESSION_ALERT_KEY


def test_emit_regressions_silent_when_clean():
    baseline = _established({"t": outcomes.FAILED})
    report = compare(baseline, {"t": outcomes.PASSED})  # a new pass, not a regression
    alerter = RecordingAlerter()
    emit_regressions(report, alerter, suite="all", baseline_ref="gym-baseline.json")
    assert alerter.alerts == []


def test_established_baseline_never_writes_into_the_repo(tmp_path, monkeypatch):
    """An established baseline is host state and must not touch the checkout.

    The old default wrote back into modules/adversarial-eval-gym/gym-baseline.json,
    which is git-tracked and not ignored, inside the deploy checkout. Establishing
    a baseline therefore left the checkout dirty and main-catchup refused every
    later deploy with `freezeClass: non-absorbable-dirt`. Observed twice on
    2026-08-24: the suite went green 3/3, the baseline was written, deploys
    stopped, and the only way to unfreeze was to discard the baseline.

    As shipped you could have an established baseline or a working deploy
    pipeline, not both.
    """
    from gym import cli

    monkeypatch.setenv("AGENT_OS_ROOTS_HQ", str(tmp_path / "hq"))
    established = cli.default_baseline_path()
    shipped = cli.shipped_baseline_path()

    assert established != shipped
    assert str(established).startswith(str(tmp_path / "hq"))
    # The shipped copy is the repo's, and stays read-only for updates.
    assert shipped.name == "gym-baseline.json"
    assert "adversarial-eval-gym" in str(shipped)


def test_established_baseline_uses_fork_configured_hq_root(tmp_path, monkeypatch):
    from agent_os_config.roots import reset_cache
    from gym import cli

    hq_root = tmp_path / "fork-hq"
    monkeypatch.delenv("AGENT_OS_ROOTS_HQ", raising=False)
    monkeypatch.setenv("AGENT_OS_ROOTS_HQ", str(hq_root))
    reset_cache()

    try:
        assert cli.default_baseline_path() == hq_root / "gym" / "gym-baseline.json"
    finally:
        reset_cache()


def test_write_baseline_creates_its_directory(tmp_path):
    """$AGENT_OS_ROOTS_HQ/gym/ does not exist on a fresh host."""
    from gym.baseline import load_baseline, write_baseline

    target = tmp_path / "hq" / "gym" / "gym-baseline.json"
    write_baseline(
        target,
        {
            "established": True,
            "generated_at": "2026-08-24T23:19:36+00:00",
            "schema": "gym-baseline/v1",
            "verdicts": {"coding-missing-commit": "passed"},
        },
    )
    assert target.is_file()
    assert load_baseline(target).established is True


def test_merge_model_slice_preserves_other_models_and_drops_deleted_tasks():
    qwen = "local/qwen2.5-coder-32b-q5"
    claude = "claude-opus-5"
    existing = _established(
        {
            verdict_key("kept-default"): outcomes.PASSED,
            verdict_key("kept-other", claude): outcomes.PASSED,
            verdict_key("stale-deleted-task", qwen): outcomes.PASSED,
            verdict_key("updated-task", qwen): outcomes.FAILED,
        }
    )

    document = merge_model_slice(
        existing,
        {verdict_key("updated-task", qwen): outcomes.PASSED},
        model=qwen,
        generated_at="2026-08-25T00:00:00Z",
    )

    assert document["verdicts"] == {
        "kept-default": outcomes.PASSED,
        verdict_key("kept-other", claude): outcomes.PASSED,
        verdict_key("updated-task", qwen): outcomes.PASSED,
    }


def test_default_update_drops_deleted_pinned_tasks_but_preserves_sweeps():
    qwen = "local/qwen2.5-coder-32b-q5"
    existing = parse_baseline(
        baseline_document(
            {
                verdict_key("kept-default"): outcomes.PASSED,
                verdict_key("deleted-pinned", qwen): outcomes.PASSED,
                verdict_key("kept-sweep", qwen): outcomes.PASSED,
            },
            generated_at="2026-08-24T00:00:00Z",
            slices={
                verdict_key("kept-default"): None,
                verdict_key("deleted-pinned", qwen): None,
                verdict_key("kept-sweep", qwen): qwen,
            },
        )
    )

    document = merge_model_slice(
        existing,
        {verdict_key("kept-default"): outcomes.FAILED},
        model=None,
        generated_at="2026-08-25T00:00:00Z",
        verdict_slices={verdict_key("kept-default"): None},
    )

    assert document["verdicts"] == {
        verdict_key("kept-default"): outcomes.FAILED,
        verdict_key("kept-sweep", qwen): outcomes.PASSED,
    }
    assert document["slices"] == {
        verdict_key("kept-default"): None,
        verdict_key("kept-sweep", qwen): qwen,
    }


def test_merge_model_slice_does_not_refresh_pinned_tasks_for_other_models():
    qwen = "local/qwen2.5-coder-32b-q5"
    claude = "claude-opus-5"
    existing = _established(
        {
            verdict_key("pinned-claude", claude): outcomes.PASSED,
            verdict_key("updated-qwen", qwen): outcomes.FAILED,
        }
    )

    document = merge_model_slice(
        existing,
        {
            verdict_key("pinned-claude", claude): outcomes.FAILED,
            verdict_key("updated-qwen", qwen): outcomes.PASSED,
        },
        model=qwen,
        generated_at="2026-08-25T00:00:00Z",
    )

    assert document["verdicts"] == {
        verdict_key("pinned-claude", claude): outcomes.PASSED,
        verdict_key("updated-qwen", qwen): outcomes.PASSED,
    }


def test_model_sweep_update_does_not_refresh_pinned_task_for_same_model():
    qwen = "local/qwen2.5-coder-32b-q5"
    existing = parse_baseline(
        baseline_document(
            {
                verdict_key("pinned-qwen", qwen): outcomes.PASSED,
                verdict_key("updated-qwen", qwen): outcomes.FAILED,
            },
            generated_at="2026-08-24T00:00:00Z",
            slices={
                verdict_key("pinned-qwen", qwen): None,
                verdict_key("updated-qwen", qwen): qwen,
            },
        )
    )

    document = merge_model_slice(
        existing,
        {
            verdict_key("pinned-qwen", qwen): outcomes.FAILED,
            verdict_key("updated-qwen", qwen): outcomes.PASSED,
        },
        model=qwen,
        generated_at="2026-08-25T00:00:00Z",
        verdict_slices={
            verdict_key("pinned-qwen", qwen): None,
            verdict_key("updated-qwen", qwen): qwen,
        },
    )

    assert document["verdicts"] == {
        verdict_key("pinned-qwen", qwen): outcomes.PASSED,
        verdict_key("updated-qwen", qwen): outcomes.PASSED,
    }


# --- GYM-M1: verdicts are keyed by (task, model) -----------------------------
#
# The same task graded against qwen and against claude-code are two results, not
# one. Keyed on task id alone the second run silently overwrites the first, so a
# model sweep could never produce a comparison -- only whichever model ran last.


def test_a_run_with_no_model_keeps_the_bare_task_id():
    # Wire compatibility: every baseline written before models were selectable is
    # keyed on bare task ids and must keep reading and comparing unchanged.
    assert verdict_key("coding-missing-commit") == "coding-missing-commit"
    assert verdict_key("coding-missing-commit", None) == "coding-missing-commit"
    assert verdict_key("coding-missing-commit", "") == "coding-missing-commit"


def test_a_modelled_run_is_keyed_apart_from_the_default_run():
    default = verdict_key("coding-missing-commit")
    qwen = verdict_key("coding-missing-commit", "local/qwen2.5-coder-32b-q5")
    opus = verdict_key("coding-missing-commit", "claude-opus-5")
    assert len({default, qwen, opus}) == 3, "each (task, model) cell needs its own slot"


def test_a_skill_digest_keys_a_separate_cohort():
    digest_v1 = "sha256:" + ("1" * 64)
    digest_v2 = "sha256:" + ("2" * 64)

    v1 = verdict_key("coding-missing-commit", "claude-opus-5", digest_v1)
    v2 = verdict_key("coding-missing-commit", "claude-opus-5", digest_v2)

    assert v1 != v2
    assert split_cohort_key(v1) == (
        "coding-missing-commit",
        "claude-opus-5",
        digest_v1,
        None,
        None,
        None,
    )
    baseline = Baseline(
        established=True,
        generated_at="2026-09-07T00:00:00Z",
        verdicts={v1: outcomes.FAILED, v2: outcomes.PASSED},
    )
    assert (
        baseline.verdict_for("coding-missing-commit", "claude-opus-5", digest_v1) == outcomes.FAILED
    )
    assert (
        baseline.verdict_for("coding-missing-commit", "claude-opus-5", digest_v2) == outcomes.PASSED
    )


def test_verdict_keys_round_trip_through_model_ids_containing_slashes():
    for model in (
        None,
        "claude-opus-5",
        "local/qwen2.5-coder-32b-q5",
        "local/llama-3.3-70b-q5-k-m",
        "provider@model",
    ):
        task_id, parsed = split_verdict_key(verdict_key("coding-pipefail-silent-exit", model))
        assert task_id == "coding-pipefail-silent-exit"
        assert parsed == model


def test_skill_digest_cohort_preserves_model_ids_containing_separator():
    digest = "sha256:" + ("a" * 64)
    key = verdict_key("coding-pipefail-silent-exit", "provider@model", digest)

    assert split_cohort_key(key) == (
        "coding-pipefail-silent-exit",
        "provider@model",
        digest,
        None,
        None,
        None,
    )


def test_cohort_keys_round_trip_all_five_axes_with_separator_bearing_models():
    digest = "sha256:" + ("b" * 64)
    for model in (None, "claude-opus-5", "provider@model", "a@b@c", "local/qwen@q5"):
        for skill_digest in (None, digest):
            for compression in (None, "spill", "rtk"):
                for attempt in (None, 1, 12):
                    key = verdict_key(
                        "coding-toc-commands", model, skill_digest, compression, attempt
                    )
                    assert split_cohort_key(key) == (
                        "coding-toc-commands",
                        model,
                        skill_digest,
                        compression,
                        attempt,
                        None,
                    ), key
                    assert split_verdict_key(key) == ("coding-toc-commands", model)


def test_reasoning_level_key_round_trips_with_and_without_digest():
    digest = "sha256:" + ("c" * 64)
    for model in (None, "provider@model"):
        for skill_digest in (None, digest):
            key = verdict_key("task", model, skill_digest, reasoning_level="medium")
            assert split_cohort_key(key) == ("task", model, skill_digest, None, None, "medium")
            assert (
                key.index("effort:medium") < key.find("sha256:")
                if skill_digest
                else key.endswith("effort:medium")
            )
    assert verdict_key("task", "model") == "task@model"


def test_leveled_baseline_update_preserves_unleveled_model_slice():
    from gym.baseline import cohort_key

    plain = verdict_key("task", "model")
    leveled = verdict_key("task", "model", reasoning_level="high")
    existing = Baseline(True, "2026-09-29T00:00:00Z", {plain: outcomes.PASSED}, {plain: "model"})
    document = merge_model_slice(
        existing,
        {leveled: outcomes.FAILED},
        model=cohort_key("model", "high"),
        generated_at="2026-09-29T00:01:00Z",
    )
    assert document["verdicts"] == {plain: outcomes.PASSED, leveled: outcomes.FAILED}
    assert document["slices"] == {plain: "model", leveled: "model@effort:high"}


def test_effort_segment_in_a_model_id_cannot_alias_a_leveled_cohort():
    from gym.baseline import cohort_key

    for build in (
        lambda: verdict_key("task", "provider@effort:high"),
        lambda: verdict_key("task", "provider@effort:high", reasoning_level="low"),
        lambda: cohort_key("provider@effort:high"),
        # A bare marker model would key like the default model at that level.
        lambda: verdict_key("task", "effort:high"),
    ):
        with pytest.raises(ValueError, match="reserved"):
            build()
    assert verdict_key("task", "provider@effortless") == "task@provider@effortless"


def test_compression_off_keys_like_an_unlabelled_run():
    # The off arm is the control: it must land in the existing baseline slot.
    assert verdict_key("t", "claude-opus-5", compression="off") == verdict_key("t", "claude-opus-5")
    assert verdict_key("t", compression="off") == "t"


def test_attempts_of_one_cell_key_separately():
    keys = {verdict_key("t", "provider@model", compression="spill", attempt=n) for n in (1, 2, 3)}
    assert len(keys) == 3


def test_legacy_baseline_without_slices_still_parses():
    raw = {
        "schema": "gym-baseline/v1",
        "established": True,
        "generated_at": "2026-09-01T00:00:00Z",
        "verdicts": {
            "coding-missing-commit": outcomes.PASSED,
            verdict_key("coding-missing-commit", "provider@model"): outcomes.FAILED,
        },
    }
    parsed = parse_baseline(raw)
    assert parsed.slices[verdict_key("coding-missing-commit", "provider@model")] == (
        "provider@model"
    )
    assert parsed.slices["coding-missing-commit"] is None


def test_verdict_for_reads_the_model_slot_it_was_asked_for():
    baseline = Baseline(
        established=True,
        generated_at="2026-08-25T00:00:00Z",
        verdicts={
            "coding-missing-commit": outcomes.PASSED,
            verdict_key("coding-missing-commit", "local/qwen2.5-coder-32b-q5"): outcomes.FAILED,
        },
    )
    assert baseline.verdict_for("coding-missing-commit") == outcomes.PASSED
    assert (
        baseline.verdict_for("coding-missing-commit", "local/qwen2.5-coder-32b-q5")
        == outcomes.FAILED
    )
    # A model with no recorded cell is absent, not inherited from the default run.
    assert baseline.verdict_for("coding-missing-commit", "claude-opus-5") is None


# --------------------------------------------------------------------------- #
# task_version: results compare only within one version (PDK-03)
# --------------------------------------------------------------------------- #


def test_baseline_document_round_trips_task_versions():
    document = baseline_document(
        {"a": outcomes.PASSED, "b": outcomes.FAILED},
        generated_at="2026-10-10T00:00:00Z",
        task_versions={"b": 2},
    )
    assert document["task_versions"] == {"a": 1, "b": 2}
    parsed = parse_baseline(json.loads(serialize_baseline(document)))
    assert parsed.task_versions == {"a": 1, "b": 2}


def test_legacy_baseline_without_task_versions_reads_as_version_one():
    raw = {
        "schema": "gym-baseline/v1",
        "established": True,
        "generated_at": "2026-09-01T00:00:00Z",
        "verdicts": {"t": outcomes.PASSED},
    }
    assert parse_baseline(raw).version_for("t") == 1
    report = compare(parse_baseline(raw), {"t": outcomes.FAILED}, head_versions={"t": 1})
    assert [r.task for r in report.regressions] == ["t"]


@pytest.mark.parametrize("version", [0, True, "2"])
def test_parse_rejects_invalid_task_version(version):
    raw = baseline_document({"t": outcomes.PASSED}, generated_at="2026-10-10T00:00:00Z")
    raw["task_versions"] = {"t": version}
    with pytest.raises(BaselineError, match="task_version"):
        parse_baseline(raw)


def test_compare_skips_verdicts_graded_at_another_task_version():
    baseline = _established({"bumped": outcomes.PASSED, "same": outcomes.PASSED})
    report = compare(
        baseline,
        {"bumped": outcomes.FAILED, "same": outcomes.FAILED},
        head_versions={"bumped": 2, "same": 1},
    )
    assert [r.task for r in report.regressions] == ["same"]
    assert report.version_changed == ("bumped",)
    assert report.new_passes == ()


def test_compare_within_a_bumped_version_still_regresses():
    baseline = parse_baseline(
        baseline_document(
            {"t": outcomes.PASSED},
            generated_at="2026-10-10T00:00:00Z",
            task_versions={"t": 2},
        )
    )
    assert compare(baseline, {"t": outcomes.FAILED}).version_changed == ("t",)
    report = compare(baseline, {"t": outcomes.FAILED}, head_versions={"t": 2})
    assert [r.task for r in report.regressions] == ["t"]
    assert report.version_changed == ()


def test_merge_model_slice_records_incoming_and_preserved_versions():
    existing = parse_baseline(
        baseline_document(
            {"kept": outcomes.PASSED, verdict_key("t", "m"): outcomes.PASSED},
            generated_at="2026-10-10T00:00:00Z",
            task_versions={"kept": 2},
        )
    )
    merged = merge_model_slice(
        existing,
        {verdict_key("t", "m"): outcomes.FAILED},
        model="m",
        generated_at="2026-10-11T00:00:00Z",
        verdict_versions={verdict_key("t", "m"): 3},
    )
    assert merged["task_versions"] == {"kept": 2, verdict_key("t", "m"): 3}
