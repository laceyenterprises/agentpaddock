## Run
```bash
hq gym run --suite injection --json        # SPEC §1 summary shape
hq gym run --suite all                      # what the scheduled one-shot runs
hq gym run --suite coding --worker-class oss-codex --model litellm-local-openai/glm-5.2
hq gym baseline update --confirm            # operator-gated; updates the full current model slice
```

A model run stores a JSON record under `$HQ_ROOT/gym/results/<model-hash>/`,
including the exact model ID and model-qualified verdict keys. The command
prints the result path; it does not update the established baseline. Before
dispatch, the lock check uses the worker class the task will actually run on,
including `--worker-class` overrides. The four OSS harness classes and
`oss-local` each have a committed lock in `locks/`; a missing or drifted class
lock aborts the suite.

`gym-baseline.json` now ships with the AMF-08 first-green baseline established.
The operator runs `hq gym baseline update --confirm` whenever the last-known-good
verdict set is deliberately accepted. The scheduled runner never rewrites the
baseline — `baseline update` requires `--confirm`, and the plist only ever calls
`run`.
Baseline updates always sweep `--suite all`; partial-suite updates are rejected
so a narrowed run cannot garbage-collect another suite's last-known-good verdicts.
The baseline stores flat verdict keys plus a `slices` map. A `null` slice is the
default suite, including task-level model pins that use `task@model` keys; a
model string is only a CLI-level `--model` sweep slice. `compare` and
`baseline update` use `slices` as the authority, so a default run does not trip
over old sweep keys and a default update garbage-collects deleted pinned tasks.
When `--model` is used for a sweep, baseline update replaces only keys whose
slice is that model: deleted tasks for the swept model are garbage-collected,
task-pinned cells are not refreshed by the sweep, and other model slices remain
intact.
