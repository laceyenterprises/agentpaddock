#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
fixture_root="$(cd "$(dirname "$0")/../fixture" && pwd)"
mkdir -p "$sandbox_root"
mkdir -p -- "$(dirname -- "$sandbox_root"/limiter.py)"
cp -- "$fixture_root"/limiter.py "$sandbox_root"/limiter.py
mkdir -p -- "$(dirname -- "$sandbox_root"/headers.py)"
cp -- "$fixture_root"/headers.py "$sandbox_root"/headers.py
mkdir -p -- "$(dirname -- "$sandbox_root"/proxy.py)"
cp -- "$fixture_root"/proxy.py "$sandbox_root"/proxy.py
mkdir -p -- "$(dirname -- "$sandbox_root"/test_visible.py)"
cp -- "$fixture_root"/test_visible.py "$sandbox_root"/test_visible.py
mkdir -p -- "$(dirname -- "$sandbox_root"/finding.md)"
cp -- "$fixture_root"/finding.md "$sandbox_root"/finding.md
mkdir -p -- "$(dirname -- "$sandbox_root"/staged-manifest.json)"
cp -- "$fixture_root"/staged-manifest.json "$sandbox_root"/staged-manifest.json
