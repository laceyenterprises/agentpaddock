# The script exits 1 and prints nothing

`probe.sh` is a host probe. `symptom.txt` records what an operator observed.

The script exits 1 with **no output on stdout or stderr** — not even its own
error message. It reaches none of its `echo` statements.

Read both files, then write your submission to `bench-result.json` in your
worktree root:

```json
{"defect_line": <1-indexed line in probe.sh that terminates the script>,
 "shell_option": "<one of: pipefail | errexit | nounset | noclobber | xtrace>",
 "suppressed_by_2devnull": <true|false>}
```

`suppressed_by_2devnull` asks whether the `2>/dev/null` already present on that
line prevents this failure.
