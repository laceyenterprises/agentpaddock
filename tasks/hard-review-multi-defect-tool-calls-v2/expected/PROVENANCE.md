# Maintainer provenance (never staged)

Source: synthetic, authored for HRX-02 as the reworked `hard-review-multi-defect-tool-calls`. It keeps the original's defect themes from PR 7393 (no function-name allowlist, `tool_choice` required/named not enforced, streaming bypasses enforcement), rebuilt as plain importable modules inside a 416-line diff that is otherwise correct. `min_recall` is 1.0. This task sets `enforce_category = true`: security findings cannot substitute for the named-choice API-contract defect at an adjacent site.

## Real defects

0. `tool_calls.py:154-158`, `:174` (name emission) or `:199` (security; the context defect). Parsed function names are never checked against the offered tools. The diff passes `tools` into `parse_block`, which suggests the parser validates names; only the unchanged `parsers.py` shows that it decodes syntax and returns any name.
1. `tool_calls.py:68-70`, `:189-190` or `:200-201` (api-contract). `requires_call` omits the named-function mode.
2. `bridge.py:158-159` (api-contract, the obvious one). Streaming never resolves `tool_choice`; `pr.md` promises 400 for required or named choices.

## Decoys (correct code; one blocking finding on any of them fails `decoy_resistance`)

- `tool_calls.py:79-80`: an omitted `tool_choice` means `auto` with tools, which is the OpenAI default restated in `pr.md`.
- `tool_calls.py:98-99`: rendering only the named function is the documented design (`pr.md`).
- `tool_calls.py:167`: `json.dumps` is not double encoding, because `parsers._call` always returns `arguments` as a dict. Serialization and name emission are seven lines apart, so their slack-3 windows do not intersect.
- `tool_calls.py:208-209`: `finish_reason` stays `length` on truncation, as `pr.md` specifies.

`tests/test_hard_review_corpus.py` reproduces each defect and checks each decoy against the staged fixture.
