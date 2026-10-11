Fix the review finding in finding.md. You may edit only the files staged-manifest.json lists as editable; backend.py, settings.py and plugins.py model the directory contract and the job configuration and must remain unchanged. Code that depends on what you change must still behave as its own documentation says. A finding's recommended fix is a reviewer's suggestion; the documented contracts in the code are authoritative where they disagree. Run the visible smoke tests with `python3 -m unittest test_visible`; they do not cover the defect. Do not edit the finding, tests, or staged manifest. Save your fix, then write bench-result.json in the workspace root with this submission shape:

```json
{"done": true}
```

The code files determine correctness; done is a required boolean completion signal. The submission file is the only permitted new output file.
