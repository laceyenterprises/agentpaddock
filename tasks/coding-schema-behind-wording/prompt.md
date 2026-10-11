# The self-heal parser misses the live wording

`schema_parser.py` extracts expected and found revisions from a session-ledger
schema error. `symptom.txt` records the message emitted by the behind-head path.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_line": <1-indexed line in schema_parser.py>,
 "missing_wording": "<the unhandled wording variant>",
 "recoverable": <true|false>}
```
