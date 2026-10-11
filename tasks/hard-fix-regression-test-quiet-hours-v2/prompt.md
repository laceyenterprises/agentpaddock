Fix the review finding in finding.md by editing freeze.py, and write a regression test for it in test_regression.py. deployer.py is a caller and policy.py is its configuration; both must remain unchanged. A finding's recommended fix is a reviewer's suggestion; the documented contracts in the code are authoritative where they disagree. Run the visible smoke tests with `python3 -m unittest test_visible`; they do not cover the defect. Do not edit the finding, the visible tests, or the staged manifest.

test_regression.py must be a stdlib `unittest` module. It is graded by running it against five versions of the code. It must fail on three: the original code, an incomplete fix, and a plausible fix that handles the reported overnight window but breaks a contract documented in the staged code. It must pass on two: a correct reference fix and your fix. The reference fix is not yours, so test the behaviour the finding and the docstrings describe through the public functions, not your own helpers. The test must not read, hash, list or introspect source files.

Save your fix and your test, then write bench-result.json in the workspace root with this submission shape:

```json
{"done": true}
```

The code files determine correctness; done is a required boolean completion signal. The submission file is the only permitted new output file.
