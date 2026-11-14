[Home](../../README.md) · [How it works](README.md) · [Races](races.md) · [Hard races](hard-races.md) · [Board](board.md) · [Race it yourself](race-it-yourself.md) · [Under the hood](the-gym.md) · **Batteries** · [Design](../README.md) · [Vocabulary](../vocabulary.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-batteries-dark.svg">
  <img alt="Batteries: Model efficiency batteries, matrix reports, interrupted runs and the fix audit data model." src="assets/banner-batteries-light.svg">
</picture>

**On this page:** [Model efficiency batteries (MEG-04)](#model-efficiency-batteries-meg-04)

## Model efficiency batteries (MEG-04)

`hq gym matrix run|status|stop` runs a seeded model × effort × task × repetition
battery. The pilot lives in [`batteries/meg-pilot.toml`](../../batteries/meg-pilot.toml);
model IDs and budgets are data. Preview with
`hq gym matrix run --battery meg-pilot --repetitions 1 --dry-run`.
A real run requires an operator-selected positive `--max-total-tokens`.

The manifest and each cell live under `$HQ_ROOT/gym/matrix/<run-id>/`.
Resume polls saved launch IDs, retains completed grades, and accepts the base
battery, a subset trim with the same seed, or an expansion overlay referencing
the immutable base digest. Expansion overlays use `[[pairs]]` with `family`,
`model`, `level`, and target `repetitions`; they add repeats to existing pairs.
A trim remains excluded on subsequent base or overlay resumes.

Every launch uses low priority and origin-owned retries. Enqueueing yields to
throttled/background dispatch modes, the local-time battery window, and fresh
weekly quota evidence for each family's auth path. Token caps, stop requests,
and missing completed-cell token telemetry stop new work while accepted cells
finish. Status includes deferred/unsupported/in-flight counts, spend and overshoot.
Quota command timeouts and OS errors pause enqueueing until a later tick can
read status. Weekly readings may be ahead of the host clock by at most
`quota_reading_max_age_minutes`; readings farther away in either direction
are unusable. Each family/model/level pair probes one launch at a time until
one completes without a refusal or transient error, then shares the global
`max_in_flight` capacity. Saved attempts preserve that evidence across resume
and trims.
See the operator runbook
for safe resume and telemetry recovery.

Completed cross-cell launch aliases remain excluded, charged and reserved.
The owner-only `hq gym matrix quarantine-bindings` command records a separate,
exclusive `binding-recovery.json` after stop/drain and preserved incident evidence.
It seals original attempt bytes and the original run axes/gates; explicit resume
may continue pending cells without rewriting grades, refunding spend or expanding
limits. Changed evidence and unresolved acceptance still fail closed. See the
quarantine runbook.
Resume validates the persisted quarantine context before normalizing battery
defaults, then validates the effective budget after reapplying approved overrides.
The unchanged approved token cap is retained even when the battery default differs.

To restore an exhausted quarantined run's budget after explicit operator approval,
use `hq gym matrix amend-quarantine-budget <run-id> --max-total-tokens <total-cap>
--reason '<approval reason>' --evidence-reference '<operator decision receipt>'`.
It publishes one immutable `binding-budget-amendment.json` anchored to the
original quarantine hash, then updates only the total token cap and its override.
All cumulative spending and cell bytes remain unchanged; STOP stays set and no
worker launches. The original or exactly approved context is accepted so a crash
between publication and manifest update can be retried safely. Any other gate or
evidence drift is refused. This does not resolve unknown dispatch acceptance or
authorize a resume outside the window/quota gates. Further amendments are refused.


### Matrix efficiency reports (MEG-07)

`hq gym matrix report <run-id>` writes
`$HQ_ROOT/gym/reports/REPORT-meg-<battery>-<date>.md` and a JSON twin under
the resolved HQ state root, alongside matrix run state. To publish evidence,
copy the pair into docs/reports/ in a contributor worktree and submit a PR.
The comparison seed (`modules/worker-pool/worker-classes.json`) is read from
the checkout resolved by `agent_os_config.roots.deploy_root()`; report
generation leaves that checkout untouched. It reads the
live governed class defaults and refuses a seed fallback. The command takes the
run lock so a running matrix cannot change its input mid-report. An active run
returns a CLI error naming the run without reading report inputs.

The aggregate rows include SEW's 95% Wilson intervals and independent-arm
Newcombe method 10 comparisons against the incumbent. Only the Newcombe
interval decides resolution; per-task rows are diagnostic, with no claim at
n=3. Cost per success includes failures and captured retries. Missing cost or
quota telemetry reads `unknown`. Exclusions are counted by reason and may
overlap; excluded cells do not enter the aggregate denominators or costs.

`--emit-expansion <path.toml>` emits unresolved frontier and adjacent pairs,
with target repetitions increased by the run's base repetition count. Adjacent
means the next Pareto layer after removing the frontier. Quota and dollar
frontiers are separate labelled axes, since those units cannot be compared.
Unknown on both axes cannot qualify for the frontier. The overlay retains the
run's base digest and can be passed to
`hq gym matrix run --resume <run-id> --battery-path <path.toml>` after the
operator approves more sampling. When no pairs qualify, the report and JSON twin
are still written, the command succeeds, and the requested overlay path is left
untouched.
The report command never launches workers or changes defaults.


### Deploys and interrupted runs

Explicit exit-6 drain refusals pause the same staged launch until the drain
clears. The total wait per launch defaults to 20 minutes; set
`GYM_DRAIN_WAIT_TIMEOUT_SECONDS` to override it for run, matrix, or scheduled
runs. Gym only reads drain status and never resumes a drain. An expired wait
records a harness `error` naming the epoch. If a prior invocation may have been
accepted, acceptance remains unresolved and matrix admission still halts.

Schema-behind-Alembic-head refusals also pause the same staged launch, with
bounded exponential backoff for up to 10 minutes of schema sleeps per launch.
Drain polling, drain retry pacing, and transient dispatch retries do not consume
that schema budget. Gym recognizes the message
`schema is behind Alembic head` case-insensitively on stdout or stderr, without
relying on a backend-specific exception name. Set
`GYM_SCHEMA_WAIT_TIMEOUT_SECONDS` to override this bound. An expired wait records
a harness `error` containing a bounded schema diagnostic and versions, without
a model outcome in a suite run. HQ does not emit a distinct pre-admission schema
refusal: a ledger open after enqueue can produce the same message. Gym therefore
retains the workspace and launch key on expiry, and matrix admission halts for
acceptance recovery even if the first invocation reported schema drift. The real
worker-pool backend therefore halts the matrix on every schema timeout; it does
not mark that cell done and continue. A backend
that proves refusal before admission may raise a plain `DispatchSchemaTimeout`;
matrix records that as a terminal harness error and never redispatches the cell.
The schema wait budget is per launch, so a suite with multiple cells can wait
that budget for each cell during a persistent outage.

If schema refusals give way to a drain timeout or exhausted transient retries,
suite runs still record a harness `error`. Any unresolved dispatch acceptance
retains the staged workspace and launch key; matrix runs halt for recovery.

Verifier support code is captured when the grader module loads. Before the first
cell dispatch, the suite snapshots every selected task verifier. Every sandbox
stages those bytes, so a deploy during dispatch cannot combine an old grader
with newly deployed helpers or change a later cell's verifier.
The snapshot also pins hidden files and fix fixtures, including the editable
manifest. Task-file read failures are retained in the snapshot and deferred to
grading, where they become an affected cell's harness error; other tasks can
still run.

The same pin holds each task's public files (everything outside `hidden/` and
`expected/`). Dispatch stages the prompt, stage scripts, and fixtures from that
copy, so a deploy mid-run cannot hand a later cell a newer task. Public files
are streamed into a temporary directory on disk; the pin retains paths and
file modes rather than fixture bytes in heap memory. Staging copies from this
snapshot, which is removed when the enclosing run finishes or raises. `hq gym
matrix` pins verifiers and task inputs once at matrix start. Each cell's
grading reuses that pin instead of re-reading the disk when the cell finishes.

#### One gym-library version per run (GYMSKEW-02)

Several gym modules are imported lazily, inside functions. If a deploy lands
mid-run, the first lazy import loads the new file against old modules already
in `sys.modules`. On 2026-10-06 that left every fix cell at `ImportError: cannot
import name 'is_fix_family' from 'gym.schema'`. To prevent it, `hq gym run`,
`hq gym baseline update`, `hq gym matrix run`, `hq gym toc-matrix` and `hq gym
endpoint` call `gym.preload_library()` before any cell dispatches. It walks
the package with `pkgutil.walk_packages` and imports every module, so each later
lazy import resolves from `sys.modules` at the version the run started with. The
lazy imports themselves stay in place.

Matrix resume uses `hq gym matrix run --resume <run-id>`, so it takes the same
preload path as a fresh run. The new process pins its own library version before
reading matrix state or dispatching cells; it does not reuse the prior process's
library pin.

The preload skips the modules in `gym.library.EXCLUDED`. Each has a documented
reason, and `tests/test_library_preload.py` fails when a new module is neither
preloaded nor excluded, or when an exclusion's reason stops being true:

- `gym.matrix_report` imports the optional searchlight overlay (`sew`), and
  only the short-lived `matrix report` verb uses it.
- `gym.toc_eval` imports `cwp_dispatch`. It is a driver that grades nothing
  in-process; each attempt is a fresh `hq gym run` that preloads itself.

Preload wraps filesystem, import, and syntax failures in `GymLibraryChanged`.
Pinned loaders also supply their verified source bytes to traceback tools,
including Python source-encoding handling, so diagnostics match executed code.

A content-identity guard backs up the preload:

- When the package is first imported, `gym/__init__.py` records a sha256 of
  every `.py` file. The preload refuses to pin, exiting 1 with
  `gym_library_changed_mid_run`, if the disk changed between process start and
  the end of the preload, or if the process-start identity could not be read.
  It never substitutes a later identity for an unreadable starting snapshot.
  Run entrypoints print the refusal on stderr and return 1 without a traceback.
  It also checks the grader's verifier support bundle
  against the pinned files.
- A `sys.meta_path` finder guards every `gym.*` import that still reaches the
  disk, such as an excluded module. It executes only the exact pinned bytes. A
  new or changed file raises `GymLibraryChanged`.
- The cell records a harness `error` whose reason starts with
  `gym_library_changed_mid_run`. This applies to the guard error, and to an
  `ImportError` from a gym module after the disk has diverged from the pin.
  Dispatch re-raises the error instead of returning `dispatch_failed`, and
  matrix records the cell `done`/`error` instead of deferring it. A
  mixed-version cell never grades as a model failure.

A dispatch, grader, or verifier exception records an `error` cell with its
exception type and reason, then continues to the next task. Harness errors count
in the summary's `errors`, make the CLI exit nonzero, and prevent baseline
updates; they do not trigger model regression alerts.

`hq gym run` writes a result JSON for both default and explicit models under
`$HQ_ROOT/gym/results/`. It creates an empty checkpoint after setup checks and
atomically replaces that same file after each cell. Interrupts or early process
termination leave the completed cells available; an in-flight cell is absent.
Setup failures before dispatch still fail closed. Storage failures are reported
and stop the run because subsequent results could not be preserved.


### Unknown acceptance investigation

`hq gym matrix acceptance-report <run-id> --cell-id <cell-id>
--launch-request-id <original-lrq> --evidence-reference <incident-proof-ref>`
reads a stopped run under its existing nonblocking native run lock and prints
JSON to stdout. Repeat `--launch-request-id` for at most eight candidate LRQs.
No workers are dispatched, no result is graded, and no run file is written.
Exit **2** means the acceptance outcome remains unresolved; exit 0 is deliberately
unavailable. An absent canonical worker, a terminal worker, or a rolled-back LRQ
is not a durable historical nonacceptance certificate.

The report hashes every original JSON file and STOP, keeps charged attempts,
labels canonical read failures, identifies cross-cell/current worker ownership,
and explicitly leaves historical process absence and logical ownership unproved.
Absent launch rows also leave intent binding explicitly unverified. Missing
`manifest.json` or saved cell attempts raises an actionable diagnostic rather
than treating incomplete history as zero charges. The command closes only the
reader it opens, through `LedgerReader.close()`; Postgres owns no local connection.
Candidate LRQs and evidence references are investigation inputs, never authority
for rebinding a cell. No canonical API currently supplies a comprehensive
nonacceptance certificate, so `confirmed_nonacceptance` remains false. Unknown
acceptance token exposure is null, not zero. The existing runner halt remains.
Ordinary matrix reports exclude these cells as `acceptance_unknown`, rather than
asserting they were `never_run`. The FRC-06 walkthrough (`FRC-06-walkthrough.md`, kept
with the Agent OS host plugin) records evidence, regression checks, limitations
and operator follow-through.


### Fix audit data model (GYMFIXAUDIT-01)

Fix `Cell.to_dict()` and run JSON `summary.cells[]` carry an optional `fix_audit`:
`{version: 1, format: "cms-der", ciphertext: "<base64>"}`. The serialized envelope
is capped at 256 KiB, encrypted with OpenSSL CMS AES-256-GCM for the
operator-provisioned public RSA certificate (`GYM_FIX_AUDIT_CERT`). Only the
operator holds the decryption key. Artifacts live inside the run result and
inherit its retention; no plaintext audit is persisted by this feature.

Decrypted schema version 1 contains `task`, `launch_request_id`, `verdict`,
`integrity_flags`, `held_verdict`, `files` and `cases`. File entries contain
`file`, `submitted`, `diff`, and `truncated`, or `missing: true`. Original bytes
and the editable manifest are pinned with the verifier snapshot before dispatch;
worker-written manifests never select audit paths. Case entries contain `index`,
`label` (`case-N`), `outcome` (`passed`, `failed`, `raised`, `error`, `timed_out`),
`elapsed_seconds`, `executed` (false when grading never reached the case), and optionally `raised_type` (builtin exception identifier or
`custom`). A matching raised expectation is `passed` with `raised_type` attached.
No calls, expected values, observations, protocol frames or diagnostics enter
the case artifact. Exceptional grading records carry `incomplete: true`; records
that exhaust the cap disclose `truncated: true` and may omit files/cases.

`hq gym show <run> --cell <index|launch-id|task-id> [--task <task>]` decrypts and
prints the audit in an operator shell with `--certificate` and `--key` (or
`GYM_FIX_AUDIT_CERT` / `GYM_FIX_AUDIT_KEY`). See the runbook's
Auditing integrity_hold
for provisioning and the private-key boundary. There is no backfill for deleted
worker workspaces.

Standalone results include per-cell `execution_provenance` with the resolved
runner, harness, Docker image and effective model (including user-config models).
Verifier diagnostics, integrity-held verdicts and grading exceptions are redacted
after grading; workspace files remain intact for verifier correctness. Both
container streams are drained continuously with an 8 MiB combined output budget
and a 1 MiB event/line budget. Overflow triggers confirmed container containment
and `output_limit_exceeded`, never grading partial evidence.

---

← [Under the hood](the-gym.md) · [Design](../README.md) →
