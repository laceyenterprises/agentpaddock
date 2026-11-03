[Home](../../README.md) · [How it works](README.md) · [Races](races.md) · **Hard races** · [Board](board.md) · [Race it yourself](race-it-yourself.md) · [Under the hood](the-gym.md) · [Batteries](batteries.md) · [Design](../README.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-hard-races-dark.svg">
  <img alt="Hard races: The review, fix and docs pilot, then hard review and hard fix: decoys, several targets, and grading." src="assets/banner-hard-races-light.svg">
</picture>

**On this page:** [Review, fix and docs pilot (RFD)](#review-fix-and-docs-pilot-rfd) · [Hard review and hard fix (HRF)](#hard-review-and-hard-fix-hrf)

## Review, fix and docs pilot (RFD)

Each suite discovers six tasks. `review` submits bounded lists of findings
(file, line range, blocking flag, category and summary); `fix` captures edited
Python files; `docs` captures a document and checks required facts, stale claims,
invented flags/keys and preserved headings deterministically.

To add a task, copy the nearest same-suite `tasks/<id>/` contract and stage only
public fixtures. Declare `[capture] files = ["path"]` for workspace outputs:
at most eight relative paths, regular files only, at most 256 KiB each. Symlinks
and escaping paths are refused; missing files remain missing. Read outputs with
`gv.workspace_text(path)` and `gv.workspace_files()`. Ground truth in
`expected/` and tests in `hidden/` are never staged. Only fix-family suites
(`fix`, `hard-fix`) may declare `[hidden_tests]`: a JSON case list as the
entrypoint (the schema accepts only `hidden/<file>.json`; every shipped task
uses `tasks/<id>/hidden/cases.json`) of `{call, expected}` cases, a positive
`expected_tests` count, and `timeout_seconds` at most 120.
`gv.hidden_tests()` reads the runner outcome. Use the Docker contract above;
absence of containment is a harness error, never an unsandboxed fallback.

For a real-derived review/fix pair, run
`python3 scripts/review_task_from_pr.py --repo OWNER/NAME --pr N` from this module, trim its
unverified drafts and confirm the ground truth before moving them into `tasks/`.
Add corpus tests for a reference pass and known-wrong failures; fix tasks also
need original and insufficient-patch failures. Run `python3 -m pytest tests -q`.
A pair derived from a private review must not ship that review's text. Paraphrase
each finding, rename private identifiers, write the remediation yourself, and cite
no PR or review link; `PROVENANCE.md` says only that the task is derived from a
private review. The seven tasks scrubbed at `task_version` 2 (PDK-03) follow this
rule, and `tests/test_task_versions.py` checks them against the hashed phrases
and review shingles in `tests/fixtures/private_review_forbidden_strings.json`.
Docker-dependent containment tests explicitly skip when the caller cannot reach
the engine; non-Docker tests still run. A reachable engine with a missing image
or broken containment fails. Workers must never gain access to the operator's
Colima socket. For the required pre-merge real-engine evidence, the eval-bench
operator runs `GYM_REQUIRE_REAL_ENGINE=1 python3 -m pytest tests/test_hidden_tests.py -q`
and posts the results. This test-only flag turns missing Docker, unreachable
engines, readiness timeouts and execution errors into failures rather than skips;
it never changes the production containment or fix-activation guards.

`batteries/rfd-pilot.toml` selects all three suites, codex and claude families,
at `high`. Set its token budget before a real run. Fix cells remain activation
errors until the separately tracked trusted-controller work lands. The unchanged
scheduled `--suite all` includes these cells; they are infrastructure errors,
not model failures or regressions without a baseline pass.

`projects/gym-review-fix-docs/activation/rfd.yaml` discovers tasks in the
resolved deploy checkout, checks engine reachability before pinned-image
presence, runs ordinary passing and socket-denial fixtures on the real backend, and calls
`require_fix_activation()`. It reports the gate state even when image/engine
checks fail. The probe uses the public `gym.hidden_tests.containment_image()`
accessor for the runner's pinned image and accepts only a tagged ENETUNREACH or
EHOSTUNREACH denial from the socket fixture. It stages fixtures and hidden-runner
bind sources inside a disposable mode-0700 directory under the resolved HQ
root's `tmp/`; the Docker VM must share that root. PMA does not depend on the
daemon's inherited `TMPDIR`. Engine connection failures and `docker info` timeouts print the JSON report
and exit nonzero with a `service unavailable` diagnostic so PMA's bounded
transient retry applies. A reachable engine with a missing image or invalid
mount remains a failed assertion. PMA runs these assertions automatically
on a deploy that touches this pack. They pass only when containment works and
`fix_activation` is false with the trusted-controller split rejection reason:
success proves that the guard stays closed, without authorizing fix dispatch.
`blocked-activation.json` is the passing predicate fixture; the hypothetical
`prospective-activation.json` is a negative fixture that must fail. Neither is
host evidence. The trusted-controller follow-up must update the expectation
together with an authorized guard change. RFD-08 is operator calibration
following deployment and separately authorized activation.

PMA activation is the canonical deployed containment evidence. A local real-engine
verification must use `GYM_REQUIRE_REAL_ENGINE=1` and report zero skipped tests;
a green exit with containment skips is not evidence. Sandbox readiness and fixture
timeouts on a reachable engine fail the assertion rather than triggering transient retries.

Hidden-case authoring limits: `timeout_seconds` is a shared wall-clock budget
for all cases, including each container cold start and force-removal cleanup.
Allow sufficient overhead for the slowest supported VM. Each JSON `expected`
must serialize to at most 8192 UTF-8 bytes; the complete response frame has a
16 KiB stdout cap, including its nonce and framing. Oversized observations are
harness errors. Type-sensitive calls must encode their Python type explicitly.
`raised` expectations must be module-qualified exception-name strings; calls requiring exception identity should
encode the module-qualified exception name in their `ok` observation.

Hidden JSON comparison uses canonical JSON representations: integer `1` and
float `1.0` differ, as do booleans and numbers. Authors must encode the numeric
representation they require. `raised` expectations require a module-qualified
identifier such as `builtins.ValueError`.

The shared wall budget includes container startup, but excludes bounded owned
container/process cleanup. `exit_code` reports the first nonzero case exit, or
zero if every executed case exits zero. `case_results` records each executed
case's exit code, timeout, protocol error flag, elapsed seconds (including
cleanup), and cleanup seconds. Unexecuted cases count as timeout errors and
have no execution record. Use these timings to calibrate startup headroom.

Current runner contract: `gym-review-fix-docs@72a6d429578b`, SPEC RFD-09.


NIGHTMODE-01 admission uses CPU idle and memory pressure. macOS load counts
kernel and uninterruptible threads, so a high load average with idle CPU is
not a saturation signal. Gym cells dispatch at low priority. MEG's battery
window remains authoritative; background and throttled do not by themselves
pause its queue, while an explicit paused mode does.

The shared registered keys are
`worker_pool.dispatch.resource_admission.min_cpu_idle_percent` (15) and
`worker_pool.dispatch.resource_admission.safety_load_per_core` (32). Memory
pressure CRIT blocks eval admission; dispatch still enforces memory reservations.
Unavailable resource readings fail open to normal dispatch gates. A single-run
headroom refusal raises `AdmissionRefused`; the suite records a harness `error`
(excluded from model regression comparisons), never an `agent_error`. Matrix
runs defer the cell for a later tick.

The poll deadline (3600 seconds from dispatch acceptance, queue time
included) never abandons work in progress (GYMWAIT-01). When it passes, the gym
reads the launch request status:

- a launch that is `running` is supervised for up to
  `GYM_RUNNING_WAIT_TIMEOUT_SECONDS` more (default 14400) and graded when it
  finishes;
- a launch that never started (`requested` or `leased`) is cancelled, so it
  cannot run later with nobody left to grade it;
- any other state keeps the plain deadline.

The cell still reports `terminalStatus: "timeout"` when no result was graded,
and its reason says which of these happened. A launch still running after the
extended wait is left running, not cancelled; collect it as a saved launch.

Single-run resource admission waits and resamples for at most
`GYM_DRAIN_WAIT_TIMEOUT_SECONDS` (default 1200 seconds) before reporting an
`AdmissionRefused` harness error. Suite JSON includes `refused`, the number of
cells that were not measured after this wait; these cells also count as errors
and cannot represent passing coverage. Missing headroom imports or sensor
failures log a warning and continue to normal dispatch gates. Matrix admission
checks the schedule window before sampling headroom once per tick.

Provider monitoring drains complete fixed stdout snapshots with bounded parser
memory and warns once when an owner boundary makes the log unreadable. Concurrent
tasks share immutable economics inputs but own separate ledger connections.
Interruptions stop new admissions and signal running pollers to request scoped
cancellation; an unconfirmed paid launch retains original-deadline supervision.
Provider containment retries transient cancellation failures up to three times
with 30-second subprocess timeouts and 2-/5-second backoffs (about 97 seconds
total); interruption-triggered cancellation uses one attempt (up to 30 seconds,
without backoff). Saved reasons include `cancel attempts=N; final reason=…`.
See the gym runbook.

TOC tasks may declare `required_large_output_commands` from the worker pool
seam's canonical command set. When present, the list must be nonempty and
contain distinct labels; unsupported labels cannot produce coverage evidence.
Ordinary gym tasks omit this optional OS-integration field.


## Hard review and hard fix (HRF)

`hard-review` discovers nine tasks and `hard-fix` sixteen. They exist because every
frontier combo passes all six RFD review tasks and every measured model passes
the RFD fix tasks. Task ids start with `hard-review-` and `hard-fix-`, so they
never match RFD's `review-*` and `fix-*` globs; `--suite review|fix` still
select exactly the RFD tasks. `hard-fix` is in the fix family
(`gym.schema.is_fix_family`). It gets the hidden-test permission, Docker
containment, the activation seam, the original-bytes snapshot, `integrity_hold`
and the sealed `fix_audit` envelope exactly as `fix` does. `hard-review` may not
declare `[hidden_tests]` or `[regression_test]`.

Each suite holds six HRF originals plus HRX `-v2` reworks of the originals the
HRF calibration
found every combo passing. The originals stay unchanged under their old ids,
so earlier results remain comparable.

| `-v2` task | What the rework removes |
|---|---|
| `hard-review-multi-defect-outbox-v2`, `hard-review-multi-defect-tool-calls-v2` | Short hunks: the defects sit in 400+ otherwise-correct lines with at least three decoys, one defect is visible only from an unchanged staged file, and `min_recall` is 1.0 |
| `hard-review-cross-file-v2` | Name search: the broken consumer is reached through a `module:function` subscriber table |
| `hard-fix-regression-test-dedupe-v2`, `hard-fix-regression-test-quiet-hours-v2` | Symptom-only tests: `must_fail_variants = ["plausible-wrong"]` |
| `hard-fix-two-findings-v2` | Trusting both cards: one recommended fix is subtly wrong, as a different staged file shows; ships `literal-advice` |
| `hard-fix-wrong-recommendation-retry-charge-v2` | Evidence next to the finding: the proof the advice is wrong sits in a staged caller and gateway contract; ships `literal-advice` |
| `hard-fix-cross-file-v2` | Name search: the second edit reaches the changed function only through a configured `module:function` name |

The five hard-fix `-v3` tasks retain the earlier tasks under their existing
ids and versions. The v2 findings largely spelled out the repair, and their
small hidden examples did not exercise several interacting caller contracts:
list scope, weekly window ownership, weighted capacity, retry identity and
per-account failure scope. The added scenarios extend those contracts rather
than changing the historical tasks. Passing a symptom test was too close to completing
the task. The v3 prompts describe outcomes; the staged contracts determine
correct behavior.

| New task | Contracts exercised together |
|---|---|
| `hard-fix-cross-file-v3` | Authoritative lookup after cache misses, configured calls, per-account continuation and batch boundaries |
| `hard-fix-regression-test-dedupe-v3` | Independent list subscriptions, opaque compound identity, raw spelling, row identity and one-shot input |
| `hard-fix-regression-test-quiet-hours-v3` | Weekly opening-day ownership, overnight spill, precise boundaries and empty windows |
| `hard-fix-two-findings-v3` | Weighted admission, multiple expirations, minimum retry delay and exact integer conversion |
| `hard-fix-wrong-recommendation-retry-charge-v3` | Stable payment identity, mutable descriptions, compound-key collisions and uncertain retry outcomes |

Design rule: declare at least two plausible wrong fixes in each v3 task's
`expected/must-fail.json`, including the hidden case indices expected to catch
each. Every wrong fix must pass the protected visible tests and fail through
the normal grader and verifier. A separate correct implementation must pass
as well. Regression tasks also declare three `must_fail_variants` in
`task.toml`, and check a symptom-only submitted test against those variants.
These files are never staged. The existing capture, hidden execution,
timeouts and deterministic synthetic-input conventions apply unchanged.
Cross-harness/model calibration is a separate operator step after merge;
wrong-variant rejection is authoring evidence, not measured model discrimination.

### Review grading: recall, decoys and any-of windows

A hard-review verifier calls `review_checks.record(gv, ...)` with the optional
parameters below. RFD verifiers pass none of them and grade byte-identically
(`tests/test_review_checks_rfd_parity.py`).

- **Recall.** `min_recall` (a number in (0, 1]) replaces the all-or-nothing
  `defect_found` with a required `recall` check. Each defect still needs its
  own blocking finding. The check passes when found / defects >= `min_recall`,
  and its reason reads `found N of M defects; required K`. Set it so a reviewer
  who stops at the obvious defect fails: the multi-defect tasks use 0.66 of
  three and 0.75 of four. Clean controls cannot set it.
- **Decoys.** `decoys` are windows on code that looks wrong but is correct
  given `pr.md` or the staged context. A blocking finding that overlaps a decoy
  (widened by `slack`) counts toward a required `decoy_resistance` check with
  budget `max_decoy_blocking` (default 0), not toward `precision`, even if it
  also earned defect credit. Decoys must sit more than `slack` lines from every
  defect and tolerated window; an overlap raises, as does `max_decoy_blocking`
  without decoys.
- **Any-of windows.** A defect may be `{"any_of": [window, ...],
  "expected_category": ...}`, for a defect visible at a contract change and at
  an unchanged caller. A blocking finding on any alternative credits the defect
  once; a second finding on another alternative is an excess blocking finding.
  Tolerated windows cannot use `any_of`. Finding-to-defect assignment uses
  augmenting paths, so input order never decides credit.
- **Category enforcement.** `enforce_category = true` restricts defect credit
  to findings with the defect's `expected_category` (or the task default).
  Unspecified categories remain unconstrained. The tool-calls v2 task uses this
  to prevent duplicate security findings from crediting the adjacent named-choice
  API-contract defect. The default is false, preserving advisory categories for
  existing tasks.
- **Several read files.** `read_files` may list several files, and
  `read_evidence` requires each. A cross-file task lists the unchanged caller.

### Fix grading: several targets, variants and the regression-test check

- **Several targets.** The staged manifest's `editable` may list up to four
  files (`fix_audit.MAX_EDITABLE`). Each editable file is declared in
  `[capture] files` and ships a fixture placeholder, including the regression
  test the agent writes.
- **Variants.** `hidden/variants/reference/` and `hidden/variants/insufficient/`
  are required and each ship exactly the non-test editable files. Authors edit
  the `expected/<variant>/` copies; `scripts/regenerate_hard_fix.py` copies them
  into `hidden/variants/`, and a corpus test asserts byte equality.
  `expected/alternative/` (a correct fix that differs from the reference) and
  `expected/literal-advice/` (a wrong-recommendation task's fix that follows
  `finding.md` literally and must fail a hidden case) are corpus-test material
  only.
- **Regression-test check.** A fix-family task with `[hidden_tests]` may
  declare `[regression_test] path = "test_<name>.py"` (a captured, editable,
  top-level file) and `timeout_seconds` from 1 to 60 (default 60). After the
  hidden cases pass, the grader runs the agent's file with stdlib `unittest`
  four times. Each run uses a fresh container with the hidden-case containment,
  limits and nonce isolation, and replaces every non-test editable file with the
  variant's code:

  | Run | Non-test files | Required outcome | Subcheck |
  |---|---|---|---|
  | 1 | original fixture | fails | `regression_test_fails_on_original` |
  | 2 | `insufficient` variant | fails | `regression_test_fails_on_insufficient` |
  | 3 | `reference` variant | passes | `regression_test_passes_on_reference` |
  | 4 | the submission | passes | `regression_test_passes_on_submission` |

  "Fails" means at least one failure or error. "Passes" means at least one test
  ran with no failures, errors, skips, expected failures or unexpected
  successes. Anything else is `inconclusive`, `timed_out` or `error`, and fails
  its subcheck. The trusted controller takes counts from the unittest result,
  never from printed output. If the hidden cases fail or the test file is
  missing, all four runs are `not_run` and all four subchecks fail. The four
  subchecks are grader-owned; a verifier that emits one is a harness error.
  The agent sees the original and its own fix, never the reference or the
  insufficient variant. A test that only detects "the code changed" passes on
  the insufficient variant. A test keyed to the agent's own implementation
  fails on the reference.
- **Named wrong variants (HRX-01).** `[regression_test] must_fail_variants`
  lists up to three distinct names. Each ships `hidden/variants/<name>/` with
  exactly the non-test editable files, like `insufficient`, and may not reuse a
  built-in variant name. The grader adds one more contained run per name, in
  a fresh container under the same containment and nonce isolation, and a required
  `regression_test_fails_on_<name>` subcheck that the run must fail. These
  runs are `not_run` alongside the other four when the hidden cases fail. The two
  regression `-v2` tasks declare `plausible-wrong`: a fix that passes the
  visible tests and a test that only reproduces the reported symptom, but
  breaks a contract stated in staged material. A task without the key runs the
  four original runs and grades exactly as before.
- **Sniffing tripwires.** `gym.integrity.scan_regression_test` scans the
  captured test file, ignoring comments and matching case-sensitively, for
  `inspect`, `dis`, `marshal`, `__code__` and `co_*` code attributes,
  `getsource`, `__file__`, module-spec and loader access (`importlib.util`,
  `linecache`, `tokenize`, ...), hashing (`hashlib`, `zlib`, `binascii`, CRCs),
  `.py` path literals, file reads (`open(`, `read_text`, `read_bytes`,
  `os.read`), directory listings, and result-channel tampering (`os.write`,
  `sys.argv`, `TestResult`, ...). A match holds the cell in `integrity_hold`
  with `regression_test:<marker>` flags. The bench excludes held cells, and the
  operator audits them through the sealed envelope. Like the protocol audit
  above, this is lexical and not an isolation boundary.

### Adding a hard task

- **hard-review.** Copy the nearest `tasks/hard-review-*` task. Start
  `task.toml` and `verifier.py` with a `# Source PR` comment that cites the
  source PR and review, "synthetic", or, for a task derived from a private
  review, `none public; derived from a private review`. Stage `change.diff`, `pr.md` and the
  post-change files. Keep ground truth in `verifier.py` and
  `tasks/<id>/expected/ground_truth.json`, which a corpus test keeps in agreement, plus
  `tasks/<id>/expected/reference.json`. Decoy tasks also record one `decoy_claims` entry
  per decoy. `python3 scripts/review_task_from_pr.py --repo OWNER/NAME --pr N` scaffolds
  real-derived drafts.
- **hard-fix.** Copy the nearest `tasks/hard-fix-*` task. Fixtures are
  stdlib-only Python. The correct behaviour must be derivable from staged
  material (a visible caller, a docstring or a visible test), never from hidden
  cases. Author the fixture, `finding.md`, `tasks/<id>/hidden/cases.json` and
  `expected/{reference,insufficient}/`, then run
  `python3 scripts/regenerate_hard_fix.py tasks/<id> --write`. It refreshes the
  staged manifest, the stage script, the verifier's `PROTECTED` and `ORIGINAL`
  snapshots and `hidden/variants/`. Regression-test tasks also check in
  `expected/regression-tests/` (`good`, `asserts-nothing`, `source-changed`,
  `keyed-to-implementation`, `sniffing`).
- Pin the task in `tests/test_hard_review_corpus.py` or
  `tests/test_hard_fix_corpus.py`; both list the corpus explicitly. Adding a
  task also changes discovery counts and any pilot exclusions in this repository.
  Host-owned activation files (`hrf.yaml`, `hrx.yaml` and their samples) live
  outside this public checkout; the operator must synchronize them when deploying
  a corpus expansion. The HRX v2 probe continues to cover only v2 tasks.

### Battery and activation

`batteries/hrf-pilot.toml` selects both suites, codex (`gpt-6.1-sol`,
`gpt-6-astra`) and claude-code (`claude-opus-5-5`, `claude-sonnet-5-5`) at
`high`, three repetitions (144 cells). It excludes the `-v2` and `-v3` tasks, which are
calibrated separately. Set its token budget before a real run.

`projects/gym-hard-review-fix/activation/hrf.yaml` holds two PMA probes, run
after a deploy that touches the pack. RFD's `rfd.yaml` is unchanged and must
still pass.

- `hrf-task-discovery` must count nine `hard-review` tasks, sixteen `hard-fix`
  tasks, and six in each of `review` and `fix` after the operator synchronizes
  the host-owned probe and its recorded sample for the expanded corpus.
- `hrf-regression-test-containment` builds a synthetic one-target `hard-fix`
  task under the shared HQ scratch root and calls `run_regression_test` twice.
  A trivial test must pass all four subchecks. A socket-opening test must see a
  Linux ENETUNREACH or EHOSTUNREACH (errno 101 or 113) denial in all four
  variant containers. The probe also requires the fix gate open and the
  sniffing tripwire live. It orders `docker info` before image inspection; an
  unreachable engine exits nonzero with `service unavailable` for PMA's bounded
  transient retry, as RFD's probe does. Subprocess timeouts, including during
  `docker info` or `docker image inspect`, also trigger that retry even after
  the engine was reachable. A missing pinned image or a reported regression-test
  fixture timeout remains a subject failure.

`projects/gym-hard-review-fix/activation/samples/prospective-activation.json` is the passing predicate fixture, not host
evidence; PMA activation on the deployed host is. Current contract:
`gym-hard-review-fix@b3689f1ea261`.

`projects/gym-hard-review-fix-rework/activation/hrx.yaml` holds one PMA probe,
`hrx-v2-task-discovery`. It lists the `-v2` ids the deploy checkout discovers
in each hard suite and the `must_fail_variants` each declares. It requires
exactly the three hard-review and five hard-fix ids above, and
`["plausible-wrong"]` on both regression `-v2` tasks. A missing id means the
task did not deploy; missing `must_fail_variants` means the plausible-wrong
check is no longer declared. A task that fails to load makes the probe exit
nonzero. `tests/test_hrx_activation.py` validates the file and grades
`projects/gym-hard-review-fix-rework/activation/samples/discovery.json`.

---

← [Races](races.md) · [Board](board.md) →
