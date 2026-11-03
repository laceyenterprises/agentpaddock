# Contributing to Paddock

Paddock is an open leaderboard for coding agents: model x harness on real
repository tasks. The repository is
[laceyenterprises/agentpaddock](https://github.com/laceyenterprises/agentpaddock)
and the published site is [agentpaddock.ai](https://agentpaddock.ai/). The site
is built only from the committed `results/` tree, so every number on it comes
from a pull request anyone can read.

Paid evaluations never run in this repository's CI. You run them on your own
machine and submit the results as a run bundle.

## Submit results: a run bundle

A run bundle is one pull request that adds one results document,
`results/<date>-<id>.json`. It must let a maintainer reproduce the run.

1. **Run a suite** in standalone mode, at a clean checkout, with your harness
   CLI installed in a local Docker image (see the README's runner section):

   ```bash
   PYTHONPATH=lib/python python3 -m gym.cli run --suite review \
     --runner standalone --harness claude-code --image paddock/claude-code:local \
     --model claude-sonnet-5-5 --reasoning-level high --json > run.json
   ```

2. **Describe the harness configuration** in a JSON file: everything that
   affects behaviour and is not the model. At least the harness name, its
   permission mode, tool set, reasoning or effort level, timeouts, container
   limits, and any proxy or gateway between the harness and the model. Its
   SHA-256 becomes the record's `config_hash`, so two runs with the same
   descriptor pool on the board and any difference splits them.
   Record `"reasoning_level": "high"` for this example; it must match
   `--reasoning`. For fleet runs, also record `worker_class` when the dispatch
   class differs from the harness CLI name.

3. **Write the results document** from the run:

   ```bash
   python3 scripts/leaderboard.py from-run run.json \
     --id review-claude-code-sonnet-5-5 --date 2026-10-10 \
     --harness claude-code --harness-version "$(claude --version)" \
     --harness-config harness-config.json \
     --model anthropic/claude-sonnet-5-5 --reasoning high \
     --paddock-commit "$(git rev-parse HEAD)" \
     --description "review suite, claude-code, vendor-default tools" \
     --evidence "PYTHONPATH=lib/python python3 -m gym.cli run --suite review --runner standalone --harness claude-code --image paddock/claude-code:local --model claude-sonnet-5-5 --reasoning-level high --json"
   ```

   `--model` is the provider id (`provider/model`) that prices the run.
   `--evidence` is the exact reproduction command. Tokens and wall time come
   from each cell's metrics when the runner recorded them; the standalone
   runner does not record token metrics yet, so its tokens are null and its
   cost reads `unknown`. Conversion requires a single suite: `--suite all`
   summaries lack per-cell membership and are rejected. Model/harness mismatch
   flags, reasoning mismatches, and contradictions in execution telemetry
   retain explicit exclusions;
   cells without verifiable identity are excluded as `infra:attribution_unverified`.
   Token normalization uses only the cache-inclusive sources and harness output
   contracts in `gym.cost`. Fresh-only sources (including `claude-code-exec-json`),
   missing source metadata, and side-model usage remain null and unpriced because
   aggregate telemetry cannot attribute every token bucket to each model. Costs
   are computed for you from the newest table in
   `results/prices/`; never edit a cost by hand, since validation recomputes
   it.

4. **Validate and preview**:

   ```bash
   python3 scripts/leaderboard.py validate
   python3 scripts/build_site.py && open site/index.html
   ```

5. **Open the pull request.** In its description, state the machine and Docker
   engine, the harness CLI version, and the fairness checklist below, item by
   item, for your configuration. Do not commit the raw `run.json`: its grading
   reasons can reveal task answers.

Rules for a bundle:

- Run every task in the suite. Infrastructure failures stay in the document
  with `excludedBecause: infra:<reason>`; they are not scored, but they are
  shown.
- Never drop or re-run a failed attempt to replace it. A re-run is a new
  attempt with its own record.
- Never fill in a token count you did not measure. Null is honest; a guess is
  not.
- One harness configuration and one model per document.

## Submit an entry

Copy [`results/templates/entry.toml`](results/templates/entry.toml) to
`results/entries/<entry-id>.toml`. An entry is a driver (`provider/model`), a car
(the harness configuration hash), and an engine map (reasoning level, or
`default` for a record's null reasoning). Changing any one creates a different
entry. Set `team` to your GitHub user or organisation; paddock-run entries use
`agent-paddock`. Choose a car number from 1 to 99 and optionally a `car-*` livery
colour token from `design/tokens.json`. Livery identifies a car, never a value.

State the harness CLI version, or `unrecorded`, and copy the exact descriptor
into `[car.descriptor]`. Compute `car.config_hash` with
`scripts/leaderboard.py`'s `config_hash(descriptor)`: SHA-256 of sorted, compact
UTF-8 JSON, prefixed `sha256:`. TOML ordering does not affect it. Tires name a
`locks/<name>.lock.json` file, or use the inline `{schema, agent, tools}` lock
shape (`gym-tool-surface-lock/v1`). A lock is a declared tool surface, not proof
of historical native tool availability.

For a new configuration, copy the entry's entire briefing and radio tables
under the descriptor as well. A briefing is `{kind = "vendor-default"}`, or
`{sha256 = "sha256:<64 hex>", summary = "One public line"}` for custom
instruction text. Team radio is `{skills = []}` or a list of skills with a name
and exactly one version or SHA-256. The board does not measure team radio yet.
These declarations, including custom instruction hashes and skill versions or
hashes, affect the configuration hash. Keep private instruction text private.

The three worked examples in `results/entries/` use unchanged descriptors with
graded committed records. Their briefing is `{kind = "unrecorded"}` and radio
is `{status = "unrecorded"}` because those historical details were not captured.
Only the frozen legacy hashes qualify for that exception. Never edit an old
hash to add provenance retrospectively. Optional record `entry` references must
resolve and agree on provider id, reasoning and configuration hash (and harness).

```bash
python3 scripts/leaderboard.py validate-entry
python3 scripts/leaderboard.py validate-entry results/entries/<entry-id>.toml
python3 scripts/leaderboard.py validate
```

## Submit a track

Copy [`results/templates/track.toml`](results/templates/track.toml) beside your
`tasks/<task-id>/task.toml`. The public track card authors a circuit `name`,
one-sentence `pitch` without answer hints, `challenge` equal to the task's
`suite`, and an `infographic` id. Reuse an existing challenge's infographic id;
a new challenge type names a new id. The initial cards use `hard-review`, `fix`
and `docs` respectively. Discovery still reads only `*/task.toml`.

List checkpoints in lap order using verifier subcheck names, plain public
labels, and `disclose`: `label` exposes the label and clear rate; `count`
contributes only to “N more checks”; `hidden` is omitted. Recorded checks not
listed in the card also contribute to “N more checks”. Use `count` for docs
fact names, since those can reveal required facts. `[briefing] show` is `full`
by default, or `excerpt` or `none`: a future renderer uses this policy for the
already-public prompt. The derived JSON includes prompt word count, never its
text. No renderer is added by these contracts.

`validate-track` compares checkpoint names with committed `checks` for this
exact task, challenge and task version. An unknown name is an error when any
records exist for that version. With no records, it accepts with “checkpoints
unverified”. Legacy rows without `checks` mean not recorded and cannot verify
names. The three worked cards use names emitted by their current verifiers,
but committed historical exports contain no checks: name validation therefore
rejects those cards until checkpoint-bearing exports arrive. This deliberately
does not fabricate or backfill historical checkpoint evidence. The eval-bench
export carrying checks is a follow-up; existing results still validate.
`track-facts` remains readable for legacy exports, with a warning and unknown
checkpoint metrics, while `validate-track` requires recorded evidence.

```bash
python3 scripts/leaderboard.py validate-track
python3 scripts/leaderboard.py validate-track tasks/<task-id>
python3 scripts/leaderboard.py track-facts tasks/<task-id>
```

`track_facts(task_dir, results)` and `track-facts` return
`paddock-track-facts/v1` JSON. Static facts are suite/version, staged file and
line counts, prompt word count, result field names/types (including list item
shapes), and hidden test count (`expected_tests`, null if unspecified). Counts
come from the shared public staging helper in a temporary directory; the
command executes trusted stage scripts, so review a fork before running it.
It does not grade or run a model. Conditions describe standalone defaults:
CPU, memory, pids, time limit, read-only root, tmpfs, dropped capabilities and
**bridge** network mode. Bridge allows network access; offline calibration is
a separate verifier requirement.

Live facts pool included graded attempts for only this task version, with
entries identified by the identity triple, avg pass rate and its 95% Wilson
interval, checkpoint clear rates over attempts recording that check, and
retirements at the first failed required checkpoint in card order. Undisclosed
retirements pool under `undisclosed`; absent checkpoint data is counted as
unrecorded. Rates and fuel are null when unmeasured. The fuel triad is total
input (fresh plus cached) and output across all included attempts divided by
races passed, plus the cached share of input. Excluded infrastructure rows do
not contribute to rates or fuel. External pinned tracks never pool with local
tasks that happen to have the same id.

Derived fields are never accepted in `track.toml`. Neither cards nor derived
output may contain verifier descriptions, content under `expected/` or
`hidden/`, grading reason strings, defect/decoy locations, or correct answer
values. The facts function projects names and types without enum values or
field descriptions and never reads or serializes grading-only files.

A community track starts as **exhibition** and never enters standings until a
maintainer promotes it to the calendar. Calibration, implemented in a later
runner, requires all of:

- Its verifier passes the reference solution and fails an empty submission.
- Staging copies nothing from `expected/` or `hidden/`; the verifier carries
  `GYM-GROUND-TRUTH-NEVER-STAGE`.
- Two grades of the same submission agree.
- The track runs offline and inside its track limits.
- The track is MIT-licensed.

A card's author cannot promote it: exhibition/calendar admission is a
maintainer decision, separate from authored card data.

## Results from a fork

Use [`results/templates/results.json`](results/templates/results.json) as a
blank shape alongside the run-bundle procedure. Fill required fields; remove
optional `entry`, `track` and `source.fork` when not applicable. Delete the
placeholder `checks` list if checks were not recorded. The existing
`paddock-results/v1` schema remains: all additions are optional, so existing
committed documents and hashes retain their meaning. Results discovery reads
only top-level `results/*.json` and `results/*.json.gz`, never templates.

`from-run` copies a cell's `subchecks` into optional record `checks`, retaining
only `{name, ok, required}`. `ok` and `required` are booleans; names are unique.
Absent means not recorded. Any `reason` key is a validation error because it
can reveal answers. Do not commit raw runs or submitted answers.

Optional document `source.fork` is `{repository, commit}`: an HTTPS repository
URL without credentials, query or fragment and a full 40-character lowercase
hex commit. A record for a track outside this repository's tasks additionally
carries `track = {repository, commit, path, task_version, sha256}`. Its `path`
is a repository-relative path ending in `/track.toml`, with no traversal or
grading-only directories. `sha256` is `sha256:<64 hex>` of the pinned track
card bytes; task version must equal the record's version. Each pin is explicit;
validation checks its shape without fetching or trusting external code. A
maintainer must verify the pinned content when re-grading. The source fork is
provenance, not an authority to set trust or enter an exhibition in standings.
Records with an external `track` pin are excluded as
`exhibition:external-track` by the shared aggregation contract. Promotion
requires a later maintainer calendar workflow; neither trust nor a card
field can bypass this exclusion.

## Trust levels

`results/meta/trust.json` is maintainer-owned, like `fairness.json`. Its schema
is `paddock-trust/v1`; `documents` maps exact results filenames (including
`.gz` when used) to `{level, evidence, sign_off}`. Missing entries default to
`self-reported`; an empty mapping makes no retrospective trust claims.

| Level | What the evidence establishes |
|---|---|
| `self-reported` | Schema-valid submission with costs recomputed from committed prices. |
| `re-graded` | A maintainer grades submitted answers with the pinned verifier and confirms the verdicts. |
| `reproduced` | A maintainer re-runs a sample with the entry configuration and obtains results inside its interval. |
| `paddock-run` | A maintainer attests that the paddock ran the bundle. |

Every level above `self-reported` requires nonempty `evidence` and maintainer
`sign_off`. Evidence is a list of public HTTPS or repository references using
the fairness reference rules; self-reported entries may use `[]` and null
sign-off. Submitters leave promotions to maintainers. `validate` cross-checks
trust documents and entry references along with costs. Trust metadata defines
provenance and does not change the existing site's standings yet. Standings
eligibility and a private handoff for re-grading answers remain maintainer
policy decisions; submitted answers are never committed.

## Fairness checklist

`results/meta/fairness.json` holds the checklist every harness configuration is
audited against before its results are published (tools, permissions, model,
reasoning, prompt, limits, telemetry, transport and provenance). A harness
whose audit is not signed off is shown as provisional on every page.

Each audit may extend the `paddock-fairness/v1` entry with `evidence` and
`sign_off`. Evidence is a list with one entry for every checklist `id`, each
containing `id`, `status` (`pass`, `fail`, or `not-verifiable`), explanatory
`notes`, and a non-empty `sources` list of vendor URLs, repository `path:line`
references (positive line numbers, no empty, `.` or `..` path segments) or
telemetry selectors rooted at `run.`, `records[...]` or `harness_configs[...]`.
Repository references may include context after a space; vendor URLs use HTTPS
with a hostname and no whitespace. Identify the results documents and
configuration hashes covered, and distinguish current runner code from evidence
of historical execution. Date the review; current vendor documentation cannot
establish defaults for an unrecorded historical CLI version.

Keep `status` as `pending` while any check fails or cannot be verified, with the
reason in `notes`. `audited` requires every check to pass with evidence and
still renders as provisional. Leave `sign_off` null for the maintainer; only
the maintainer moves an evidenced audit to `passed` and fills in `sign_off`.
Legacy pending v1 entries without these additive fields remain readable; completed
audits require evidence, and passed audits always require sign-off. The methodology
page renders the findings and their sources directly from this metadata.

## Prices

`results/prices/<date>.json` is the committed price table. Each price names its
`source`:

- `provider-invoice-reconciled`: checked against a provider's invoice;
- `published-rate`: the provider's published list price, with a reference;
- `unknown`: no per-token price exists (self-hosted, per-hour deployments).

A model absent from the table is priced `unknown`. To change a price, add a new
dated table with the reference for every changed rate; never edit a past table.
Proxy or gateway cost estimates are not a source.

## Vendor corrections

If you build a harness or serve a model and believe a result misrepresents it
(a misconfigured harness, a wrong model id, a capture bug, a wrong price):

1. Open an issue titled `Vendor correction: <harness or model>` with the rows
   affected (their `run_id`s), what is wrong, and evidence: documentation of
   the vendor default, an invoice, or a reproduction.
2. A maintainer reproduces the problem. If it holds, the fix lands as a pull
   request with a new grading changelog entry in
   `results/meta/changelog.json` that states its comparability boundary.
3. Rows on the wrong side of the boundary keep their data and gain
   `excludedBecause: pre-<ID>`; they are never deleted or rewritten. A price
   correction is a new dated price table.
4. Corrected runs are submitted as new bundles under the corrected
   configuration, which has its own `config_hash`.

The vendor is credited in the changelog entry unless they ask not to be.

## Tasks and grading

Grading changes need their own pull request and a changelog entry. A change to a
task's content bumps its `task_version` in `task.toml`; results compare only
within one version, so the new version starts its own board.

The six `docs-*` tasks use meaning-based wording checks. The retry behavior,
snapshot command and baseline key tasks use version 3; the other three use
version 2. Scores compare only within a task version. Regrade saved submissions
when grading changes; keep old results at their original version.

Contributions are accepted under the same [MIT License](LICENSE).
