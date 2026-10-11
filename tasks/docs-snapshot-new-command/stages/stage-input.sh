#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/surface.py" <<'RFD_INPUT'
import argparse
p = argparse.ArgumentParser(prog="hq snapshot inspect")
p.add_argument("--limit", type=int, default=20)
p.add_argument("--json", action="store_true")
def inspect(rows, limit=20):
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]
# Exit 0 on successful inspection, 2 for invalid arguments; never mutates rows.
RFD_INPUT
