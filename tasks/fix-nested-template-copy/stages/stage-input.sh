#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/target.py" "$sandbox_root/target.py"
cp "$fixture_root/support.py" "$sandbox_root/support.py"
cp "$fixture_root/test_visible.py" "$sandbox_root/test_visible.py"
cp "$fixture_root/finding.md" "$sandbox_root/finding.md"
cp "$fixture_root/staged-manifest.json" "$sandbox_root/staged-manifest.json"
