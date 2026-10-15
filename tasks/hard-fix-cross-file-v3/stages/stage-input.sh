#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
mkdir -p -- "$(dirname -- "$sandbox_root"/backend.py)"
cp -- "$fixture_root"/backend.py "$sandbox_root"/backend.py
mkdir -p -- "$(dirname -- "$sandbox_root"/directory.py)"
cp -- "$fixture_root"/directory.py "$sandbox_root"/directory.py
mkdir -p -- "$(dirname -- "$sandbox_root"/settings.py)"
cp -- "$fixture_root"/settings.py "$sandbox_root"/settings.py
mkdir -p -- "$(dirname -- "$sandbox_root"/plugins.py)"
cp -- "$fixture_root"/plugins.py "$sandbox_root"/plugins.py
mkdir -p -- "$(dirname -- "$sandbox_root"/reconcile.py)"
cp -- "$fixture_root"/reconcile.py "$sandbox_root"/reconcile.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_visible.py)"
cp -- "$fixture_root"/test_visible.py "$sandbox_root"/test_visible.py
mkdir -p -- "$(dirname -- "$sandbox_root"/finding.md)"
cp -- "$fixture_root"/finding.md "$sandbox_root"/finding.md
mkdir -p -- "$(dirname -- "$sandbox_root"/staged-manifest.json)"
cp -- "$fixture_root"/staged-manifest.json "$sandbox_root"/staged-manifest.json
