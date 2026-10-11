#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/dispatcher.py" "$sandbox_root/dispatcher.py"
cp "$fixture_root/sender.py" "$sandbox_root/sender.py"
cp "$fixture_root/store.py" "$sandbox_root/store.py"
cp "$fixture_root/change.diff" "$sandbox_root/change.diff"
cp "$fixture_root/pr.md" "$sandbox_root/pr.md"
