#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
cp "$fixture_root/alerts.py" "$sandbox_root/alerts.py"
cp "$fixture_root/digest.py" "$sandbox_root/digest.py"
cp "$fixture_root/notify.py" "$sandbox_root/notify.py"
cp "$fixture_root/notifyctl.py" "$sandbox_root/notifyctl.py"
cp "$fixture_root/quiet_hours.py" "$sandbox_root/quiet_hours.py"
cp "$fixture_root/change.diff" "$sandbox_root/change.diff"
cp "$fixture_root/pr.md" "$sandbox_root/pr.md"
