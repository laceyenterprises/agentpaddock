# Example RCA task

Read the staged incident artifact at `incident/artifact.txt`, determine the root
cause, and write your submission to `bench-result.json` in your worktree root as
a JSON object:

```json
{"root_cause": "<one of: read-before-write | write-before-read | no-op>",
 "evidence_id": "<the run id cited in the artifact>"}
```

Read the artifact before writing your result.
