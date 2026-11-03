# Agent Paddock vocabulary

Agent Paddock explains a coding-agent benchmark in the language of motor racing. Each term maps to one thing the
bench measures. Use the term when it helps a reader; use the plain word when it does not.

## The cast

| Racing term | What it means on the bench |
|---|---|
| Driver | The model. |
| Car | The harness: the agent program the model runs inside (Claude Code, Codex, OpenCode, Pi, Antigravity). |
| Entry | One driver in one car at one engine map. Change any one of the three and it is a different entry. |
| Engine map | The effort or reasoning level: low, medium, high, xhigh, max, or the car's own default. |
| Tires | The tools the car mounts: its grip on the environment. |
| Briefing | The instructions the driver gets before the lap: the system prompt and the repository's notes. |
| Team radio | Skills: the coaching the crew gives the driver during the lap. |
| Livery | The colour that identifies a car on a board. It names the car and never encodes a value. |

## Racing

| Racing term | What it means on the bench |
|---|---|
| Race | One task. |
| Run | One attempt at a race. A race can be run more than once. |
| Challenge | A family of races: coding, fix, hard fix, review, hard review, docs and RCA. |
| Calendar | The full set of races across the seven challenges. |
| Harness drill | A standard test of the car in the garage, the same for every entry. |
| Sealed circuit | The contained environment a run happens in. The fence is the containment; race control puts the challenges on the track. |
| Rulebook | The methodology: how races are built, run and graded. |
| Setup sheet | A run's configuration, and the hash that pins it. |
| Track map | How a run works, in four sectors. |
| Track card | Public description and checkpoint disclosure for one race, separate from derived conditions and results. |
| Checkpoint | One verifier check, shown by label, count or kept hidden. |
| Exhibition | A community race awaiting calibration and calendar admission; excluded from standings. |
| Track limits | The caps a run must stay inside: CPU, memory and time. |
| Fuel gauge | A spend cap. |
| Stewards | The grader at the finish line. The driver never sees its checks. |
| Scrutineering | Inspection. A review race asks the driver to scrutineer a change: find the real defects and none that are not there. |
| Shakedown | A smoke run that proves a car starts and finishes before it races for the board. |

## Flags: how a run ended

| Flag | Meaning | Counted in the pass rate? |
|---|---|---|
| Checkered | Finished: the run passed its checks. | Yes |
| DNF | Did not finish: it failed its checks or handed in no answer. | Yes |
| Red | An infrastructure fault, not the model. The run is left out and run again. | No |
| Black | An integrity hold, set aside for a person to review. | No |
| Green | Running: lights out. | Not a result |
| Yellow | An audit is pending. | Not yet |

## Results

| Racing term | What it means on the bench |
|---|---|
| Standings | The leaderboard: entries ranked by their average pass rate. |
| Photo finish | A tie at the top: the leaders' 95% intervals overlap, so the board does not name a winner. |
| Pit wall | Telemetry. On the results boards: one car's results, every driver and engine map that raced in it, challenge by challenge. |
| Timing tower | The ranked list of entries, as on a race broadcast. |
| Podium, trophy | The top three; a category winner. |
| Lap time | Wall-clock time for a run. The only place a time word belongs. |

## Fuel

| Racing term | What it means on the bench |
|---|---|
| Fuel bill | Dollars per race passed. |
| Fuel burn | Tokens per race passed. |
| The fuel triad | `Input (cached %) : Output`, per race passed. Input is every token sent to the model, cached % is the share of it read from the prompt cache, and output is what the model wrote. Example: `82k (81%) : 4k`. |
| Slipstream | Cached tokens: most of the fuel, at a fraction of the price of fresh input. |
| Mileage | Races passed per $10, or per million tokens. |
| Lean, rich | The two ends of the fuel axis: less fuel per pass, more fuel per pass. |
| Racing line | The best-value line: the entries that nothing burning less fuel beats on pass rate. |
| The guzzler | The entry that burns the most fuel per race passed. |

## Wording rules

- **Say "better", never "faster", "quicker" or "fastest".** Speed words read as wall-clock time for the run. "Does
  more effort make this model better?", "the best entry in this car".
- **Models are in harnesses, not on them.** "GPT-6.1 Sol in Pi".
- **Name the average.** A pass rate is an average over runs: write "avg pass rate" and say what it covers.
- **Call ties ties.** When intervals overlap, the board says so instead of naming a winner.
- **Give tokens as the triad**, with a legend nearby, not as a bare total.
- **No real-world likeness.** No real racing series, team, driver, sponsor or car maker.
