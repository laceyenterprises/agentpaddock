# Discriminates async ordering and generation ownership

Read loader.py and scenario.py. UI contract: the newest STARTED generation
may publish immediately on its own completion, even with older work pending;
older completions may never replace it. Cancellation is best-effort and callbacks
can still arrive. Select a publish predicate replacing `if not self.pending`.
Count schedules whose final visible value is wrong under the original code.
For schedule (3,1,2) give visible after each completion as comma-separated
values (use null for None), for both original and fixed code. Also give the
number of all six schedules with wrong final value after the selected fix.

Do not edit the staged inputs. Write only your structured diagnosis to
`bench-result.json` in your worktree root, using these fields (integer values
are JSON numbers, string traces have no spaces):

```json
{
  "fix": "one of: pending-empty | generation-equals-current | generation-in-pending | cancel-older-only",
  "original_wrong_schedules": "<integer>",
  "original_trace": "<string>",
  "fixed_trace": "<string>",
  "fixed_wrong_schedules": "<integer>"
}
```
