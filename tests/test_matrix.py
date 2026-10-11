"""MEG-04 acceptance: all dispatches and quota readings are fake."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gym import matrix
from gym.suite import discover_tasks, default_tasks_dir

NOW = datetime(2026, 9, 30, 23, tzinfo=timezone.utc)


def battery():
    return dict(
        id="test",
        seed=42,
        repetitions=1,
        levels=["low", "high"],
        suites=["coding"],
        families={"codex": dict(worker_class="codex", models=["m"], pending=["future"])},
        budget=dict(
            max_in_flight=2,
            max_cells=100,
            max_total_tokens=1000,
            min_weekly_remaining_pct=50,
            quota_reading_max_age_minutes=60,
            window="22:00-07:00",
        ),
    )


def identities():
    return [
        ("codex", "codex", "m", level, task) for level in ["low", "high"] for task in ["a", "b"]
    ]


def quota(state="ok", used=20, observed=None):
    return {
        "providerStatuses": [
            dict(
                provider="openai",
                authPath="oauth",
                state=state,
                providerRateLimits=[
                    dict(
                        windowMinutes=10080,
                        usedPercent=used,
                        observedAt=(observed or NOW).isoformat(),
                    )
                ],
            )
        ]
    }


class Fake:
    def __init__(self):
        self.dispatched, self.polled = [], []
        self.active_mode, self.reading = "normal", quota()
        self.results, self.refusal, self.recovered = {}, None, None

    def requirements(self, worker_class):
        return [("openai", "oauth")]

    def resource_pause(self):
        return getattr(self, "pause", None)

    def mode(self):
        return self.active_mode

    def quota(self):
        return self.reading

    def dispatch(self, cell):
        if self.refusal:
            raise RuntimeError(self.refusal)
        self.dispatched.append(cell["id"])
        return f"launch-{len(self.dispatched)}", "/fake"

    def poll(self, cell):
        self.polled.append(cell["launch_request_id"])
        return self.results.pop(cell["launch_request_id"], None)

    def recover_total(self, cell):
        return self.recovered


def prepare(tmp_path, backend=None, **kwargs):
    return matrix.MatrixRun.prepare(
        tmp_path / "run",
        battery(),
        "sha256:base",
        identities(),
        backend or Fake(),
        clock=lambda: NOW,
        **kwargs,
    )


def result(tokens=10, **kwargs):
    return dict(cell={"metrics": {"token_usage_running_total": tokens}}, **kwargs)


def resume(run, data=None, ids=None, digest="sha256:base", **kwargs):
    return matrix.MatrixRun.prepare(
        run.directory,
        data or battery(),
        digest,
        ids or identities(),
        run.backend,
        resume=True,
        clock=lambda: NOW,
        **kwargs,
    )


def test_allowlist_refuses_before_dispatch_and_seed_order(tmp_path):
    fake = Fake()
    with pytest.raises(ValueError, match="'m'.*'codex'"):
        matrix.validate_allowlists(identities(), {"codex": ["other"]})
    assert not fake.dispatched
    matrix.validate_allowlists(identities(), {"codex": ["m"]})
    a = prepare(tmp_path / "a")
    b = prepare(tmp_path / "b")
    assert a.manifest["order"] == b.manifest["order"]
    assert all(c["model"] != "future" for c in a.cells)
    assert a.manifest["pending"] == {"codex": ["future"]}


def test_duplicate_launch_receipt_halts_without_second_binding_or_charge(tmp_path):
    run = prepare(tmp_path)
    run.backend.dispatch = lambda cell: ("shared-launch", "/fake")
    run.tick()
    assert run.cells[0]["launch_request_id"] == "shared-launch"
    duplicate = run.cells[1]
    assert duplicate["status"] == "dispatching"
    assert duplicate["duplicate_launch_request_id"] == "shared-launch"
    assert "launch_request_id" not in duplicate
    assert not duplicate["attempts"]
    assert duplicate.get("dispatch_count", 0) == 0
    assert "recover launch id" in run.manifest["halt_reason"]
    run.backend.results["shared-launch"] = result(tokens=10)
    run.tick()
    assert run.manifest["tokens_spent"] == 10
    assert sum(c["status"] == "done" for c in run.cells) == 1
    again = resume(run)
    again.tick()
    assert again.cells[1]["status"] == "dispatching"
    assert "recover launch id" in again.manifest["halt_reason"]


def test_resume_refuses_existing_cross_cell_launch_alias_without_rewriting(tmp_path):
    run = prepare(tmp_path)
    for cell in run.cells[:2]:
        cell.update(status="done", launch_request_id="shared")
        cell["attempts"] = [dict(launch_request_id="shared", **result(tokens=20))]
    run.save()
    snapshots = {p: p.read_bytes() for p in run.directory.glob("*.json")}
    with pytest.raises(ValueError, match="bound to multiple cells"):
        resume(run)
    assert {p: p.read_bytes() for p in run.directory.glob("*.json")} == snapshots
    assert not run.backend.dispatched


def test_old_attempt_receipt_cannot_be_assigned_to_another_cell(tmp_path):
    run = prepare(tmp_path)
    run.cells[0]["status"] = "done"
    run.cells[0]["attempts"] = [dict(launch_request_id="old-launch", **result(tokens=20))]
    run.backend.dispatch = lambda cell: ("old-launch", "/fake")
    run.tick()
    assert run.cells[1]["status"] == "dispatching"
    assert "launch_request_id" not in run.cells[1]
    assert run.manifest["tokens_spent"] == 20


def test_retry_may_reuse_own_historical_receipt(tmp_path):
    run = prepare(tmp_path)
    cell = run.cells[0]
    cell["status"] = "deferred"
    cell["attempts"] = [dict(launch_request_id="own-launch", **result(tokens=20))]
    run.backend.dispatch = lambda dispatched: (
        "own-launch" if dispatched is cell else "new-launch",
        "/fake",
    )
    run.tick()
    assert cell["status"] == "in_flight"
    assert cell["launch_request_id"] == "own-launch"
    assert "duplicate_launch_request_id" not in cell


def test_resume_polls_saved_launch_and_skips_done(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    assert len(run.backend.dispatched) == 2
    launch = run.cells[0]["launch_request_id"]
    run.backend.results[launch] = result()
    run.backend.pause = "CPU headroom exhausted"
    run.tick()
    run = resume(run)
    run.tick()
    assert len(run.backend.dispatched) == 2
    assert run.cells[0]["status"] == "done"
    assert launch not in run.backend.polled[-1:]


def test_resume_trim_and_overlay(tmp_path):
    run = prepare(tmp_path)
    trim = battery()
    trim["levels"] = ["low"]
    trimmed_ids = [v for v in identities() if v[3] == "low"]
    run = resume(run, trim, trimmed_ids, "sha256:trim")
    assert sum(c["status"] == "trimmed" for c in run.cells) == 2
    assert run.manifest["base_digest"] == "sha256:base"
    overlay = dict(
        base="sha256:base", pairs=[dict(family="codex", model="m", level="low", repetitions=3)]
    )
    run = resume(run, overlay)
    assert len(run.cells) == 8
    assert run.manifest["trim_digest"] == "sha256:trim"
    assert sum(c["status"] == "trimmed" for c in run.cells) == 2
    assert [c["repetition"] for c in run.cells].count(3) == 2


@pytest.mark.parametrize(
    "field,index,value", [("model", 2, "new"), ("level", 3, "medium"), ("task", 4, "new")]
)
def test_resume_refuses_added_axis(tmp_path, field, index, value):
    run = prepare(tmp_path)
    extra = list(identities()[0])
    extra[index] = value
    with pytest.raises(ValueError, match=field):
        resume(run, ids=identities() + [tuple(extra)])
    assert not run.backend.dispatched


def test_resume_refuses_seed_base_and_lower_repetitions(tmp_path):
    run = prepare(tmp_path)
    changed = battery()
    changed["seed"] = 0
    with pytest.raises(ValueError, match="seed"):
        resume(run, changed)
    with pytest.raises(ValueError, match="base"):
        resume(run, dict(base="sha256:wrong", pairs=[]))
    run = resume(run, repetitions=2)
    with pytest.raises(ValueError, match="decrease"):
        resume(run)


@pytest.mark.parametrize("mode", ["paused"])
def test_dispatch_modes_pause(tmp_path, mode):
    run = prepare(tmp_path)
    run.backend.active_mode = mode
    assert run.tick()
    assert not run.backend.dispatched
    assert mode in run.status()["pauses"]["codex"]


@pytest.mark.parametrize("state", ["exhausted", "unknown", "grounded", "capped"])
def test_not_ok_has_no_override(tmp_path, state):
    run = prepare(tmp_path, ignore_quota_floor=["codex"])
    run.backend.reading = quota(state=state)
    run.tick()
    assert not run.backend.dispatched


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("hq", 30), OSError("EIO")])
def test_live_quota_failure_pauses_and_next_tick_recovers(tmp_path, monkeypatch, failure):
    run = prepare(tmp_path)
    live = matrix.LiveBackend(tmp_path, [])
    run.backend.quota = live.quota

    def fail_quota(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail_quota)
    assert run.tick()
    assert not run.backend.dispatched
    assert run.status()["pauses"]["codex"] == "quota status unavailable"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, matrix.json.dumps(quota())),
    )
    assert run.tick()
    assert len(run.backend.dispatched) == 2


@pytest.mark.parametrize(
    "offset_seconds,usable",
    [(1, True), (3600, True), (3601, False), (-3600, True), (-3601, False)],
)
def test_quota_clock_skew_is_bounded(offset_seconds, usable):
    reason, _ = matrix.quota_gate(
        quota(observed=NOW + timedelta(seconds=offset_seconds)),
        [("openai", "oauth")],
        battery()["budget"],
        NOW,
    )
    assert (reason is None) is usable


@pytest.mark.parametrize("transient,expected_inflight", [(False, 3), (True, 1)])
def test_pair_concurrency_requires_completed_launch(tmp_path, transient, expected_inflight):
    b = battery()
    b["budget"]["max_in_flight"] = 3
    run = matrix.MatrixRun.prepare(
        tmp_path / "run",
        b,
        "base",
        [v for v in identities() if v[3] == "low"],
        Fake(),
        repetitions=2,
        clock=lambda: NOW,
    )
    run.tick()
    assert run.status()["counts"]["in_flight"] == 1
    first = next(c for c in run.cells if c["status"] == "in_flight")
    run.backend.results[first["launch_request_id"]] = result(transient=transient)
    run.tick()
    assert run.status()["counts"]["in_flight"] == expected_inflight


@pytest.mark.parametrize("change", ["missing", "stale", "unclassified", "floor", "grounded"])
def test_quota_gate_and_family_override(tmp_path, change):
    run = prepare(tmp_path)
    row = run.backend.reading["providerStatuses"][0]
    if change == "missing":
        row["providerRateLimits"] = []
    if change == "stale":
        row["providerRateLimits"][0]["observedAt"] = "2026-09-29T00:00:00+00:00"
    if change == "unclassified":
        row["state"] = "unclassified"
    if change == "floor":
        row["providerRateLimits"][0]["usedPercent"] = 51
    if change == "grounded":
        row["afhGrounding"] = {"grounded": True}
    run.tick()
    assert not run.backend.dispatched
    run = resume(run, ignore_quota_floor=["codex"])
    run.tick()
    assert bool(run.backend.dispatched) == (change != "grounded")


def test_override_only_named_family(tmp_path):
    b = battery()
    b["families"]["second"] = dict(worker_class="codex", models=["m"])
    ids = identities() + [("second", *v[1:]) for v in identities()]
    backend = Fake()
    backend.reading["providerStatuses"][0]["providerRateLimits"] = []
    run = matrix.MatrixRun.prepare(
        tmp_path / "run", b, "base", ids, backend, ignore_quota_floor=["codex"], clock=lambda: NOW
    )
    run.tick()
    assert all(c["family"] == "codex" for c in run.cells if c["status"] == "in_flight")
    assert "second" in run.status()["pauses"]


@pytest.mark.parametrize(
    "hour,minute,opened", [(23, 0, True), (6, 59, True), (7, 0, False), (21, 59, False)]
)
def test_wrapping_window(hour, minute, opened):
    assert matrix.window_open("22:00-07:00", NOW.replace(hour=hour, minute=minute)) is opened
    assert matrix.window_open("07:00-22:00", NOW.replace(hour=hour, minute=minute)) is (not opened)


def test_equal_window_rejected():
    with pytest.raises(ValueError, match="equals"):
        matrix.window_open("22:00-22:00", NOW)


def test_outside_window_still_polls_and_grades(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    run.clock = lambda: NOW.replace(hour=12)
    for c in run.cells:
        if c.get("launch_request_id"):
            run.backend.results[c["launch_request_id"]] = result()
    run.tick()
    assert len(run.backend.dispatched) == 2
    assert run.status()["counts"]["done"] == 2


@pytest.mark.parametrize("cap", ["tokens", "cells", "stop"])
def test_caps_drain_inflight(tmp_path, cap):
    run = prepare(tmp_path, max_total_tokens=15, max_cells=2 if cap == "cells" else 100)
    run.tick()
    if cap == "stop":
        (run.directory / "stop").touch()
    launches = [c["launch_request_id"] for c in run.cells if c["status"] == "in_flight"]
    run.backend.results[launches[0]] = result(20)
    assert run.tick()
    assert len(run.backend.dispatched) == 2
    run.backend.results[launches[1]] = result(30)
    assert not run.tick()
    assert run.status()["counts"]["done"] == 2
    assert run.status()["tokens_spent"] == 50
    assert run.status()["token_overshoot"] == 35


def test_zero_budget_refuses(tmp_path):
    with pytest.raises(ValueError, match="0 refuses"):
        prepare(tmp_path, max_total_tokens=0)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "refusal", ["admission_refused", "quota_exhausted model m", "provider 5xx", "rate_limit"]
)
def test_transient_dispatch_refusals_defer(tmp_path, refusal):
    run = prepare(tmp_path)
    run.backend.refusal = refusal
    run.tick()
    assert run.status()["counts"]["deferred"] == 2
    assert run.status()["counts"]["pending"] == 2
    assert not run.status()["counts"]["unsupported"]


@pytest.mark.parametrize("refusal", list(matrix.PERMANENT_REFUSAL_CLASSES))
def test_permanent_refusals_skip_pair(tmp_path, refusal):
    run = prepare(tmp_path)
    run.backend.refusal = refusal
    run.tick()
    assert run.status()["counts"]["unsupported"] == 4


def test_terminal_quota_refusal_defers_and_accounts_spend(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    cell = next(c for c in run.cells if c["status"] == "in_flight")
    run.backend.results[cell["launch_request_id"]] = result(30, transient=True)
    run.backend.active_mode = "throttled"
    run.tick()
    assert cell["status"] == "deferred"
    assert run.status()["tokens_spent"] == 30


def test_missing_telemetry_pauses_then_recovery_resumes(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    launches = [c["launch_request_id"] for c in run.cells if c["status"] == "in_flight"]
    run.backend.results[launches[0]] = result(None)
    run.tick()
    assert run.status()["budget_status"] == "unmeasured"
    assert len(run.backend.dispatched) == 2
    run.backend.results[launches[1]] = result(10)
    assert not run.tick()
    assert run.status()["counts"]["done"] == 2
    assert run.status()["unmeasured_cells"]
    run = resume(run)
    run.backend.recovered = 20
    run.tick()
    assert run.status()["budget_status"] == "measured"
    assert len(run.backend.dispatched) > 2


def test_measured_replacement_needs_reason(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    c = next(c for c in run.cells if c["status"] == "in_flight")
    launch = c["launch_request_id"]
    run.backend.results[launch] = result(None)
    run.tick()
    run.manifest["measured_replacements"] = {launch: {"total": 15}}
    assert run.total()[1]
    run.manifest["measured_replacements"][launch]["reason"] = (
        "captured run total from verified transcript"
    )
    assert not run.total()[1]


def test_pilot_battery_loads():
    tasks = discover_tasks(default_tasks_dir())
    path = Path(matrix.__file__).resolve().parents[3] / "batteries" / "meg-pilot.toml"
    data, digest, ids = matrix.load_battery(path, tasks)
    assert len({v[2] for v in ids}) == 10
    assert "example-read-before-write" not in {v[4] for v in ids}
    assert data["budget"]["max_total_tokens"] == 0
    assert digest.startswith("sha256:")


def test_codex_pilot_battery_is_a_codex_trim_plus_the_incumbent():
    tasks = discover_tasks(default_tasks_dir())
    batteries = Path(matrix.__file__).resolve().parents[3] / "batteries"
    base, _, base_ids = matrix.load_battery(batteries / "meg-pilot.toml", tasks)
    data, digest, ids = matrix.load_battery(batteries / "meg-pilot-codex.toml", tasks)
    assert {v[0] for v in ids} == {"codex"}
    assert data["seed"] == base["seed"]
    assert data["budget"]["max_total_tokens"] == 0
    assert base["budget"] == data["budget"]
    assert base["families"]["codex"]["worker_class"] == data["families"]["codex"]["worker_class"]
    assert {v for v in base_ids if v[0] == "codex"}.issubset(set(ids))
    added = {v[2] for v in ids} - {v[2] for v in base_ids}
    # The report compares each row with the governed codex default, so the
    # battery must carry it even though the 2026-09-29 base predates it.
    assert added == {"gpt-6.1-sol"}
    assert {v[3:] for v in ids} == {v[3:] for v in base_ids if v[0] == "codex"}
    assert digest.startswith("sha256:")


def test_dry_run_does_not_write_and_accepts_zero(tmp_path):
    run = prepare(tmp_path, max_total_tokens=0, dry_run=True)
    assert len(run.cells) == 4
    assert not run.directory.exists()


def test_overlay_cannot_lower_saved_pair_repetitions(tmp_path):
    run = prepare(tmp_path)
    overlay = dict(
        base="sha256:base", pairs=[dict(family="codex", model="m", level="low", repetitions=3)]
    )
    run = resume(run, overlay)
    overlay["pairs"][0]["repetitions"] = 2
    with pytest.raises(ValueError, match="decrease"):
        resume(run, overlay)


def test_base_resume_keeps_trimmed_cells_excluded(tmp_path):
    run = prepare(tmp_path)
    trim = battery()
    trim["levels"] = ["low"]
    run = resume(run, trim, [v for v in identities() if v[3] == "low"], "sha256:trim")
    run = resume(run)
    assert run.status()["counts"]["trimmed"] == 2


def test_live_dispatch_argv_is_low_and_origin_owned(tmp_path, monkeypatch):
    import json
    import subprocess
    from gym.schema import Task

    root = tmp_path / "hq"
    (root / "workers").mkdir(parents=True)
    task = Task(
        id="a",
        suite="coding",
        persona="claude-code",
        result_schema=(),
        verifier_entrypoint="verifier.py",
    )
    backend = matrix.LiveBackend(root, [task])
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"launchRequestId": "launch"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cell = matrix.cell_record(("codex", "codex", "m", "low", "a", 1))
    backend.dispatch(cell)
    argv = calls[0]
    for flag, value in [
        ("--priority", "low"),
        ("--fleet-retry-policy", "origin-owned"),
        ("--worker-class", "codex"),
        ("--model", "m"),
        ("--reasoning-level", "low"),
    ]:
        assert argv[argv.index(flag) + 1] == value


def test_terminal_permanent_refusal_skips_pair_only_on_first_cell(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    first = next(c for c in run.cells if c["status"] == "in_flight")
    run.backend.results[first["launch_request_id"]] = result(10, refusal_class="model_not_entitled")
    run.backend.active_mode = "throttled"
    run.tick()
    assert sum(c["status"] == "unsupported" for c in run.cells if c["level"] == first["level"]) == 2


def test_later_permanent_refusal_is_deferred(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    first = next(c for c in run.cells if c["status"] == "in_flight")
    run.backend.results[first["launch_request_id"]] = result(10)
    run.tick()
    second = next(
        c for c in run.cells if c["status"] == "in_flight" and c["level"] == first["level"]
    )
    run.backend.results[second["launch_request_id"]] = result(
        10, refusal_class="model_not_entitled"
    )
    run.backend.active_mode = "throttled"
    run.tick()
    assert second["status"] == "deferred"
    assert not run.status()["counts"]["unsupported"]


def test_overlay_new_pair_rejected_without_mutation(tmp_path):
    run = prepare(tmp_path)
    before = (run.directory / "manifest.json").read_text()
    with pytest.raises(ValueError, match="pair differs"):
        resume(
            run,
            dict(
                base="sha256:base",
                pairs=[dict(family="codex", model="new", level="low", repetitions=2)],
            ),
        )
    assert (run.directory / "manifest.json").read_text() == before


def test_governed_allowlist_refuses_seed_fallback(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from cwp_dispatch import worker_classes_registry

    monkeypatch.setattr(
        worker_classes_registry,
        "load_registry_with_source",
        lambda root: SimpleNamespace(source="json", entries={}),
    )
    with pytest.raises(ValueError, match="seed is not authoritative"):
        matrix.governed_allowlists(tmp_path)


def test_ignore_window_opens_enqueue(tmp_path):
    run = prepare(tmp_path, ignore_window=True)
    run.clock = lambda: NOW.replace(hour=12)
    run.backend.reading = quota(observed=NOW.replace(hour=12))
    run.tick()
    assert len(run.backend.dispatched) == 2


def test_dispatch_acceptance_unknown_never_redispatches(tmp_path):
    run = prepare(tmp_path)
    run.cells[0]["status"] = "dispatching"
    run.save()
    run = resume(run)
    assert not run.tick()
    assert not run.backend.dispatched
    assert "acceptance unknown" in run.status()["halt_reason"]
    assert run.status()["counts"]["dispatching"] == 1
    assert sum(run.status()["counts"].values()) == len(run.cells)


@pytest.mark.parametrize("schema_timeout", [False, True])
def test_dispatch_uncertain_receipt_saves_original_sandbox_and_halts(tmp_path, schema_timeout):
    from gym.dispatch import DispatchAcceptanceUnknown, DispatchSchemaAcceptanceUnknown

    run = prepare(tmp_path)
    sandbox = tmp_path / "accepted-workspace"
    sandbox.mkdir()
    (sandbox / "bench-result.json").write_text('{"accepted":true}')
    calls = []

    def dispatch(cell):
        calls.append(cell["id"])
        exception = DispatchSchemaAcceptanceUnknown if schema_timeout else DispatchAcceptanceUnknown
        raise exception("acknowledgement lost", sandbox=sandbox, launch_key="gym-original")

    run.backend.dispatch = dispatch
    run.tick()
    assert len(calls) == 1
    cell = next(cell for cell in run.cells if cell["status"] == "dispatching")
    assert cell["sandbox"] == str(sandbox)
    assert cell["dispatch_intent_key"] == "gym-original"
    assert "gym-original" in cell["reason"]
    assert "acceptance unknown" in run.manifest["halt_reason"]
    resumed = resume(run)
    assert not resumed.tick()
    assert not resumed.backend.dispatched
    assert (sandbox / "bench-result.json").exists()


def test_schema_timeout_records_terminal_harness_error(tmp_path):
    from gym.dispatch import DispatchSchemaTimeout
    from gym.matrix_report import aggregate

    run = prepare(tmp_path)
    calls = []

    def dispatch(cell):
        calls.append(cell["id"])
        raise DispatchSchemaTimeout("dispatch schema wait bound exceeded: revision_268")

    run.backend.dispatch = dispatch
    for _ in range(4):
        run.tick()
    assert len(calls) == len(run.cells)
    assert all(c["status"] == "done" and c["outcome"] == "error" for c in run.cells)
    assert all("revision_268" in c["reason"] and not c["attempts"] for c in run.cells)
    assert all("retry_not_before" not in c for c in run.cells)
    assert aggregate(run.cells)["excluded"]["harness_error"] == len(run.cells)
    resumed = resume(run)
    assert not resumed.tick()
    assert not resumed.backend.dispatched


@pytest.mark.parametrize(
    "message",
    [
        "unknown effort xhigh for model m",
        "--effort 'xhigh' is not supported for model m",
        "invalid choice: xhigh for --effort",
    ],
)
def test_cli_effort_rejections_are_permanent(message):
    assert matrix.refusal_class(message) == "unsupported_effort"


def test_terminal_null_metrics_can_recover(tmp_path):
    run = prepare(tmp_path)
    run.tick()
    c = next(c for c in run.cells if c["status"] == "in_flight")
    run.backend.results[c["launch_request_id"]] = {"cell": {"metrics": None}}
    run.tick()
    assert run.status()["budget_status"] == "unmeasured"
    run.backend.recovered = 10
    run.tick()
    assert run.status()["budget_status"] == "measured"


def test_saved_budget_overrides_survive_base_resume(tmp_path):
    run = prepare(tmp_path, max_total_tokens=777, max_cells=3)
    b = battery()
    b["budget"]["max_total_tokens"] = 0
    run = resume(run, b)
    assert run.manifest["budget"]["max_total_tokens"] == 777
    assert run.manifest["budget"]["max_cells"] == 3


def test_admission_refusals_cool_down_and_allow_other_cells(tmp_path):
    from datetime import timedelta

    run = prepare(tmp_path)
    run.backend.refusal = "admission_refused"
    run.tick()
    deferred = {c["id"] for c in run.cells if c["status"] == "deferred"}
    run.backend.refusal = None
    run.tick()
    assert not (set(run.backend.dispatched) & deferred)
    for c in run.cells:
        if c.get("launch_request_id"):
            run.backend.results[c["launch_request_id"]] = result()
    run.clock = lambda: NOW + timedelta(seconds=31)
    run.tick()
    assert set(run.backend.dispatched) & deferred


def test_expansion_keeps_unsupported_pair_skipped(tmp_path):
    run = prepare(tmp_path)
    run.backend.refusal = "model_not_entitled"
    run.tick()
    run = resume(run, repetitions=2)
    assert run.status()["counts"]["unsupported"] == 8


def test_cli_allowlist_failure_never_dispatches(tmp_path, monkeypatch):
    from gym import dispatch

    path = tmp_path / "battery.toml"
    path.write_text("""id = "test"
seed = 42
repetitions = 1
levels = ["low"]
suites = ["coding"]
[families.codex]
worker_class = "codex"
models = ["not-allowed"]
[budget]
max_in_flight = 2
max_cells = 10
max_total_tokens = 100
min_weekly_remaining_pct = 50
quota_reading_max_age_minutes = 60
window = "22:00-07:00"
""")
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda root: tmp_path)
    monkeypatch.setattr(matrix, "governed_allowlists", lambda root: {"codex": ["allowed"]})
    monkeypatch.setattr(
        matrix.LiveBackend, "dispatch", lambda *_: pytest.fail("real dispatch attempted")
    )
    with pytest.raises(SystemExit) as exc:
        matrix.main(["run", "--battery-path", str(path)])
    assert exc.value.code == 2
    assert not (tmp_path / "gym").exists()


def test_drain_timeout_records_terminal_harness_error(tmp_path):
    from gym.dispatch import DispatchDrainTimeout

    run = prepare(tmp_path)

    def dispatch(cell):
        raise DispatchDrainTimeout("dispatch drain epoch=622: wait bound exceeded")

    run.backend.dispatch = dispatch
    for _ in range(4):
        run.tick()
    assert all(c["status"] == "done" and c["outcome"] == "error" for c in run.cells)
    assert all("epoch=622" in c["reason"] and not c["attempts"] for c in run.cells)
    from gym.matrix_report import aggregate

    report = aggregate(run.cells)
    assert report["excluded"]["harness_error"] == len(run.cells)


@pytest.mark.parametrize("mode", ["normal", "background", "throttled"])
def test_night_modes_enqueue_with_headroom(tmp_path, mode):
    run = prepare(tmp_path)
    run.backend.active_mode = mode
    assert run.tick()
    assert run.backend.dispatched


def test_memory_crit_pauses_matrix(tmp_path):
    run = prepare(tmp_path)
    run.backend.pause = "memory pressure CRIT"
    assert run.tick()
    assert not run.backend.dispatched


def test_paused_matrix_samples_headroom_once_per_tick(tmp_path, monkeypatch):
    run = prepare(tmp_path)
    calls = []
    monkeypatch.setattr(
        run.backend, "resource_pause", lambda: calls.append(1) or "CPU headroom exhausted"
    )
    run.tick()
    assert calls == [1]
    assert not run.backend.dispatched


def test_outside_window_does_not_probe_headroom(tmp_path, monkeypatch):
    run = prepare(tmp_path)
    run.clock = lambda: NOW.replace(hour=12)
    monkeypatch.setattr(
        run.backend, "resource_pause", lambda: pytest.fail("closed window must not probe")
    )
    run.tick()
    assert not run.backend.dispatched
