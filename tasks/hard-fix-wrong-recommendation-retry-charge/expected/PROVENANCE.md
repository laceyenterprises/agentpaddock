# Maintainer provenance (never staged)

Synthetic (HRF-05). Shape: wrong-recommendation task, after the
retry-a-non-idempotent-write class of payment bug. No source PR.

The finding's recommendation (retry `create_charge` on `GatewayTimeout`, at
most 3 attempts, re-raise the last) is subtly wrong: it never passes an
idempotency key, so a timeout that arrives after the ledger applied the charge
is retried into a second charge.

Where the correct behaviour is staged: `gateway.py` says a timed-out charge
"may or may not have been applied", that calls without an `idempotency_key`
each create a new charge, and that calls sharing a key create at most one.
`settlement.py` shows the exhausted case must still raise `GatewayTimeout`.
Nothing in `hidden/` is needed to derive the fix.

Variants:
- `reference/`: one stable key per order (`charge-<id>`), at most 3 attempts,
  re-raises the last timeout.
- `literal-advice/`: the recommendation as written, no key. Passes the
  lost-request case and the exhausted case; double-charges in the
  applied-then-timed-out case.
- `insufficient/`: keyed retries, but returns None when every attempt times
  out, so settlement records the order as charged.
- `alternative/`: a different correct fix, one random key per `charge` call.

Hidden cases: (1) a lost request retried, plus a second order that must be its
own charge (a constant key fails here); (2) a charge applied and then timed
out; (3) every attempt timed out.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-wrong-recommendation-retry-charge --write`.
