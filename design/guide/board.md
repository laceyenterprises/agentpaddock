[Home](../../README.md) · [How it works](README.md) · [Races](races.md) · [Hard races](hard-races.md) · **Board** · [Race it yourself](race-it-yourself.md) · [Under the hood](the-gym.md) · [Batteries](batteries.md) · [Design](../README.md) · [Vocabulary](../vocabulary.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-board-dark.svg">
  <img alt="The board: How the standings are built from committed results." src="assets/banner-board-light.svg">
</picture>

**On this page:** [Leaderboard (PDK-04)](#leaderboard-pdk-04)

## Leaderboard (PDK-04)

Community [entry, track and fork-results contracts](../../CONTRIBUTING.md#submit-an-entry)
provide driver configuration setup sheets, public track cards and optional
checkpoint records. `scripts/leaderboard.py validate-entry`, `validate-track`
and `track-facts <task-dir>` validate and derive their public facts; `validate`
also checks entry references and maintainer-owned trust levels. Historical hashes
stay unchanged, missing telemetry stays unknown, and community tracks begin as
exhibition. The main standings count only `reproduced` and `paddock-run`
results; the community board shows every trust level with its badge. Submitters
register an Ed25519 public key bound to their GitHub account, then upload signed
answers to the paddock submission API for re-grading, as defined in the
[API contract](../../CONTRIBUTING.md#signed-answers-and-submission-api-contract).
These contracts prepare later race cards, entry pages and board rendering;
they do not implement those pages or the submission endpoint.

The open leaderboard is built only from the committed `results/` tree:

| Path | What it holds |
|---|---|
| `results/<date>-<id>.json` | one results document per run bundle or import; each record is one cell (one graded attempt of one task by one harness configuration and model) |
| `results/<date>-<id>.json.gz` | use for a document whose text would make a pull request diff larger than about 200 KB; write with `leaderboard.write_document` for deterministic gzip (level 9, mtime 0, no stored file name) |
| `results/prices/<date>.json` | dated price tables; every price's `source` is `provider-invoice-reconciled`, `published-rate` or `unknown` |
| `results/meta/changelog.json` | the grading changelog; each entry states its comparability boundary and the `excludedBecause` tag (`pre-<ID>`) it puts on voided rows |
| `results/meta/fairness.json` | the harness-configuration fairness checklist and per-harness audit status (SPEC R8) |

Plain `.json` files must contain UTF-8 JSON text; gzip content requires the
`.json.gz` suffix. `leaderboard.write_document` and `write_document_text`
(for already rendered JSON) write a temporary sibling and atomically replace
the destination after closing it. The importer removes the alternate storage
sibling only after that write succeeds, so a failed write preserves the prior
document. Keep only one storage form per document; validation rejects duplicates.

A record carries suite, task and `task_version`; harness name, version and
config hash (the SHA-256 of a descriptor kept in the document); the model's
provider id; verdict, score, tokens (fresh input, cache reads, output) and
wall time; run id and Agent Paddock commit; `excludedBecause`; and its cost.
Validation recomputes every cost from the tokens and the named price table, so
a hand-typed figure fails. Unmeasured tokens are null, never zero, and a cost
without a priced source reads `unknown`.

```bash
python3 scripts/leaderboard.py validate          # schema-check results/
python3 scripts/build_site.py                    # write site/ (gitignored); open site/index.html
python3 scripts/build_site.py --check            # fail if site/ or the share card is stale
python3 scripts/render_social_card.py            # re-render scripts/social-card.png (Chrome, network)
python3 scripts/import_eval_bench.py --reports /path/to/exported-reports --check
```

Run these commands from the repository root. For the importer, replace
`/path/to/exported-reports` with the directory containing the published raw
eval-bench JSON exports listed in `scripts/import_eval_bench.py`; those source
exports are not included in this repository.

The build fails with a render command if the committed share card is missing.
The renderer discovers Chrome automatically; `CHROME` overrides discovery with
an executable path or PATH command, and an invalid override fails immediately.
Rendering failures include the last 8 KiB of browser stderr for diagnosis.

The site has per-suite boards with 95% Wilson intervals (one board per suite
and task version), a harness x model matrix, a tokens-per-pass chart, a
per-task discrimination view, the methodology with the price tables and the
fairness checklist, and the grading changelog. Every link is relative.

`results/` starts with the eval-bench evidence (RFD and HRF calibrations,
the OSSHARN-04 open-weight matrix and the OMT-08 local rows), imported with
every source exclusion kept. The source exports carry no token measurements,
so those rows' tokens are null and their cost `unknown`. The frontier coding
and rca aggregates of the
first eval-bench report have no
published per-attempt rows and are not imported. `CONTRIBUTING.md` covers
submitting a run bundle and the vendor-correction process.

The per-attempt `eval-bench-evidence/v2` importer is ready for registration as
`(published_filename, import_eval_bench)` in `SOURCES` once that export is
published. `build()` imports legacy sources first at their pinned 2026-10-10
prices, then v2 rows at each export's own table: `EVAL_BENCH_PRICE_TABLES`
pins a published export to the table it was published with (the 2026-10-29
export to 2026-10-25), and an unlisted export uses `EVAL_BENCH_PRICE_TABLE`.
A later export at a newer table therefore leaves earlier documents
byte-identical. It skips grading ids already in
legacy imports or other committed results, preserves per-version regrades, and
reports counts for published ids and `version-unrecorded` rows on stderr.
Its own prior `eval-bench` output is excluded from that lookup so regeneration
and `--check` remain repeatable. Native, provider-qualified and proxy labels
for the added OpenAI models and Gemini 4 Argon resolve to the same provider id;
`reasoningLevel` supplies effort when the label has none, and conflicting
explicit efforts are rejected. No v2 source is registered yet.

---

← [Hard races](hard-races.md) · [Race it yourself](race-it-yourself.md) →
