"""Runner mode resolution: which :class:`~gym.grader.TrajectorySource` runs a cell.

``TrajectorySource`` stays the only execution seam (PDK-01). A *host* is
anything that can build one: Agent OS provides the fleet source through the
``paddock.hosts`` entry-point group, and the gym itself ships the standalone
Docker source. Resolution order:

1. an explicit runner (``--runner`` on the CLI, or the ``explicit`` argument);
2. ``PADDOCK_RUNNER`` in the environment;
3. ``runner`` in the user config (``$PADDOCK_CONFIG``, else
   ``$XDG_CONFIG_HOME/paddock/config.toml``, else ``~/.config/paddock/config.toml``);
4. the first discovered host plugin whose ``available()`` is true;
5. ``standalone``.

An explicitly named host that is not installed or not available is an error,
never a silent fallback: a fleet run that quietly went standalone would grade a
different harness under the fleet's baseline.

Stdlib and ``gym`` only. Host plugins import their own dependencies lazily.
"""

from __future__ import annotations

import importlib
import os
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from gym.grader import TrajectorySource

ENTRY_POINT_GROUP = "paddock.hosts"
STANDALONE = "standalone"
RUNNER_ENV = "PADDOCK_RUNNER"
CONFIG_ENV = "PADDOCK_CONFIG"

# Agent OS runs the gym from PYTHONPATH, not from an installed distribution,
# so its entry point is not visible to importlib.metadata in-tree. This
# fallback keeps auto-detection working there; the extracted package drops it
# and relies on the entry point alone.
IN_TREE_HOSTS: tuple[str, ...] = ("gym.agent_os_host:plugin",)


class RunnerResolutionError(RuntimeError):
    """The requested runner cannot be resolved or built."""


@runtime_checkable
class HostPlugin(Protocol):
    """What a ``paddock.hosts`` entry point resolves to.

    A plugin may also define ``services()`` returning its
    :class:`gym.host_services.HostServices`; without it the host runs on the
    standalone services (PDK-02).
    """

    name: str

    def available(self) -> bool:
        """True when this host can run cells in the current process."""
        ...

    def trajectory_source(self, **options: Any) -> TrajectorySource:
        """Build the host's source. Unknown options are ignored, None means default."""
        ...


@dataclass(frozen=True)
class Resolution:
    mode: str
    reason: str
    plugin: HostPlugin | None = None


def _load(spec: str) -> HostPlugin:
    module_name, _, attribute = spec.partition(":")
    target: Any = importlib.import_module(module_name)
    for part in attribute.split(".") if attribute else ():
        target = getattr(target, part)
    return target


def discover(
    *,
    entry_points: Callable[[], Iterable[Any]] | None = None,
    in_tree: Iterable[str] = IN_TREE_HOSTS,
) -> dict[str, HostPlugin]:
    """Every host plugin by name, entry points first, then the in-tree fallback.

    A plugin that fails to load is skipped: an installed-but-broken host must
    not stop a standalone run. Naming that host explicitly still fails, because
    it is then missing from the result.
    """
    if entry_points is None:

        def entry_points() -> Iterable[Any]:
            return metadata.entry_points(group=ENTRY_POINT_GROUP)

    plugins: dict[str, HostPlugin] = {}
    candidates: list[Callable[[], HostPlugin]] = [ep.load for ep in entry_points()]
    candidates.extend(lambda spec=spec: _load(spec) for spec in in_tree)
    for load in candidates:
        try:
            plugin = load()
        except Exception:  # noqa: BLE001 - a broken optional host is not fatal
            continue
        name = getattr(plugin, "name", None)
        if isinstance(name, str) and name and name != STANDALONE:
            plugins.setdefault(name, plugin)
    return plugins


def config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV]).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "paddock" / "config.toml"


def load_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The user config as a dict; absent is empty, unreadable is an error."""
    path = config_path(env)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RunnerResolutionError(f"user config unreadable: {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RunnerResolutionError(f"user config is not valid TOML: {path}: {exc}") from exc
    return data


def _available(plugin: HostPlugin) -> bool:
    try:
        return bool(plugin.available())
    except Exception:  # noqa: BLE001 - probing must not crash the runner
        return False


def resolve_mode(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, Any] | None = None,
    plugins: Mapping[str, HostPlugin] | None = None,
) -> Resolution:
    env = os.environ if env is None else env
    config = load_config(env) if config is None else config
    plugins = discover() if plugins is None else plugins

    requested: tuple[str, str] | None = None
    if explicit:
        requested = (explicit, "explicit runner")
    elif env.get(RUNNER_ENV):
        requested = (env[RUNNER_ENV], f"{RUNNER_ENV} environment variable")
    elif config.get("runner"):
        requested = (str(config["runner"]), f"runner in {config_path(env)}")

    if requested is not None:
        mode, reason = requested
        if mode == STANDALONE:
            return Resolution(STANDALONE, reason)
        plugin = plugins.get(mode)
        if plugin is None:
            known = ", ".join(sorted({STANDALONE, *plugins})) or STANDALONE
            raise RunnerResolutionError(
                f"runner {mode!r} ({reason}) is not an installed host; known: {known}"
            )
        if not _available(plugin):
            raise RunnerResolutionError(
                f"runner {mode!r} ({reason}) is installed but not available in this process"
            )
        return Resolution(mode, reason, plugin)

    for name in sorted(plugins):
        if _available(plugins[name]):
            return Resolution(name, f"auto-detected host plugin {name!r}", plugins[name])
    return Resolution(STANDALONE, "no host plugin available")


def resolve_source(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, Any] | None = None,
    plugins: Mapping[str, HostPlugin] | None = None,
    **options: Any,
) -> TrajectorySource:
    """Build the source for the resolved runner, passing ``options`` through."""
    env = os.environ if env is None else env
    config = load_config(env) if config is None else config
    resolution = resolve_mode(explicit, env=env, config=config, plugins=plugins)
    if resolution.plugin is not None:
        return resolution.plugin.trajectory_source(**options)

    from gym.standalone import StandaloneTrajectorySource

    return StandaloneTrajectorySource.from_config(config, env=env, **options)


__all__ = [
    "CONFIG_ENV",
    "ENTRY_POINT_GROUP",
    "HostPlugin",
    "RUNNER_ENV",
    "Resolution",
    "RunnerResolutionError",
    "STANDALONE",
    "config_path",
    "discover",
    "load_config",
    "resolve_mode",
    "resolve_source",
]
