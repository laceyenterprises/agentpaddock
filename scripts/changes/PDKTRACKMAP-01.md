# Checks companions

`import_eval_bench.py` accepts `--checks-source` and `--checks-document` to
enrich an existing document without importing new runs or rewriting results.
`--check` verifies the resulting deterministic companion.

`leaderboard.result_paths` excludes `.checks.json` and `.checks.json.gz`
companions from results documents. `load_results` attaches checks from the
compressed companion after matching its document id and each grading's
identity and score. `check_identity` and `matching_checks` share that match
rule with the importer. Existing inline checks take precedence.
