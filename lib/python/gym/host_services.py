"""Host services: what the gym needs from its host, behind small interfaces (PDK-02).

The gym never imports a host's packages. Everything host-specific it used to
reach for directly comes from one :class:`HostServices` object:

* a data directory (baselines, results, endpoint leases) and a scratch
  directory the hidden-test container can mount;
* a run-record sink, where each suite run's result document is kept;
* a secrets provider, which resolves a credential reference in memory;
* a pricing provider, which returns the price table with its ``source`` and
  date;
* a lock provider: the live tool surface that ``locks/<agent>.lock.json``
  pins (:class:`gym.locks.ToolSurfaceResolver`);
* an optional audit-encryption recipient for fix audits;
* an alerter, where ``gym_regression`` pages go (:class:`gym.alerting.Alerter`);
* and a few host facts: host config (the taint mode reads it), reasoning-level
  normalization, the tool-output-compression command labels, and the home
  where harness session files live.

:class:`StandaloneServices` implements all of them with local files. A host
plugin (:mod:`gym.hosts`) that exposes ``services()`` supplies its own; Agent
OS provides :class:`gym.agent_os_services.AgentOSServices`.

Resolution, by :func:`current`:

1. services installed with :func:`install` (tests, embedders);
2. the host the runner resolves to (:func:`gym.hosts.resolve_mode`), when its
   plugin exposes ``services()``;
3. standalone.

A runner that resolves to a named host that is not available raises, the same
as it does for the trajectory source: a fleet run must not quietly read
standalone state.

Stdlib and ``gym`` only: this module ships in the standalone package.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

DATA_DIR_ENV = "PADDOCK_DATA_DIR"
PRICE_TABLE_ENV = "PADDOCK_PRICE_TABLE"
AUDIT_RECIPIENT_NAME = "gym-audit-recipient.pem"
PRICE_TABLE_NAME = "price-table.yaml"
RESULTS_NAME = "results"
ALERTS_NAME = "alerts.jsonl"
ENV_SECRET_PREFIX = "env:"


class HostServiceError(RuntimeError):
    """A host service cannot answer."""


class SecretUnavailable(HostServiceError):
    """A credential reference cannot be resolved; the message never holds a value."""


class PricingUnavailable(HostServiceError):
    """No price table can be loaded; costs then read unknown, never zero."""


@runtime_checkable
class SecretsProvider(Protocol):
    def resolve(self, ref: str, *, purpose: str) -> str:
        """The credential ``ref`` names, in memory only, or :class:`SecretUnavailable`."""
        ...


@dataclass(frozen=True)
class PriceTable:
    """A loaded price table and where it came from.

    ``table`` is what :func:`gym.cost.price_cell` prices with. ``source`` names
    the file it was read from, and ``as_of`` is the newest ``as_of`` date among
    its entries (None when no entry carries one).
    """

    table: Any
    source: str
    as_of: str | None


@runtime_checkable
class PricingProvider(Protocol):
    def price_table(self) -> PriceTable:
        """The current price table, or :class:`PricingUnavailable`."""
        ...


@runtime_checkable
class RunRecordSink(Protocol):
    def write(
        self, document: Mapping[str, Any], *, bucket: str, target: Path | None = None
    ) -> Path:
        """Store one run's result document under ``bucket``; return where it went."""
        ...

    def find(self, run_id: str) -> list[Path]:
        """Every stored record whose id is ``run_id``."""
        ...


@dataclass(frozen=True)
class LocalRunRecords:
    """Run records as ``<root>/<bucket>/<run-id>.json``, written atomically."""

    root: Path

    def write(
        self, document: Mapping[str, Any], *, bucket: str, target: Path | None = None
    ) -> Path:
        directory = self.root / bucket
        directory.mkdir(parents=True, exist_ok=True)
        target = target or directory / f"{uuid.uuid4().hex}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
        return target

    def find(self, run_id: str) -> list[Path]:
        return list(self.root.glob(f"*/{run_id}.json"))


@dataclass(frozen=True)
class EnvironmentSecrets:
    """``env:NAME`` references: the environment first, then the config's ``[credentials]``."""

    env: Mapping[str, str]
    credentials: Mapping[str, str]

    def __repr__(self) -> str:
        return f"EnvironmentSecrets(credentials={sorted(self.credentials)!r})"

    def resolve(self, ref: str, *, purpose: str) -> str:
        if not ref.startswith(ENV_SECRET_PREFIX) or not ref[len(ENV_SECRET_PREFIX) :]:
            raise SecretUnavailable(
                f"standalone credentials are env:NAME references; cannot resolve one for {purpose}"
            )
        name = ref[len(ENV_SECRET_PREFIX) :]
        value = self.env.get(name) or self.credentials.get(name)
        if not value:
            raise SecretUnavailable(f"{name} is not set in the environment or [credentials]")
        return value


def _newest_as_of(table: Any) -> str | None:
    dates: list[str] = []
    for section in ("models", "vendors"):
        entries = getattr(table, section, None)
        if isinstance(entries, Mapping):
            for entry in entries.values():
                value = entry.get("as_of") if isinstance(entry, Mapping) else None
                if isinstance(value, str) and value:
                    dates.append(value)
    return max(dates) if dates else None


def load_price_table_file(path: Path | None) -> PriceTable:
    """Load a SEW-format price table; ``None`` is the table SEW ships."""
    try:
        from sew.cost_model import default_price_table_path, load_price_table
    except ImportError as exc:
        raise PricingUnavailable(f"pricing needs the sew package: {exc}") from exc
    target = default_price_table_path() if path is None else path
    try:
        table = load_price_table(target)
    except Exception as exc:  # noqa: BLE001 - any load failure is "no table"
        raise PricingUnavailable(f"cannot load the price table {target}: {exc}") from exc
    return PriceTable(table=table, source=str(target), as_of=_newest_as_of(table))


@dataclass(frozen=True)
class LocalPriceTable:
    """A price table from a local file."""

    path: Path

    def price_table(self) -> PriceTable:
        if not self.path.is_file():
            raise PricingUnavailable(
                f"no price table at {self.path}; set {PRICE_TABLE_ENV} or add one"
            )
        return load_price_table_file(self.path)


@dataclass(frozen=True)
class ConfiguredToolSurfaces:
    """Live tool surfaces declared in the user config's ``[tool_surfaces]`` table."""

    surfaces: Mapping[str, Any]

    def resolve(self, agent: str) -> list[str]:
        from gym.locks import ToolSurfaceUnavailable, normalize_surface

        tools = self.surfaces.get(agent)
        if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
            raise ToolSurfaceUnavailable(
                f"no tool surface for {agent!r}: declare it as a list of tool names "
                f"under [tool_surfaces] in the paddock config"
            )
        return normalize_surface(tools)


class HostServices:
    """The services interface. Subclasses override what their host provides."""

    name = "abstract"

    def data_dir(self, *, launched_tree: bool = False) -> Path:
        """The gym's state root.

        ``launched_tree`` asks for the root the launching process tree
        exported, which a host may resolve differently from its configured
        root (Agent OS: ``$HQ_ROOT`` first). Hosts with one root ignore it.
        """
        raise NotImplementedError

    def scratch_dir(self) -> Path:
        """Host scratch the hidden-test container can bind-mount."""
        raise NotImplementedError

    def run_records(self) -> RunRecordSink:
        raise NotImplementedError

    def secrets(self) -> SecretsProvider:
        raise NotImplementedError

    def pricing(self) -> PricingProvider:
        raise NotImplementedError

    def tool_surfaces(self) -> Any:
        """The lock provider: a :class:`gym.locks.ToolSurfaceResolver`."""
        raise NotImplementedError

    def audit_recipient(self) -> Path | None:
        """Where the fix-audit recipient certificate is expected, or None for none."""
        raise NotImplementedError

    def alerter(self) -> Any:
        """Where regression alerts go: a :class:`gym.alerting.Alerter`."""
        raise NotImplementedError

    def host_config(self) -> Any | None:
        """Host config exposing ``.get(dotted, default)``, or None when there is none."""
        raise NotImplementedError

    def normalize_reasoning_level(self, value: str, *, field: str) -> str:
        """The host's spelling of a reasoning level; ValueError when it is not one."""
        raise NotImplementedError

    def large_output_commands(self) -> frozenset[str] | None:
        """Tool-output-compression command labels, or None when the host has none."""
        raise NotImplementedError

    def harness_home(self) -> Path:
        """The home directory the harnesses write their session files under."""
        raise NotImplementedError


def default_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$PADDOCK_DATA_DIR``, else ``$XDG_DATA_HOME/paddock``, else ``~/.local/share/paddock``."""
    env = os.environ if env is None else env
    if env.get(DATA_DIR_ENV):
        return Path(env[DATA_DIR_ENV]).expanduser()
    base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser() / "paddock"


class StandaloneServices(HostServices):
    """Local files under one data directory, plus the user config."""

    name = "standalone"

    def __init__(
        self,
        root: Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._env = dict(os.environ if env is None else env)
        if config is None:
            from gym.hosts import load_config

            config = load_config(self._env)
        self._config = config
        self._root = Path(root) if root is not None else default_data_dir(self._env)

    def __repr__(self) -> str:
        return f"StandaloneServices(root={str(self._root)!r})"

    def _table(self, name: str) -> Mapping[str, Any]:
        table = self._config.get(name) or {}
        if not isinstance(table, Mapping):
            raise HostServiceError(f"[{name}] in the paddock config must be a table")
        return table

    def data_dir(self, *, launched_tree: bool = False) -> Path:
        return self._root

    def scratch_dir(self) -> Path:
        return self._root / "tmp"

    def run_records(self) -> RunRecordSink:
        return LocalRunRecords(self._root / RESULTS_NAME)

    def secrets(self) -> SecretsProvider:
        credentials = self._table("credentials")
        return EnvironmentSecrets(
            env=self._env, credentials={str(k): str(v) for k, v in credentials.items()}
        )

    def pricing(self) -> PricingProvider:
        configured = self._env.get(PRICE_TABLE_ENV)
        path = Path(configured).expanduser() if configured else self._root / PRICE_TABLE_NAME
        return LocalPriceTable(path)

    def tool_surfaces(self) -> Any:
        return ConfiguredToolSurfaces(self._table("tool_surfaces"))

    def audit_recipient(self) -> Path | None:
        return self._root / "audit" / AUDIT_RECIPIENT_NAME

    def alerter(self) -> Any:
        from gym.alerting import FileAlerter

        return FileAlerter(self._root / ALERTS_NAME)

    def host_config(self) -> Any | None:
        return None

    def normalize_reasoning_level(self, value: str, *, field: str) -> str:
        # Each harness CLI validates its own levels; standalone only refuses blanks.
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty reasoning level")
        return value.strip().lower()

    def large_output_commands(self) -> frozenset[str] | None:
        return None

    def harness_home(self) -> Path:
        return Path.home()


_installed: HostServices | None = None
_installed_lock = threading.Lock()


def install(services: HostServices | None) -> HostServices | None:
    """Pin the services :func:`current` returns (None unpins); returns the previous pin."""
    global _installed
    with _installed_lock:
        previous, _installed = _installed, services
    return previous


def resolve(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, Any] | None = None,
    plugins: Mapping[str, Any] | None = None,
) -> HostServices:
    """The services of the host the runner resolves to, else standalone."""
    from gym import hosts

    env = os.environ if env is None else env
    config = hosts.load_config(env) if config is None else config
    resolution = hosts.resolve_mode(explicit, env=env, config=config, plugins=plugins)
    factory = getattr(resolution.plugin, "services", None) if resolution.plugin else None
    if callable(factory):
        services = factory()
        if not isinstance(services, HostServices):
            raise hosts.RunnerResolutionError(
                f"host {resolution.mode!r} returned {type(services).__name__}, not HostServices"
            )
        return services
    return StandaloneServices(env=env, config=config)


def current() -> HostServices:
    """The installed services, else those of the resolved host."""
    installed = _installed
    return installed if installed is not None else resolve()


__all__ = [
    "AUDIT_RECIPIENT_NAME",
    "ConfiguredToolSurfaces",
    "DATA_DIR_ENV",
    "EnvironmentSecrets",
    "HostServiceError",
    "HostServices",
    "LocalPriceTable",
    "LocalRunRecords",
    "PRICE_TABLE_ENV",
    "PriceTable",
    "PricingProvider",
    "PricingUnavailable",
    "RunRecordSink",
    "SecretUnavailable",
    "SecretsProvider",
    "StandaloneServices",
    "current",
    "default_data_dir",
    "install",
    "load_price_table_file",
    "resolve",
]
