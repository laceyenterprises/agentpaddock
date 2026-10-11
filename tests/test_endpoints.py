"""All provider/LiteLLM operations mocked; no paid deployment is touched."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from gym.endpoint_api import API, EndpointError
from gym.endpoints import Manager, select_shape

SETTINGS = {
    "account": "test-account",
    "fireworks_ref": "op://test/provider/key",  # publication-scan: allow(op-reference): synthetic
    "litellm_ref": "op://test/router/key",  # publication-scan: allow(op-reference): synthetic
    "litellm_url": "http://127.0.0.1:4000",
}
MODEL = "accounts/fireworks/models/test-model"
# A URL carrying credentials, which the manager must refuse to persist.
CREDENTIAL_URL = "http://user:RAW_SECRET@localhost:4000"  # publication-scan: allow(secret): refused input


def shape(name, gpu="NVIDIA_H100_80GB", count=1, validated=True):
    return {
        "name": name,
        "validated": validated,
        "snapshot": {"acceleratorType": gpu, "acceleratorCount": count},
    }


@pytest.fixture
def setup(tmp_path):
    clock = [1000.0]
    api = Mock()
    api.shapes.return_value = [
        shape("expensive", "NVIDIA_B200_180GB"),
        shape("cheap"),
        shape("multi", count=2),
        shape("invalid", validated=False),
    ]
    api.get.return_value = {"state": "READY"}
    api.deployments.return_value = []

    def delete(name):
        api.get.return_value = {"state": "DELETED"}

    api.delete.side_effect = delete
    manager = Manager(
        tmp_path,
        lambda settings: api,
        now=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    return manager, api, clock


def up(manager):
    return manager.up(SETTINGS, MODEL, 20, 7200, route_timeout=60)


def test_cheapest_validated_single_gpu():
    assert select_shape(
        [
            shape("two", count=2),
            shape("bad", validated=False),
            shape("b200", "NVIDIA_B200_180GB"),
            shape("h100"),
        ]
    ) == (8, "h100")
    with pytest.raises(EndpointError):
        select_shape([shape("unknown", "UNKNOWN")])


def test_ready_registers_route_and_durable_lease(setup):
    manager, api, _ = setup
    lease = up(manager)
    assert lease["state"] == "ready"
    assert lease["shape"] == "cheap"
    api.warm.assert_called_once()
    api.route_up.assert_called_once()
    assert manager.load(lease["id"])["route_id"] == lease["route_id"]
    assert manager.status()[0]["accrued_usd"] == 0


@pytest.mark.parametrize(
    "status,kwargs",
    [
        (
            {"state": "CREATING", "status": {"code": "RESOURCE_EXHAUSTED"}},
            {"capacity_timeout": 10},
        ),
        ({"state": "CREATING"}, {"creation_timeout": 10}),
    ],
)
def test_setup_deadline_cleans_up(setup, status, kwargs):
    manager, api, clock = setup
    api.get.return_value = status
    with pytest.raises(EndpointError, match="cleaned up"):
        manager.up(SETTINGS, MODEL, 20, 7200, route_timeout=60, **kwargs)
    api.delete.assert_called_once()
    api.route_up.assert_not_called()
    assert manager.leases()[0]["state"] == "closed"
    assert clock[0] >= 1010


def test_cap_refuses_dispatch_without_teardown(setup):
    manager, api, clock = setup
    lease = up(manager)
    clock[0] += 8900
    with pytest.raises(EndpointError, match="dispatch refused"):
        manager.guard(lease["route"], 60, "cell")
    api.delete.assert_not_called()
    assert manager.load(lease["id"])["state"] == "ready"


def test_admission_reserves_entire_cell_and_teardown(setup):
    manager, api, clock = setup
    lease = up(manager)
    clock[0] += 100
    with pytest.raises(EndpointError):
        manager.guard(lease["route"], 7100, "cell")
    api.delete.assert_not_called()
    assert not manager.load(lease["id"])["cells"]


def test_reaper_expired_orphan_foreign(setup):
    manager, api, clock = setup
    lease = up(manager)
    orphan = "accounts/test-account/deployments/evalbench-manual"
    api.deployments.return_value = [
        {"name": lease["deployment"], "state": "READY"},
        {"name": orphan, "state": "READY"},
        {"name": "accounts/test-account/deployments/production"},
        {"name": "accounts/foreign/deployments/evalbench-manual"},
    ]
    clock[0] += 7300
    manager.reap()
    deleted = {call.args[0] for call in api.delete.call_args_list}
    assert deleted == {lease["deployment"], orphan}
    assert manager.load(lease["id"])["state"] == "closed"


def test_down_idempotent_partial_failure_retry_and_surface(setup):
    manager, api, _ = setup
    lease = up(manager)
    api.route_down.side_effect = RuntimeError("sensitive-provider-response")
    with pytest.raises(EndpointError, match="route_delete"):
        manager.down(lease["id"])
    pending = manager.load(lease["id"])
    assert pending["state"] == "closing"
    assert pending["cleanup_failures"] == ["route_delete"]
    api.route_down.side_effect = None
    manager.reap()
    manager.down(lease["id"])
    api.delete.assert_called_once()
    assert api.route_down.call_count == 2
    assert (
        "sensitive-provider-response" not in (manager.root / "audit.jsonl").read_text()
    )


def test_deletion_pending_does_not_stop_cost_clock(setup):
    manager, api, clock = setup
    lease = up(manager)
    api.delete.side_effect = None
    api.get.return_value = {"state": "DELETING"}
    assert manager.down(lease["id"])["state"] == "closing"
    api.delete.assert_not_called()
    clock[0] += 3600
    assert manager.status()[0]["accrued_usd"] == 8
    api.get.return_value = {"state": "DELETED"}
    assert manager.down(lease["id"])["state"] == "closed"


def test_no_secret_in_disk_logs_or_audit(setup, capsys):
    manager, api, _ = setup
    api.warm.side_effect = RuntimeError("SUPER-SECRET-VALUE")
    with pytest.raises(EndpointError) as failure:
        up(manager)
    assert "SUPER-SECRET-VALUE" not in str(failure.value)
    assert "SUPER-SECRET-VALUE" not in capsys.readouterr().err
    assert all(
        "SUPER-SECRET-VALUE" not in p.read_text()
        for p in manager.root.iterdir()
        if p.is_file()
    )


def test_cell_gpu_cost_no_double_count_and_economics(setup):
    from gym.cost import cell_economics

    manager, _, clock = setup
    lease = up(manager)
    manager.guard(lease["route"], 60, "key1")
    manager.bind_cell(lease["route"], "key1", "lrq1")
    manager.guard(lease["route"], 60, "lrq2")
    clock[0] += 3600
    first = manager.cell_cost(lease["route"], "lrq1")
    second = manager.cell_cost(lease["route"], "lrq2")
    assert first["cost_usd"] + second["cost_usd"] == 8
    assert manager.cell_cost(lease["route"], "lrq1") == first
    assert cell_economics(first)["cost_basis"] == "gpu_hours"


def test_unknown_reserved_route_fails_closed(setup):
    manager, _, _ = setup
    with pytest.raises(EndpointError, match="no lease"):
        manager.guard("evalbench-gym-unknown", 60)
    assert manager.guard("unrelated-model", 60) is None


def test_http_requests_no_api_key_literal(monkeypatch):
    from gym import endpoint_api

    monkeypatch.setattr(endpoint_api, "secret", lambda *args: "SECRET")
    api = API(Path("/mock/hq"), SETTINGS)
    api.request = Mock(return_value={})
    lease = {
        "route": "test",
        "route_id": "route-id",
        "inference_model": MODEL,
        "route_timeout_seconds": 600,
        "id": "evalbench-gym-test",
        "provider_expire_time": "2026-10-05T10:00:00Z",
    }
    api.route_up(lease)
    body = api.request.call_args.args[3]
    assert "api_key" not in body["litellm_params"]
    assert body["litellm_params"]["timeout"] == 600
    api.create(lease, MODEL, "validated-shape")
    body = api.request.call_args.args[3]
    assert body["minReplicaCount"] == 0 and body["maxReplicaCount"] == 1
    assert body["autoscalingPolicy"]["scaleToZeroWindow"] == "1200s"
    assert body["expireTime"] == lease["provider_expire_time"]


def test_ojo_reaper_registration():
    repo = Path(__file__).resolve().parents[1]
    manifest = repo / "platform/os-job-orchestrator/manifest.d/prod.ojo.json"
    if not manifest.is_file():
        pytest.skip("host reaper manifest is not shipped")
    jobs = json.loads(manifest.read_text())["jobs"]
    job = next(j for j in jobs if j["id"] == "gym-endpoint-reaper")
    assert job["enabled"]
    assert job["schedule"]["everyMs"] <= 30000
    assert "gym endpoint reap" in job["command"][-1]
    assert job["alert_sink"]["channel"] == "telegram-alerts"


def test_orphan_reaped_after_lease_file_lost(setup):
    manager, api, _ = setup
    lease = up(manager)
    (manager.root / (lease["id"] + ".json")).unlink()
    api.deployments.return_value = [{"name": lease["deployment"], "state": "READY"}]
    manager.reap()
    api.delete.assert_called_once_with(lease["deployment"])


@pytest.mark.parametrize(
    "override",
    [
        {"fireworks_ref": "RAW_SECRET"},
        {"litellm_ref": "RAW_SECRET"},
        {"litellm_url": CREDENTIAL_URL},
        {"litellm_url": "https://router.test?token=RAW_SECRET"},
    ],
)
def test_invalid_credentials_never_persist(setup, override):
    manager, api, _ = setup
    with pytest.raises(EndpointError):
        manager.up({**SETTINGS, **override}, MODEL, 20, 7200)
    api.create.assert_not_called()
    assert not manager.root.exists()


def test_shared_dispatch_seam_refuses_before_hq_subprocess(setup, monkeypatch):
    from gym import endpoints

    WorkerPoolTrajectorySource = pytest.importorskip(
        "gym.dispatch", reason="host dispatch is not shipped"
    ).WorkerPoolTrajectorySource
    from gym.schema import Task

    manager, api, clock = setup
    lease = up(manager)
    root = manager.root.parents[1]
    (root / "workers").mkdir()
    clock[0] += 8900
    monkeypatch.setattr(endpoints, "Manager", lambda root: manager)
    import subprocess

    run = Mock(side_effect=AssertionError("no cell should dispatch"))
    monkeypatch.setattr(subprocess, "run", run)
    source = WorkerPoolTrajectorySource(
        hq_root=root, hq_bin=Path("/mock/hq"), model=lease["route"]
    )
    task = Task(
        id="cap-probe",
        title="Cap",
        suite="unit",
        persona="worker",
        result_schema=(),
        verifier_entrypoint="verifier.py",
    )
    with pytest.raises(EndpointError, match="dispatch refused"):
        source._dispatch(task)
    run.assert_not_called()
    api.delete.assert_not_called()
    assert list((root / "workers").iterdir()) == []


def test_http_errors_never_echo_secret(monkeypatch):
    from urllib.error import HTTPError
    from gym import endpoint_api

    monkeypatch.setattr(endpoint_api, "secret", lambda *args: "SUPER_SECRET")

    def fail(req, **kwargs):
        assert req.headers["Authorization"] == "Bearer SUPER_SECRET"
        raise HTTPError(req.full_url, 401, "SUPER_SECRET", {}, None)

    monkeypatch.setattr(endpoint_api, "urlopen", fail)
    api = API(Path("/mock/hq"), SETTINGS)
    with pytest.raises(EndpointError) as failure:
        api.get("accounts/test-account/deployments/evalbench-test")
    assert "SUPER_SECRET" not in str(failure.value)


def test_final_idle_teardown_cost_rejoins_results(setup, tmp_path):
    from gym.endpoint_results import rejoin

    manager, _, clock = setup
    lease = up(manager)
    manager.guard(lease["route"], 60, "lrq1")
    clock[0] += 3600
    assert manager.cell_cost(lease["route"], "lrq1")["cost_usd"] == 8
    clock[0] += 900
    manager.down(lease["id"])
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(
            {"cells": [{"launchRequestId": "lrq1", "verdict": "passed", "metrics": {}}]}
        )
    )
    assert rejoin(manager, lease["id"], path)["updated_cells"] == 1
    cell = json.loads(path.read_text())["cells"][0]
    assert cell["cost_usd"] == 10
    assert cell["cost_basis"] == "gpu_hours"
    rejoin(manager, lease["id"], path)
    assert manager.status()[0]["unallocated_usd"] == 0


def test_reaper_explicit_gym_annotation_owns_unprefixed_orphan(setup):
    manager, api, _ = setup
    up(manager)
    labeled = "accounts/test-account/deployments/gym-owned"
    api.deployments.return_value = [
        {"name": labeled, "annotations": {"agent-os-gym": "true"}},
        {
            "name": "accounts/foreign/deployments/gym-owned",
            "annotations": {"agent-os-gym": "true"},
        },
    ]
    manager.reap()
    api.delete.assert_called_once_with(labeled)


@pytest.mark.parametrize("contents", ["{bad-json", "{}", '{"deployment":"foreign"}'])
def test_malformed_lease_does_not_block_valid_cell_cost(setup, contents):
    manager, _, clock = setup
    lease = up(manager)
    manager.guard(lease["route"], 60, "lrq-valid")
    (manager.root / "evalbench-gym-bad.json").write_text(contents)
    clock[0] += 3600
    assert manager.cell_cost(lease["route"], "lrq-valid")["cost_usd"] == 8
    assert [item["id"] for item in manager.leases()] == [lease["id"]]
    audits = [
        json.loads(line)
        for line in (manager.root / "audit.jsonl").read_text().splitlines()
    ]
    assert any(
        row["lease"] == "evalbench-gym-bad"
        and row["action"] == "lease_load"
        and row["outcome"] == "failed"
        for row in audits
    )


@pytest.mark.parametrize("prefix", ["", "litellm-local-openai/", "openai/"])
def test_reserved_route_forms_share_guard_and_binding(setup, prefix):
    manager, _, _ = setup
    lease = up(manager)
    route = prefix + lease["route"]
    manager.guard(route, 60, "intent")
    manager.bind_cell(route, "intent", "lrq")
    assert "lrq" in manager.load(lease["id"])["cells"]
    with pytest.raises(EndpointError, match="no lease"):
        manager.guard(prefix + "evalbench-gym-missing", 60)


def test_cell_cost_non_endpoint_never_touches_store(setup, monkeypatch):
    manager, _, _ = setup
    monkeypatch.setattr(manager, "lock", Mock(side_effect=PermissionError("denied")))
    assert manager.cell_cost("codex", "lrq") is None


def test_endpoint_refusal_does_not_revoke_existing_cell(setup):
    manager, api, clock = setup
    lease = up(manager)
    manager.guard(lease["route"], 3600, "accepted")
    clock[0] += 3500
    with pytest.raises(EndpointError):
        manager.guard(lease["route"], 3600, "refused")
    retained = manager.load(lease["id"])
    assert retained["state"] == "ready"
    assert set(retained["cells"]) == {"accepted"}
    api.delete.assert_not_called()
    api.route_down.assert_not_called()


@pytest.mark.parametrize("explicit", [False, True])
def test_endpoint_and_dispatch_share_hq_root(monkeypatch, tmp_path, explicit):
    roots = pytest.importorskip(
        "agent_os_config.roots", reason="host root configuration is not shipped"
    )
    _resolve_hq_root = pytest.importorskip(
        "gym.dispatch", reason="host dispatch is not shipped"
    )._resolve_hq_root
    from gym.endpoints import root_path

    monkeypatch.setattr(
        roots, "hq_root_preferring_legacy_env", lambda: str(tmp_path / "configured")
    )
    root = tmp_path / "explicit" if explicit else None
    assert root_path(root) == _resolve_hq_root(root) / "gym" / "endpoints"


def test_up_refuses_budget_too_small_for_one_cell(setup):
    manager, api, _ = setup
    with pytest.raises(EndpointError, match="one cell"):
        manager.up(SETTINGS, MODEL, 1, 7200, route_timeout=60)
    api.create.assert_not_called()


def test_deletion_pending_is_quiet_bounded_and_not_redeleted(setup):
    manager, api, clock = setup
    lease = up(manager)
    api.delete.side_effect = lambda name: setattr(
        api.get, "return_value", {"state": "DELETING"}
    )
    pending = manager.down(lease["id"])
    assert pending["state"] == "closing"
    assert pending["cleanup_failures"] == ["deployment_delete_pending"]
    assert "deleted_at" not in pending
    manager.down(lease["id"])
    api.delete.assert_called_once()
    clock[0] += 601
    with pytest.raises(EndpointError, match="cleanup incomplete"):
        manager.down(lease["id"])
    api.delete.assert_called_once()


def test_reaper_does_not_redelete_orphan_already_deleting(setup):
    manager, api, _ = setup
    up(manager)
    api.deployments.return_value = [
        {
            "name": "accounts/test-account/deployments/evalbench-pending",
            "state": "DELETING",
        }
    ]
    manager.reap()
    api.delete.assert_not_called()


def test_up_reserves_creation_and_warmup_before_full_cell(setup):
    manager, api, _ = setup
    with pytest.raises(EndpointError, match="one cell"):
        manager.up(SETTINGS, MODEL, 10, 7200)
    api.create.assert_not_called()
