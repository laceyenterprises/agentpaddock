#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
mkdir -p -- "$(dirname -- "$sandbox_root"/subscribers.py)"
cp -- "$fixture_root"/subscribers.py "$sandbox_root"/subscribers.py
mkdir -p -- "$(dirname -- "$sandbox_root"/newsletter.py)"
cp -- "$fixture_root"/newsletter.py "$sandbox_root"/newsletter.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_regression.py)"
cp -- "$fixture_root"/test_regression.py "$sandbox_root"/test_regression.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_visible.py)"
cp -- "$fixture_root"/test_visible.py "$sandbox_root"/test_visible.py
mkdir -p -- "$(dirname -- "$sandbox_root"/finding.md)"
cp -- "$fixture_root"/finding.md "$sandbox_root"/finding.md
mkdir -p -- "$(dirname -- "$sandbox_root"/staged-manifest.json)"
cp -- "$fixture_root"/staged-manifest.json "$sandbox_root"/staged-manifest.json
