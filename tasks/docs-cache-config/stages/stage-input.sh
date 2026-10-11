#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/surface.py" <<'RFD_INPUT'
DEFAULTS = {"cache.max_entries": 128, "cache.ttl_seconds": 60}
# ttl_seconds=0 disables caching. Negative values are invalid (exit 2).
# Eviction is least-recently-used, not FIFO. --no-cache bypasses reads and writes.
RFD_INPUT
cat >"$sandbox_root/guide.md" <<'RFD_INPUT'
## Cache
cache.max_entries defaults to 256. cache.ttl_seconds defaults to 300. Eviction is FIFO.
RFD_INPUT
