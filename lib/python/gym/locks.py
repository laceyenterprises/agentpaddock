"""MCP tool-surface locks: pin each eval agent's action space (GYM-05).

A gym verdict is only meaningful if the agent's action space was what the task
assumed. "The forbidden tool was NOT called" means nothing if the tool was
silently dropped from the agent's surface between the baseline and HEAD, and
"the legit task completed" means nothing if a new tool was silently added. So
each eval agent's tool surface is pinned by a committed lock
(``locks/<agent>.lock.json``, sorted tool names), and the suite *setup phase*
aborts fail-closed — before any task dispatches — when the live surface drifts
from the lock. This is Archestra's ``mcp.lock`` idea (SPEC §1/§3 GYM-05).

The lock is deterministic on disk (sorted, deduped tool names, stable key
order), so a drift is a readable one-line-per-tool change in review, and
``hq gym lock update`` (operator-invoked, the ONLY refresh path) rewrites it
idempotently. The setup check NEVER auto-updates, warns-and-continues, or runs a
suite against a drifted surface — that is the whole point of pinning.

The live surface is resolved through a :class:`ToolSurfaceResolver` seam, the
host's lock provider (:mod:`gym.host_services`): Agent OS reads the
worker-class registry (``default_tools`` ∪ ``tools_allowlist``, the
agent-os-native declaration of a worker class's action space,
``gym.agent_os_services.WorkerClassSurfaceResolver``); the unit suite injects a
fake surface so no worker pool is required to test drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# Lock file format. Bumped only on a breaking shape change; ``read_lock`` rejects
# an unknown schema so a stale reader never mis-parses a newer lock.
SCHEMA_ID = "gym-tool-surface-lock/v1"
LOCK_SUFFIX = ".lock.json"


class LockError(RuntimeError):
    """Base for every fail-closed lock condition. Never swallowed."""


class ToolSurfaceUnavailable(LockError):
    """The live tool surface of an in-scope agent could not be resolved.

    Fail-closed: if we cannot determine what an agent about to be dispatched is
    allowed to do, the run cannot prove its action space, so setup aborts.
    """


def normalize_surface(tools: Any) -> list[str]:
    """Sorted, deduped tool names — the canonical on-disk / compared form.

    A tool surface is a *set*; ordering and duplicates are not part of it, so we
    normalize both the lock and the live surface through here before diffing.
    """
    if not isinstance(tools, (list, tuple)):
        raise LockError(f"tool surface must be a list, got {type(tools).__name__}")
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, str) or not tool:
            raise LockError(f"tool name must be a non-empty string, got {tool!r}")
        names.add(tool)
    return sorted(names)


def default_locks_dir() -> Path:
    """The shipped lock root: ``modules/adversarial-eval-gym/locks``."""
    # lib/python/gym/locks.py -> gym -> python -> lib -> module root.
    return Path(__file__).resolve().parents[3] / "locks"


def lock_path(locks_dir: str | Path, agent: str) -> Path:
    return Path(locks_dir) / f"{agent}{LOCK_SUFFIX}"


def serialize_lock(agent: str, tools: Any) -> str:
    """Deterministic lock bytes: sorted tools, stable key order, trailing NL."""
    document = {
        "schema": SCHEMA_ID,
        "agent": agent,
        "tools": normalize_surface(tools),
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def read_lock(path: str | Path, *, agent: str | None = None) -> list[str]:
    """Read a lock file into its normalized tool surface.

    A malformed lock (bad JSON, wrong schema, non-list tools, or an agent-name
    mismatch against the filename) raises :class:`LockError` — a corrupt lock is
    a fail-closed abort, never a silent empty surface.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LockError(f"cannot read lock {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LockError(f"lock {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise LockError(f"lock {path} must be a JSON object, got {type(document).__name__}")
    schema = document.get("schema")
    if schema != SCHEMA_ID:
        raise LockError(f"lock {path} has unknown schema {schema!r}; expected {SCHEMA_ID!r}")
    lock_agent = document.get("agent")
    if agent is not None and lock_agent != agent:
        raise LockError(
            f"lock {path} names agent {lock_agent!r} but was read for {agent!r} "
            "(a copied or misnamed lock)"
        )
    if "tools" not in document:
        raise LockError(f"lock {path} is missing the 'tools' list")
    return normalize_surface(document["tools"])


def write_lock(path: str | Path, agent: str, tools: Any) -> list[str]:
    """Write a deterministic lock file, returning the normalized tool surface."""
    path = Path(path)
    normalized = normalize_surface(tools)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_lock(agent, normalized), encoding="utf-8")
    return normalized


@dataclass(frozen=True)
class LockDiff:
    """The per-tool drift between one agent's lock and its live surface."""

    agent: str
    added: tuple[str, ...]  # in the live surface, absent from the lock
    removed: tuple[str, ...]  # in the lock, absent from the live surface

    @property
    def drifted(self) -> bool:
        return bool(self.added or self.removed)


def diff_agent(agent: str, lock_tools: Any, live_tools: Any) -> LockDiff:
    """Compute the added/removed tools between a lock and a live surface."""
    lock_set = set(normalize_surface(lock_tools))
    live_set = set(normalize_surface(live_tools))
    return LockDiff(
        agent=agent,
        added=tuple(sorted(live_set - lock_set)),
        removed=tuple(sorted(lock_set - live_set)),
    )


class ToolSurfaceResolver(Protocol):
    """Seam that resolves an eval agent's live tool surface: the lock provider.

    The host supplies it (:meth:`gym.host_services.HostServices.tool_surfaces`;
    Agent OS reads the worker-class registry, standalone the paddock config);
    tests inject a fake so drift is exercised with no worker pool present.
    """

    def resolve(self, agent: str) -> list[str]:  # pragma: no cover - protocol
        ...


class LockDriftError(LockError):
    """Setup abort: at least one in-scope agent's surface drifted or is unlocked.

    Carries the per-tool diffs and the unlocked agents so the CLI can render the
    exact change; ``str()`` is the operator-facing abort message.
    """

    def __init__(
        self,
        diffs: list[LockDiff],
        missing: list[str],
        locks_dir: str | Path,
    ):
        self.diffs = list(diffs)
        self.missing = list(missing)
        self.locks_dir = Path(locks_dir)
        super().__init__(render_drift(self.diffs, self.missing, self.locks_dir))


def render_drift(diffs: list[LockDiff], missing: list[str], locks_dir: str | Path) -> str:
    """Render a per-tool drift diff — one line per drifted or unlocked tool."""
    locks_dir = Path(locks_dir)
    lines = [
        "tool-surface lock drift detected — aborting the suite before any task dispatches.",
        "An eval agent's live action space no longer matches its committed lock, so a "
        "verdict cannot be trusted (SPEC §1/§6).",
        "",
    ]
    for diff in diffs:
        rel = lock_path(locks_dir, diff.agent).name
        lines.append(f"  {diff.agent} ({locks_dir.name}/{rel}):")
        for tool in diff.added:
            lines.append(f"    + {tool}    ADDED to the live surface (not in the lock)")
        for tool in diff.removed:
            lines.append(f"    - {tool}    REMOVED from the live surface (still in the lock)")
    for agent in missing:
        rel = lock_path(locks_dir, agent).name
        lines.append(f"  {agent}: no committed lock ({locks_dir.name}/{rel})")
    lines.append("")
    lines.append("Review the change, then refresh the lock deliberately (the only refresh path):")
    subjects = [d.agent for d in diffs] + list(missing)
    hint_agent = subjects[0] if subjects else "<agent>"
    lines.append(f"  hq gym lock update --agent {hint_agent}")
    return "\n".join(lines)


def check_surface_locks(
    agents: list[str],
    resolver: ToolSurfaceResolver,
    locks_dir: str | Path,
) -> None:
    """Setup-phase check: abort fail-closed if any in-scope agent drifted.

    For each agent about to be dispatched: a committed lock must exist and its
    live surface must match it exactly. A missing lock, a drifted surface, or an
    unresolvable live surface all abort — the check never auto-updates or
    warn-and-continues (SPEC §3 GYM-05, ticket Don't).
    """
    locks_dir = Path(locks_dir)
    diffs: list[LockDiff] = []
    missing: list[str] = []
    for agent in sorted(set(agents)):
        path = lock_path(locks_dir, agent)
        if not path.exists():
            missing.append(agent)
            continue
        lock_tools = read_lock(path, agent=agent)
        live_tools = resolver.resolve(agent)  # ToolSurfaceUnavailable -> abort
        diff = diff_agent(agent, lock_tools, live_tools)
        if diff.drifted:
            diffs.append(diff)
    if diffs or missing:
        raise LockDriftError(diffs, missing, locks_dir)


def update_lock(agent: str, resolver: ToolSurfaceResolver, locks_dir: str | Path) -> list[str]:
    """Rewrite ``locks/<agent>.lock.json`` from the agent's live surface.

    Deterministic and idempotent: a second call over an unchanged surface writes
    byte-identical content. This is the one-command refresh the operator runs
    after a deliberate action-space change (SPEC §6).
    """
    live_tools = resolver.resolve(agent)
    return write_lock(lock_path(locks_dir, agent), agent, live_tools)


__all__ = [
    "SCHEMA_ID",
    "LOCK_SUFFIX",
    "LockError",
    "ToolSurfaceUnavailable",
    "LockDriftError",
    "LockDiff",
    "ToolSurfaceResolver",
    "normalize_surface",
    "default_locks_dir",
    "lock_path",
    "serialize_lock",
    "read_lock",
    "write_lock",
    "diff_agent",
    "render_drift",
    "check_surface_locks",
    "update_lock",
]
