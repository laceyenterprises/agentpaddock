#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"
cd "${sandbox_root}"

# A pinned identity and dates keep the staged history identical on every host:
# the host's git config must not decide whether the commit succeeds.
git_c() {
  GIT_AUTHOR_DATE="2026-09-01T12:00:00Z" GIT_COMMITTER_DATE="2026-09-01T12:00:00Z" \
    git -c user.email=gym@agent-os.invalid -c user.name=gym \
    -c commit.gpgsign=false -c init.defaultBranch=main "$@"
}

# On hosts with AGENT_OS_WORKER_RUN_AS_USER set, the worker runs as a
# different UID than this stage, and git refuses a repository another user
# owns. safe.directory is only honoured from global/system config or `-c`, so
# the prompt tells the agent to pass `-c safe.directory="$PWD"` in that case,
# and the verifier accepts `git -c ... log`.
git_c init -q .

cat >main.py <<'PY'
def greet():
    return "hello"
PY
cat >test.py <<'PY'
import main


def test_greet():
    assert main.greet() == "hello"
PY
git_c add main.py test.py
git_c commit -q -m "greet: add greeting and its test"

cat >main.py <<'PY'
def greet():
    return "bye"
PY
git_c add main.py
git_c commit -q -m "greet: reuse the farewell banner string"

test "$(git_c rev-list --count HEAD)" -eq 2
