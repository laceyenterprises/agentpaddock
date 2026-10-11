"""Review corpus acceptance: grade submissions through the real verifier boundary."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from gym import grader, outcomes
from gym.grader import Trajectory
from gym.review_checks import result_schema_snippet
from gym.schema import load_task

GYM = Path(__file__).resolve().parents[1]
TASKS = tuple(sorted((GYM / "tasks").glob("review-*/task.toml")))
DEFECTS = tuple(p for p in TASKS if "clean-retention-plan" not in str(p))
CLEAN = GYM / "tasks/review-clean-retention-plan/task.toml"


def expected(manifest):
    return json.loads((manifest.parent / "expected/reference.json").read_text())


def truth(manifest):
    return json.loads((manifest.parent / "expected/ground_truth.json").read_text())


def grade(manifest, result, *, read=True):
    calls = (
        [
            {"name": "Bash", "arguments": {"cmd": f"cat {path}"}}
            for path in truth(manifest)["read_files"]
        ]
        if read
        else []
    )
    trajectory = Trajectory(
        terminal_status="succeeded",
        submitted_result=result,
        transcript_rows=[],
        control_rows=[],
        harness_calls=calls,
    )
    return grader.grade(load_task(manifest.parent), trajectory)


def check(cell, name):
    return next(item for item in cell.subchecks if item["name"] == name)


def test_six_tasks_use_shared_review_schema():
    assert len(TASKS) == 6
    assert len(DEFECTS) == 5
    origins = [truth(path)["provenance"]["kind"] for path in TASKS]
    assert origins.count("real-derived") == 3
    assert origins.count("synthetic") == 3  # Includes the clean control.
    for path in TASKS:
        task = load_task(path.parent)
        assert task.id == path.parent.name
        assert task.suite == "review"
        assert result_schema_snippet().strip() in path.read_text()
        assert "Source PR" in path.read_text()


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_reference_findings_pass(manifest):
    cell = grade(manifest, expected(manifest))
    assert cell.outcome == outcomes.PASSED, cell.reason
    assert all(item["ok"] for item in cell.subchecks)


@pytest.mark.parametrize("manifest", DEFECTS, ids=lambda p: p.parent.name)
def test_flag_everything_fails_precision(manifest):
    result = expected(manifest)
    hit = result["findings"][0]
    # Even a correct hit cannot rescue three unrelated blocking allegations.
    for index in range(3):
        result["findings"].append({**hit, "file": f"unrelated-{index}.py"})
    cell = grade(manifest, result)
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "precision")["ok"]


@pytest.mark.parametrize("manifest", DEFECTS, ids=lambda p: p.parent.name)
def test_miss_fails(manifest):
    cell = grade(manifest, {"findings": []})
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "defect_found")["ok"]


@pytest.mark.parametrize("manifest", DEFECTS, ids=lambda p: p.parent.name)
def test_near_miss_outside_slack_fails(manifest):
    result = expected(manifest)
    window = truth(manifest)["defects"][0]
    line = window["line_end"] + truth(manifest)["slack"] + 1
    result["findings"][0].update(line_start=line, line_end=line)
    cell = grade(manifest, result)
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "defect_found")["ok"]


@pytest.mark.parametrize("manifest", DEFECTS, ids=lambda p: p.parent.name)
def test_slack_boundary_passes_and_nonblocking_hit_fails(manifest):
    result = expected(manifest)
    window = truth(manifest)["defects"][0]
    line = window["line_end"] + truth(manifest)["slack"]
    result["findings"][0].update(line_start=line, line_end=line)
    assert grade(manifest, result).outcome == outcomes.PASSED
    result["findings"][0]["blocking"] = False
    assert grade(manifest, result).outcome == outcomes.FAILED


def test_clean_control_fails_with_one_blocking_finding():
    finding = dict(
        file="retention.py",
        line_start=25,
        line_end=25,
        blocking=True,
        category="correctness",
        summary="Archive phase is unsafe",
    )
    cell = grade(CLEAN, {"findings": [finding]})
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "no_blocking")["ok"]
    finding["blocking"] = False
    assert grade(CLEAN, {"findings": [finding]}).outcome == outcomes.PASSED


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_reference_requires_read_evidence(manifest):
    cell = grade(manifest, expected(manifest), read=False)
    assert cell.outcome == outcomes.FAILED
    assert not check(cell, "read_evidence")["ok"]


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_stages_contain_only_judging_inputs(manifest, tmp_path):
    directory = manifest.parent
    tmp_path = tmp_path / "sandbox"
    tmp_path.mkdir()
    subprocess.run(
        ["bash", str(directory / "stages/stage-input.sh"), str(tmp_path)],
        check=True,
        capture_output=True,
    )
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"change.diff", "pr.md", *truth(manifest)["read_files"]}
    assert not any(p.is_symlink() for p in tmp_path.rglob("*"))
    staged = "\n".join(p.read_text() for p in tmp_path.iterdir())
    for token in (
        "GYM-GROUND-TRUTH-NEVER-STAGE",
        "expected_category",
        "review_checks.record",
        "Blocking issues",
        "Recommended fix",
        "reference-fix",
        "ground_truth",
    ):
        assert token not in staged
    for finding in expected(manifest)["findings"]:
        assert finding["summary"] not in staged
    # Diff and post-change file must agree; reverse application produces the
    # before-state, and forward application reconstructs every staged byte.
    files = truth(manifest)["read_files"]
    originals = {f: (tmp_path / f).read_bytes() for f in files}
    subprocess.run(
        ["git", "apply", "--reverse", str(tmp_path / "change.diff")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "apply", str(tmp_path / "change.diff")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert {f: (tmp_path / f).read_bytes() for f in files} == originals


def load_fixture(task_id, filename):
    path = GYM / "tasks" / task_id / "fixture" / filename
    spec = importlib.util.spec_from_file_location("review_fixture", path)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their defining module while processing annotations.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_corrupt_checkpoint_blocks_constructor_but_absent_cache_is_valid(tmp_path):
    module = load_fixture("review-corrupt-refresh-checkpoint", "progress_journal.py")
    assert module.ProgressJournal(tmp_path).files == {}
    directory = tmp_path / "progress/files"
    directory.mkdir(parents=True)
    checkpoint = directory / "one.json"
    checkpoint.write_text('{"version":')
    with pytest.raises(json.JSONDecodeError):
        module.ProgressJournal(tmp_path)
    checkpoint.unlink()
    (directory.parent / "identities.json").write_text("{")
    with pytest.raises(json.JSONDecodeError):
        module.ProgressJournal(tmp_path)


def test_synthetic_side_effect_precedes_guard():
    module = load_fixture("review-publish-guard-order", "publisher.py")
    calls = []

    class Store:
        def send(self, account, payload):
            calls.append((account, payload))
            return "receipt"

        def is_enabled(self, account):
            return False

    with pytest.raises(PermissionError):
        module.publish(Store(), "disabled", "payload")
    assert calls == [("disabled", "payload")]


def test_synthetic_retry_failure_advances_cursor():
    module = load_fixture("review-retry-empty-page", "reader.py")

    class Client:
        def fetch(self, cursor):
            raise module.TemporaryUnavailable()

    class Store:
        cursor = 7

        def append(self, page):
            assert page == []

    store = Store()
    assert module.drain(Client(), store) == []
    assert store.cursor == 8


def test_clean_control_intentional_phases_and_failure_semantics():
    module = load_fixture("review-clean-retention-plan", "retention.py")
    workers = [
        dict(id="active", terminal=False, dirty=False),
        dict(id="dirty", terminal=True, dirty=True),
        dict(id="one", terminal=True, dirty=False),
        dict(id="two", terminal=True, dirty=False),
    ]
    original = copy.deepcopy(workers)
    calls = []

    def archive(worker):
        calls.append(("archive", worker["id"]))

    def remove(worker):
        calls.append(("remove", worker["id"]))

    assert module.sweep(iter(workers), archive, remove) == ["one", "two"]
    assert calls == [("archive", "one"), ("archive", "two"), ("remove", "one"), ("remove", "two")]
    assert workers == original
    calls.clear()

    def fail_archive(worker):
        archive(worker)
        if worker["id"] == "two":
            raise OSError("archive failed")

    with pytest.raises(OSError):
        module.sweep(workers, fail_archive, remove)
    assert calls == [("archive", "one"), ("archive", "two")]
    calls.clear()

    def fail_remove(worker):
        remove(worker)
        raise OSError("remove failed")

    with pytest.raises(OSError):
        module.sweep(workers, archive, fail_remove)
    assert calls == [("archive", "one"), ("archive", "two"), ("remove", "one")]


def test_real_branch_override_resets_unpushed_tip(tmp_path):
    """Exercise the actual trimmed shell against disposable Git repositories."""

    def git(*args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    workspace = tmp_path / "rescue"
    git("init", "--bare", str(remote))
    git("init", str(repo))
    git("config", "user.name", "Gym Fixture", cwd=repo)
    git("config", "user.email", "fixture@example.invalid", cwd=repo)
    git("checkout", "-b", "rescue", cwd=repo)
    (repo / "file").write_text("published\n")
    git("add", "file", cwd=repo)
    git("commit", "-m", "published", cwd=repo)
    published = git("rev-parse", "HEAD", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "origin", "rescue", cwd=repo)
    (repo / "file").write_text("unpublished\n")
    git("commit", "-am", "local-only", cwd=repo)
    local = git("rev-parse", "HEAD", cwd=repo)
    # No worktree holds the local rescue ref, the helper's supported -B path.
    git("checkout", "--detach", cwd=repo)
    fixture = GYM / "tasks/review-rescue-local-tip/fixture/provision.sh"
    script = """source "$1"
REPO_PATH="$2"; REPO=fixture; BRANCH=rescue; WORKSPACE_PATH="$3"; TRUNK=main; TASK_KIND=coding
# Test-local contracts: exclusive mutation, no branch holder, successful cleanup.
worktree_lock_path() { printf '%s/lock' "$REPO_PATH"; }
acquire_lock_dir() { :; }
release_lock_dir() { :; }
worktree_holding_branch() { :; }
rollback_provision() { :; }
record_stage() { :; }
prepare_clean_base() { :; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
fetch_origin_branch() { git -C "$1" fetch origin "$2"; }
provision_branch_override
"""
    subprocess.run(
        ["bash", "-c", script, "fixture", str(fixture), str(repo), str(workspace)],
        check=True,
        capture_output=True,
    )
    assert git("rev-parse", "HEAD", cwd=workspace) == published
    assert git("rev-parse", "rescue", cwd=repo) == published
    assert local != published
    assert (
        subprocess.run(["git", "merge-base", "--is-ancestor", local, "rescue"], cwd=repo).returncode
        == 1
    )


def test_stale_lock_recovery_can_remove_new_holder():
    """Deterministic interleaving of two waiters, without timing or real locks."""
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to replay the reviewed JS acquisition function")
    path = GYM / "tasks/review-stale-lock-recovery/fixture/file-lock.mjs"
    code = path.read_text()
    # Imports and sleep are replaced with controlled filesystem callbacks.
    code = code[code.index("const LOCK_STALE_MS") :]
    harness = r"""
const vm = require("node:vm");
let owner = "orphan";
let removed = [];
const context = {
    mkdirSync() {}, dirname() { return "."; }, sleepSync() {},
    openSync() {
        if (owner !== null) { const error = new Error(); error.code = "EEXIST"; throw error; }
        owner = "waiter-B";
        return 7;
    },
    statSync() {
        // B observes stale metadata. A then removes the orphan and acquires
        // a new live lock before B executes its recovery deletion.
        const observed = {mtimeMs: 0};
        owner = "waiter-A";
        return observed;
    },
    rmSync() { removed.push(owner); owner = null; },
};
vm.runInNewContext(CODE + "\nacquireFileLock('registry', {now: () => 20000});", context);
process.stdout.write(JSON.stringify({removed, owner}));
""".replace("CODE", json.dumps(code))
    result = json.loads(subprocess.check_output([node, "-e", harness], text=True))
    assert result == {"removed": ["waiter-A"], "owner": "waiter-B"}


@pytest.mark.parametrize("repetition", range(22))
def test_stale_lock_reference_preserves_replacement_holder(tmp_path, repetition):
    """Replay the dangerous interleaving against the applied reference patch.

    The callbacks model flock ownership, not native filesystem containment.
    Repetition measures this deterministic regression guard, not a random race.
    """
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to replay the reference remediation")
    task = GYM / "tasks/review-stale-lock-recovery"
    source = tmp_path / "file-lock.mjs"
    shutil.copyfile(task / "fixture/file-lock.mjs", source)
    subprocess.run(
        ["git", "apply", str(task / "expected/source-remediation.diff")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=10,
    )
    code = source.read_text()
    code = code[code.index("const LOCK_") :]
    harness = r"""
const vm = require("node:vm");
let owner = "orphan", ticks = 20000, nextFd = 0, reads = 0;
let pathExists = true, removed = [], fds = new Set();
function error(code) { return Object.assign(new Error(code), {code}); }
const context = {
    mkdirSync() {}, dirname() { return "."; },
    sleepSync() { ticks += 6000; },
    openSync(path, flags) {
        if (path.endsWith(".break")) { fds.add(++nextFd); return nextFd; }
        if (flags === "wx" && pathExists) throw error("EEXIST");
        pathExists = true;
        if (flags === "wx") owner = "contender";
        fds.add(++nextFd);
        return nextFd;
    },
    statSync() {
        const observed = {ino: 1, mtimeMs: 0};
        // Old holder releases and a new one acquires AFTER the identity read,
        // before the old reference executes rmSync. Breaker is not shared.
        if (++reads === 2) owner = "replacement";
        return observed;
    },
    rmSync(path) {
        if (path.endsWith(".break")) return;
        removed.push(owner); owner = null; pathExists = false;
    },
    flockSync(fd) {
        // The same handoff under the kernel protocol: replacement owns the
        // permanent inode before the contender can acquire its flock.
        owner = "replacement";
        throw error("EWOULDBLOCK");
    },
    closeSync(fd) { fds.delete(fd); },
    now() { return ticks; },
};
vm.runInNewContext(CODE, context);
let timedOut = false;
try { vm.runInNewContext("acquireFileLock('registry', {now});", context); }
catch (error) { timedOut = error.message.includes("timed out"); }
process.stdout.write(JSON.stringify({owner, removed, timedOut, openFds: fds.size}));
""".replace("CODE", json.dumps(code))
    result = json.loads(subprocess.check_output([node, "-e", harness], text=True, timeout=10))
    assert result == {"owner": "replacement", "removed": [], "timedOut": True, "openFds": 0}


def test_stale_lock_reference_releases_without_unlink(tmp_path):
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to replay the reference remediation")
    task = GYM / "tasks/review-stale-lock-recovery"
    source = tmp_path / "file-lock.mjs"
    shutil.copyfile(task / "fixture/file-lock.mjs", source)
    subprocess.run(
        ["git", "apply", str(task / "expected/source-remediation.diff")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=10,
    )
    code = source.read_text()
    code = code[code.index("const LOCK_") :]
    harness = r"""
const vm = require("node:vm");
let nextFd = 0, owner = null, openFds = new Set(), removed = [];
const context = {
    mkdirSync() {}, dirname() { return "."; }, sleepSync() {},
    openSync(path, flags) {
        if (flags !== "a") throw new Error("lock inode must remain permanent");
        openFds.add(++nextFd); return nextFd;
    },
    flockSync(fd, operation) {
        if (operation !== "exnb") throw new Error("must acquire exclusive nonblocking");
        if (owner !== null) throw Object.assign(new Error(), {code: "EAGAIN"});
        owner = fd;
    },
    closeSync(fd) { openFds.delete(fd); if (owner === fd) owner = null; },
    rmSync(path) { removed.push(path); },
};
vm.runInNewContext(CODE, context);
vm.runInNewContext(`
    const first = acquireFileLock('registry');
    releaseFileLock(first);
    const second = acquireFileLock('registry');
    releaseFileLock(second);
`, context);
process.stdout.write(JSON.stringify({owner, removed, openFds: openFds.size}));
""".replace("CODE", json.dumps(code))
    result = json.loads(subprocess.check_output([node, "-e", harness], text=True, timeout=10))
    assert result == {"owner": None, "removed": [], "openFds": 0}


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_verifier_windows_match_expected_and_staged_source(manifest):
    import ast

    data = truth(manifest)
    tree = ast.parse((manifest.parent / "verifier.py").read_text())
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
    )
    declared = ast.literal_eval(call.keywords[0].value)
    assert declared == {k: v for k, v in data.items() if k not in ("marker", "provenance")}
    needles = {
        "review-corrupt-refresh-checkpoint": "payload = json.loads",
        "review-rescue-local-tip": 'if git -C "$REPO_PATH" ls-remote',
        "review-stale-lock-recovery": "rmSync(lockPath",
        "review-publish-guard-order": "receipt = store.send",
        "review-retry-empty-page": "except TemporaryUnavailable:",
    }
    for defect in data["defects"]:
        lines = (manifest.parent / "fixture" / defect["file"]).read_text().splitlines()
        assert needles[manifest.parent.name] in lines[defect["line_start"] - 1]
        assert defect["line_end"] <= len(lines)
