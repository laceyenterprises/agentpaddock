#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/audit.py" "$sandbox_root/audit.py"
cp "$fixture_root/events.py" "$sandbox_root/events.py"
cp "$fixture_root/registry.py" "$sandbox_root/registry.py"
cp "$fixture_root/webhooks.py" "$sandbox_root/webhooks.py"
cp "$fixture_root/wiring.py" "$sandbox_root/wiring.py"
cp "$fixture_root/change.diff" "$sandbox_root/change.diff"
cp "$fixture_root/pr.md" "$sandbox_root/pr.md"
