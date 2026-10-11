#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/decisions.py" "$sandbox_root/decisions.py"
cp "$fixture_root/reconcile.py" "$sandbox_root/reconcile.py"
cp "$fixture_root/replies.py" "$sandbox_root/replies.py"
cp "$fixture_root/change.diff" "$sandbox_root/change.diff"
cp "$fixture_root/pr.md" "$sandbox_root/pr.md"
