Resolve the review findings in finding.md using the staged repository. Preserve the public behavior required by its callers and contracts. Only paths marked editable in staged-manifest.json may change. Run `python3 -m unittest test_visible` before submitting.

Save your changes and write bench-result.json in the workspace root with this submission shape:

```json
{"done": true}
```

This required boolean signals completion; the captured files determine correctness. The submission JSON is the only permitted new output file.
