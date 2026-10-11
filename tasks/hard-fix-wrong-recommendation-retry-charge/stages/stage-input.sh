#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
mkdir -p -- "$(dirname -- "$sandbox_root"/billing.py)"
cp -- "$fixture_root"/billing.py "$sandbox_root"/billing.py
mkdir -p -- "$(dirname -- "$sandbox_root"/gateway.py)"
cp -- "$fixture_root"/gateway.py "$sandbox_root"/gateway.py
mkdir -p -- "$(dirname -- "$sandbox_root"/settlement.py)"
cp -- "$fixture_root"/settlement.py "$sandbox_root"/settlement.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_visible.py)"
cp -- "$fixture_root"/test_visible.py "$sandbox_root"/test_visible.py
mkdir -p -- "$(dirname -- "$sandbox_root"/finding.md)"
cp -- "$fixture_root"/finding.md "$sandbox_root"/finding.md
mkdir -p -- "$(dirname -- "$sandbox_root"/staged-manifest.json)"
cp -- "$fixture_root"/staged-manifest.json "$sandbox_root"/staged-manifest.json
