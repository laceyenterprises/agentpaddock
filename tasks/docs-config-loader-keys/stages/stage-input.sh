#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/surface.py" <<'RFD_INPUT'
_ENUM_ROLES_BUILD_PACK_DEFAULT_WORKER_CLASS = ("plan", "codex", "claude-code")

# Excerpt from roles.__keys schema:
roles_keys = {
                "build_pack_default_worker_class": {
                    "__type": _TYPE_STRING,
                    "__default": "plan",
                    "__enum": _ENUM_ROLES_BUILD_PACK_DEFAULT_WORKER_CLASS,
                },
}
RFD_INPUT
cat >"$sandbox_root/guide.md" <<'RFD_INPUT'
## Config keys
| `roles.build_pack_default_worker_class` | (none) |
RFD_INPUT
