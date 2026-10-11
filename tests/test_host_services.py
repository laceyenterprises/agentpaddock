"""PDK-02: host services, standalone (local files) and Agent OS (unchanged behaviour)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("gym.agent_os_host", reason="host integration is not shipped")
pytest.importorskip("gym.agent_os_services", reason="host integration is not shipped")

from gym import (
    agent_os_host,
    alerting,
    cli,
    endpoints,
    fix_audit,
    harness_toolcalls,
    hidden_tests,
    host_services,
    taint,
)
from gym.agent_os_services import AgentOSServices, HqSecrets, SentinelAlerter, SewPriceTable
from gym.agent_os_services import WorkerClassSurfaceResolver
from gym.grader import GraderError
from gym.host_services import (
    EnvironmentSecrets,
    HostServices,
    LocalRunRecords,
    PricingUnavailable,
    SecretUnavailable,
    StandaloneServices,
)
from gym.locks import ToolSurfaceUnavailable
from gym.schema import TaskSchemaError, parse_task_dict

MODULE_ROOT = Path(__file__).resolve().parents[1]
# Synthetic secret-manager references; no vault has these names.
OP_REF = "op://vault/item/field"  # publication-scan: allow(op-reference): synthetic
SHORT_OP_REF = "op://v/i/f"  # publication-scan: allow(op-reference): synthetic


@pytest.fixture
def standalone(tmp_path):
    """Standalone services over tmp_path, installed for the test."""
    services = StandaloneServices(
        tmp_path / "data",
        env={"PADDOCK_TOKEN": "from-env"},
        config={
            "credentials": {"PADDOCK_TOKEN": "from-config", "ONLY_CONFIG": "config-value"},
            "tool_surfaces": {"codex": ["write", "read", "read"]},
        },
    )
    previous = host_services.install(services)
    yield services
    host_services.install(previous)


# -- standalone ---------------------------------------------------------------


def test_default_data_dir_precedence(tmp_path):
    assert host_services.default_data_dir({"PADDOCK_DATA_DIR": str(tmp_path / "d")}) == (
        tmp_path / "d"
    )
    assert host_services.default_data_dir({"XDG_DATA_HOME": str(tmp_path / "x")}) == (
        tmp_path / "x" / "paddock"
    )
    assert host_services.default_data_dir({}) == Path.home() / ".local" / "share" / "paddock"


def test_standalone_paths_live_under_the_data_dir(standalone, tmp_path):
    data = tmp_path / "data"
    assert standalone.data_dir() == standalone.data_dir(launched_tree=True) == data
    assert cli.default_baseline_path() == data / "gym-baseline.json"
    assert endpoints.root_path() == data / "endpoints"
    assert hidden_tests.shared_work_root() == data / "tmp"
    assert (data / "tmp").is_dir()
    assert harness_toolcalls.default_acpx_sessions_dir() == Path.home() / ".acpx" / "sessions"


def test_run_records_round_trip(tmp_path):
    records = LocalRunRecords(tmp_path / "results")
    path = records.write({"b": 1, "a": [2]}, bucket="cohort")
    assert path.parent == tmp_path / "results" / "cohort"
    assert path.read_text() == json.dumps({"a": [2], "b": 1}, indent=2, sort_keys=True) + "\n"
    assert not list(path.parent.glob("*.tmp"))
    assert records.find(path.stem) == [path]
    assert records.write({"c": 3}, bucket="cohort", target=path) == path
    assert json.loads(path.read_text()) == {"c": 3}


def test_cli_stores_and_finds_results_through_the_sink(standalone, tmp_path):
    sink = cli.default_run_records()
    assert isinstance(sink, LocalRunRecords) and sink.root == tmp_path / "data" / "results"


def test_environment_secrets_env_first_and_never_echo_values():
    secrets = EnvironmentSecrets(
        env={"TOKEN": "env-value"}, credentials={"TOKEN": "cfg", "OTHER": "cfg-value"}
    )
    assert secrets.resolve("env:TOKEN", purpose="test") == "env-value"
    assert secrets.resolve("env:OTHER", purpose="test") == "cfg-value"
    for ref in (OP_REF, "TOKEN", "env:", "env:MISSING"):
        with pytest.raises(SecretUnavailable) as error:
            secrets.resolve(ref, purpose="test")
        assert "value" not in str(error.value)
    assert "cfg-value" not in repr(secrets) and "env-value" not in repr(secrets)


def test_standalone_secrets_feed_the_endpoint_api(standalone):
    from gym.endpoint_api import EndpointError, secret

    assert secret(standalone.secrets(), "env:PADDOCK_TOKEN") == "from-env"
    assert secret(standalone.secrets(), "env:ONLY_CONFIG") == "config-value"
    with pytest.raises(EndpointError, match="env:NAME"):
        secret(standalone.secrets(), OP_REF)


def test_standalone_pricing_reads_a_local_table(tmp_path):
    sew = pytest.importorskip("sew.cost_model")
    services = StandaloneServices(tmp_path / "data", env={}, config={})
    with pytest.raises(PricingUnavailable, match="no price table"):
        services.pricing().price_table()
    shipped = sew.default_price_table_path()
    target = tmp_path / "data" / "price-table.yaml"
    target.parent.mkdir(parents=True)
    shutil.copy(shipped, target)
    table = services.pricing().price_table()
    assert table.source == str(target)
    entries = [*table.table.models.values(), *table.table.vendors.values()]
    dates = [entry["as_of"] for entry in entries]
    assert table.as_of == max(dates)
    elsewhere = tmp_path / "custom.yaml"
    shutil.copy(shipped, elsewhere)
    env_services = StandaloneServices(
        tmp_path / "data", env={"PADDOCK_PRICE_TABLE": str(elsewhere)}, config={}
    )
    assert env_services.pricing().price_table().source == str(elsewhere)


def test_standalone_pricing_rejects_a_bad_table(tmp_path):
    pytest.importorskip("sew.cost_model")
    target = tmp_path / "bad.yaml"
    target.write_text("version: 2\n")
    services = StandaloneServices(tmp_path, env={"PADDOCK_PRICE_TABLE": str(target)}, config={})
    with pytest.raises(PricingUnavailable, match="cannot load"):
        services.pricing().price_table()


def test_standalone_lock_provider_reads_the_config(standalone):
    assert cli._default_resolver().resolve("codex") == ["read", "write"]
    with pytest.raises(ToolSurfaceUnavailable, match=r"\[tool_surfaces\]"):
        cli._default_resolver().resolve("claude-code")


def test_standalone_audit_recipient_is_optional_and_local(standalone, tmp_path, monkeypatch):
    monkeypatch.delenv("GYM_FIX_AUDIT_CERT", raising=False)
    expected = tmp_path / "data" / "audit" / "gym-audit-recipient.pem"
    with pytest.raises(GraderError, match=str(expected)):
        fix_audit.recipient()
    expected.parent.mkdir(parents=True)
    expected.write_text("cert")
    assert fix_audit.recipient() == str(expected)


def test_recipient_none_means_the_host_has_none(monkeypatch):
    class NoRecipient(StandaloneServices):
        def audit_recipient(self):
            return None

    monkeypatch.delenv("GYM_FIX_AUDIT_CERT", raising=False)
    previous = host_services.install(NoRecipient(env={}, config={}))
    try:
        with pytest.raises(GraderError, match="GYM_FIX_AUDIT_CERT"):
            fix_audit.recipient()
    finally:
        host_services.install(previous)


def test_standalone_host_facts(standalone):
    assert taint.load_live_taint_mode() == taint.UNAVAILABLE
    assert cli._reasoning_level(" High ") == "high"
    with pytest.raises(Exception):
        cli._reasoning_level("  ")
    assert standalone.large_output_commands() is None


def test_standalone_schema_checks_toc_shape_only(standalone):
    import copy

    from test_schema import VALID  # the minimal valid task.toml dict

    data = copy.deepcopy(VALID)
    data["required_large_output_commands"] = ["any-label", "git"]
    assert parse_task_dict(data).required_large_output_commands == ("any-label", "git")
    for bad in ([], ["git", "git"], [""], [1]):
        data["required_large_output_commands"] = bad
        with pytest.raises(TaskSchemaError):
            parse_task_dict(data)


def test_standalone_alerts_go_to_a_local_file(standalone, tmp_path):
    alerter = standalone.alerter()
    alerter.emit(key="gym_regression", message="REGRESSION: t", metadata={"task": "t"})
    lines = (tmp_path / "data" / "alerts.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "key": "gym_regression",
            "message": "REGRESSION: t",
            "metadata": {"task": "t"},
            "source": alerting.ALERT_SOURCE,
        }
    ]


# -- resolution ---------------------------------------------------------------


class _Plugin:
    name = "fake"

    def __init__(self, services=None):
        self._services = services

    def available(self):
        return True

    def trajectory_source(self, **options):
        return SimpleNamespace()


def test_resolution_uses_the_plugins_services_else_standalone(tmp_path):
    marker = StandaloneServices(tmp_path / "marker", env={}, config={})
    with_services = _Plugin()
    with_services.services = lambda: marker
    assert host_services.resolve(env={}, config={}, plugins={"fake": with_services}) is marker

    plain = host_services.resolve(env={}, config={}, plugins={"fake": _Plugin()})
    assert isinstance(plain, StandaloneServices)
    explicit = host_services.resolve(
        "standalone", env={}, config={}, plugins={"fake": with_services}
    )
    assert isinstance(explicit, StandaloneServices) and explicit is not marker


def test_resolution_refuses_a_plugin_that_returns_no_services():
    from gym.hosts import RunnerResolutionError

    bad = _Plugin()
    bad.services = lambda: object()
    with pytest.raises(RunnerResolutionError, match="not HostServices"):
        host_services.resolve(env={}, config={}, plugins={"fake": bad})


def test_named_host_that_is_missing_fails_loudly():
    from gym.hosts import RunnerResolutionError

    with pytest.raises(RunnerResolutionError):
        host_services.resolve(env={"PADDOCK_RUNNER": "agent-os"}, config={}, plugins={})


def test_install_pins_current_and_unpins():
    pinned = StandaloneServices(env={}, config={})
    previous = host_services.install(pinned)
    try:
        assert host_services.current() is pinned
    finally:
        assert host_services.install(previous) is pinned
    assert host_services.current() is not pinned


def test_agent_os_host_supplies_its_services():
    assert isinstance(agent_os_host.plugin.services(), AgentOSServices)
    assert isinstance(agent_os_host.plugin.services(), HostServices)


# -- Agent OS: what the gym read before PDK-02 ---------------------------------


def test_agent_os_paths_are_the_hq_root_paths(tmp_path, monkeypatch):
    from agent_os_config import roots

    monkeypatch.setattr(roots, "hq_root", lambda: str(tmp_path / "configured"))
    monkeypatch.setattr(roots, "hq_root_preferring_legacy_env", lambda: str(tmp_path / "env"))
    monkeypatch.setattr(roots, "runtime_home", lambda: str(tmp_path / "runtime"))
    services = AgentOSServices()
    configured = tmp_path / "configured"
    assert services.data_dir() == configured / "gym"
    assert services.data_dir(launched_tree=True) == tmp_path / "env" / "gym"
    assert services.scratch_dir() == configured / "tmp"
    assert services.run_records().root == configured / "gym" / "results"
    assert services.audit_recipient() == configured / "gym/audit/gym-audit-recipient.pem"
    assert services.harness_home() == tmp_path / "runtime"
    assert cli.default_baseline_path() == configured / "gym" / "gym-baseline.json"
    assert endpoints.root_path() == tmp_path / "env" / "gym" / "endpoints"


def test_agent_os_host_facts_come_from_the_worker_pool(monkeypatch):
    from cwp_dispatch import agent_os_config_helper, tool_output_compression

    services = AgentOSServices()
    assert services.large_output_commands() == frozenset(tool_output_compression.COMMANDS)
    assert services.normalize_reasoning_level("max", field="--reasoning-level") == "xhigh"
    with pytest.raises(ValueError):
        services.normalize_reasoning_level("unknown", field="--reasoning-level")
    config = object()
    monkeypatch.setattr(agent_os_config_helper, "get_config", lambda: config)
    assert services.host_config() is config
    assert isinstance(services.tool_surfaces(), WorkerClassSurfaceResolver)
    assert isinstance(services.pricing(), SewPriceTable)
    assert isinstance(services.alerter(), SentinelAlerter)


def test_hq_secrets_keeps_the_secrets_bus_contract(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b"value\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    secrets = HqSecrets(hq=Path("/mock/hq"))
    assert secrets.resolve(OP_REF, purpose="gym dedicated endpoint lifecycle") == "value"
    assert calls == [
        (
            [
                "/mock/hq",
                "secrets",
                "resolve",
                "--purpose",
                "gym dedicated endpoint lifecycle",
                "--fail-fast",
                OP_REF,
            ],
            {"capture_output": True, "timeout": 45},
        )
    ]
    with pytest.raises(SecretUnavailable, match="credential must be a secrets-bus reference"):
        secrets.resolve("env:TOKEN", purpose="p")
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 1, b"", b"")
    )
    with pytest.raises(SecretUnavailable, match="secrets bus resolution failed"):
        secrets.resolve(OP_REF, purpose="p")


def test_endpoint_manager_resolves_through_the_host_secrets(monkeypatch):
    seen = []

    class Recording:
        def resolve(self, ref, *, purpose):
            seen.append((ref, purpose))
            return "TOKEN"

    class Services(AgentOSServices):
        def secrets(self):
            return Recording()

    previous = host_services.install(Services())
    try:
        api = endpoints.Manager(root=Path("/unused"))._api({"fireworks_ref": SHORT_OP_REF})
        from gym.endpoint_api import secret

        assert secret(api.secrets, SHORT_OP_REF) == "TOKEN"
    finally:
        host_services.install(previous)
    assert seen == [(SHORT_OP_REF, "gym dedicated endpoint lifecycle")]


def test_sentinel_alerter_payload_is_unchanged(monkeypatch):
    import sys

    sent = []
    fake = SimpleNamespace(enqueue_alert=lambda **kwargs: sent.append(kwargs))
    monkeypatch.setitem(sys.modules, "sentinel.notifications", fake)
    SentinelAlerter().emit(
        key="gym_regression", message="m", metadata={"task": "t", "idempotency_key": "k"}
    )
    assert sent == [
        {
            "message": "m",
            "metadata": {"task": "t", "idempotency_key": "k", "alert_key": "gym_regression"},
            "idempotency_key": "k",
            "target": "default",
            "source_job_id": "gym-scheduled",
            "source_run_id": "gym-scheduled",
            "node_bin": None,
        }
    ]
