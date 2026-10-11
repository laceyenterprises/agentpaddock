"""PDK-01: runner mode resolution and the Agent OS host plugin."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from gym import agent_os_host, cli, hosts
from gym.standalone import StandaloneTrajectorySource

MODULE_ROOT = Path(__file__).resolve().parents[1]


class _Host:
    def __init__(self, name, *, available=True):
        self.name = name
        self._available = available
        self.built = None

    def available(self):
        if isinstance(self._available, Exception):
            raise self._available
        return self._available

    def trajectory_source(self, **options):
        self.built = options
        return SimpleNamespace(host=self.name, options=options)


STANDALONE_CONFIG = {"standalone": {"harness": "codex", "image": "img"}}


def _resolve(explicit=None, *, env=None, config=None, plugins=None):
    return hosts.resolve_mode(
        explicit, env=env or {}, config=config or {}, plugins=plugins if plugins is not None else {}
    )


def test_explicit_runner_wins_over_env_config_and_detection():
    plugins = {"agent-os": _Host("agent-os"), "other": _Host("other")}
    resolution = _resolve(
        "other",
        env={hosts.RUNNER_ENV: "agent-os"},
        config={"runner": "agent-os"},
        plugins=plugins,
    )
    assert (resolution.mode, resolution.reason) == ("other", "explicit runner")
    assert _resolve("standalone", plugins=plugins).mode == "standalone"


def test_env_then_user_config_precede_auto_detection():
    plugins = {"agent-os": _Host("agent-os")}
    by_env = _resolve(
        env={hosts.RUNNER_ENV: "standalone"}, config={"runner": "agent-os"}, plugins=plugins
    )
    assert (by_env.mode, by_env.reason) == ("standalone", "PADDOCK_RUNNER environment variable")
    by_config = _resolve(config={"runner": "standalone"}, plugins=plugins)
    assert by_config.mode == "standalone" and "runner in" in by_config.reason


def test_auto_detects_the_available_host_else_falls_back_to_standalone():
    host = _Host("agent-os")
    assert _resolve(plugins={"agent-os": host}).plugin is host
    unavailable = {"agent-os": _Host("agent-os", available=False)}
    assert _resolve(plugins=unavailable).mode == "standalone"
    crashing = {"agent-os": _Host("agent-os", available=RuntimeError("probe blew up"))}
    assert _resolve(plugins=crashing).mode == "standalone"
    assert _resolve(plugins={}).reason == "no host plugin available"


@pytest.mark.parametrize(
    ("explicit", "plugins", "message"),
    [
        ("nope", {}, "not an installed host"),
        ("agent-os", {"agent-os": _Host("agent-os", available=False)}, "not available"),
    ],
)
def test_a_named_host_never_silently_falls_back(explicit, plugins, message):
    with pytest.raises(hosts.RunnerResolutionError, match=message):
        _resolve(explicit, plugins=plugins)


def test_resolve_source_builds_the_host_or_the_standalone_source():
    host = _Host("agent-os")
    built = hosts.resolve_source(None, env={}, config={}, plugins={"agent-os": host}, model="m")
    assert (built.host, host.built) == ("agent-os", {"model": "m"})
    standalone = hosts.resolve_source("standalone", env={}, config=STANDALONE_CONFIG, plugins={})
    assert isinstance(standalone, StandaloneTrajectorySource)
    assert (standalone.harness, standalone.image) == ("codex", "img")


def test_user_config_location_and_parse_errors(tmp_path):
    path = tmp_path / "config.toml"
    env = {hosts.CONFIG_ENV: str(path)}
    assert hosts.load_config(env) == {}
    path.write_text('runner = "standalone"\n', encoding="utf-8")
    assert hosts.load_config(env) == {"runner": "standalone"}
    path.write_text("runner = \n", encoding="utf-8")
    with pytest.raises(hosts.RunnerResolutionError, match="not valid TOML"):
        hosts.load_config(env)
    xdg = hosts.config_path({"XDG_CONFIG_HOME": str(tmp_path)})
    assert xdg == tmp_path / "paddock" / "config.toml"


def test_discover_skips_broken_plugins_and_keeps_the_first_name():
    good = _Host("agent-os")

    def broken():
        raise ImportError("missing dependency")

    entry_points = [SimpleNamespace(load=broken), SimpleNamespace(load=lambda: good)]
    found = hosts.discover(entry_points=lambda: entry_points, in_tree=("gym.agent_os_host:plugin",))
    assert found == {"agent-os": good}


def test_agent_os_host_is_found_in_tree_and_not_declared_by_the_distribution():
    # agentpaddock does not ship the Agent OS host, so it must not declare it
    # (PDK-05); Agent OS runs the gym from PYTHONPATH and finds it in-tree.
    pyproject = tomllib.loads((MODULE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "entry-points" not in pyproject["project"]
    assert pyproject["project"]["scripts"] == {"paddock": "gym.cli:console_main"}
    assert hosts.discover(entry_points=lambda: [])["agent-os"] is agent_os_host.plugin
    # The fleet wrapper pins the host, so a broken import fails instead of
    # quietly grading under the standalone runner.
    wrapper = (MODULE_ROOT / "bin" / "hq-gym").read_text(encoding="utf-8")
    assert 'export PADDOCK_RUNNER="${PADDOCK_RUNNER:-agent-os}"' in wrapper


def test_fleet_path_is_unchanged_under_the_agent_os_host(monkeypatch):
    from gym.dispatch import WorkerPoolTrajectorySource

    assert agent_os_host.plugin.available()
    monkeypatch.setenv(hosts.RUNNER_ENV, "agent-os")
    source = cli._default_source(
        model="m", worker_class="codex", priority="low", reasoning_level="high", compression="off"
    )
    assert source == WorkerPoolTrajectorySource(
        model="m", worker_class="codex", priority="low", reasoning_level="high", compression="off"
    )
    assert cli._default_source() == WorkerPoolTrajectorySource()
    with pytest.raises(hosts.RunnerResolutionError, match="--harness"):
        cli._default_source(harness="codex")


def test_cli_reports_an_unresolvable_runner_as_a_usage_error(monkeypatch, capsys):
    monkeypatch.setenv(hosts.CONFIG_ENV, "/nonexistent/paddock.toml")
    monkeypatch.delenv(hosts.RUNNER_ENV, raising=False)
    assert cli.main(["run", "--runner", "missing-host"]) == cli.EXIT_ERROR
    assert "not an installed host" in capsys.readouterr().err
    assert cli.main(["run", "--runner", "standalone"]) == cli.EXIT_ERROR
    assert "needs a harness" in capsys.readouterr().err
