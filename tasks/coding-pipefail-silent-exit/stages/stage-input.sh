#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/probe.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

BACKUP_ROOT="$(resolve_backup_root)"
CRED_ENV="${PROBE_OP_ENV:-/nonexistent}"

LATEST_META="$(find "$BACKUP_ROOT/base" -mindepth 2 -maxdepth 2 -name backup.json -print 2>/dev/null | sort | tail -n 1)"

if [[ -z "$LATEST_META" ]]; then
  if [[ ! -f "$CRED_ENV" ]]; then
    echo "probe: advisory - not provisioned on this host" >&2
    exit 0
  fi
  echo "probe: no backup metadata found under $BACKUP_ROOT/base" >&2
  exit 66
fi

echo "probe: verifying $LATEST_META"
exit 0
SH

cat >"${sandbox_root}/symptom.txt" <<'TXT'
Operator ran the probe on a host where the capability is not provisioned:

  $ bash probe.sh
  exit=1
  --- stderr ---
  --- stdout ---

$BACKUP_ROOT/base does not exist on this host. The advisory branch was expected
to fire and exit 0. No message was printed at all.
TXT
