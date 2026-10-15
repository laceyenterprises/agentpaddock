#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
mkdir -p -- "$(dirname -- "$sandbox_root"/freeze.py)"
cp -- "$fixture_root"/freeze.py "$sandbox_root"/freeze.py
mkdir -p -- "$(dirname -- "$sandbox_root"/deployer.py)"
cp -- "$fixture_root"/deployer.py "$sandbox_root"/deployer.py
mkdir -p -- "$(dirname -- "$sandbox_root"/policy.py)"
cp -- "$fixture_root"/policy.py "$sandbox_root"/policy.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_regression.py)"
cp -- "$fixture_root"/test_regression.py "$sandbox_root"/test_regression.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_visible.py)"
cp -- "$fixture_root"/test_visible.py "$sandbox_root"/test_visible.py
mkdir -p -- "$(dirname -- "$sandbox_root"/finding.md)"
cp -- "$fixture_root"/finding.md "$sandbox_root"/finding.md
mkdir -p -- "$(dirname -- "$sandbox_root"/staged-manifest.json)"
cp -- "$fixture_root"/staged-manifest.json "$sandbox_root"/staged-manifest.json
