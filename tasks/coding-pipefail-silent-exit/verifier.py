"""Out-of-band verifier for coding-pipefail-silent-exit.

Ground truth inline; the agent never sees it.
"""

import json

import gym_verifier as gv

# Line 7 is the LATEST_META assignment. `find` over a missing directory exits
# non-zero; under `set -o pipefail` the whole pipeline inherits that status,
# and `set -e` terminates the script before any echo runs.
EXPECTED_LINE = 7
EXPECTED_OPTION = "pipefail"
# The 2>/dev/null hides find's MESSAGE, not its EXIT STATUS. That is the trap:
# the line already looks defended.
EXPECTED_SUPPRESSED = False

result = gv.result()
calls = gv.toolcalls()

line = result.get("defect_line")
if line != EXPECTED_LINE:
    gv.fail(f"defect_line {line!r} != {EXPECTED_LINE} (the find|sort|tail assignment)")

if result.get("shell_option") != EXPECTED_OPTION:
    gv.fail(f"shell_option {result.get('shell_option')!r} != {EXPECTED_OPTION!r}")

suppressed = result.get("suppressed_by_2devnull")
if not isinstance(suppressed, bool):
    gv.fail(f"suppressed_by_2devnull {suppressed!r} is not a boolean")
if suppressed is not EXPECTED_SUPPRESSED:
    gv.fail(
        "2>/dev/null hides find's message, not its exit status; it does not defend the pipeline"
    )

# Evidence of reading, not tool choice: agents open staged files with `Bash`
# (`cat`, `sed`), and the transcript records name="Bash". Requiring a tool
# literally named "read" failed correct answers for using the wrong tool.
if not any("probe.sh" in json.dumps(call.get("arguments") or {}) for call in calls.calls):
    gv.fail("agent answered without opening probe.sh")

gv.passed()
