# AGENTS.md

Rules for anyone changing Paddock, human or agent. Each one is enforced by a
check in `ci / fast`, the single required check.

## Records stay in the pull request body

Reviewers, review bots, merge automation and build agents keep their records
in the pull request body or its comments: verdicts, findings, remediation
notes, validation and acceptance write-ups, run logs. None of it is committed.
A record in the tree is permanent, public and soon wrong.

`scripts/internal_note_guard.py` rejects a tree holding such a record. If it
flags your change, move the text into the pull request body. Write a result as
a results document under `results/` (see [CONTRIBUTING.md](CONTRIBUTING.md)),
never as a report.

## The repository is public from its first commit

Every push runs `scripts/publication_scan.py`. It refuses secrets and
credentials, secret-manager references, home-directory paths, tailnet
addresses, private-network URLs, machine hostnames, other repositories of the
publishing organization, the operator's identity, and `github.io` links. The
site is served only at [agentpaddock.ai](https://agentpaddock.ai).

The pre-push mirror runs both scans with `--tracked-only`: they inspect
Git-tracked working-tree files (including staged additions), excluding
untracked local metadata. Tracked files in cache or build directories are
still checked. Without that flag, the scans inspect the full tree outside
their built-output and cache exclusions, including exports without Git.

A test may need a refused value on purpose, such as a URL with credentials that
the code must reject. Mark that line with
`publication-scan: allow(<category>): <reason>`; the marker covers one
category on one line. Prefer building the value from fragments in the test.

## CI budget

Actions minutes cost money, so CI does only what must gate a merge:

- Two workflows exist, and only two: `ci` (job `fast`) and `pages`.
- `ci / fast` runs on `ubuntu-latest` with one Python and a 5-minute timeout,
  for pull requests and pushes to `main`, with `paths` filters and
  `concurrency` that cancels superseded runs. It runs the publication scan, the
  internal-note guard, the CI-policy test and the fast unit subset.
- `pages` builds the site from `results/` on pushes to `main` or manual
  dispatch, only when the repository variable `PADDOCK_SITE_LIVE` is `true`.
  Until then the job is skipped and nothing is built or deployed. To launch,
  enable Pages for the repository and set that variable to `true`; nothing
  else changes.
- No schedules, matrices, macOS, Windows or larger runners. No eval,
  benchmark, live-model or Docker-containment jobs: run those on your machine
  and submit a run bundle. The runner has Docker, so `ci / fast` sets
  `PADDOCK_NO_DOCKER=1` and the Docker-backed tests skip there.

`tests/test_ci_policy.py` enforces every line above. Any workflow change is
cost change control: the pull request quotes the maintainer's authorization,
and the policy test must still pass.

The fast unit subset is the explicit list in the [ci workflow](.github/workflows/ci.yml).
Add a test file there when it runs standalone in seconds. Docker-backed tests and
the slow corpora run locally (`python3 -m pytest tests -q`).
