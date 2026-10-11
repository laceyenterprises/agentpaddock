# Maintainer provenance (never staged)

Synthetic (HRF-05). Shape: wrong-recommendation task, after the
fall-back-to-defaults class of config-reload bug. No source PR.

The finding's recommendation (catch `json.JSONDecodeError` in
`load_overrides`, log it, return `{}`) is subtly wrong twice over: `{}` makes
`daemon.reload` rebuild the config from `DEFAULTS`, which switches an incident
`merge_enabled: false` back on, and it leaves every other unusable shape
(non-object, unknown key, undecodable bytes) escaping as a crash.

Where the correct behaviour is staged: the `daemon.reload` docstring says a
`config.ConfigError` keeps the last good configuration and sets `degraded`,
and that a bad edit must never switch `merge_enabled` back to its default. The
`load_overrides` docstring says every unusable existing file raises
`ConfigError` and only a missing file returns `{}`. Nothing in `hidden/` is
needed to derive the fix.

Variants:
- `reference/`: decode and parse errors become `ConfigError`; the shape is
  checked (object, known keys, exact default types).
- `literal-advice/`: the recommendation as written. Survives the reported
  trailing-comma reload; reverts the incident override and still crashes on
  the other shapes.
- `insufficient/`: parse and decode errors become `ConfigError`, but the shape
  is not checked, so `[]` and an unknown key still crash.
- `alternative/`: a different correct fix that collects every bad key before
  raising.

Hidden cases: (1) the reported crash; (2) a good load, then a malformed edit:
the override and a degraded marker survive; (3) every unusable shape is a
`ConfigError`, a missing file is `{}`, and a fixed file clears `degraded`.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-wrong-recommendation-config-reload --write`.
