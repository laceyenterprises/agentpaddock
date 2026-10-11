"""HRF-03: several editable targets and agent-written regression tests.

The agent's unittest file runs four times in the hidden-case containment, with
the non-test files replaced by the original, insufficient, reference and
submitted code. The synthetic task lives in tests/fixtures/, never in tasks/.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from gym import fix_audit, grader, hidden_tests, outcomes
from gym.grader import GraderError, Trajectory
from gym.integrity import REGRESSION_TEST_MARKERS, scan_regression_test
from gym.schema import FIX_FAMILY_SUITES, SUITES, TaskSchemaError, load_task, parse_task_dict

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests/fixtures/hard-fix-regression-probe"
SUBCHECKS = [
    "regression_test_fails_on_original",
    "regression_test_fails_on_insufficient",
    "regression_test_passes_on_reference",
    "regression_test_passes_on_submission",
]

GOOD_TEST = """import unittest
from target import dedupe


class Regression(unittest.TestCase):
    def test_case_and_whitespace_are_one_name(self):
        self.assertEqual(dedupe(["a", " A", "b"]), ["a", "b"])
"""
# Detects only that the code changed: the insufficient variant passes it too.
CHANGED_TEST = """import unittest
from target import dedupe


class Regression(unittest.TestCase):
    def test_something_changed(self):
        self.assertEqual(dedupe([" a", "a "]), [" a"])
"""
# Keyed to a helper only this submission has: the reference fails it.
KEYED_TEST = """import unittest
from rules import canonical


class Regression(unittest.TestCase):
    def test_helper(self):
        self.assertEqual(canonical(" A"), "a")
"""
SNIFFING_TEST = """import inspect
import unittest
import rules


class Regression(unittest.TestCase):
    def test_source(self):
        self.assertIn("casefold", inspect.getsource(rules))
"""
# A correct fix that differs from the reference, with a helper of its own.
ALTERNATIVE_RULES = """def canonical(name):
    return name.strip().lower()


def normalize(name):
    \"\"\"Return the dedupe key: surrounding whitespace and case are ignored.\"\"\"
    return canonical(name)
"""


def probe():
    return load_task(PROBE)


def stage(directory, test=None, *, variant="reference", rules=None):
    """Stage the fixture, the fixed code and the agent's test into a workspace."""
    for name in ("target.py", "rules.py", "test_regression.py", "staged-manifest.json"):
        shutil.copyfile(PROBE / "fixture" / name, directory / name)
    if variant is not None:
        for name in ("target.py", "rules.py"):
            shutil.copyfile(PROBE / "expected" / variant / name, directory / name)
    if rules is not None:
        (directory / "rules.py").write_text(rules)
    if test is not None:
        (directory / "test_regression.py").write_text(test)
    return directory


def run_test(task, workspace, **kwargs):
    kwargs.setdefault("hidden_passed", True)
    return hidden_tests.run_regression_test(task, workspace, **kwargs)


def outcomes_of(result):
    return {run["variant"]: run["outcome"] for run in result["runs"]}


def checks(cell):
    return {item["name"]: item["ok"] for item in cell.subchecks}


class Source:
    def __init__(self, sandbox_root):
        self.sandbox_root = sandbox_root

    def run(self, task):
        return Trajectory(
            "succeeded", {"done": True}, sandbox_root=self.sandbox_root, launch_request_id="lr"
        )


def copy_probe(tmp_path):
    root = tmp_path / "hard-fix-regression-probe"
    shutil.copytree(PROBE, root)
    return root


# --- schema --------------------------------------------------------------------


def _task(suite="hard-fix", **regression):
    data = {
        "id": "regression-probe",
        "suite": suite,
        "result_schema": {"done": {"type": "boolean"}},
        "verifier": {},
        "capture": {"files": ["target.py", "test_regression.py"]},
        "hidden_tests": {"entrypoint": "hidden/cases.json", "expected_tests": 1},
        "regression_test": {"path": "test_regression.py", **regression},
    }
    return data


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_fix_family_may_declare_regression_test(suite):
    task = parse_task_dict(_task(suite, timeout_seconds=60))
    assert task.regression_test_path == "test_regression.py"
    assert task.regression_test_timeout_seconds == 60
    assert parse_task_dict(_task(suite)).regression_test_timeout_seconds == 60


@pytest.mark.parametrize("suite", sorted(SUITES - FIX_FAMILY_SUITES))
def test_other_suites_may_not_declare_regression_test(suite):
    data = _task(suite)
    del data["hidden_tests"]
    with pytest.raises(TaskSchemaError, match="regression_test requires suite"):
        parse_task_dict(data)


def test_regression_test_requires_hidden_tests():
    data = _task()
    del data["hidden_tests"]
    with pytest.raises(TaskSchemaError, match=r"requires \[hidden_tests\]"):
        parse_task_dict(data)


@pytest.mark.parametrize(
    "regression",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"timeout_seconds": True},
        {"timeout_seconds": "20"},
        {"path": "test_other.py"},
        {"path": "sub/test_regression.py"},
        {"path": "regression.py"},
        {"path": "../test_regression.py"},
        {"unknown": 1},
    ],
)
def test_regression_schema_bounds(regression):
    with pytest.raises(TaskSchemaError):
        parse_task_dict(_task(**regression))


def test_rfd_fix_tasks_declare_no_regression_test():
    for path in sorted((ROOT / "tasks").glob("fix-*/task.toml")):
        assert load_task(path.parent).regression_test_path is None


# --- several editable targets --------------------------------------------------


def test_probe_edits_three_files_with_placeholders():
    task = probe()
    snapshot = grader.snapshot_verifiers([task])
    editable = fix_audit.editable_files(task, snapshot)
    assert editable == ["target.py", "rules.py", "test_regression.py"]
    for name in editable:
        assert snapshot[PROBE / "fixture" / name] == (PROBE / "fixture" / name).read_bytes()


def _rewrite_manifest(root, editable):
    path = root / "fixture/staged-manifest.json"
    path.write_text(json.dumps({"editable": editable, "files": editable}))


@pytest.mark.parametrize(
    "editable,match",
    [
        (["target.py", "rules.py", "test_regression.py", "a.py", "b.py"], "at most 4"),
        (["target.py", "target.py"], "distinct"),
        ("target.py", "distinct"),
        (["target.py", "elsewhere.py"], "declared for capture"),
        (["target.py", "rules.py"], "regression_test.path must be an editable"),
    ],
)
def test_editable_list_refused(tmp_path, editable, match):
    root = copy_probe(tmp_path)
    _rewrite_manifest(root, editable)
    with pytest.raises(GraderError, match=match):
        fix_audit.editable_files(load_task(root), None)


def test_up_to_four_editables_accepted(tmp_path):
    root = copy_probe(tmp_path)
    toml = root / "task.toml"
    toml.write_text(
        toml.read_text().replace('"staged-manifest.json"]', '"extra.py", "staged-manifest.json"]')
    )
    (root / "fixture/extra.py").write_text("")
    _rewrite_manifest(root, ["target.py", "rules.py", "extra.py", "test_regression.py"])
    assert len(fix_audit.editable_files(load_task(root), None)) == 4


def test_missing_placeholder_refused(tmp_path):
    root = copy_probe(tmp_path)
    (root / "fixture/test_regression.py").unlink()
    task = load_task(root)
    with pytest.raises(GraderError, match="needs a fixture placeholder"):
        fix_audit.editable_files(task, grader.snapshot_verifiers([task]))


def test_audit_covers_every_editable(tmp_path):
    workspace = stage(tmp_path, GOOD_TEST)
    payload = fix_audit.submission(probe(), workspace, grader.snapshot_verifiers([probe()]))
    assert [entry["file"] for entry in payload["files"]] == [
        "target.py",
        "rules.py",
        "test_regression.py",
    ]
    by_file = {entry["file"]: entry for entry in payload["files"]}
    assert "+        key = normalize(name)" in by_file["target.py"]["diff"]
    assert "+    return name.strip().casefold()" in by_file["rules.py"]["diff"]
    assert by_file["test_regression.py"]["submitted"] == GOOD_TEST


# --- variants -------------------------------------------------------------------


def _variant_task_dirs():
    roots = sorted((ROOT / "tasks").glob("hard-fix-*"))
    roots += sorted((ROOT / "tests/fixtures").glob("hard-fix-*"))
    return [root for root in roots if (root / "hidden/variants").is_dir()]


@pytest.mark.parametrize("root", _variant_task_dirs(), ids=lambda path: path.name)
def test_hidden_variants_equal_expected_copies(root):
    variants = root / "hidden/variants"
    shipped = sorted(path for path in variants.rglob("*") if path.is_file())
    assert shipped
    for path in shipped:
        relative = path.relative_to(variants)
        assert (root / "expected" / relative).read_bytes() == path.read_bytes(), relative
    assert {"reference", "insufficient"} <= {path.name for path in variants.iterdir()}


def test_every_hard_fix_task_ships_required_variants():
    for root in sorted((ROOT / "tasks").glob("hard-fix-*")):
        for name in ("reference", "insufficient"):
            assert (root / "hidden/variants" / name).is_dir(), (root.name, name)


def test_variant_must_ship_every_non_test_editable(tmp_path):
    root = copy_probe(tmp_path)
    (root / "hidden/variants/insufficient/rules.py").unlink()
    task = load_task(root)
    (tmp_path / "ws").mkdir()
    with pytest.raises(GraderError, match="hidden/variants/insufficient must ship exactly"):
        run_test(task, stage(tmp_path / "ws"))


def test_variant_may_not_ship_extra_files(tmp_path):
    root = copy_probe(tmp_path)
    (root / "hidden/variants/reference/test_regression.py").write_text("")
    task = load_task(root)
    (tmp_path / "ws").mkdir()
    with pytest.raises(GraderError, match="hidden/variants/reference must ship exactly"):
        run_test(task, stage(tmp_path / "ws"))


# --- four variants through the synthetic protocol seam -----------------------------


def test_correct_test_passes_all_four(tmp_path, protocol_backend):
    result = run_test(probe(), stage(tmp_path, GOOD_TEST, rules=ALTERNATIVE_RULES))
    assert result["status"] == "completed"
    assert outcomes_of(result) == {
        "original": "failed",
        "insufficient": "failed",
        "reference": "passed",
        "submission": "passed",
    }
    counts = {run["variant"]: run["counts"] for run in result["runs"]}
    assert counts["reference"] == {
        "run": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "expected_failures": 0,
        "unexpected_successes": 0,
    }
    assert counts["original"]["failures"] == 1
    assert all(check["ok"] for check in hidden_tests.regression_subchecks(result))


def test_source_changed_test_fails_on_insufficient(tmp_path, protocol_backend):
    result = run_test(probe(), stage(tmp_path, CHANGED_TEST))
    assert outcomes_of(result)["insufficient"] == "passed"
    oks = {c["name"]: c["ok"] for c in hidden_tests.regression_subchecks(result)}
    assert oks == dict.fromkeys(SUBCHECKS, True) | {"regression_test_fails_on_insufficient": False}


def test_submission_keyed_test_fails_on_reference(tmp_path, protocol_backend):
    result = run_test(probe(), stage(tmp_path, KEYED_TEST, rules=ALTERNATIVE_RULES))
    assert outcomes_of(result) == {
        "original": "failed",
        "insufficient": "failed",
        "reference": "failed",
        "submission": "passed",
    }
    oks = {c["name"]: c["ok"] for c in hidden_tests.regression_subchecks(result)}
    assert oks == dict.fromkeys(SUBCHECKS, True) | {"regression_test_passes_on_reference": False}


@pytest.mark.parametrize(
    "test,outcome",
    [
        (None, "inconclusive"),  # the fixture placeholder runs no test
        (
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.skipTest('later')\n",
            "inconclusive",
        ),
        (
            "import unittest\n\nclass T(unittest.TestCase):\n    @unittest.expectedFailure\n    def test_a(self):\n        raise AssertionError\n",
            "inconclusive",
        ),
        ("import unittest\nraise SystemExit(0)\n", "error"),
        ("import unittest\nimport no_such_module\n", "failed"),
    ],
    ids=["no-tests", "skip", "expected-failure", "exit-at-import", "import-error"],
)
def test_pass_requires_a_clean_run(tmp_path, protocol_backend, test, outcome):
    result = run_test(probe(), stage(tmp_path, test))
    assert outcomes_of(result)["reference"] == outcome
    assert not hidden_tests.regression_subchecks(result)[2]["ok"]


def test_counts_come_from_the_result_not_printed_output(tmp_path, protocol_backend):
    forged = (
        "import sys, unittest\n"
        "print('Ran 1 test\\n\\nOK')\n"
        'print(\'GYM_RESULT {"ok": {"run": 1}}\', flush=True)\n'
        "sys.stderr.write('OK\\n')\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_a(self):\n"
        "        print('OK')\n"
        "        self.fail('always')\n"
    )
    result = run_test(probe(), stage(tmp_path, forged))
    assert set(outcomes_of(result).values()) == {"failed"}


def test_hidden_failure_skips_regression_runs(tmp_path, protocol_backend):
    result = run_test(probe(), stage(tmp_path, GOOD_TEST), hidden_passed=False)
    assert result["status"] == "not_run"
    assert set(outcomes_of(result).values()) == {"not_run"}
    assert not any(check["ok"] for check in hidden_tests.regression_subchecks(result))


def test_missing_test_file_is_not_run(tmp_path, protocol_backend):
    workspace = stage(tmp_path, GOOD_TEST)
    (workspace / "test_regression.py").unlink()
    result = run_test(probe(), workspace)
    assert (result["status"], result["reason"]) == ("not_run", "test file missing")
    assert set(outcomes_of(result).values()) == {"not_run"}


def test_trusted_variants_replace_every_non_test_file(tmp_path, protocol_backend):
    # The test reads what it was given; trusted runs see only snapshotted code.
    test = (
        "import unittest\n"
        "import rules, target\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_marker(self):\n"
        "        self.assertTrue(getattr(rules, 'AGENT', False) or getattr(target, 'AGENT', False))\n"
    )
    workspace = stage(tmp_path, test)
    for name in ("rules.py", "target.py"):
        path = workspace / name
        path.write_text(path.read_text() + "\nAGENT = True\n")
    result = run_test(probe(), workspace)
    assert outcomes_of(result) == {
        "original": "failed",
        "insufficient": "failed",
        "reference": "failed",
        "submission": "passed",
    }


def test_each_run_has_its_own_nonce_and_container(tmp_path, protocol_backend, monkeypatch):
    requests = []
    original = hidden_tests._execute

    def execute(command, work, timeout, *, input_data=b""):
        if input_data:
            requests.append(json.loads(input_data))
            assert timeout == probe().regression_test_timeout_seconds
        return original(command, work, timeout, input_data=input_data)

    monkeypatch.setattr(hidden_tests, "_execute", execute)
    run_test(probe(), stage(tmp_path, GOOD_TEST))
    assert [request["call"] for request in requests] == ["test_regression"] * 4
    assert len({request["nonce"] for request in requests}) == 4


@pytest.mark.parametrize("backend", ["protocol_backend", "docker_backend"])
def test_regression_child_has_no_nonce(tmp_path, request, backend):
    request.getfixturevalue(backend)
    test = (
        "import sys, unittest\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_isolated(self):\n"
        "        self.assertFalse(hasattr(sys.modules['__main__'], 'nonce'))\n"
        "        frame = sys._getframe()\n"
        "        while frame:\n"
        "            self.assertNotIn('nonce', frame.f_globals)\n"
        "            self.assertNotIn('nonce', frame.f_locals)\n"
        "            frame = frame.f_back\n"
    )
    result = run_test(
        probe(),
        stage(tmp_path, test),
        work_root=hidden_tests.shared_work_root() if backend == "docker_backend" else None,
    )
    assert set(outcomes_of(result).values()) == {"passed"}


def test_runner_refuses_tasks_without_a_regression_test(tmp_path):
    with pytest.raises(GraderError, match="fix-family"):
        run_test(replace(probe(), regression_test_path=None), tmp_path)
    with pytest.raises(GraderError, match="fix-family"):
        run_test(replace(probe(), suite="hard-review"), tmp_path)


# --- grading ---------------------------------------------------------------------


def test_correct_test_grades_passed_with_four_subchecks(tmp_path, protocol_backend, audit_keys):
    cell = grader.grade_task(probe(), Source(stage(tmp_path, GOOD_TEST, rules=ALTERNATIVE_RULES)))
    assert cell.outcome == outcomes.PASSED, cell.reason
    assert [c["name"] for c in cell.subchecks] == [
        "edited_only_allowed_files",
        "hidden_tests_pass",
        *SUBCHECKS,
    ]
    assert all(c["ok"] and c["required"] for c in cell.subchecks)
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert [run["outcome"] for run in plain["regression_runs"]] == [
        "failed",
        "failed",
        "passed",
        "passed",
    ]
    assert {key for run in plain["regression_runs"] for key in run} == {
        "variant",
        "outcome",
        "counts",
        "elapsed_seconds",
    }
    assert [entry["file"] for entry in plain["files"]] == [
        "target.py",
        "rules.py",
        "test_regression.py",
    ]


def test_source_changed_test_grades_failed(tmp_path, protocol_backend):
    cell = grader.grade_task(probe(), Source(stage(tmp_path, CHANGED_TEST)))
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["regression_test_fails_on_insufficient"]
    assert checks(cell)["hidden_tests_pass"]
    assert "regression test on insufficient: expected failed, got passed" in cell.reason


def test_keyed_test_grades_failed(tmp_path, protocol_backend):
    cell = grader.grade_task(probe(), Source(stage(tmp_path, KEYED_TEST, rules=ALTERNATIVE_RULES)))
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["regression_test_passes_on_reference"]


def test_original_code_fails_hidden_and_skips_regression(tmp_path, protocol_backend, audit_keys):
    cell = grader.grade_task(probe(), Source(stage(tmp_path, GOOD_TEST, variant=None)))
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["hidden_tests_pass"]
    assert not any(checks(cell)[name] for name in SUBCHECKS)
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert {run["outcome"] for run in plain["regression_runs"]} == {"not_run"}


@pytest.mark.parametrize(
    "test,flag",
    [
        (SNIFFING_TEST, "regression_test:inspect"),
        (
            "import unittest\nimport target\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertIn('casefold', target.__file__)\n",
            "regression_test:__file__",
        ),
        (
            "import hashlib, unittest\nimport target\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertTrue(hashlib.sha256(b'').hexdigest())\n",
            "regression_test:hashing",
        ),
        (
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertIn('casefold', open('rules.py').read())\n",
            "regression_test:file_read",
        ),
        (
            "import os, sys, unittest\n"
            "os.write(int(sys.argv[1]), b'{}')\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n",
            "regression_test:result_channel",
        ),
    ],
    ids=["inspect", "__file__", "hashing", "source-read", "result-channel"],
)
def test_sniffing_test_is_held(tmp_path, protocol_backend, audit_keys, test, flag):
    cell = grader.grade_task(probe(), Source(stage(tmp_path, test)))
    assert cell.outcome == outcomes.INTEGRITY_HOLD
    assert flag in cell.integrity_flags
    assert set(SUBCHECKS) <= {c["name"] for c in cell.held_verdict["subchecks"]}
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain["integrity_flags"] == cell.integrity_flags


def test_verifier_may_not_emit_regression_subchecks(tmp_path, protocol_backend):
    root = copy_probe(tmp_path)
    verifier = root / "verifier.py"
    verifier.write_text(
        verifier.read_text().replace(
            "gv.passed()", 'gv.check("regression_test_fails_on_original", True)\ngv.passed()'
        )
    )
    (tmp_path / "ws").mkdir()
    with pytest.raises(GraderError, match="grader-owned"):
        grader.grade(load_task(root), Source(stage(tmp_path / "ws", GOOD_TEST)).run(None))


def test_verifier_reads_regression_result(tmp_path, protocol_backend):
    root = copy_probe(tmp_path)
    verifier = root / "verifier.py"
    verifier.write_text(
        verifier.read_text().replace(
            "gv.passed()",
            "runs = gv.regression_test()['runs']\n"
            "gv.check('saw_runs', [r['variant'] for r in runs] == "
            "['original', 'insufficient', 'reference', 'submission'], required=True)\n"
            "gv.passed()",
        )
    )
    (tmp_path / "ws").mkdir()
    cell = grader.grade(load_task(root), Source(stage(tmp_path / "ws", GOOD_TEST)).run(None))
    assert checks(cell)["saw_runs"]


def test_dispatch_error_envelope_lists_unrun_variants(audit_keys, tmp_path):
    def broken(*args, **kwargs):
        raise GraderError("synthetic verifier failure")

    import gym.grader as module

    original = module._run_verifier_out_of_band
    module._run_verifier_out_of_band = broken
    try:
        with pytest.raises(GraderError) as raised:
            grader.grade(probe(), Source(stage(tmp_path, GOOD_TEST)).run(None))
    finally:
        module._run_verifier_out_of_band = original
    plain = fix_audit.decrypt(raised.value.fix_audit, *map(str, audit_keys))
    assert plain["incomplete"] is True
    assert [run["outcome"] for run in plain["regression_runs"]] == ["not_run"] * 4


# --- integrity scan ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,flag",
    [
        ("import inspect", "inspect"),
        ("from dis import dis", "dis"),
        ("import marshal", "marshal"),
        ("f.__code__", "__code__"),
        ("code.co_code", "co_code"),
        ("code.co_consts", "co_code"),
        ("getsource(rules)", "getsource"),
        ("rules.__file__", "__file__"),
        ("rules.__spec__.origin", "module_spec"),
        ("rules.__loader__.get_source('rules')", "module_spec"),
        ("import linecache", "module_spec"),
        ("importlib.util.find_spec('rules')", "module_spec"),
        ("import zlib", "hashing"),
        ("hashlib.md5(source)", "hashing"),
        ("name = 'rules' + '.py'", "source_path"),
        ("Path('rules.py')", "source_path"),
        ("open(name).read()", "file_read"),
        ("Path(name).read_text()", "file_read"),
        ("os.listdir('.')", "listing"),
        ("os.walk('.')", "listing"),
        ("os.write(3, b'')", "result_channel"),
        ("sys.argv[1]", "result_channel"),
        ("unittest.TestResult.addFailure = None", "result_channel"),
    ],
)
def test_regression_markers(text, flag):
    assert "regression_test:" + flag in scan_regression_test(text)


def test_every_regression_marker_has_a_probe():
    probes = {
        "inspect", "dis", "marshal", "__code__", "co_code", "getsource", "__file__",
        "module_spec", "hashing", "source_path", "file_read", "listing", "result_channel",
    }  # fmt: skip
    assert set(REGRESSION_TEST_MARKERS) == probes


@pytest.mark.parametrize("test", [GOOD_TEST, CHANGED_TEST, KEYED_TEST])
def test_behavioural_tests_are_not_flagged(test):
    commented = test + "# inspect the dis output; never open rules.py\n"
    assert scan_regression_test(commented) == []
    assert scan_regression_test(test + "\nif __name__ == '__main__':\n    unittest.main()\n") == []


# --- hostile regression tests are contained like hostile hidden cases ---------------


def _contained_test(body):
    return (
        "import unittest\n\nclass Hostile(unittest.TestCase):\n    def test_hostile(self):\n"
        + "".join("        " + line + "\n" for line in body.splitlines())
    )


def _docker_run(tmp_path, test, **task_changes):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = replace(probe(), **task_changes)
    return run_test(task, stage(workspace, test), work_root=hidden_tests.shared_work_root())


@pytest.mark.parametrize(
    "body",
    [
        "import socket\ns = socket.socket()\ntry:\n    with self.assertRaises(OSError):\n"
        "        s.connect(('1.1.1.1', 443))\nfinally:\n    s.close()",
        "import os\nfrom pathlib import Path\nself.assertEqual(os.getuid(), 65534)\n"
        "self.assertFalse(Path('/var/run/docker.sock').exists())\n"
        "self.assertFalse(Path('/work/hidden').exists())",
        "for path in ('/work/target.py', '/work/new.py', '/etc/new'):\n"
        "    with self.assertRaises(OSError):\n        open(path, 'w')",
        "import os, time\nchildren = []\ntry:\n    with self.assertRaises(OSError):\n"
        "        for i in range(40):\n            pid = os.fork()\n            if pid == 0:\n"
        "                time.sleep(10)\n                os._exit(0)\n            children.append(pid)\n"
        "finally:\n    for pid in children:\n        os.kill(pid, 9)\n        os.waitpid(pid, 0)",
    ],
    ids=["socket", "uid-and-mounts", "readonly", "pids"],
)
def test_hostile_test_contained(tmp_path, docker_backend, body):
    result = _docker_run(tmp_path, _contained_test(body))
    assert set(outcomes_of(result).values()) == {"passed"}, result["output_tail"]


def test_hostile_test_parent_memory_and_descriptors_inaccessible(tmp_path, docker_backend):
    body = (
        "import ctypes, gc, os\n"
        "self.assertFalse(any(isinstance(o, dict) and 'nonce' in o for o in gc.get_objects()))\n"
        "for pid in {1, os.getppid()}:\n"
        "    for path in ('mem', 'fd/1', 'fd/2'):\n"
        "        flags = os.O_WRONLY if path.startswith('fd/') else os.O_RDONLY\n"
        "        with self.assertRaises(OSError):\n"
        "            os.close(os.open('/proc/' + str(pid) + '/' + path, flags))\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "self.assertEqual(libc.ptrace(16, os.getppid(), 0, 0), -1)"
    )
    result = _docker_run(tmp_path, _contained_test(body))
    assert set(outcomes_of(result).values()) == {"passed"}, result["output_tail"]


def test_hostile_test_home_read_contained(tmp_path, docker_backend):
    with tempfile.NamedTemporaryFile(dir=Path.home(), prefix="gym-canary-") as canary:
        canary.write(b"private-canary")
        canary.flush()
        body = f"with self.assertRaises(OSError):\n    open({canary.name!r}).read()"
        result = _docker_run(tmp_path, _contained_test(body))
    assert set(outcomes_of(result).values()) == {"passed"}, result["output_tail"]
    assert "private-canary" not in result["output_tail"]


def test_hostile_test_write_contained(tmp_path, docker_backend):
    outside = tmp_path / "escaped"
    body = f"with self.assertRaises(OSError):\n    open({str(outside)!r}, 'w').write('bad')"
    result = _docker_run(tmp_path, _contained_test(body))
    assert set(outcomes_of(result).values()) == {"passed"}, result["output_tail"]
    assert not outside.exists()


def test_hostile_test_env_print_contained(tmp_path, docker_backend, monkeypatch):
    monkeypatch.setenv("GYM_PARENT_CANARY", "private-parent-canary")
    body = (
        "import os\nprint(dict(os.environ))\n"
        "allowed = {'PATH', 'HOME', 'TMPDIR', 'PYTHONDONTWRITEBYTECODE', 'LC_CTYPE', 'HOSTNAME',"
        " 'LANG', 'GPG_KEY', 'PYTHON_VERSION', 'PYTHON_SHA256'}\n"
        "self.assertEqual(sorted(set(os.environ) - allowed), [])"
    )
    result = _docker_run(tmp_path, _contained_test(body))
    assert set(outcomes_of(result).values()) == {"passed"}, result["output_tail"]
    assert "private-parent-canary" not in result["output_tail"]


def _owned_containers(monkeypatch):
    original = hidden_tests._command
    containers = []

    def command(work, args):
        built = original(work, args)
        containers.append(built[built.index("--name") + 1])
        return built

    monkeypatch.setattr(hidden_tests, "_command", command)
    return containers


def _assert_containers_gone(containers):
    assert containers
    for name in containers:
        inspect = subprocess.run(
            [hidden_tests.shutil.which("docker"), "container", "inspect", name],
            env=hidden_tests._client_env(),
            capture_output=True,
            timeout=15,
        )
        assert inspect.returncode != 0
        assert b"No such" in inspect.stderr


def test_hostile_test_infinite_loop_contained(tmp_path, docker_backend, monkeypatch):
    containers = _owned_containers(monkeypatch)
    result = _docker_run(
        tmp_path, _contained_test("while True:\n    pass"), regression_test_timeout_seconds=2
    )
    assert set(outcomes_of(result).values()) == {"timed_out"}
    assert all(run["timed_out"] for run in result["runs"])
    assert not any(check["ok"] for check in hidden_tests.regression_subchecks(result))
    _assert_containers_gone(containers)


def test_hostile_test_fork_bomb_contained(tmp_path, docker_backend, monkeypatch):
    containers = _owned_containers(monkeypatch)
    body = (
        "import os, time\nfor i in range(8):\n    if os.fork() == 0:\n"
        "        while True:\n            time.sleep(.1)\nwhile True:\n    time.sleep(.1)"
    )
    result = _docker_run(tmp_path, _contained_test(body), regression_test_timeout_seconds=2)
    assert all(run["timed_out"] for run in result["runs"])
    _assert_containers_gone(containers)


def test_hostile_test_output_bounded(tmp_path, docker_backend):
    body = "import os\nos.write(1, b'x' * 100000)"
    result = _docker_run(tmp_path, _contained_test(body))
    assert set(outcomes_of(result).values()) == {"error"}
    assert len(result["output_tail"].encode()) <= 16384


def test_real_engine_grades_correct_test(tmp_path, docker_backend):
    result = run_test(
        probe(),
        stage(tmp_path, GOOD_TEST, rules=ALTERNATIVE_RULES),
        work_root=hidden_tests.shared_work_root(),
    )
    assert outcomes_of(result) == {
        "original": "failed",
        "insufficient": "failed",
        "reference": "passed",
        "submission": "passed",
    }
