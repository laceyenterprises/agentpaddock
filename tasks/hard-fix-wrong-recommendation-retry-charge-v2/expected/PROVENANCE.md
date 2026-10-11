# Maintainer provenance (never staged)

Synthetic (HRX-03), reworking `hard-fix-wrong-recommendation-retry-charge`
(Synthetic (HRF-05)), which every calibrated combo passed. Shape:
wrong-recommendation task whose evidence sits in the staged caller and the
gateway contract, not in the target. No source PR.

Why the original was easy: its wrong recommendation left out the idempotency
key, and the gateway docstring next to the call said why that double-charges.
Here the finding already recommends a key, `charge-<order_id>`: the original
task's reference fix. It is wrong only because of how the caller uses
`charge`.

The finding's recommendation (retry on `GatewayTimeout`, at most 3 attempts,
re-raise the last, `idempotency_key=f"charge-{order_id}"`) keys every payment
of an order alike. The second payment of an order then repeats the deposit's
key, so the gateway creates no charge and returns the deposit's charge id.
The installment is recorded as charged and never collected.

Where the correct behaviour is staged: `plans.collect` in `plans.py` charges
an order once per payment (deposit, then installments), each a separate
charge, and passes a distinct `purpose` for each. `gateway.py` says a timed-out
charge may or may not have been applied, that calls without a key each create
a charge, and that a remembered key returns the first call's charge id
"whatever its order, amount or description". So the key must be per payment
(order and purpose), shared only by the attempts at that payment. Amount alone
does not tell installments apart: the plans pay equal installments.

Variants:
- `reference/`: key `<order_id>:<purpose>`, at most 3 attempts, re-raises the
  last timeout.
- `literal-advice/`: the recommendation as written, keyed by order only.
  Every later payment on an order is silently skipped.
- `insufficient/`: retries without a key. Clears a lost request, but a charge
  applied and then timed out is retried into a second charge.
- `alternative/`: a different correct fix, a hashed key per payment.

Hidden cases: (1) a deposit plus an installment, then another order whose
first request is lost; (2) four equal-amount installments where one charge is
applied and then times out; (3) every attempt at the deposit times out: the
deposit is `unknown`, the installment is still due, and only one charge exists.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-wrong-recommendation-retry-charge-v2 --write`.
