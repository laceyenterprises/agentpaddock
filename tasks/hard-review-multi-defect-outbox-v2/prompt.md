Review change.diff using pr.md and the post-change files in this sandbox. Read the code before submitting. Report only actionable findings; distinguish blocking defects from suggestions. Write bench-result.json with a findings list. Each object requires file, line_start, line_end (positive 1-based inclusive lines in the staged post-change file), blocking (boolean), category and summary. Categories: correctness, concurrency, data-loss, security, error-handling, resource-leak, api-contract, test-gap, docs. An empty list is valid for a correct change. Do not edit the reviewed files.

Submission shape (the example is illustrative, not a finding about this change):

```json
{"findings": [{"file": "<staged file path>", "line_start": 1, "line_end": 1, "blocking": true, "category": "correctness", "summary": "<specific defect, trigger and consequence>"}]}
```

Use actual line numbers and the appropriate category. For a clean review submit `{"findings": []}`.
