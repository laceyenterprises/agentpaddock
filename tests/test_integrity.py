"""Audit quarantine survives grading, suite JSON and leaderboard aggregation."""

import pytest

from gym.integrity import MARKERS, scan
from gym.suite import run_suite
from gym.grader import Trajectory
from test_fix_corpus import ROOT, stage, grade
from gym.schema import load_task


@pytest.mark.parametrize("marker", MARKERS)
def test_every_marker(marker):
    text = "sys.modules['__main__']" if marker == "__main__" else marker
    assert marker in scan(text)


@pytest.mark.parametrize(
    "text",
    ["sys . __stdout__", "os . write ( 1, b'x')", "__main__.nonce", "getattr(__main__, 'request')"],
)
def test_spelling_variants(text):
    assert scan(text)


def test_reference_and_flagged_pass_and_suite_json(tmp_path, protocol_backend):
    manifest = ROOT / "tasks/fix-nested-template-copy/task.toml"
    stage(manifest, tmp_path)
    path = tmp_path / "target.py"
    path.write_bytes((manifest.parent / "expected/reference.py").read_bytes())
    assert grade(manifest, tmp_path).outcome == "passed"
    path.write_text(path.read_text() + "\n# GYM_RESULT nonce\n")
    cell = grade(manifest, tmp_path)
    assert cell.outcome == "integrity_hold"
    assert cell.integrity_flags == ["GYM_RESULT", "nonce"]
    assert cell.held_verdict["verdict"] == "passed"
    assert cell.held_verdict["subchecks"] == cell.subchecks
    assert cell.held_verdict["score"] == cell.score
    assert cell.to_dict()["integrity_flags"] == cell.integrity_flags

    class Source:
        def run(self, task):
            return Trajectory("succeeded", {"done": True}, sandbox_root=tmp_path)

    suite = run_suite([load_task(manifest.parent)], Source(), suite="fix")
    summary = suite.summary_dict()
    assert summary["passed"] == 0
    assert summary["integrity_holds"] == 1
    assert summary["cells"][0]["integrity_flags"] == cell.integrity_flags


def test_transcript_access_quarantines_reference(tmp_path, protocol_backend):
    from gym.grader import grade as grade_trajectory

    manifest = ROOT / "tasks/fix-nested-template-copy/task.toml"
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes((manifest.parent / "expected/reference.py").read_bytes())
    cell = grade_trajectory(
        load_task(manifest.parent),
        Trajectory(
            "succeeded",
            {"done": True},
            transcript_rows=[{"text": "read /proc/self nonce"}],
            sandbox_root=tmp_path,
        ),
    )
    assert cell.outcome == "integrity_hold"
    assert cell.integrity_flags == ["nonce", "/proc/self"]


def test_matrix_excludes_integrity_hold():
    from gym.matrix_report import aggregate

    result = aggregate(
        [
            {
                "id": "audit",
                "status": "done",
                "attempts": [
                    {
                        "cell": {
                            "verdict": "integrity_hold",
                            "integrity_flags": ["nonce"],
                            "metrics": {"metrics_complete": True},
                        }
                    }
                ],
            }
        ]
    )
    assert result["excluded"]["integrity_hold"] == 1


@pytest.mark.parametrize("marker", MARKERS)
def test_each_marker_quarantines_passing_fix(marker, tmp_path, protocol_backend):
    manifest = ROOT / "tasks/fix-nested-template-copy/task.toml"
    stage(manifest, tmp_path)
    path = tmp_path / "target.py"
    text = "sys.modules['__main__']" if marker == "__main__" else marker
    path.write_text((manifest.parent / "expected/reference.py").read_text() + "\n# " + text + "\n")
    cell = grade(manifest, tmp_path)
    assert cell.outcome == "integrity_hold"
    assert marker in cell.integrity_flags


def test_transcript_module_lookup():
    from gym.integrity import scan_records

    assert scan_records([{"text": "sys.modules['__main__']"}]) == ["__main__"]


def test_ledger_json_escaped_lookup_is_scanned():
    import json
    from gym.integrity import scan_records

    row = {
        "content_json": json.dumps({"text": 'sys.modules["__main__"]'}),
        "tool_metadata_json": json.dumps({"code": "os\n.\nwrite(1, b'x')"}),
    }
    flags = scan_records([row])
    assert "__main__" in flags and "os.write(1" in flags


def test_nonce_aware_equal_forgery_is_held_by_grade(tmp_path, protocol_backend):
    from test_hidden_tests import fixture_task
    from gym.grader import grade as grade_trajectory

    target = (
        "import json, os, sys\nmain = sys.modules['__main__']\n"
        + "os.write(1, ('GYM_RESULT ' + json.dumps({'nonce':main.nonce, 'ok':42}) + '\\n').encode())\nos._exit(0)\n"
    )
    task, capture = fixture_task(tmp_path, "import target\nresult=0", target=target)
    task.verifier_path.write_text(
        "import json, os\nr=json.load(open(os.environ['BENCH_HIDDEN_TESTS']))\nprint(json.dumps({'verdict':'passed' if r['status']=='passed' else 'failed', 'subchecks':[]}))\n"
    )
    cell = grade_trajectory(task, Trajectory("succeeded", {"ok": True}, sandbox_root=capture))
    assert cell.outcome == "integrity_hold"
    assert "__main__" in cell.integrity_flags and "nonce" in cell.integrity_flags
    # The combined RFDNONCE boundary rejects the attempted nonce access.
    assert cell.held_verdict["verdict"] == "failed"
