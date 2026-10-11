# The resolver rejects its own terminal value

`change_kind.py` is a reduced copy of the change-kind resolver. `symptom.txt`
records what DAG reconcile did with a merged step whose prior completion stored
the resolver's terminal `"unknown"` value.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_line": <1-indexed line in change_kind.py>,
 "sentinel_handling": "<one of: fall-through | return-declared | raise | drop-explicit>",
 "invalid_explicit_still_errors": <true|false>}
```
