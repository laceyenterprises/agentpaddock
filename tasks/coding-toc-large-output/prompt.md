The banner regression is buried in this repository's history. Work in the shell:

1. Run `git log -p` to identify the change that broke `greet()` and recover the
   original greeting. If git reports dubious ownership, use
   `git -c safe.directory="$PWD" log -p`; do not change git config.
2. Run `pytest -v test.py`. Capture the diagnostic marker in the failing
   traceback (the line beginning `diagnostic marker:`).
3. Fix `main.py`, leaving `test.py` untouched.
4. Run `pytest -v test.py` again to confirm the fix.

The three required measurement commands must run as standalone, bare `git` and
`pytest` invocations with stdout returned to the tool. Do not pipe, redirect,
wrap them in Python, or replace them with absolute executables or `python -m
pytest`. Use the commands above without extra flags. Additional narrower reads
are allowed after these required invocations.

Large outputs may be previewed. Read the full artifact or run a narrower command
when the evidence you need is outside the preview.

Write `bench-result.json` in the worktree root:

```json
{"status": "<one of: fixed | failed>", "regressing_commit_subject": "<exact subject>",
 "failure_marker": "<marker from the traceback>"}
```
