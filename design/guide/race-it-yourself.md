[Home](../../README.md) · [How it works](README.md) · [Races](races.md) · [Hard races](hard-races.md) · [Board](board.md) · **Race it yourself** · [Under the hood](the-gym.md) · [Batteries](batteries.md) · [Design](../README.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-race-it-yourself-dark.svg">
  <img alt="Race it yourself: Run the bench on your own machine: runners, host services, the repository layout and the tests." src="assets/banner-race-it-yourself-light.svg">
</picture>

**On this page:** [Runners (PDK-01)](#runners-pdk-01) · [Host services (PDK-02)](#host-services-pdk-02) · [Layout](#layout) · [Tests](#tests)

## Runners (PDK-01)

`TrajectorySource` (`grader.py`) is the only execution seam. `hq gym run` and
`hq gym baseline update` pick the source by runner mode, resolved in
`hosts.py` in this order:

1. `--runner <name>`;
2. `PADDOCK_RUNNER`;
3. `runner = "..."` in the user config (`$PADDOCK_CONFIG`, else
   `$XDG_CONFIG_HOME/paddock/config.toml`, else `~/.config/paddock/config.toml`);
4. the first available `paddock.hosts` plugin;
5. `standalone`.

If a host is named but not installed or not available, the run fails. It never
falls back silently. `bin/hq-gym` pins `PADDOCK_RUNNER=agent-os`, so the fleet
path does not depend on auto-detection. The `agent-os` host (`agent_os_host.py`,
found through the in-tree fallback `hosts.IN_TREE_HOSTS`, since the agentpaddock
distribution declares no host) builds the unchanged `WorkerPoolTrajectorySource`.

A source reports launch success in `Trajectory.launch_succeeded`, using its
host's own status reader; the gym never interprets a host's status vocabulary.
An explicit boolean wins, false included. When it is `None`, only the exact
status `succeeded` is a clean run: `completed`, `complete` and `done` are
unknown, never success. Launch success, the grade and any published
deliverable are separate facts.

A missing terminal status is unknown too. A trajectory stub without that
attribute needs an explicit boolean host verdict to count as a clean run;
without one and without a submission, grading returns `agent_error` and
reports the missing status as `None`.

`launch_succeeded` follows `result_redactor` in the `Trajectory` constructor,
preserving the existing fifteen positional arguments, including the redaction
callback. Sources should supply the launch verdict by keyword.

The `standalone` runner (`standalone.py`) runs one harness CLI per task in
`docker run`. Supported harnesses are `claude-code`, `codex`, `opencode` and
`pi`; gemini is deferred. The container gets:

- the staged sandbox mounted at `/workspace`;
- `--cap-drop=ALL`, `no-new-privileges` and a read-only root;
- `--pull=never`, so the image must already contain the CLI;
- finite limits of 2048 MiB RAM with no swap, 2 CPUs, and 256 PIDs by default.

Grok is registered but reports `unavailable` because Linux Docker has no
reviewed Grok artifact. Unavailable harnesses skip concurrent preparation,
credential resolution, staging and Docker startup. Their provenance names the
selected harness; only Grok includes the Grok role/auth availability cells.

It is configured with `--harness` and `--image`, or with the `[standalone]` table:

```toml
runner = "standalone"

[standalone]
harness = "codex"
image = "paddock/codex:local"
model = "<model-id>"       # optional; a task's [dispatch] model wins
network = "bridge"
timeout_seconds = 3600
memory_mb = 2048           # positive integer; swap is disabled
cpus = 2.0                 # finite positive CPU quota
pids_limit = 256           # positive integer

[standalone.claude-code]   # per-harness overrides
image = "paddock/claude-code:local"
```

Credentials come only from the environment, or from a `[credentials]` table in
the same file; the environment wins. They reach the container by name
(`--env=NAME`), never in argv. Their values are redacted from every tool call
and error that the Trajectory carries, before any text truncation. The pi and
OpenCode parsers retain the latest arguments for each streamed call ID in
first-seen order; calls without IDs remain separate. These parsers return full
arguments, and standalone caps nested argument strings at 2000 characters only
after redaction. Fleet-only options (`--priority`,
`--worker-class`, `--compression`) are refused in standalone mode.
OpenCode reasoning overrides are also refused until its CLI adapter supports
an explicit effort setting; its resolved effort is unknown and cannot satisfy
an effort-labelled benchmark cohort.

Standalone supervises the Docker client in 100 ms intervals and observes the
suite's shared interruption event. On timeout, interruption, or client exit it
checks termination against the engine: a stopped state or the named container's
confirmed removal is required before reading submissions or releasing sandboxes.
Removal is recognized from an exact diagnostic line naming that container, even
when Docker emits other warnings before or after it.
Control calls have a five-second timeout and retry up to three times with 100 ms
and 200 ms delays. If a batch cannot confirm containment, new suite admissions
halt permanently for that source, other active cells cancel, and supervision
continues in bounded control batches with a one-second delay until containment
is confirmed. No writable sandbox is graded. Concurrent interruption waits for
these owned containers to stop before suite cleanup returns; an unavailable
engine can therefore delay shutdown until an operator restores it. Resource
and deadline settings reject zero, negative, non-finite, and invalid values.
Real-engine enforcement probes are local/on-demand, outside CI.


## Host services (PDK-02)

Modules that ship in paddock import no Agent OS package and read no `$HQ_ROOT`.
What they need from a host comes from `host_services.current()`:

| Service | Standalone (local files) | Agent OS (`agent_os_services.py`) |
|---|---|---|
| data directory | `$PADDOCK_DATA_DIR`, else `$XDG_DATA_HOME/paddock`, else `~/.local/share/paddock` | `$HQ_ROOT/gym` (endpoints: `HQ_ROOT` env first) |
| scratch directory | `<data>/tmp` | `$HQ_ROOT/tmp` |
| run-record sink | `<data>/results/<cohort>/<run>.json` | `$HQ_ROOT/gym/results/...` |
| secrets provider | `env:NAME`, from the environment, then `[credentials]` | `op://` through `hq secrets resolve` |
| pricing provider | `$PADDOCK_PRICE_TABLE`, else `<data>/price-table.yaml` | SEW's price table |
| lock provider (live tool surface) | `[tool_surfaces]` in the paddock config | the worker-class registry |
| audit-encryption recipient (optional) | `<data>/audit/gym-audit-recipient.pem` | `$HQ_ROOT/gym/audit/gym-audit-recipient.pem` |
| alerter (`gym_regression`) | `<data>/alerts.jsonl` | the shared sentinel alert path |
| host config (taint mode) | none, so injection tasks read `unavailable` | worker-pool CFG |
| reasoning levels | passed to the harness CLI | `cwp_dispatch.reasoning_level` |
| TOC command labels | none, so only the shape is checked | `cwp_dispatch.tool_output_compression` |

The pricing provider returns the table with its `source` (the file) and date
(the newest entry `as_of`). `GYM_FIX_AUDIT_CERT` still overrides the audit
recipient. Services are resolved through the runner's host: a plugin that
defines `services()` supplies them, else standalone. `bin/hq-gym` pins the
`agent-os` host, so the fleet reads exactly what it read before.

`dispatch.py`, `ledger.py`, `matrix.py`, `matrix_report.py`,
`acceptance_recovery.py`, `toc_eval.py`, `agent_os_host.py` and
`agent_os_services.py` are the host plugin: they drive the fleet and stay in
agent-os when the rest leaves (PDK-07). `tests/test_host_import_guard.py`
fails if any other module, or a script, imports an Agent OS package or a
host-plugin module.


## Layout

```
modules/adversarial-eval-gym/
  lib/python/gym/
    outcomes.py          # the six-outcome vocabulary
    schema.py            # strict task.toml parser + result validation
    toolcalls.py         # ordered tool name/arg extractor + snapshot
    verifier_runtime.py  # stdlib helper imported by every verifier.py
    grader.py            # trajectory -> one outcome per cell (out of band)
    ledger.py            # session-ledger reads (LedgerDatabase, like budget_enforcement.py)
    dispatch.py          # real hq dispatch + poll seam (WorkerPoolTrajectorySource)
    staging.py           # stage inputs + read bench-result.json (both runners; PDK-01)
    hosts.py             # runner mode resolution + paddock.hosts plugins (PDK-01)
    agent_os_host.py     # Agent OS host plugin -> WorkerPoolTrajectorySource (PDK-01)
    host_services.py     # host-service interfaces + standalone local-file services (PDK-02)
    agent_os_services.py # Agent OS host services: HQ_ROOT, hq secrets, SEW prices (PDK-02)
    standalone.py        # StandaloneTrajectorySource: harness CLI in Docker (PDK-01)
    taint.py             # injection-task taint gate (GYM-03)
    suite.py             # discover + run a suite -> §1 summary (GYM-04)
    baseline.py          # gym-baseline.json I/O + pass->fail comparison (GYM-04)
    alerting.py          # the gym_regression alert seam (GYM-04)
    locks.py             # tool-surface locks: setup-phase drift abort (GYM-05)
    cli.py               # `hq gym run` / `baseline update` / `lock` (GYM-04/05)
    toc_matrix.py        # compression A/B quota gate, arm gate, cost-per-success (TOC-05)
    toc_eval.py          # `hq gym toc-matrix` driver (TOC-05)
  scripts/leaderboard.py # results/ schema, validation, pricing, aggregation (PDK-04)
  scripts/build_site.py  # static leaderboard site from results/ only (PDK-04)
  scripts/social_card.html       # the 1200x630 share card's source (PDKSOCIAL-01)
  scripts/social-card.png        # the committed share image; the build copies it
  scripts/render_social_card.py  # headless Chrome: card source -> social-card.png
  scripts/import_eval_bench.py # eval-bench evidence -> results/ (PDK-04)
  results/               # leaderboard results, price tables, changelog, fairness (PDK-04)
  results/entries/       # driver configuration setup sheets, pinned by config hash
  results/templates/     # blank entry, track and fork-results contracts
  design/                # design system: tokens, glyphs, vocabulary, illustration mocks (PDKDESIGN-01)
  CONTRIBUTING.md        # run bundles, reproduction, vendor corrections (PDK-04)
  bin/hq-gym             # hq gym wrapper (routed from worker-pool/bin/hq)
  launchd/               # scheduled-run StartInterval one-shot plist template (GYM-04)
  locks/<agent>.lock.json # committed per-agent tool surface (sorted; GYM-05)
  gym-baseline.json      # committed last-known-good verdicts
  tasks/<id>/
    task.toml            # id, suite, persona, stages, result_schema, [verifier]
    verifier.py          # out-of-band verifier
    prompt.md            # persona prompt
    stages/*.sh          # stage inputs into the sandbox (never expected/)
    expected/            # ground truth — NEVER staged, NEVER reachable from the verifier
  tests/
```

Each gym dispatch call supplies an explicit unique launch idempotency key,
reused across its internal transient retries so an accepted launch is deduplicated
if its acknowledgement is lost. Internal retries retain the same worker, prompt
and staged workspace, so a deduplicated receipt still points to its accepted
result. Once HQ has been invoked, exceptions, nonzero exits and malformed or
missing receipts retain that workspace; an unresolved acceptance stops matrix
admission with the sandbox and launch key recorded in the diagnostic. Only a
staging failure or an initial HQ process-spawn failure may delete the new
workspace and defer the cell. A spawn failure on a later retry still preserves
the workspace because a prior call may have accepted it. A nonzero exit or an
"unsupported" diagnostic alone does not prove that acceptance never occurred;
it cannot safely enable automatic cleanup or a replacement launch. The ticket
identifies the corpus task; concurrent model, effort,
compression and repetition cells may share that ticket and prompt, so HQ's
default task/class/prompt key cannot identify a cell. The matrix also refuses
a launch receipt already bound to another cell, saves the conflicting receipt
and staged sandbox, and halts new admissions for operator recovery. It does
not grade or charge that launch a second time. Recovery must preserve saved
attempts and the approved run budget; a binding failure is not model evidence.
Resume also refuses launch IDs shared across saved cells or their old attempts,
without rewriting those snapshots. Resolve the evidence before retrying resume.

Every shipped task declares a `[dispatch]` prompt that resolves to a real
file; the corpus test enforces that invariant. The gym prepends its workspace
boundary guard to that prompt. Staging rejects symlinks in the worker sandbox
before writing the fixture manifest, including dangling and directory links,
so linked inputs cannot silently bypass fixture verification.

After a worker reaches terminal state, transcript and control evidence reads
each get at most three attempts, with 0.25s and 0.5s backoff. Retries cover
Postgres subprocess failures/timeouts, connection errors, and SQLite
`OperationalError` (including database locks). Exhaustion remains a harness
error and skips grading; it never substitutes empty evidence. The diagnostic
names the failed read and attempt count, and includes at most the last 500
characters of already-redacted `psql` stderr when available.


## Tests

Pure stdlib core — no worker pool or ledger needed to grade a synthetic trajectory:

Run from the repository root:

```bash
python3.13 -m venv /tmp/gym-venv && /tmp/gym-venv/bin/pip install pytest
/tmp/gym-venv/bin/python -m pytest tests -q
```

The suite covers: schema fixtures (mandatory tables, loud on unknown keys),
fake-dispatch pass/fail grading, one outcome per cell for all six outcomes,
tool-call present/absent extraction, **verifier isolation** (a probe verifier
that tries to read `expected/` and import the backend, and is defeated), and the
**injection corpus** (`test_injection_corpus.py`): tool-presence → `failed`,
pass/fail trajectories, the enforce-mode denial evidence, and the skip/xfail gate
wiring — including the invariant that no injection task silently passes without
taint enforce.

---

← [Board](board.md) · [Under the hood](the-gym.md) →
