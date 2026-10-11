# Find the predicate that cannot run

`query.sql` is a candidate-selection query. `schema.sql` is the schema it runs
against on the production database (PostgreSQL).

The query is syntactically valid and has shipped. It still cannot execute.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_line": <1-indexed line number in query.sql>,
 "defect_kind": "<one of: type-mismatch | missing-index | sql-injection | n-plus-one | no-defect>",
 "offending_column": "<the column whose type makes the predicate invalid>"}
```

Read `schema.sql` before answering. The defect is not visible from `query.sql`
alone.
