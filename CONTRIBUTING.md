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

## Fairness checklist

`results/meta/fairness.json` holds the checklist every harness configuration is
audited against before its results are published (tools, permissions, model,
reasoning, prompt, limits, telemetry, transport and provenance). A harness
whose audit is not signed off is shown as provisional on every page.

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

The six `docs-*` tasks use version 2 for meaning-based wording checks. Their
version 1 scores used narrower phrasing checks and cannot be compared with
version 2 scores; re-check saved submissions under the new version.
