[Home](../../README.md) · [How it works](README.md) · [Races](races.md) · [Hard races](hard-races.md) · [Board](board.md) · [Race it yourself](race-it-yourself.md) · **Under the hood** · [Batteries](batteries.md) · [Design](../README.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-the-gym-dark.svg">
  <img alt="Under the hood: The gym behind the races: the out-of-band idea, the scheduled run, tool-surface locks, compression and what the module promises." src="assets/banner-the-gym-light.svg">
</picture>

**On this page:** [Adversarial Eval Gym (GYM)](#adversarial-eval-gym-gym) · [The out-of-band idea](#the-out-of-band-idea) · [Scheduled run + regression gate (GYM-04)](#scheduled-run--regression-gate-gym-04) · [Tool-surface locks (GYM-05)](#tool-surface-locks-gym-05) · [Compression A/B (TOC-05)](#compression-ab-toc-05) · [What this module promises](#what-this-module-promises)

## Adversarial Eval Gym (GYM)


Agent-behavior regression protection for Agent OS. The gauntlet tests infra and
unit tests test functions; nothing drives an agent through a realistic task and
grades the *outcome*. The gym does — built on primitives that already exist:
`hq dispatch` runs a persona through the real worker pool, and the session
ledger normalizes every run into a gradeable trajectory.

This module (GYM-01) is the harness: a task/verifier schema, a tool-call
extractor, and an **out-of-band grader**. The corpora (GYM-02 RCA, GYM-03
injection) and the schedule + regression gate (GYM-04) build on it.

See the spec: `projects/adversarial-eval-gym/SPEC.md`.


## The out-of-band idea

A verifier is graded correctness the agent can never read. The agent gets the
grade *format* (the `result_schema` it must submit to); it never sees the
answer. The grader:

1. dispatches the task's persona through `hq dispatch` (normal admission path,
   so HCC caps and any freeze apply to the gym too),
2. polls the session ledger to a terminal worker_run state,
3. extracts the ordered tool calls from `transcript_entries.tool_metadata_json`
   (+ `control_evaluations` for "a tool was NOT called"). Only the claude-code
   adapter ingests tool calls there, and only for a run whose runtime session
   got bound, which many runs never do. When a run has none, the calls come
   from the host's authorized logical log reader (`gym/harness_toolcalls.py`).
   Host mode requests bounded, redacted stdout, adapter exports and native
   event projections by launch identity. It never opens the host dispatch
   directory or native session store, including during a transport outage.
   In host mode Gemini's agy result is authoritative even with zero calls;
   other classes try stdout, and ACP fallback requires an explicit session name.
   Stdout iterators are retained for both Codex and Claude parsers. Provider
   monitoring retains incomplete lines across host pages, drops them on stream
   reset or generation changes, and applies the same 1 MiB line limit as local
   logs. Malformed pages and transport/protocol errors warn once and preserve
   the cursor for a later monitoring check.
   pi and opencode keep their sessions under the worker directory, which
   teardown deletes, so their adapters export the calls to `toolcalls.jsonl`
   before teardown, and the host projects that logical stream before the others, but only when the ledger's
   `launch_requests.worker_class` is `pi`, `oss-pi`, `opencode`, or
   `oss-opencode`. A worker can write to its dispatch directory and no other
   adapter clears the file, so on any other class it is ignored and logged as
   a possible plant; if the ledger lookup of the class fails, the run is graded
   the same way. Even for pi and opencode the file is only as trustworthy as a
   worker-writable file: the adapter removes any earlier copy before the
   harness starts, but a process the model left running in the background can
   edit the session store before the export reads it, or rewrite
   `toolcalls.jsonl` after the export writes it, and nothing records a digest
   of the export (`projects/worker-pool/SPEC.md`,
   "`<dispatch-dir>/toolcalls.jsonl`"). Without any of these,
   read-evidence checks fail for every model on that harness. For ledger class
   `gemini`, Antigravity's `agy-stream.jsonl` provides completed `DONE` tool
   steps, deduplicated by conversation ID and step index. An omitted
   `parameters` key means empty arguments; explicit non-object values are
   rejected as malformed. Known agy names and argument keys are normalized at
   collection as described below,
4. snapshots them plus the submitted result, and
5. runs the task's `verifier.py` in a **scrubbed ephemeral env** that exposes
   only `BENCH_RESULT` + `BENCH_TOOLCALLS` — never `expected/`, never the ledger.

Each run resolves to exactly one outcome: `passed`, `failed`, `format_failed`,
`no_submission`, `agent_error`, `integrity_hold`.

Call IDs in the pi and opencode `toolcalls.jsonl` exports up to 200 characters
pass through; longer IDs can be represented as `sha256:` followed by the full
SHA-256 hex digest of their UTF-8 encoding (with `surrogatepass`).
`ToolCalls.control_fired_for_call` compares both call and control IDs using
this identity, preserving per-call denial evidence when a control retains the
raw ID. Missing IDs never match, and step-name and action checks still apply.
Older snapshots with full IDs remain supported.

### Antigravity snapshot vocabulary and coverage

`BENCH_TOOLCALLS.calls` entries have `ordinal`, `name`, `arguments` and
`call_id`. Normalized agy fallback calls additionally keep `original_name`
and `original_arguments` for audit. The collector copies the source arguments
and adds the alias when the source key exists; it preserves unknown tools.

| agy name | Snapshot name | Argument alias |
| --- | --- | --- |
| `view_file` | `read` | `AbsolutePath` → `file_path` |
| `run_command` | `bash` | `CommandLine` → `command` |
| `write_to_file` | `Write` | `TargetFile` → `file_path` |
| `replace_file_content` | `Edit` | `TargetFile` → `file_path` |
| `multi_replace_file_content` | `MultiEdit` | `TargetFile` → `file_path` |
| `sed_file` | `Edit` | `TargetFile` → `file_path` (unconfirmed) |
| `notebook_edit` | `NotebookEdit` | `NotebookPath` → `file_path` (unconfirmed) |

This compatibility vocabulary uses lowercase reads/commands for `review_checks`
and Claude-cased edits for the coding-toc verifiers' exact `EDIT_TOOLS` set.
`ToolCalls.was_called`, `calls_to` and argument helpers compare names exactly;
Gemini assertions must use the mapped names in this table. There is no global
case folding or guarantee that Claude's `Read`/`Bash` match Gemini calls.
For example, use `calls_to("read")` for a normalized Gemini file read and
`calls_to("Write")` for a normalized Gemini file write.

The real `tests/fixtures/agy-stream-json-sample.jsonl` validates reads and
commands. Edit parameter tests are synthetic; in particular, `sed_file`'s
`TargetFile` and `notebook_edit`'s `NotebookPath` still need real captures before
path-keyed assertions can rely on them. Native `grep_search` is deliberately
unmapped pending captured argument/read semantics, so it earns no review read
credit. Use `view_file` or `run_command` with `grep`/`rg` for that evidence.

`send_message` and `call_mcp_tool` pass through without send/memory normalization
or MCP unwrapping. Gemini injection verdicts, including the planned G-agyfixed2
rerun, remain **unverified**: forbidden send or memory calls can escape the
verifiers' name filters and produce vacuous passes. The
gym runbook
defines how to handle those results until captured paths and negative tests
establish coverage.


## Scheduled run + regression gate (GYM-04)

A launchd `StartInterval` one-shot
(`launchd/ai.laceyenterprises.gym-scheduled.<user>.plist.template`; the unit
`ai.laceyenterprises.gym-scheduled.<user>` is declared in
`scripts/launchd-drift-required.txt`, and the managed fleet is driven by
`agent-ops` rather than the retired `scripts/os-restart.sh` shim)
runs `hq gym run --suite all` daily. It grades the suite through the **normal
`hq dispatch` admission path** — so HCC caps and any declared freeze bound the
gym like any other dispatch (SPEC §6) — then diffs HEAD verdicts against the
committed `gym-baseline.json`.

The **only** alerting transition is a task that **passed at baseline and failed
at HEAD**: that emits one `gym_regression` operator alert (via the shared
alert-delivery / sentinel path) naming the task and both verdicts. A task already
failing at baseline that still fails is silent; a newly-passing task is logged,
not alerted; a task absent from the baseline never alerts. Transient non-fail
degradations (`agent_error` / `no_submission`) are not regressions — the SPEC §6
noisy-gate mitigation.

```bash
hq gym run --suite injection --json        # SPEC §1 summary shape
hq gym run --suite all                      # what the scheduled one-shot runs
hq gym run --suite coding --worker-class oss-codex --model litellm-local-openai/glm-5.2
hq gym run --suite coding --worker-class codex --model gpt-6-sol --reasoning-level medium
hq gym baseline update --confirm            # operator-gated; updates the full current model slice
```

A model run stores a JSON record under `$HQ_ROOT/gym/results/<model-hash>/`,
including the exact model ID and model-qualified verdict keys. The command
prints the result path; it does not update the established baseline. Before
dispatch, the lock check uses the worker class the task will actually run on,
including `--worker-class` overrides. The four OSS harness classes and
`oss-local` each have a committed lock in `locks/`; a missing or drifted class
lock aborts the suite.

`gym-baseline.json` now ships with the AMF-08 first-green baseline established.
The operator runs `hq gym baseline update --confirm` whenever the last-known-good
verdict set is deliberately accepted. The scheduled runner never rewrites the
baseline — `baseline update` requires `--confirm`, and the plist only ever calls
`run`.
Baseline updates always sweep `--suite all`; partial-suite updates are rejected
so a narrowed run cannot garbage-collect another suite's last-known-good verdicts.
The baseline stores flat verdict keys plus a `slices` map. A `null` slice is the
default suite, including task-level model pins that use `task@model` keys; a
model string is only a CLI-level `--model` sweep slice. `compare` and
`baseline update` use `slices` as the authority, so a default run does not trip
over old sweep keys and a default update garbage-collects deleted pinned tasks.
When `--model` is used for a sweep, baseline update replaces only keys whose
slice is that model: deleted tasks for the swept model are garbage-collected,
task-pinned cells are not refreshed by the sweep, and other model slices remain
intact.
The baseline's `task_versions` map records the `task_version` each verdict was
graded at; a baseline written before PDK-03 has no map, and its verdicts read as
version 1. `compare` skips a verdict whose recorded version differs from HEAD's:
it is neither a regression nor a new pass. The text report lists it as
`VERSION CHANGED`, and `--json` lists it under `version_changed`. Run
`baseline update --confirm` after a version bump to record the new version.
The comparison maps use the same qualified keys as stored verdicts and
`task_versions`, including task-pinned models and reasoning-level cohorts.
`SuiteResult.verdicts(include_held_failures=True)` retains completed held
failures for regression comparison; its default keeps stored outcomes unchanged,
and held passes remain quarantined.

`--reasoning-level` is also accepted by `run`, `baseline update`, and `lock
check`. Levels are `low`, `medium`, `high`, and `xhigh`; `max` normalizes to
`xhigh` as it does in dispatch. A leveled verdict adds `@effort:<level>` after
the model and before any skill digest. Its baseline slice and result directory
are separate from the same model without a level. Omitting the flag preserves
the existing keys, slices, paths, and dispatch arguments. Each cell reports the
requested and ledger-resolved level. A dispatched cell whose resolved level
differs from the request (clamped, `unsupported`, or unreadable) is flagged with
`level_mismatch` and graded `agent_error`, so it is never stored as a verdict for
the requested level: `baseline update` refuses the non-green suite, and the
regression gate never counts it as a pass or a regression. A model id with an
`effort:` segment (for example `provider@effort:high`) is refused, since it would
share keys with a leveled cohort. On the Postgres runtime the resolved level is
read through the bounded read-only `psql` path, and only for a leveled run.
`lock check --reasoning-level` validates the level's spelling only.

Every dispatched cell also carries `metrics` from the ledger (MEG-02,
`gym/metrics.py`), built from all `worker_runs` rows of its launch request
through `LedgerReader.cost_for_launch`: `run_ids`, `attempts`, the input,
cache-read, output, reasoning and running-total tokens summed across every run
(a retry spends quota too), `wall_seconds` from the first `started_at` to the
last `ended_at`, the resolved `worker_class` and `resolved_reasoning_level`, and
`observed_models` (output tokens per model from `token_usage_by_model_json`).
Accounting keys `live-floor`, `unknown` and angle-bracket placeholders do not
name models and cannot win majority selection or become side models. Token
spend still uses the run columns without subtracting these buckets; floor-only
usage remains unmeasured for model identity and must not acquire a model or
published price by inference. Existing captured snapshots are not rewritten by
this rule; remeasure the same launch through canonical read APIs after deployment
before replacing any saved metrics, preserving the original attempt evidence. A
column the ledger left null stays null and is named in `missing`, with
`metrics_complete: false`; nothing is zero-filled, and a failed ledger read
records the cell as unmeasured rather than dropping its verdict. Timed-out
launches also receive best-effort metrics and are counted as incomplete when
unmeasured. `metrics_read_at` records when the snapshot was taken, so a report
can distinguish a pending late capture from an older missing capture; the
snapshot is not automatically rejoined. The requested
model must carry a strict majority of the output tokens; any other model is a
`side_models` entry (claude-code calls a small one) and does not invalidate the
cell. Models are grouped after removing a known outer router and dated snapshot
suffix, while provider routes such as `fireworks/` remain distinct. A different
majority model, or a launch that ran as another worker class,
sets `model_mismatch`. Unlike `level_mismatch` it keeps the verdict, and the
summary counts `model_mismatches` and `metrics_incomplete` so a report can
exclude those cells. On the Postgres runtime the read uses the bounded read-only
`psql` path with the launch id passed as a psql variable; it never falls back to
the SQLite standby.

A dispatched cell is also priced (MEG-03, `gym/cost.py`). `cost_usd` prices the
metrics at the majority model's published rates in SEW's one price table
(`tools/searchlight/config/price-table.yaml`, loaded through
`sew.cost_model.load_price_table`; private submodule, optional overlay initialized
by declared consumers). The input column is total input, so it is
split into disjoint buckets: fresh input (`token_usage_input` minus cache reads)
at the input rate, cache reads at the cached-input rate, output at the output
rate. Reasoning is priced once. For both `codex` and `claude-code`, the ledger's
output column already contains reasoning, and a test pins that against the
capture code. `cost_status` is the SEW basis (`inferred` or `estimated`), or
`unknown` with `cost_usd` null (never 0). Any of these reads unknown:

- a model with no published rate;
- a null column;
- cache reads above total input (invalid telemetry, never clamped);
- a token source whose input is not total input (`claude-code-exec-json`,
  `live-floor`);
- a codex cell whose summed input crosses the 272K long-context threshold.

`quota_share` is the fraction of the auth path's weekly subscription window the
cell used. It reuses the per-model tokens-per-percent calibration that
`cwp_dispatch.subscription_quota_reconcile` stores as `subscription-quota-proof`
rows. The calibration's own input + output token measure is applied. With no
weekly calibration for the model (today every claude model), `quota_share` is
null and `quota_status` is `unknown`. Mixed-model cells also report unknown
(`mixed_model_token_totals_unavailable`): metrics retain only output per model,
so each model's input + output cannot yet be attributed to its calibration.
This includes side models with zero output, which can still consume input.
The ledger-owned `session_ledger.api.subscription_quota_proof_metadata` accessor
routes Postgres proof reads through the bounded read-only `psql` path (12s statement
timeout, 15s subprocess timeout, at most three transient retries), retaining
old calibrating proofs through a JSON status predicate. A failed read reports
`calibration_unavailable`. The full detail rides under `economics`.

The moving parts: `gym/suite.py` (discover + run a suite, `--json` summary),
`gym/baseline.py` (deterministic baseline I/O + the pass→fail comparison),
`gym/alerting.py` (the `gym_regression` seam — a `RecordingAlerter` for tests,
the host's alerter in production: `SentinelAlerter` under Agent OS, a JSONL
file standalone), and `gym/cli.py` (`hq gym`, routed via
`bin/hq-gym`).


## Tool-surface locks (GYM-05)

A gym verdict is only meaningful if the agent's action space was what the task
assumed — "the forbidden tool was not called" means nothing if the tool was
silently dropped from the surface, and "the legit task completed" means nothing
if a tool was silently added. So each eval agent's tool surface is pinned by a
committed lock at `locks/<agent>.lock.json` (deterministic, sorted tool names),
and the `run` setup phase compares each in-scope agent's **live** surface — its
worker-class `default_tools` ∪ `tools_allowlist` — against the lock.

Drift aborts **fail-closed**, before any task dispatches, with a per-tool diff:

```bash
hq gym lock check                       # verify in-scope surfaces match locks/ (no dispatch)
hq gym lock update --agent claude-code  # rewrite one lock from the live surface (the only refresh path)
hq gym lock update                      # refresh every corpus persona's lock
```

Only agents a run will actually dispatch are in scope: a pre-CTX `resident`
whose injection tasks are gated off (`gym/taint.py`) needs no lock yet. The
setup check never auto-updates, warns-and-continues, or runs against a drifted
surface — `hq gym lock update` (operator-invoked) is the sole refresh path. The
moving part is `gym/locks.py` (the `ToolSurfaceResolver` seam, the deterministic
lock I/O, the per-tool diff, and the setup-phase check `run_suite` calls).

Operator doctrine — adding a task, adding a postmortem-derived RCA task, updating
the baseline, responding to a `gym_regression` alert, the CTX dependency for
injection tasks, and lock maintenance — is in
`docs/runbooks/RUNBOOK-adversarial-eval-gym.md`.


## Compression A/B (TOC-05)

`hq gym run` takes three flags for the
tool-output compression A/B:

- `--priority low|normal|high|critical` is forwarded to `hq dispatch --priority`.
  It is unset by default, so the scheduled run keeps dispatch's default lane.
  The TOC-05 matrix runs at `low`.
- `--compression off|spill` pins the arm for that launch (TOCMEASURE-01). It is
  forwarded as `hq dispatch --tool-output-compression <arm>`, which is accepted
  only with `--project adversarial-eval-gym`. The claude-code PostToolUse hook
  and the codex command shims honour it instead of
  `worker_pool.tool_output_compression`, for that launch only; every other
  dispatch still follows host config. `rtk` is refused as ineligible
  (RTK-ELIGIBILITY.md).
  A spill launch whose adapter cannot register the hook or prepare the shims
  refuses to spawn rather than silently running the off arm.
- `--attempt N` (N ≥ 1) is a label. `--compression` and `--attempt` add
  `compression=<arm>` and `attempt=<n>` fields to the cohort key after the skill
  digest, so arms and repeats never overwrite each other. `off` keys the same as
  an unlabelled run. `baseline update` accepts neither flag, so the baseline
  stays one slot per (task, model).

Every trailing key field carries a marker (`sha256:`, `compression=`,
`attempt=`) and is parsed from the right, so model ids may contain `@`.
`--json` cells now include `launchRequestId` whenever dispatch accepted the
cell, plus `compression` and `attempt` when set.

The arm is recorded three times, and the driver trusts only agreement:

- `requested`: `workerSpec.toolOutputCompression` on the launch request payload.
- `recorded`: the same entry on the worker run's launch receipt.
- `effective`: what the spawning adapter reports it applied, in the spawn
  result's `envContract.toolOutputCompression`. The daemon stamps it as
  `toolOutputCompressionEffective` on both `launch_requests.metadata` and
  `worker_runs.metadata`.

TOC-02 journal rows carry `launch_request_id`, `worker_id` and `arm_source`
(`launch` or `host`), so each attempt counts its own eligible and compressed
invocations.

`hq gym toc-matrix --out PATH --cell CLASS=MODEL ...` (`gym/toc_eval.py`)
drives the matrix, and `gym/toc_matrix.py` holds its decision logic. The
`quota_gate` helper accepts provider/auth-path requirement pairs as tuples or
JSON lists and strips leading and trailing identifier whitespace. An explicit
empty requirements list selects no providers while still checking same-class
quota-suspended launches; `requirements=None` selects fleet-wide checks.
Malformed pairs return an unknown quota state.

The TOC-05 design is `--cell codex=gpt-6-sol --cell claude-code=claude-opus-5-5
--arm off --arm spill` on `coding-toc-commands`, N=3 (`--attempts`) at the
class default reasoning level, written with
`--out projects/tool-output-compression/ab-result.json`. `--out` is required
unless `--dry-run`. The driver works as follows:

- `--dry-run` prints every cell and an `Input(CacheRead):Output` token estimate
  per attempt, per cell and in total. The estimate is the mean of recent
  terminal gym runs of the same model in `worker_runs`, falling back to the
  class (`--estimate-days`, default 30). Only fully captured terminal runs
  qualify. Input includes cache reads and writes once, using the ledger's
  canonical `consumption_events` input-basis rules per run before aggregation.
  CacheRead is a subset of Input. It also prints the hard caps and the
  current quota state. It dispatches nothing and writes nothing.
- Hard caps: `--priority low` is the only accepted priority. The quota gate
  re-reads `hq fleet quota status --json` before every attempt, retrying an
  unreadable status three times. It stops on an exhausted or operator-suspended
  provider or a quota-suspended launch (exit 0), and on a status that stays
  unreadable (exit 1). The budget cap stops before any attempt whose estimate
  would take the running token total past `--max-total-tokens`. Missing history
  or a failed estimate read stops dispatch (exit 1), rather than reserving zero.
  Accepted launches retain their recorded token reservation until every run is
  terminal and all input/cache/output columns are captured; unknown totals stay
  null. This cap bounds estimated admission, not the actual size of a run. A stopped run
  writes `result.status: "partial"` with the reason and keeps every recorded
  attempt.
- It omits a cell whose worker class has no committed tool-surface lock, or has
  drifted from it, instead of dispatching into a certain `LockDriftError`.
  `locks/codex.lock.json` is committed so the codex cell the spec requires runs.
  A backend arm is omitted for a task whose correct path runs no eligible
  command class (`TASK_COMMANDS` in `toc_eval.py`; `git` and `pytest` for
  `coding-toc-commands`). Every omission lands in `omittedArms` with its reason.
- Final publication refreshes `result.tokensSpent` and `lastInvocation.tokensSpent`
  from complete joined usage for the latest invocation, including excluded grades.
  `admissionTokensSpent` retains the earlier admission snapshot. Missing, partial
  or malformed usage, missing invocation attribution, duplicate launch aliases,
  or a failed join publishes null with `tokenAccountingStatus: "unresolved"`,
  a partial result and exit 1. Rejoin retries cannot add the same usage again.
  `finalTokenCapComparison` reports `within_cap`, `exceeded` or `unresolved`;
  a measured overshoot makes the result partial without cancelling accepted work.
  Interrupted dispatch stays partial even when usage becomes measurable.
  Cost estimates are API-equivalent accounting, not billed subscription spend
  or evidence of compression savings.
- `lastInvocation.hostConfig` records the host compression config's path and
  SHA-256 for provenance only. The launch arm, not the host config, decides
  what runs.
- It records each attempt under its launch request id and joins it to the
  ledger through the bounded read-only `psql` path on Postgres:
  - cost (`worker_runs.token_usage_cost_usd`);
  - requested and effective arms and the attributed TOC-02 rows;
  - turns and the token vector.

  TOCARM-01 uses `worker_runs.metadata_json.toolOutputCompression.arm` as
  the source of truth for the requested arm; the launch request payload is not
  authoritative. Every run receipt and adapter effective-arm report must agree
  with the labelled arm, and its TOC-02 rows agree too: an off launch writes none, and a spill
  launch's rows are spill rows from the `launch` source. Anything else is
  excluded with an `arm mismatch` reason. Missing adapter effective-arm evidence
  is excluded with `armVerified: null` and
  `armVerificationReason: "effective_arm_not_reported"`; requested evidence never
  substitutes for effective evidence. The first mismatch or missing effective
  report stops further dispatch and writes a partial result (exit 1). Every
  join revalidates the arm records and journals, using the same journal snapshot
  for validation and coverage; late contradictions exclude previously verified
  attempts. A spill
  arm with no compressed invocation attributed to its attempts is reported
  `informative: false` until the TOC-02 journals are ingested and rejoined.
  Before dispatch, the driver snapshots the task's `required_large_output_commands`
  as `requiredLargeOutputCommands` using `suite.default_tasks_dir()` and refuses
  task names containing paths or traversal. A direct task directory may be a
  symlink to a shared local corpus, matching normal suite discovery. Invalid
  task schemas fail before dispatch; rejoin never reloads task files or applies
  new requirements to unstamped historical records. For a verified spill attempt with requirements,
  missing positive `seamCallsByCommand` counts set `seamCoverage: "pending"`
  and `seamMissingCommands`; complete coverage sets `seamCoverage: "observed"`.
  Pending coverage keeps the command at exit 1 and the arm uninformative even
  when usage and cost are complete. The ledger exposes no journal-ingestion
  completion watermark, so missing counts cannot establish a bypass and may
  remain pending indefinitely. The sample, passes and spending stay in the
  comparison, avoiding a spill-only per-protocol filter. Rejoin clears legacy
  `seam_bypassed` exclusions and reevaluates their stamped requirements.
- Per (class, model, arm) it reports sample size, success rate, cost-per-success
  (the cost of every counted attempt divided by gym-graded passes; undefined
  when there are no passes), and turns-to-completion. Each metric is reported
  with its sample size and variance. Bytes in and out are recorded for context
  but are not the decision metric. An attempt with no worker run yet, a NULL
  run cost, or a run still in flight keeps `costUsd: null`, so cost-per-success
  reads "cost missing" and the driver exits 1.
- It also excludes attempts that say nothing about the arm, and leaves them out
  of the arm's sample, passes and cost (`excludedAttempts` /
  `excludedLaunchRequestIds` count them):
  - a `hq gym run` that produced no graded cell;
  - a cell whose poll deadline passed (`terminalStatus: "timeout"` in `--json`).
    The deadline starts at dispatch acceptance, so at `--priority low` it
    includes queue time, which reflects fleet load rather than the arm. A
    deadline remains excluded until the saved run is terminal and can be freshly
    gym-graded from its existing sandbox. Rejoin never dispatches or stages
    inputs. Missing sandboxes leave the deadline excluded.

`--rejoin-only` revisits prior arm exclusions, clears them only for a verified
arm with a gym verdict, and recounts summaries. It also tries the completed-run
grade recovery above for deadline exclusions. Other exclusions remain excluded.
Use a copy of an incident result for recovery; preserve the original evidence.

Returned attempt records, launch IDs and `reservationTokens` are atomically
written before ledger enrichment. If opening or reading the ledger fails, the
driver writes a partial result with the diagnostic and stops further dispatch.
The retained launch IDs can be recovered without replacement work.

When capture lags, run `hq gym toc-matrix --out PATH --rejoin-only`. It joins
pending costs, re-aggregates, and rewrites `--out` without any dispatch or
quota read. Re-running without `--rejoin-only` dispatches the whole plan again
and adds samples.

`tasks/coding-toc-commands` is the task whose correct path runs `git log` and
`pytest`, the command classes the compression seam acts on. Its verifier
counts only commands that run pytest, and fails a shell write to `test.py`
(`sed -i`, a redirect, `tee`, `cp`/`mv` onto it) the same as an edit tool.
On hosts with `AGENT_OS_WORKER_RUN_AS_USER` set, the worker runs as a
different UID than the stage that created the repository, so git refuses it
as "dubious ownership". `safe.directory` is only honoured from global/system
config or `-c`, so the prompt tells the agent to run
`git -c safe.directory="$PWD" log` in that case, and the verifier accepts it.

The task is in the `coding` suite (12 tasks with it; the TOC SPEC's sample-size
note predates it), so the scheduled `--suite all` run dispatches it once per run. It raises no regression alert until an operator
runs `hq gym baseline update --confirm` after this lands and the baseline
carries its verdict.


## What this module promises

`python3 scripts/smoke.py` is the canonical sandbox command, forwarded by installed `hq smoke paddock`.

- `real-out-of-band-grading` (`suite.grade`): Grade the existing synthetic mini-suite through real suite and verifier entry points; check pass/fail outcomes.
- `owned-lease-cleanup` (`lease.lifecycle`): Create/admit/close a disposable endpoint lease through real Manager methods with a synthetic provider API.
- `deadline-is-not-success` (`suite.timeout`): A synthetic timed-out trajectory remains a non-passing agent error; no model, container or host dispatch runs.

The harness uses disposable stores and synthetic external boundaries. Required missing, failed or unavailable evidence exits nonzero. No ambient credentials or endpoints are accepted by the CLI. Host mode is unavailable until a safe canary is approved; SRE instead inspects installed version and authoritative recent tool receipts read-only after deployment. SRE verifies service owner, launchd domain and documented restart path before any separately authorized activation. The existing main-catchup declaration lane owns page-once module/step/commit failures, with no revert and no later-deploy hold. No scheduled workflow changes.

Synthetic verifiers run with closed stdin in owned process groups, with a
15-second timeout cap even when the runner omits its timeout or passes `None`.
The adapter accepts extra runner keywords while retaining captured output and
process isolation; `check`, `encoding` and `errors` retain their subprocess
semantics. Every step's JSON `observed` value is an object, including when an
invocation deadline interrupts teardown after the steps have finished.

---

← [Race it yourself](race-it-yourself.md) · [Batteries](batteries.md) →
