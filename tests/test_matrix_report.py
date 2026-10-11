"""MEG-07 fixtures are local: no dispatch, ledger, or production report writes."""

import json
import fcntl
import tomllib
from copy import deepcopy

import pytest

pytest.importorskip("gym.matrix", reason="host integration is not shipped")
pytest.importorskip("gym.matrix_report", reason="host integration is not shipped")
pytest.importorskip("sew.report", reason="host integration is not shipped")
pytest.importorskip("sew.bakeoff_report", reason="host integration is not shipped")

from gym import matrix, matrix_report as report
from sew.report import _wilson_ci
from sew.bakeoff_report import newcombe_interval


@pytest.fixture
def report_checkout(tmp_path, monkeypatch):
    from agent_os_config import roots

    checkout = tmp_path / "checkout"
    seed_path = checkout / "modules" / "worker-pool" / "worker-classes.json"
    seed_path.parent.mkdir(parents=True)
    seed_path.write_text(
        json.dumps({"codex": {"defaultModel": "m", "defaultReasoningLevel": "low"}})
    )
    monkeypatch.setattr(roots, "deploy_root", lambda: str(checkout))
    return checkout


def cell(model="m", level="low", verdict="passed", cost=2, quota=0.1, repetition=1):
    c = matrix.cell_record(("f", "codex", model, level, "task", repetition))
    c.update(
        status="done",
        attempts=[
            dict(
                cell=dict(
                    verdict=verdict,
                    score=1 if verdict == "passed" else 0.5,
                    cost_usd=cost,
                    quota_share=quota,
                    economics={"quota_auth_path": "oauth"},
                    metrics=dict(
                        metrics_complete=True,
                        token_usage_running_total=100,
                        token_usage_reasoning=20,
                        wall_seconds=10,
                    ),
                )
            )
        ],
    )
    return c


def manifest(cells):
    return dict(
        battery_id="fixture",
        base_digest="sha256:fixture",
        seed=42,
        repetitions=1,
        pending={"f": ["future"]},
        budget={"max_total_tokens": 100},
        tokens_spent=200,
        order=[c["id"] for c in cells],
        base_cells=[matrix.identity(c) for c in cells],
        selected_cells=[matrix.identity(c) for c in cells],
    )


def build(cells):
    return report.build_report(
        manifest(cells),
        cells,
        {"codex": dict(model="m", level="low")},
        {"codex": dict(model="seed", level="high")},
        run_id="fixture",
    )


def test_intervals_cost_failures_retries_and_clamping():
    cells = [cell(), cell(verdict="failed", repetition=2), cell(level="medium")]
    cells[1]["attempts"].insert(0, deepcopy(cells[1]["attempts"][0]))
    result = build(cells)
    low, medium = result["rows"]
    assert low["wilson"] == _wilson_ci(1, 2)
    assert medium["comparison"]["interval"] == newcombe_interval(1, 1, 1, 2)
    assert low["dollars_per_success"] == 6
    assert low["tokens_per_success"] == 300
    assert low["quota_per_success"] == pytest.approx(0.3)
    assert low["wall_median"] == 15
    assert low["wall_p90"] == 19
    assert low["mean_score"] == 0.75
    assert low["seed_disagrees"] and low["incumbent"]["model"] == "m"
    assert low["flags"] == ["level may be clamped by the provider"]


def test_unknown_and_zero_success_are_not_zero():
    r = build([cell(cost=None, quota=None)])["rows"][0]
    assert r["dollars_per_success"] is None and r["quota_per_success"] is None
    assert not r["frontier"]
    text = report.render_markdown(build([cell(cost=None, quota=None)]))
    assert "unknown" in text
    assert build([cell(verdict="failed")])["rows"][0]["tokens_per_success"] is None


def test_null_metrics_are_excluded_without_losing_measured_cells():
    measured, unmeasured = cell(), cell(repetition=2)
    unmeasured["attempts"][0]["cell"]["metrics"] = None
    r = build([measured, unmeasured])["rows"][0]
    assert r["n"] == r["passes"] == r["wall_n"] == 1
    assert r["excluded"]["metrics_incomplete"] == 1
    assert r["wall_median"] == r["wall_p90"] == 10
    assert r["tokens_total"] == 100


def test_exclusions_are_counted_not_failed():
    cells = [cell(repetition=i) for i in range(1, 8)]
    for c, status in zip(cells, ["unsupported", "deferred", "pending", "trimmed", "in_flight"]):
        c["status"] = status
    cells[2]["attempts"] = []
    cells[5]["attempts"][0]["cell"].update(model_mismatch=True, level_mismatch=True)
    cells[6]["attempts"][0]["cell"]["metrics"]["metrics_complete"] = False
    r = build(cells)["rows"][0]
    assert r["n"] == 0
    assert all(
        r["excluded"][k] == 1
        for k in (
            "unsupported",
            "deferred",
            "never_run",
            "trimmed",
            "in_flight",
            "model_mismatch",
            "level_mismatch",
            "metrics_incomplete",
        )
    )


def test_frontier_layers_and_exact_overlay(tmp_path):
    cells = [
        cell(model=m, cost=cost, quota=cost / 100) for m, cost in [("m", 1), ("b", 2), ("c", 3)]
    ]
    result = build(cells)
    assert [r["model"] for r in result["rows"] if r["frontier"]] == ["m"]
    assert [r["model"] for r in result["rows"] if r["frontier_adjacent"]] == ["b"]
    overlay = report.expansion(result)
    assert overlay == dict(
        base="sha256:fixture",
        pairs=[
            dict(family="f", model="b", level="low", repetitions=2),
            dict(family="f", model="m", level="low", repetitions=2),
        ],
    )
    # Removing an unresolved mark removes exactly that pair.
    result["rows"][0]["comparison"]["resolution"] = "resolved"
    assert len(report.expansion(result)["pairs"]) == 1
    run = matrix.MatrixRun.prepare(
        tmp_path / "run",
        dict(
            id="fixture",
            seed=42,
            repetitions=1,
            families={"f": {"pending": []}},
            budget=dict(max_total_tokens=100),
        ),
        "sha256:fixture",
        [matrix.identity(c) for c in cells],
        None,
    )
    resumed = matrix.MatrixRun.prepare(
        run.directory, overlay, "sha256:overlay", [], None, resume=True
    )
    assert len(resumed.cells) == 5


def test_dollar_fallback_is_separate_axis():
    rows = build([cell(), cell(model="b", cost=1, quota=None)])["rows"]
    assert all(r["frontier"] for r in rows)
    assert rows[0]["axis"] == "dollars per success"


def test_written_twins_overlay_and_live_source(tmp_path, monkeypatch, report_checkout):
    cells = [cell(), cell(model="b")]
    run = tmp_path / "run"
    run.mkdir()
    matrix.write_json(run / "manifest.json", manifest(cells))
    for c in cells:
        matrix.write_json(run / f"{c['id']}.json", c)
    monkeypatch.setattr(report, "incumbents", lambda root: {"codex": dict(model="m", level="low")})
    overlay = tmp_path / "expansion.toml"
    path = report.write_report(run, tmp_path / "reports", tmp_path, overlay)
    twin = json.loads(path.with_suffix(".json").read_text())
    assert path.read_text() == report.render_markdown(twin)
    assert twin["base_digest"] == "sha256:fixture"
    assert all(not row["seed_disagrees"] for row in twin["rows"])
    assert tomllib.loads(overlay.read_text()) == report.expansion(twin)
    battery, digest, ids = matrix.load_battery(overlay, [])
    assert battery["base"] == "sha256:fixture"
    assert "n=3 per task supports no per-task claim" in path.read_text()
    assert "future: not allowlisted" in path.read_text()
    from cwp_dispatch import worker_classes_registry
    from types import SimpleNamespace

    monkeypatch.setattr(
        worker_classes_registry,
        "load_registry_with_source",
        lambda root: SimpleNamespace(source="json", entries={}),
    )
    # Restore the real function after the writer seam.
    with pytest.raises(ValueError, match="seed is not authoritative"):
        original_incumbents(tmp_path)


original_incumbents = report.incumbents


def test_quota_upper_bound_and_reset_unknown():
    c = cell()
    a = c["attempts"][0]
    for stage, used, observed in [
        ("quota_dispatch", 10, "2026-09-30T00:00:00Z"),
        ("quota_terminal", 15, "2026-09-30T01:00:00Z"),
    ]:
        a[stage] = [
            dict(
                provider="openai",
                authPath="oauth",
                weekly=[dict(usedPercent=used, observedAt=observed, resetsAt="same")],
            )
        ]
    q = report.quota_crosscheck([c])[0]
    assert q["weekly_used_percent_delta_upper_bound"] == 5
    assert q["summed_cell_quota_share"] == 0.1
    a["quota_terminal"][0]["weekly"][0]["resetsAt"] = "new"
    assert report.quota_crosscheck([c])[0]["weekly_used_percent_delta_upper_bound"] is None


def test_newcombe_is_only_resolution_rule():
    cells = [cell(model="m", verdict="failed", repetition=i) for i in range(1, 41)]
    cells += [cell(model="b", repetition=i) for i in range(1, 41)]
    result = build(cells)
    challenger = next(r for r in result["rows"] if r["model"] == "b")
    assert challenger["comparison"]["interval"] == newcombe_interval(40, 40, 0, 40)
    assert challenger["comparison"]["resolution"] == "resolved"
    assert not any(p["model"] == "b" for p in report.expansion(result)["pairs"])


def test_report_cli_routes_without_dispatch(tmp_path, monkeypatch, capsys, report_checkout):
    from gym import dispatch

    runs = tmp_path / "gym" / "matrix"
    runs.mkdir(parents=True)
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda value: tmp_path)
    calls = []
    monkeypatch.setattr(report, "write_report", lambda *args: calls.append(args) or "report.md")
    assert (
        matrix.main(["report", "fixture", "--emit-expansion", str(tmp_path / "overlay.toml")]) == 0
    )
    assert calls[0][0] == runs / "fixture"
    assert calls[0][1] == tmp_path / "gym" / "reports"
    assert calls[0][2] == tmp_path
    assert calls[0][3] == tmp_path / "overlay.toml"
    assert "report.md" in capsys.readouterr().out


@pytest.mark.parametrize("frontier", ["resolved", "unknown"])
def test_report_cli_empty_expansion_succeeds(
    tmp_path, monkeypatch, capsys, report_checkout, frontier
):
    from gym import dispatch

    if frontier == "resolved":
        cells = [cell(verdict="failed", repetition=i) for i in range(1, 41)]
        cells += [cell(model="b", repetition=i) for i in range(1, 41)]
    else:
        cells = [cell(cost=None, quota=None)]
    run = tmp_path / "gym" / "matrix" / "fixture"
    run.mkdir(parents=True)
    matrix.write_json(run / "manifest.json", manifest(cells))
    for c in cells:
        matrix.write_json(run / f"{c['id']}.json", c)
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda value: tmp_path)
    monkeypatch.setattr(report, "incumbents", lambda root: {"codex": dict(model="m", level="low")})
    checkout_before = {
        p.relative_to(report_checkout): p.read_bytes()
        for p in report_checkout.rglob("*")
        if p.is_file()
    }
    overlay = tmp_path / "expansion.toml"
    if frontier == "resolved":
        overlay.write_text("existing overlay")
    assert matrix.main(["report", "fixture", "--emit-expansion", str(overlay)]) == 0
    paths = list((tmp_path / "gym" / "reports").glob("*.md"))
    assert len(paths) == 1
    assert not (report_checkout / "docs").exists()
    assert {
        p.relative_to(report_checkout): p.read_bytes()
        for p in report_checkout.rglob("*")
        if p.is_file()
    } == checkout_before
    twin = json.loads(paths[0].with_suffix(".json").read_text())
    assert report.expansion(twin)["pairs"] == []
    assert paths[0].read_text() == report.render_markdown(twin)
    if frontier == "resolved":
        assert overlay.read_text() == "existing overlay"
        qualifying = [r for r in twin["rows"] if r["frontier"] or r["frontier_adjacent"]]
        assert qualifying and all(r["comparison"]["resolution"] == "resolved" for r in qualifying)
    else:
        assert not overlay.exists()
    captured = capsys.readouterr()
    assert str(paths[0]) in captured.out
    assert not captured.err


def test_report_cli_active_run_has_clear_error(tmp_path, monkeypatch, capsys):
    from gym import dispatch

    runs = tmp_path / "gym" / "matrix"
    runs.mkdir(parents=True)
    monkeypatch.setattr(dispatch, "_resolve_hq_root", lambda value: tmp_path)
    monkeypatch.setattr(report, "write_report", lambda *args: pytest.fail("report read during run"))
    with (runs / "fixture.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit) as exc:
            matrix.main(["report", "fixture"])
        assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "matrix run fixture is currently active" in captured.err
    assert "Traceback" not in captured.err
    assert not captured.out


def test_unknown_acceptance_is_never_labelled_never_run():
    unknown = cell()
    unknown.update(status="dispatching", attempts=[])
    r = build([unknown])["rows"][0]
    assert r["excluded"]["acceptance_unknown"] == 1
    assert r["excluded"]["never_run"] == 0
    assert r["n"] == 0
