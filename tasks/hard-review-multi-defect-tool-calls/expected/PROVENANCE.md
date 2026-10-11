# Maintainer provenance (never staged)

Derived from a private review. No public pull request or review link exists, and
none is recorded here. task_version 2 replaced the verbatim review text, private
identifiers and the private remediation with paraphrased and self-written
equivalents; results recorded at task_version 1 are not comparable.

source-review.txt is a paraphrase of the review; source-remediation.diff is a self-written fix against the staged fixture, not the original remediation commit. The three blocking findings are the three defect windows, and the self-written fix addresses all three. The review's two non-blocking findings are handled as follows: the missing `SyntaxError` in the parser's except tuple is a tolerated window, and the stale walkthrough is not staged. The malformed-history pass-through is also tolerated. The streaming finding is any-of: it can be anchored on the tools route in `chat_completions` or on `handle_chat`'s stream branch.

pr.md restates the bridge's tool-calling contract and the public fact that mlx_lm's JSON tool parser ignores `tools`, so every defect is derivable from staged material. `server_supervisor.py` is trimmed to the chat paths; line numbers are rebased. `min_recall` 0.66 needs two of three, so a reviewer who stops after the name-allowlist finding (`obvious_defect`) fails.
