# Discriminates real transaction cause from a one-line retry patch

Read all three files. Commits become durable BEFORE the acknowledgement;
rollback cannot undo them. Event IDs are globally unique and redelivery is legal.
Select the fix that preserves exactly-once balance changes, including a crash
between the apply and mark operations. Predict the original final balance.
Predict final balance after merely moving mark before the first commit (removing
the second transaction), without a receipt check. Under the correct fix give the
final balance and receipt count. A receipt check in these choices occurs inside
the transaction; simultaneous consumers are outside this task's contract.

Do not edit the staged inputs. Write only your structured diagnosis to
`bench-result.json` in your worktree root, using these fields (integer values
are JSON numbers, string traces have no spaces):

```json
{
  "fix": "one of: retry-more | mark-before-commit | check-receipt-only | atomic-receipt-and-apply",
  "original_balance": "<integer>",
  "reorder_balance": "<integer>",
  "fixed_balance": "<integer>",
  "fixed_receipts": "<integer>"
}
```
