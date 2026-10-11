# A harmless migration payload fails the pass

`daemon_excerpt.py` is the post-fast-forward section of a deploy pass.
`symptom.txt` records the failure operators saw.

The migration helper can return `None` when there is no payload to inspect. That
case is supposed to be tolerated: there is no migration failure and nothing to
audit.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_line": <1-indexed line in daemon_excerpt.py>,
 "guard_needed": <true|false>,
 "non_dict_action": "<one of: skip-audit | audit-anyway | raise-failure | coerce-empty-dict>"}
```
