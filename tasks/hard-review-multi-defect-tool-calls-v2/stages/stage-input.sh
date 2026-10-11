#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/bridge.py" "$sandbox_root/bridge.py"
cp "$fixture_root/parsers.py" "$sandbox_root/parsers.py"
cp "$fixture_root/shapes.py" "$sandbox_root/shapes.py"
cp "$fixture_root/tool_calls.py" "$sandbox_root/tool_calls.py"
cp "$fixture_root/change.diff" "$sandbox_root/change.diff"
cp "$fixture_root/pr.md" "$sandbox_root/pr.md"
