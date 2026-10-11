# A PR-looking issue number holds the cursor

`batch_activation.py` classifies PR body fetch failures for a post-merge sweep.
`symptom.txt` records an observed deploy range where a commit subject ended in a
PR-shaped suffix, but that number was an issue, not a pull request.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_line": <1-indexed line in batch_activation.py>,
 "terminal_status": "<one of: skipped | failed | blocked | dispatched>",
 "retryable": <true|false>}
```
