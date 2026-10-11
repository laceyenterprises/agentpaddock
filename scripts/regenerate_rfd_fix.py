"""Regenerate one RFD fix task's staged inputs and trusted verifier snapshot.

Run with --write to update generated files; otherwise report drift.
Fixture files own the content. Hidden cases and provenance are never rewritten.
"""

import argparse
import ast
import json
import pprint
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath

RUFF_VERSION = "0.15.20"  # Keep aligned with .github/workflows/repo-guards.yml.


class RuffUnavailable(RuntimeError):
    """The canonical formatter is missing or has a different version."""


def require_ruff():
    binary = shutil.which("ruff")
    advice = (
        f"Install the regeneration dependency with: python3 -m pip install ruff=={RUFF_VERSION}"
    )
    if binary is None:
        raise RuffUnavailable(f"Ruff {RUFF_VERSION} is required. {advice}")
    version = subprocess.run(
        [binary, "--version"],
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    if version != f"ruff {RUFF_VERSION}":
        raise RuffUnavailable(f"Expected ruff {RUFF_VERSION}, found {version}. {advice}")
    return binary


def regenerate(task_dir, write=False):
    fixture = task_dir / "fixture"
    manifest_path = fixture / "staged-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = [*manifest["files"], "staged-manifest.json"]
    for name in names:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise ValueError(f"Staged input must be a relative file path without '..': {name!r}")
    ruff = require_ruff()
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    protected = {
        name: manifest_text
        if name == "staged-manifest.json"
        else (fixture / name).read_text(encoding="utf-8")
        for name in names
        if name not in manifest["editable"]
    }
    verifier_path = task_dir / "verifier.py"
    verifier = verifier_path.read_text(encoding="utf-8")
    assignments = [
        node
        for node in ast.parse(verifier).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PROTECTED" for target in node.targets
        )
    ]
    (assignment,) = assignments
    lines = verifier.splitlines(keepends=True)
    lines[assignment.lineno - 1 : assignment.end_lineno] = [
        "PROTECTED = " + pprint.pformat(protected, width=88, sort_dicts=False) + "\n"
    ]
    stage = (
        '#!/usr/bin/env bash\nset -euo pipefail\nsandbox_root="${1:-.}"\n'
        'fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"\n'
        'mkdir -p "$sandbox_root"\n'
        + "".join(
            f'mkdir -p -- "$(dirname -- "$sandbox_root"/{shlex.quote(name)})"\n'
            f'cp -- "$fixture_root"/{shlex.quote(name)} "$sandbox_root"/{shlex.quote(name)}\n'
            for name in names
        )
    )
    generated = {
        manifest_path: manifest_text,
        task_dir / "stages/stage-input.sh": stage,
        verifier_path: "".join(lines),
    }
    # Formatting is part of the generated verifier's canonical bytes.
    generated[verifier_path] = subprocess.run(
        [ruff, "format", "--stdin-filename", str(verifier_path), "-"],
        input=generated[verifier_path],
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    drift = [
        path for path, content in generated.items() if path.read_text(encoding="utf-8") != content
    ]
    if write:
        for path, content in generated.items():
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
