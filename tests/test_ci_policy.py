"""PDK-05: the CI budget (SPEC R1) is enforced by this test, not by prose.

Paddock's whole Actions footprint is two workflows: `ci / fast` (the one
per-PR job and the required check) and the Pages build. This test parses
``.github/workflows/*`` and fails on anything that costs more:

- a runner other than a standard Linux one (no macOS, Windows or larger runners);
- a matrix, or more than one Python in a job;
- a push trigger for anything but the main branch, or any trigger other than
  pull_request, push and workflow_dispatch (no schedule, ever);
- a workflow without ``concurrency`` and ``cancel-in-progress: true``;
- a pull_request workflow without ``paths``, or a job without
  ``timeout-minutes`` of at most 5;
- any workflow beyond the two;
- an eval, benchmark, live-model or Docker-containment job anywhere: those run
  on contributor machines and arrive as result bundles. A GitHub runner has
  Docker, so `ci / fast` must also set ``PADDOCK_NO_DOCKER=1``, which keeps the
  Docker-backed tests off it.

Changing a workflow is CI-cost change control (CIGUARD-01): the PR states the
operator's authorization, and this test still has to pass.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from gym_test_support import NO_DOCKER_ENV, hide_docker, require_docker_backend

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

#: The R1 workflows: file name -> (workflow name, its only job).
R1_WORKFLOWS = {"ci.yml": ("ci", "fast"), "pages.yml": ("pages", "deploy")}
PAGES_PATHS = ["results/**", "site/**", "scripts/build_site.py"]
PAGES_LAUNCH_CONDITION = "${{ vars.PADDOCK_SITE_LIVE == 'true' }}"
ALLOWED_EVENTS = frozenset({"pull_request", "push", "workflow_dispatch"})
LINUX_RUNNERS = frozenset({"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"})
MAX_TIMEOUT_MINUTES = 5

#: Words that name an eval, benchmark, live-model or containment job or step.
HEAVY_WORDS = frozenset(
    {"bench", "benchmark", "benchmarks", "containment", "docker", "eval", "evals"}
    | {"evaluation", "evaluations", "live", "matrix", "podman"}
)
#: Commands that run a suite, a container or a paid model.
HEAVY_COMMANDS = re.compile(
    r"\b(?:paddock|hq\s+gym)\s+run\b|\bgym\.cli\s+run\b|--suite\b|\bdocker\b|\bpodman\b"
    r"|\bGYM_REQUIRE_REAL_ENGINE\b|\b[A-Z][A-Z0-9_]*_API_KEY\b",
)
#: Any secret but the run's own token means a paid provider call.
PROVIDER_SECRET = re.compile(r"\$\{\{\s*secrets\.(?!GITHUB_TOKEN\b)")
#: Built output and caches are never tracked, so CI need not cover them.
UNTRACKED = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "_site",
        "site",
        "venv",
    }
)


def load_workflows(directory: Path = WORKFLOWS_DIR) -> dict[str, Any]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def triggers(workflow: Mapping[str, Any]) -> dict[str, Any]:
    # YAML 1.1 reads a bare `on:` key as boolean true.
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict.fromkeys(on)
    return dict(on or {})


def _words(text: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", text.lower())) - {""}


def _job_violations(where: str, job: Mapping[str, Any]) -> list[str]:
    found = []
    if job.get("uses"):
        found.append(f"{where}: reusable workflows cannot be checked against the budget")
    runs_on = job.get("runs-on")
    if not isinstance(runs_on, str) or runs_on not in LINUX_RUNNERS:
        found.append(f"{where}: runs-on {runs_on!r} is not a standard Linux runner")
    if "matrix" in (job.get("strategy") or {}):
        found.append(f"{where}: matrix strategy")
    timeout = job.get("timeout-minutes")
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT_MINUTES:
        found.append(f"{where}: timeout-minutes must be 1..{MAX_TIMEOUT_MINUTES}, got {timeout!r}")
    if "container" in job or "services" in job:
        found.append(f"{where}: container or services (Docker containment)")

    steps = job.get("steps") or []
    pythons = [
        (step.get("with") or {}).get("python-version")
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    if len(pythons) > 1 or any(
        not isinstance(version, str) or re.search(r"[\s,\[]", version.strip())
        for version in pythons
    ):
        found.append(f"{where}: more than one Python ({pythons!r})")

    names = [where.rsplit(":", 1)[-1], str(job.get("name", ""))]
    names += [str(step.get("name", "")) for step in steps]
    if any(_words(name) & HEAVY_WORDS for name in names):
        found.append(f"{where}: eval, benchmark, live-model or containment job")
    for step in steps:
        if HEAVY_COMMANDS.search(str(step.get("run", ""))):
            found.append(f"{where}: step runs a suite, a container or a paid model")
        if str(step.get("uses", "")).startswith("docker://"):
            found.append(f"{where}: docker:// action (Docker containment)")
    if PROVIDER_SECRET.search(yaml.safe_dump(dict(job))):
        found.append(f"{where}: uses a provider secret (live-model job)")
    return found


def policy_violations(workflows: Mapping[str, Any]) -> list[str]:
    """Every R1 violation in ``workflows`` (file name -> parsed YAML)."""
    found = []
    if set(workflows) != set(R1_WORKFLOWS):
        found.append(f"workflows must be exactly {sorted(R1_WORKFLOWS)}, found {sorted(workflows)}")
    for file, workflow in sorted(workflows.items()):
        if not isinstance(workflow, Mapping):
            found.append(f"{file}: not a workflow mapping")
            continue
        on = triggers(workflow)
        if "schedule" in on:
            found.append(f"{file}: schedule trigger")
        for event in sorted(set(on) - ALLOWED_EVENTS - {"schedule"}):
            found.append(f"{file}: trigger {event!r} is not allowed")
        if "push" in on:
            push = on["push"] or {}
            if push.get("branches") != ["main"] or set(push) - {"branches", "paths"}:
                found.append(f"{file}: push must target only the main branch")
        if "pull_request" in on and not (on["pull_request"] or {}).get("paths"):
            found.append(f"{file}: pull_request without a paths filter")
        concurrency = workflow.get("concurrency")
        if not (
            isinstance(concurrency, Mapping)
            and concurrency.get("group")
            and concurrency.get("cancel-in-progress") is True
        ):
            found.append(f"{file}: concurrency with cancel-in-progress: true is required")

        jobs = workflow.get("jobs") or {}
        expected = R1_WORKFLOWS.get(file)
        if expected and (workflow.get("name"), sorted(jobs)) != (
            expected[0],
            [expected[1]],
        ):
            found.append(f"{file}: must be workflow {expected[0]!r} with one job {expected[1]!r}")
        for job_id, job in sorted(jobs.items()):
            found.extend(_job_violations(f"{file}:{job_id}", job or {}))

    # A runner has Docker; the suite stays off it only when told to.
    fast = ((workflows.get("ci.yml") or {}).get("jobs") or {}).get("fast") or {}
    if (fast.get("env") or {}).get(NO_DOCKER_ENV) != "1":
        found.append(f"ci.yml:fast: env {NO_DOCKER_ENV}: '1' is required (no Docker in Actions)")
    pages = triggers(workflows.get("pages.yml") or {})
    if "pull_request" in pages or (pages.get("push") or {}).get("paths") != PAGES_PATHS:
        found.append(f"pages.yml: runs only on push to main with paths {PAGES_PATHS}")
    deploy = ((workflows.get("pages.yml") or {}).get("jobs") or {}).get("deploy") or {}
    if deploy.get("if") != PAGES_LAUNCH_CONDITION:
        found.append(f"pages.yml:deploy: launch condition must be {PAGES_LAUNCH_CONDITION}")
    return found


def uncovered_entries(workflow: Mapping[str, Any], root: Path = ROOT) -> list[str]:
    """Tracked top-level entries no ci pull_request ``paths`` pattern matches.

    `ci / fast` is the required check: a PR whose files all miss the filter
    would wait forever on a check that never runs. Exports without a repository
    use the filesystem instead.
    """
    paths = set((triggers(workflow).get("pull_request") or {}).get("paths") or [])
    if any((directory / ".git").exists() for directory in (root, *root.parents)):
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
        ).stdout
        entries = {}
        for path in os.fsdecode(tracked).split("\0"):
            if path:
                entry, separator, _ = path.partition("/")
                entries[entry] = (
                    bool(separator) or (root / entry).is_dir() or entries.get(entry, False)
                )
    else:
        entries = {
            entry: (root / entry).is_dir()
            for entry in os.listdir(root)
            if entry not in UNTRACKED and not entry.endswith(".egg-info")
        }
    missing = []
    for entry, is_directory in sorted(entries.items()):
        covered = f"{entry}/**" in paths if is_directory else "*" in paths or entry in paths
        if not covered and "**" not in paths:
            missing.append(entry)
    return missing


def _is_nested_in_another_repository(root: Path) -> bool:
    # In Agent OS the gym is a module of a larger repository whose other
    # top-level entries are not this CI's concern.
    return not (root / ".git").exists() and any((p / ".git").exists() for p in root.parents)


def test_shipped_workflows_meet_the_budget():
    assert policy_violations(load_workflows()) == []


def test_ci_paths_cover_every_top_level_entry():
    if _is_nested_in_another_repository(ROOT):
        pytest.skip("not the repository root: the gym is still a module of Agent OS")
    assert uncovered_entries(load_workflows()["ci.yml"]) == []


def test_uncovered_entry_is_reported_without_repository(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "site").mkdir()
    (root / "README.md").write_text("x", encoding="utf-8")
    workflow = {"on": {"pull_request": {"paths": ["lib/**"]}}}
    assert uncovered_entries(workflow, root) == ["README.md", "docs"]
    workflow = {"on": {"pull_request": {"paths": ["*", "lib/**", "docs/**"]}}}
    assert uncovered_entries(workflow, root) == []


@pytest.fixture
def fixture_checkout(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "lib").mkdir()
    (root / "lib" / "example.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "lib"], cwd=root, check=True)
    return root


def test_untracked_top_level_directory_is_ignored(fixture_checkout):
    root = fixture_checkout
    (root / ".git-hooks").mkdir()
    (root / ".git-hooks" / "pre-push").write_text("", encoding="utf-8")
    workflow = {"on": {"pull_request": {"paths": ["lib/**"]}}}
    assert uncovered_entries(workflow, root) == []


@pytest.mark.parametrize("entry", ["docs", "site", ".git-hooks", "example.egg-info"])
def test_uncovered_tracked_directory_is_reported(fixture_checkout, entry):
    root = fixture_checkout
    (root / entry).mkdir()
    (root / entry / "example.txt").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "--force", entry], cwd=root, check=True)
    workflow = {"on": {"pull_request": {"paths": ["lib/**"]}}}
    assert uncovered_entries(workflow, root) == [entry]


def test_uncovered_tracked_file_is_reported(fixture_checkout):
    root = fixture_checkout
    (root / "README.md").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    workflow = {"on": {"pull_request": {"paths": ["lib/**"]}}}
    assert uncovered_entries(workflow, root) == ["README.md"]


def _ci(workflows):
    return workflows["ci.yml"]


def _fast(workflows):
    return workflows["ci.yml"]["jobs"]["fast"]


def _on(workflows, file="ci.yml"):
    workflow = workflows[file]
    return workflow["on"] if "on" in workflow else workflow[True]


def _set_python(workflows, version):
    for step in _fast(workflows)["steps"]:
        if str(step.get("uses", "")).startswith("actions/setup-python@"):
            step["with"]["python-version"] = version


VIOLATIONS = {
    "macos-runner": (
        lambda w: _fast(w).update({"runs-on": "macos-latest"}),
        "Linux runner",
    ),
    "windows-runner": (
        lambda w: _fast(w).update({"runs-on": "windows-latest"}),
        "Linux runner",
    ),
    "larger-runner": (
        lambda w: _fast(w).update({"runs-on": "ubuntu-latest-8-cores"}),
        "Linux runner",
    ),
    "runner-list": (
        lambda w: _fast(w).update({"runs-on": ["ubuntu-latest"]}),
        "Linux runner",
    ),
    "matrix": (
        lambda w: _fast(w).update({"strategy": {"matrix": {"os": ["ubuntu-latest"]}}}),
        "matrix",
    ),
    "python-list": (lambda w: _set_python(w, ["3.11", "3.12"]), "more than one Python"),
    "python-multiline": (
        lambda w: _set_python(w, "3.11\n3.12"),
        "more than one Python",
    ),
    "second-setup-python": (
        lambda w: _fast(w)["steps"].append(
            {"uses": "actions/setup-python@v5", "with": {"python-version": "3.13"}}
        ),
        "more than one Python",
    ),
    "push-other-branch": (
        lambda w: _on(w)["push"].update({"branches": ["main", "release"]}),
        "main branch",
    ),
    "push-any-branch": (lambda w: _on(w)["push"].pop("branches"), "main branch"),
    "push-tags": (lambda w: _on(w)["push"].update({"tags": ["v*"]}), "main branch"),
    "no-concurrency": (lambda w: _ci(w).pop("concurrency"), "cancel-in-progress"),
    "no-cancel": (
        lambda w: _ci(w)["concurrency"].update({"cancel-in-progress": False}),
        "cancel-in-progress",
    ),
    "no-paths": (lambda w: _on(w)["pull_request"].pop("paths"), "paths filter"),
    "no-timeout": (lambda w: _fast(w).pop("timeout-minutes"), "timeout-minutes"),
    "long-timeout": (
        lambda w: _fast(w).update({"timeout-minutes": 6}),
        "timeout-minutes",
    ),
    "pages-timeout": (
        lambda w: w["pages.yml"]["jobs"]["deploy"].update({"timeout-minutes": 30}),
        "timeout-minutes",
    ),
    "schedule": (
        lambda w: _on(w).update({"schedule": [{"cron": "0 3 * * *"}]}),
        "schedule",
    ),
    "pages-schedule": (
        lambda w: _on(w, "pages.yml").update({"schedule": [{"cron": "0 3 * * *"}]}),
        "schedule",
    ),
    "pull-request-target": (
        lambda w: _on(w).update({"pull_request_target": None}),
        "not allowed",
    ),
    "extra-workflow": (
        lambda w: w.update({"codeql.yml": copy.deepcopy(w["ci.yml"])}),
        "exactly",
    ),
    "extra-job": (
        lambda w: _ci(w)["jobs"].update({"lint": copy.deepcopy(_fast(w))}),
        "one job",
    ),
    "eval-job": (
        lambda w: _ci(w)["jobs"].update(
            {"eval": {**copy.deepcopy(_fast(w)), "steps": [{"run": "true"}]}}
        ),
        "eval, benchmark",
    ),
    "benchmark-step": (
        lambda w: _fast(w)["steps"].append({"name": "Benchmark harnesses", "run": "true"}),
        "eval, benchmark",
    ),
    "suite-run": (
        lambda w: _fast(w)["steps"].append({"run": "paddock run --suite review"}),
        "runs a suite",
    ),
    "docker-run": (
        lambda w: _fast(w)["steps"].append({"run": "docker run --rm img true"}),
        "runs a suite",
    ),
    "docker-visible": (lambda w: _fast(w).pop("env"), NO_DOCKER_ENV),
    "container-job": (
        lambda w: _fast(w).update({"container": {"image": "python:3.12"}}),
        "containment",
    ),
    "docker-action": (
        lambda w: _fast(w)["steps"].append({"uses": "docker://alpine:3"}),
        "docker://",
    ),
    "live-model": (
        lambda w: _fast(w)["steps"].append(
            {"run": "true", "env": {"KEY": "${{ secrets.PROVIDER_KEY }}"}}
        ),
        "provider secret",
    ),
    "api-key-env": (
        lambda w: _fast(w)["steps"].append({"run": 'test -n "$ANTHROPIC_API_KEY"'}),
        "paid model",
    ),
    "pages-on-pull-request": (
        lambda w: _on(w, "pages.yml").update({"pull_request": {"paths": ["results/**"]}}),
        "only on push to main",
    ),
    "pages-wider-paths": (
        lambda w: _on(w, "pages.yml")["push"].update({"paths": ["**"]}),
        "only on push to main",
    ),
    "pages-no-launch-switch": (
        lambda w: w["pages.yml"]["jobs"]["deploy"].pop("if"),
        "launch condition",
    ),
    "pages-launch-switch-bypassed": (
        lambda w: w["pages.yml"]["jobs"]["deploy"].update({"if": "${{ true }}"}),
        "launch condition",
    ),
    "pages-manual-launch-bypass": (
        lambda w: w["pages.yml"]["jobs"]["deploy"].update(
            {
                "if": "${{ github.event_name == 'workflow_dispatch' || vars.PADDOCK_SITE_LIVE == 'true' }}"
            }
        ),
        "launch condition",
    ),
}


@pytest.mark.parametrize("name", sorted(VIOLATIONS))
def test_each_violation_fails_the_policy(name):
    mutate, expected = VIOLATIONS[name]
    workflows = copy.deepcopy(load_workflows())
    mutate(workflows)
    found = policy_violations(workflows)
    assert any(expected in violation for violation in found), found


def test_the_github_token_is_not_a_provider_secret():
    workflows = copy.deepcopy(load_workflows())
    _fast(workflows)["steps"].append({"run": "true", "env": {"T": "${{ secrets.GITHUB_TOKEN }}"}})
    assert policy_violations(workflows) == []


def test_the_no_docker_switch_hides_the_engine(monkeypatch):
    import shutil

    from gym import hidden_tests
    from gym.grader import GraderError

    monkeypatch.delenv("GYM_REQUIRE_REAL_ENGINE", raising=False)
    hide_docker(monkeypatch)
    assert shutil.which("docker") is None
    assert shutil.which("/usr/bin/docker") is None
    with pytest.raises(pytest.skip.Exception, match="Docker executable unavailable"):
        require_docker_backend()
    with pytest.raises(GraderError, match="Docker"):
        hidden_tests._command(Path("."), ["python3", "-c", "pass"])
