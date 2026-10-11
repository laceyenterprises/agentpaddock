Fix the review finding in finding.md by editing billing.py only. gateway.py and settlement.py model external contracts and callers and must remain unchanged. A finding's recommended fix is a reviewer's suggestion; the documented contracts in the code are authoritative where they disagree. Run the visible smoke tests with `python3 -m unittest test_visible`; they do not cover the defect. Do not edit the finding, tests, or staged manifest. Save your fix, then write bench-result.json in the workspace root with this submission shape:

```json
{"done": true}
```

The code files determine correctness; done is a required boolean completion signal. The submission file is the only permitted new output file.
