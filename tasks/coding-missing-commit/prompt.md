# The pass reports work and the table stays empty

`loop.py` runs a bounded pass over a queue. `symptom.txt` records what operators
observe on the live host.

Every statement in the file executes. The table is still empty.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_kind": "<one of: missing-commit | wrong-isolation-level | connection-leak | sql-syntax-error | no-defect>",
 "insert_line": <1-indexed line in loop.py where the row is INSERTed>,
 "teardown_line": <1-indexed line in loop.py where the handle is closed>}
```
