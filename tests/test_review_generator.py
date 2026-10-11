import json
import subprocess
from pathlib import Path

import pytest
import review_task_from_pr as gen
from gym.schema import load_task
from gym.grader import grade, Trajectory
from gym import outcomes

REPO = "example-org/example-repo"


@pytest.fixture
def recorded_gh(monkeypatch):
    data = json.loads((Path(__file__).parent / "fixtures/review-generator/gh.json").read_text())
    calls = []

    def gh_json(endpoint, *, paginate=False):
        calls.append(["gh", "api", endpoint, *(["--paginate", "--slurp"] if paginate else [])])
        result = data[endpoint]
        return [item for page in result for item in page] if paginate else result

    monkeypatch.setattr(gen, "gh_json", gh_json)
    return data, calls


def test_generator_recorded_gh_no_network(tmp_path, recorded_gh):
    source = gen.fetch(123, REPO)
    paths = gen.scaffold(source, tmp_path)
    assert len(source["later_commits"]) == 1
    assert all("--paginate" in c for c in recorded_gh[1] if c[2].endswith(("reviews", "commits")))
    for directory in paths:
        task = load_task(directory)
        gt = json.loads((directory / "expected/ground_truth.json").read_text())
        assert gt["status"] == "unverified"
        assert gt["findings"][0]["file"] == {
            "status": "unverified",
            "value": "src/follow-up-merge-agent.mjs",
        }
        assert gt["findings"][0]["expected_category"] == {"status": "unverified", "value": "auth"}
        assert all(
            value["status"] == "unverified"
            for key, value in gt["findings"][0].items()
            if key != "status"
        )
        assert gen.MARKER in json.dumps(gt)
        assert "drop(commit)" in (directory / "fixture/change.diff").read_text()
        assert "UNVERIFIED" in (directory / "expected/reference-fix.diff").read_text()
        manifest = directory / "fixture/staged-manifest.json"
        if task.suite == "fix":
            assert json.loads(manifest.read_text()) == {"editable": [], "files": []}
        else:
            assert not manifest.exists()
        result = {"findings": []} if task.suite == "review" else {"summary": "fixed"}
        cell = grade(task, Trajectory(terminal_status="succeeded", submitted_result=result))
        assert cell.outcome == outcomes.FAILED
        assert "SCAFFOLD" in cell.reason
        assert "expected/" not in (directory / "prompt.md").read_text()
    with pytest.raises(FileExistsError):
        gen.scaffold(source, tmp_path)


@pytest.mark.parametrize("change", ["unmerged", "no_review", "rewritten", "no_later"])
def test_source_history_fail_closed(recorded_gh, change):
    data, _ = recorded_gh
    root = "repos/" + REPO + "/pulls/123"
    if change == "unmerged":
        data[root]["merged_at"] = None
    elif change == "no_review":
        data[root + "/reviews"] = [[]]
    elif change == "rewritten":
        data[root + "/reviews"][0][0]["commit_id"] = "unknown"
    else:
        data[root + "/commits"] = [data[root + "/commits"][0]]
    with pytest.raises(ValueError):
        gen.fetch(123, REPO)


def test_cli(recorded_gh, tmp_path):
    assert gen.main(["--repo", REPO, "--pr", "123", "--out-dir", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "body",
    [
        "## Blocking findings\n- Title: Bug\n  File: x.py\n  Lines: 1-2\n  Problem: Lost\n## Verdict\nFAIL",
        "## Blocking findings\n- **Bug**\n  - **File:** x.py\n  - **Lines**: 1-2\n  - **Problem:**\n    Lost\n## Verdict\nFAIL",
    ],
)
def test_legacy_and_nested_bold_cards(body):
    found = gen.parse_findings(body)
    assert len(found) == 1
    assert found[0]["file"] == "x.py"
    assert "Lost" in found[0]["problem"]


def test_unlocated_finding_stays_unlocated():
    assert gen.parse_findings("## Blocking findings\nSomething is wrong")[0] == {
        "problem": "Something is wrong"
    }


def test_human_review_is_not_an_adversarial_source(recorded_gh):
    data, _ = recorded_gh
    data["repos/" + REPO + "/pulls/123/reviews"][0][0]["user"]["login"] = "human"
    with pytest.raises(ValueError, match="no review"):
        gen.fetch(123, REPO)


def test_matcher_available_to_isolated_verifier(recorded_gh, tmp_path):
    directory = gen.scaffold(gen.fetch(123, REPO), tmp_path)[0]
    (directory / "verifier.py").write_text(
        "import gym_verifier as gv\nimport gym_review_checks as rc\n"
        'rc.record(gv, defects=[dict(file="src/store.py", line_start=10, line_end=12)], '
        'expected_category="data-loss")\ngv.passed()\n'
    )
    result = {
        "findings": [
            dict(
                file="src/store.py",
                line_start=10,
                line_end=12,
                category="data-loss",
                blocking=True,
                summary="loss",
            )
        ]
    }
    cell = grade(
        load_task(directory),
        Trajectory(
            terminal_status="succeeded",
            submitted_result=result,
            harness_calls=[{"name": "read", "arguments": {"path": "src/store.py"}}],
        ),
    )
    assert cell.outcome == outcomes.PASSED
    assert len(cell.subchecks) == 5


@pytest.mark.parametrize("changes", [{"line_end": 9}, {"line_start": 0}, {"file": ""}])
@pytest.mark.parametrize("clean_control", [False, True])
def test_malformed_submission_grades_failed(tmp_path, changes, clean_control):
    directory = tmp_path
    (directory / "task.toml").write_text(
        'id = "malformed-review"\ntitle = "Malformed review"\nsuite = "review"\n'
        'persona = "codex"\n'
        + gen.result_schema_snippet()
        + '[verifier]\nentrypoint = "verifier.py"\n'
    )
    mode = (
        'clean_control=True, read_files=["src/store.py"]'
        if clean_control
        else 'defects=[dict(file="src/store.py", line_start=10, line_end=12)]'
    )
    (directory / "verifier.py").write_text(
        "import gym_verifier as gv\nimport gym_review_checks as rc\n"
        f"rc.record(gv, {mode})\ngv.passed()\n"
    )
    result = {
        "findings": [
            {
                "file": "src/store.py",
                "line_start": 10,
                "line_end": 12,
                "category": "data-loss",
                "blocking": True,
                "summary": "loss",
                **changes,
            }
        ]
    }
    task = load_task(directory)
    from gym.schema import validate_result

    assert validate_result(task, result) is None
    cell = grade(
        task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result=result,
            harness_calls=[{"name": "read", "arguments": {"path": "src/store.py"}}],
        ),
    )
    assert cell.outcome == outcomes.FAILED
    check = next(c for c in cell.subchecks if c["name"] == "well_formed_findings")
    assert not check["ok"] and check["required"]
    if clean_control:
        assert not next(c for c in cell.subchecks if c["name"] == "no_blocking")["ok"]


def test_recorded_body_uses_canonical_review_fixture(recorded_gh):
    original = (
        Path(__file__).resolve().parents[3]
        / "projects/merge-agent-rescue/fixtures/review-bodies/request-changes-with-blockers-auth.md"
    )
    source = gen.fetch(123, REPO)
    assert source["review"]["body"] == original.read_text()
    assert len(gen.parse_findings(source["review"]["body"])) == 1


def test_problem_stops_at_other_card_fields():
    body = """## Blocking Issues:
- **Bug**
  - **Category:** auth
  - **File:** `x.py`
  - **Lines:** `1-2`
  - **Problem:** Lost
    Wrapped explanation.
  - **Why it matters:** Bad
    Not problem text.
  - **Recommended fix:** Repair
## Non-blocking issues
- **Other bug**
  - **Problem:** Outside the blocking section.
"""
    assert gen.parse_findings(body) == [
        {
            "title": "Bug",
            "expected_category": "auth",
            "file": "x.py",
            "lines": "1-2",
            "problem": "Lost\nWrapped explanation.",
        }
    ]


@pytest.mark.parametrize(
    "case", ["merge", "commits_cap", "reviewed_files_cap", "remediation_files_cap"]
)
def test_history_warnings_in_fixture_and_provenance(recorded_gh, tmp_path, case):
    data, _ = recorded_gh
    root = "repos/" + REPO
    commits = data[root + "/pulls/123/commits"]
    if case == "merge":
        commits[1][0]["parents"].append({"sha": "upstream"})
        expected = "merge commits"
    elif case == "commits_cap":
        commits[1].extend({"sha": f"extra-{i}", "parents": [{"sha": "parent"}]} for i in range(248))
        data[root + "/compare/" + "b" * 40 + "...extra-247"] = data[
            root + "/compare/" + "b" * 40 + "..." + "c" * 40
        ]
        expected = "250"
    else:
        start, end = ("a", "b") if case == "reviewed_files_cap" else ("b", "c")
        comparison = data[root + "/compare/" + start * 40 + "..." + end * 40]
        comparison["files"] *= 300
        expected = "300"
    source = gen.fetch(123, REPO)
    assert any(expected in warning for warning in source["warnings"])
    for directory in gen.scaffold(source, tmp_path):
        gt = json.loads((directory / "expected/ground_truth.json").read_text())
        assert gt["provenance"]["warnings"] == source["warnings"]
        assert "WARNING" in (directory / "fixture/README.md").read_text()
        assert expected in (directory / "fixture/README.md").read_text()


def test_default_output_is_outside_discovered_tasks(recorded_gh, tmp_path, monkeypatch):
    # Relocate the module so CLI default writes only to a disposable directory.
    monkeypatch.setattr(gen, "__file__", str(tmp_path / "scripts/review_task_from_pr.py"))
    assert gen.main(["--repo", REPO, "--pr", "123"]) == 0
    assert (tmp_path / "draft-tasks/review-pr-123/task.toml").exists()
    assert (tmp_path / "draft-tasks/fix-pr-123/task.toml").exists()
    assert not (tmp_path / "tasks").exists()


def test_none_with_trailing_prose_is_not_a_finding():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "projects/merge-agent-rescue/fixtures/review-bodies/none-with-trailing-prose.md"
    )
    assert gen.parse_findings(fixture.read_text()) == []


def test_failed_scaffold_cleans_only_its_own_directories(recorded_gh, tmp_path, monkeypatch):
    source = gen.fetch(123, REPO)
    original = Path.write_text
    sentinel = tmp_path / "unrelated.txt"
    sentinel.write_text("preserve")

    def write(path, *args, **kwargs):
        if path.name == "task.toml" and path.parent.name == "fix-pr-123":
            raise OSError("fixture disk full")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "write_text", write)
        with pytest.raises(OSError, match="disk full"):
            gen.scaffold(source, tmp_path)
    assert sentinel.read_text() == "preserve"
    assert not (tmp_path / "review-pr-123").exists()
    assert not (tmp_path / "fix-pr-123").exists()
    assert len(gen.scaffold(source, tmp_path)) == 2


@pytest.mark.parametrize("stderr,expected_attempts", [("HTTP 503", 3), ("permission denied", 1)])
def test_gh_errors_are_bounded_and_classified(monkeypatch, stderr, expected_attempts):
    calls, delays = [], []

    def run(command, **kwargs):
        assert kwargs["timeout"] == 30
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, stderr=stderr)

    monkeypatch.setattr(
        gen,
        "subprocess",
        type(
            "Subprocess",
            (),
            {
                "run": staticmethod(run),
                "CalledProcessError": subprocess.CalledProcessError,
                "TimeoutExpired": subprocess.TimeoutExpired,
            },
        ),
    )
    monkeypatch.setattr(gen.time, "sleep", delays.append)
    with pytest.raises(subprocess.CalledProcessError):
        gen.gh_json("fixture")
    assert len(calls) == expected_attempts
    assert len(delays) == expected_attempts - 1
