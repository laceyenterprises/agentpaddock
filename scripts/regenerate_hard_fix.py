"""Regenerate one HRF hard-fix task's staged inputs, verifier snapshots and variants.

Run with --write to update generated files; otherwise report drift.
regenerate_rfd_fix owns the staged manifest, the stage script and the
verifier's PROTECTED snapshot. This adds the verifier's ORIGINAL snapshot of
every editable source file (the regression-test file excluded) and copies
expected/<variant>/ to hidden/variants/<variant>/ for the graded variants and
any [regression_test] must_fail_variants.
Fixture and expected/ files own the content; hidden cases are never rewritten.
"""

import argparse
import ast
import json
import pprint
import subprocess
import tomllib
from pathlib import Path

from regenerate_rfd_fix import RuffUnavailable, regenerate as regenerate_rfd

GRADED_VARIANTS = ("reference", "insufficient")


def _with_original(verifier, original, ruff, path):
    assignments = [
        node
        for node in ast.parse(verifier).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ORIGINAL" for target in node.targets)
    ]
    (assignment,) = assignments
    lines = verifier.splitlines(keepends=True)
    lines[assignment.lineno - 1 : assignment.end_lineno] = [
        "ORIGINAL = " + pprint.pformat(original, width=88, sort_dicts=False) + "\n"
    ]
    # Formatting is part of the generated verifier's canonical bytes.
    return subprocess.run(
        [ruff, "format", "--stdin-filename", str(path), "-"],
        input="".join(lines),
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


def regenerate(task_dir, write=False):
    from regenerate_rfd_fix import require_ruff

    # The gym's own loader validates task.toml; this reads only three keys.
    task = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    if task.get("suite") != "hard-fix":
        raise ValueError(f"{task_dir} is not a hard-fix task")
    regression_test = task.get("regression_test", {}).get("path")
    must_fail = task.get("regression_test", {}).get("must_fail_variants", [])
    stage = task_dir / "stages/stage-input.sh"
    if write and not stage.exists():
        # A new task: the RFD generator compares before it writes.
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("", encoding="utf-8")
    drift = regenerate_rfd(task_dir, write)
    fixture = task_dir / "fixture"
    manifest = json.loads((fixture / "staged-manifest.json").read_text(encoding="utf-8"))
    sources = [name for name in manifest["editable"] if name != regression_test]
    original = {name: (fixture / name).read_text(encoding="utf-8") for name in sources}
    verifier_path = task_dir / "verifier.py"
    current = verifier_path.read_text(encoding="utf-8")
    generated = {verifier_path: _with_original(current, original, require_ruff(), verifier_path)}
    for variant in (*GRADED_VARIANTS, *must_fail):
        for name in sources:
            generated[task_dir / "hidden/variants" / variant / name] = (
                task_dir / "expected" / variant / name
            ).read_text(encoding="utf-8")
    for path, content in generated.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            if path not in drift:
                drift.append(path)
            if write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    return drift


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        drift = regenerate(args.task_dir, args.write)
    except RuffUnavailable as exc:
        parser.exit(2, f"{exc}\n")
    for path in drift:
        print(path)
    raise SystemExit(bool(drift) and not args.write)
