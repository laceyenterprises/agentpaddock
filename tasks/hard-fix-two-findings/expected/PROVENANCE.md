# Maintainer provenance (never staged)

Synthetic (HRF-05). Shape: two-findings task, after the partial-redaction and
character-versus-byte truncation classes of paging bug. No source PR.

`finding.md` stages two review cards, one per file: `redact` replaces only the
first credential (`count=1`), and `truncate` measures characters against a
byte cap and appends a three-byte ellipsis past it.

Where the correct behaviour is staged: the `redact` docstring says every
credential-looking token is replaced; the `truncate` docstring says the cap is
UTF-8 bytes, the longest fitting prefix is kept, and a character is never
split. Nothing in `hidden/` is needed to derive either fix.

Variants:
- `reference/`: `sub` without `count`; `truncate` cuts the encoded bytes and
  drops an incomplete trailing character.
- `insufficient/`: the reference `redact.py` with the original `pager.py`;
  fixes the first finding only.
- `alternative/`: a different correct fix in both files.

Hidden cases: (1) every credential is redacted; (2) truncation properties
(byte cap, whole characters, maximal prefix, unchanged when it fits) over
ASCII, two-, three- and four-byte text; (3) both together through
`format_page`. Case 2 checks properties, not one exact string.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-two-findings --write`.
