#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/schema_parser.py" <<'PY'
import re

_POSTGRES_SCHEMA_HEAD_RE = re.compile(
    r"Postgres session-ledger schema is not at Alembic head "
    r"(?P<expected>[^;]+); found (?P<found>[^.]+)\."
)


def behind_revisions(exc):
    match = _POSTGRES_SCHEMA_HEAD_RE.search(str(exc))
    if match is None:
        return None
    return match.group("found").strip(), match.group("expected").strip()
PY

cat >"${sandbox_root}/symptom.txt" <<'TXT'
The actual runtime path raised:

  Postgres session-ledger schema is behind Alembic head 20260904T223000Z;
  found 20260805T210000Z.

This is the recoverable deploy race the self-heal path exists to handle, but the
parser returned None and the caller treated it as a hard failure.
TXT
