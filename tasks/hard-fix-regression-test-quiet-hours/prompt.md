Fix the review finding in finding.md by editing quiet.py, and write a regression test for it in test_regression.py. router.py is a caller and must remain unchanged. A finding's recommended fix is a reviewer's suggestion; the documented contracts in the code are authoritative where they disagree. Run the visible smoke tests with `python3 -m unittest test_visible`; they do not cover the defect. Do not edit the finding, the visible tests, or the staged manifest.

test_regression.py must be a stdlib `unittest` module. It is graded by running it against four versions of the code: the original code and an incomplete fix (it must fail on both), and a correct reference fix and your fix (it must pass on both). The reference fix is not yours, so test the behaviour the finding and the docstrings describe through the public functions, not your own helpers. The test must not read, hash, list or introspect source files.

Save your fix and your test, then write bench-result.json in the workspace root with this submission shape:

```json
{"done": true}
```

The code files determine correctness; done is a required boolean completion signal. The submission file is the only permitted new output file.
