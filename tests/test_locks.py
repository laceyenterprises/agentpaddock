"""GYM-05 tool-surface locks: drift aborts setup; update regenerates.

Most checks use a fixture lock and an injected fake resolver — no real worker.
The shipped OSS locks are also checked against the committed worker-class
registry. The load-bearing guarantees:

  * a live surface with one ADDED tool and one with one REMOVED tool each abort
    the suite *before any task dispatches*, with a per-tool diff naming exactly
    the drifted tools;
  * a clean surface lets the suite proceed;
  * ``hq gym lock update`` rewrites the lock deterministically, is idempotent,
    and a subsequent setup check passes.
"""

from __future__ import annotations

import io
import json

import pytest
from conftest import FIXTURES_DIR, REPO_ROOT, TASKS_DIR

from gym import cli, locks, taint
from gym.grader import Trajectory
from gym.suite import discover_tasks, in_scope_agents, run_suite

MINI_SUITE = FIXTURES_DIR / "mini-suite"
_CLAUDE_SURFACE = ["bash:safe", "read", "write"]


class FakeResolver:
    """Return a canned live surface for every agent asked."""

    def __init__(self, surface):
        self.surface = list(surface)
        self.asked: list[str] = []

    def resolve(self, agent):
        self.asked.append(agent)
        return list(self.surface)


class ExplodingSource:
    """A dispatch source that must never be touched once setup aborts."""

    def __init__(self):
        self.dispatched: list[str] = []

    def run(self, task):  # pragma: no cover - asserted never called on drift
        self.dispatched.append(task.id)
        raise AssertionError("run_suite dispatched a task despite lock drift")


@pytest.fixture
def locks_dir(tmp_path):
    """A committed lock dir with a claude-code lock at the canonical surface."""
    locks.write_lock(tmp_path / "claude-code.lock.json", "claude-code", _CLAUDE_SURFACE)
    return tmp_path


# --------------------------------------------------------------------------- #
# Deterministic serialization
# --------------------------------------------------------------------------- #


def test_lock_is_sorted_and_deterministic(tmp_path):
    path = tmp_path / "claude-code.lock.json"
    # Written unsorted / with a duplicate: on disk it is sorted + deduped.
    locks.write_lock(path, "claude-code", ["write", "read", "bash:safe", "read"])
    first = path.read_text()
    document = json.loads(first)
    assert document["tools"] == ["bash:safe", "read", "write"]
    assert document["schema"] == locks.SCHEMA_ID
    assert document["agent"] == "claude-code"
    # A second write of the same surface is byte-identical.
    locks.write_lock(path, "claude-code", ["read", "write", "bash:safe"])
    assert path.read_text() == first


def test_read_lock_rejects_agent_mismatch(tmp_path):
    path = tmp_path / "claude-code.lock.json"
    locks.write_lock(path, "claude-code", _CLAUDE_SURFACE)
    with pytest.raises(locks.LockError):
        locks.read_lock(path, agent="resident")


def test_read_lock_rejects_unknown_schema(tmp_path):
    path = tmp_path / "x.lock.json"
    path.write_text(json.dumps({"schema": "nope/v9", "agent": "x", "tools": []}))
    with pytest.raises(locks.LockError):
        locks.read_lock(path)


# --------------------------------------------------------------------------- #
# Per-tool diff
# --------------------------------------------------------------------------- #


def test_diff_added_and_removed_named_exactly():
    diff = locks.diff_agent("claude-code", ["read", "write"], ["read", "browser.navigate"])
    assert diff.added == ("browser.navigate",)
    assert diff.removed == ("write",)
    assert diff.drifted


def test_clean_surface_has_no_drift():
    diff = locks.diff_agent("claude-code", _CLAUDE_SURFACE, list(reversed(_CLAUDE_SURFACE)))
    assert not diff.drifted


# --------------------------------------------------------------------------- #
# check_surface_locks — the setup-phase gate
# --------------------------------------------------------------------------- #


def test_check_passes_on_matching_surface(locks_dir):
    resolver = FakeResolver(_CLAUDE_SURFACE)
    # No raise.
    locks.check_surface_locks(["claude-code"], resolver, locks_dir)


@pytest.mark.parametrize(
    "agent", ["oss-codex", "oss-claude-code", "oss-opencode", "oss-pi", "oss-local"]
)
def test_shipped_oss_lock_matches_registry(agent):
    registry_path = REPO_ROOT / "modules" / "worker-pool" / "worker-classes.json"
    entry = json.loads(registry_path.read_text(encoding="utf-8"))[agent]
    surface = entry["defaultTools"] + entry["toolsAllowlist"]
    locks.check_surface_locks([agent], FakeResolver(surface), locks.default_locks_dir())


def test_check_aborts_on_added_tool_with_named_diff(locks_dir):
    resolver = FakeResolver(_CLAUDE_SURFACE + ["browser.navigate"])
    with pytest.raises(locks.LockDriftError) as exc:
        locks.check_surface_locks(["claude-code"], resolver, locks_dir)
    message = str(exc.value)
    assert "+ browser.navigate" in message
    # Only the drifted tool is named — untouched tools are not in the diff.
    assert "+ read" not in message and "- read" not in message
    assert "+ write" not in message and "- write" not in message
    assert exc.value.diffs[0].added == ("browser.navigate",)
    assert exc.value.diffs[0].removed == ()


def test_check_aborts_on_removed_tool_with_named_diff(locks_dir):
    resolver = FakeResolver(["bash:safe", "read"])  # dropped write
    with pytest.raises(locks.LockDriftError) as exc:
        locks.check_surface_locks(["claude-code"], resolver, locks_dir)
    message = str(exc.value)
    assert "- write" in message
    assert exc.value.diffs[0].removed == ("write",)
    assert exc.value.diffs[0].added == ()


def test_check_aborts_on_missing_lock(locks_dir):
    resolver = FakeResolver(_CLAUDE_SURFACE)
    with pytest.raises(locks.LockDriftError) as exc:
        locks.check_surface_locks(["resident"], resolver, locks_dir)
    assert "resident" in str(exc.value)
    assert exc.value.missing == ["resident"]


def test_check_aborts_on_unresolvable_surface(locks_dir):
    class Unavailable:
        def resolve(self, agent):
            raise locks.ToolSurfaceUnavailable(f"{agent} is not a worker class")

    with pytest.raises(locks.ToolSurfaceUnavailable):
        locks.check_surface_locks(["claude-code"], Unavailable(), locks_dir)


# --------------------------------------------------------------------------- #
# run_suite setup phase — drift aborts BEFORE any dispatch
# --------------------------------------------------------------------------- #


def test_run_suite_drift_aborts_before_any_dispatch(locks_dir):
    tasks = discover_tasks(MINI_SUITE, suite="all")  # persona = claude-code
    source = ExplodingSource()
    resolver = FakeResolver(_CLAUDE_SURFACE + ["net.raw"])  # one ADDED tool
    with pytest.raises(locks.LockDriftError) as exc:
        run_suite(
            tasks,
            source,
            suite="all",
            taint_mode=taint.ENFORCE,
            resolver=resolver,
            locks_dir=locks_dir,
        )
    assert "+ net.raw" in str(exc.value)
    assert source.dispatched == []  # nothing dispatched


def test_run_suite_clean_lock_proceeds(locks_dir):
    tasks = discover_tasks(MINI_SUITE, suite="all")
    trajectories = {
        "mini-pass": Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"}),
        "mini-fail": Trajectory(terminal_status="succeeded", submitted_result={"answer": "bad"}),
    }

    class MappingSource:
        def __init__(self):
            self.dispatched: list[str] = []

        def run(self, task):
            self.dispatched.append(task.id)
            return trajectories[task.id]

    source = MappingSource()
    resolver = FakeResolver(_CLAUDE_SURFACE)
    result = run_suite(
        tasks,
        source,
        suite="all",
        taint_mode=taint.ENFORCE,
        resolver=resolver,
        locks_dir=locks_dir,
    )
    assert sorted(source.dispatched) == ["mini-fail", "mini-pass"]
    assert result.passed == 1 and result.failed == 1


def test_gated_injection_persona_is_out_of_scope():
    # Under UNAVAILABLE taint the resident injection tasks never dispatch, so
    # resident is out of the lock scope and needs no committed lock.
    from conftest import TASKS_DIR

    tasks = discover_tasks(TASKS_DIR, suite="injection")
    assert in_scope_agents(tasks, taint.UNAVAILABLE) == []
    # In enforce, the resident persona becomes in-scope (and would need a lock).
    assert in_scope_agents(tasks, taint.ENFORCE) == ["resident"]


# --------------------------------------------------------------------------- #
# hq gym lock update — regenerate, idempotent, then a clean check
# --------------------------------------------------------------------------- #


def _lock_cli(argv, resolver):
    out = io.StringIO()
    code = cli.main(argv, resolver=resolver, out=out)
    return code, out.getvalue()


def test_lock_update_regenerates_and_is_idempotent(tmp_path):
    resolver = FakeResolver(_CLAUDE_SURFACE)
    argv = [
        "lock",
        "update",
        "--agent",
        "claude-code",
        "--locks-dir",
        str(tmp_path),
    ]
    code, _ = _lock_cli(argv, resolver)
    assert code == cli.EXIT_OK
    lock_file = tmp_path / "claude-code.lock.json"
    first = lock_file.read_text()
    assert json.loads(first)["tools"] == _CLAUDE_SURFACE

    # Idempotent: a second run over the same surface writes identical bytes.
    code, _ = _lock_cli(argv, resolver)
    assert code == cli.EXIT_OK
    assert lock_file.read_text() == first

    # A subsequent setup check against the regenerated lock passes.
    code, text = _lock_cli(
        [
            "lock",
            "check",
            "--suite",
            "all",
            "--tasks-dir",
            str(MINI_SUITE),
            "--locks-dir",
            str(tmp_path),
            "--taint-mode",
            "enforce",
        ],
        resolver,
    )
    assert code == cli.EXIT_OK
    assert "OK" in text


def test_lock_update_refreshes_a_drifted_lock(tmp_path):
    # Start with a stale lock, then a deliberate surface change is captured by
    # `lock update` and the following check is clean.
    locks.write_lock(tmp_path / "claude-code.lock.json", "claude-code", ["read"])
    resolver = FakeResolver(_CLAUDE_SURFACE)  # the new, wider live surface

    code, _ = _lock_cli(
        ["lock", "update", "--agent", "claude-code", "--locks-dir", str(tmp_path)],
        resolver,
    )
    assert code == cli.EXIT_OK
    assert locks.read_lock(tmp_path / "claude-code.lock.json") == _CLAUDE_SURFACE


def test_lock_update_all_skips_unresolvable_agent(tmp_path):
    # The shipped corpus has both a claude-code (rca) and a resident (injection)
    # persona: `lock update` (no --agent) writes the resolvable one and skips the
    # other with a note, without a hard failure.
    class PartialResolver:
        def resolve(self, agent):
            if agent == "claude-code":
                return list(_CLAUDE_SURFACE)
            raise locks.ToolSurfaceUnavailable(f"{agent} is not a worker class")

    code, text = _lock_cli(
        ["lock", "update", "--tasks-dir", str(TASKS_DIR), "--locks-dir", str(tmp_path)],
        PartialResolver(),
    )
    assert code == cli.EXIT_OK
    assert (tmp_path / "claude-code.lock.json").exists()
    assert not (tmp_path / "resident.lock.json").exists()
    assert "skipped resident" in text


def test_lock_update_explicit_unresolvable_agent_is_error(tmp_path):
    class Unavailable:
        def resolve(self, agent):
            raise locks.ToolSurfaceUnavailable("nope")

    code, _ = _lock_cli(
        ["lock", "update", "--agent", "resident", "--locks-dir", str(tmp_path)],
        Unavailable(),
    )
    assert code == cli.EXIT_ERROR
