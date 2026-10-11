#!/usr/bin/env bash
# Stage the redacted incident artifact into the agent sandbox. Stages run before
# dispatch and only ever write inputs the agent is allowed to see — never the
# expected/ ground truth.
set -euo pipefail

sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}/incident"
cat >"${sandbox_root}/incident/artifact.txt" <<'ARTIFACT'
run-42: worker opened the result file for write before ever reading the staged
incident artifact. Downstream verifier flagged the ordering. Root cause class:
read-before-write violation.
ARTIFACT
