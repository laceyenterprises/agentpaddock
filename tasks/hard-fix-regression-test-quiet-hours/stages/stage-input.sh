#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
mkdir -p -- "$(dirname -- "$sandbox_root"/quiet.py)"
cp -- "$fixture_root"/quiet.py "$sandbox_root"/quiet.py
mkdir -p -- "$(dirname -- "$sandbox_root"/router.py)"
cp -- "$fixture_root"/router.py "$sandbox_root"/router.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_regression.py)"
cp -- "$fixture_root"/test_regression.py "$sandbox_root"/test_regression.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_visible.py)"
cp -- "$fixture_root"/test_visible.py "$sandbox_root"/test_visible.py
mkdir -p -- "$(dirname -- "$sandbox_root"/finding.md)"
cp -- "$fixture_root"/finding.md "$sandbox_root"/finding.md
mkdir -p -- "$(dirname -- "$sandbox_root"/staged-manifest.json)"
cp -- "$fixture_root"/staged-manifest.json "$sandbox_root"/staged-manifest.json
