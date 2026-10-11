#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/retention.py" "$sandbox_root/retention.py"
cp "$fixture_root/change.diff" "$sandbox_root/change.diff"
cp "$fixture_root/pr.md" "$sandbox_root/pr.md"
