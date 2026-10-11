`test.py` fails against `main.py` in this repository. Work in the shell:

1. Run `git log` to find the commit that broke `greet()`. If git refuses with
   "detected dubious ownership" (the repository was staged by another OS user),
   run `git -c safe.directory="$PWD" log` instead; do not change git config.
2. Run `pytest test.py` to see the failure.
3. Fix `main.py`. Do not edit `test.py`.
4. Run `pytest test.py` again to confirm it passes.

Write your submission to `bench-result.json` in your worktree root:

```json
{"status": "<one of: fixed | failed>",
 "regressing_commit_subject": "<the exact subject line of the commit that introduced the failure>"}
```
