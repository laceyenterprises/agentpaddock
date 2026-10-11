# NOTIFY-04: quiet hours, a held-alert digest, and dedupe

Operators asked for quiet nights. This PR intentionally changes when alerts are
delivered.

## Behaviour changes (intentional)

1. **Quiet hours hold non-critical alerts.** An `info` or `warning` alert raised
   inside the configured quiet-hours window is no longer sent when it is raised.
   It is held in the quiet-hours digest. The first `tick()` after the window
   ends sends every held alert as one digest message. Operators approved this:
   nothing non-critical should reach the pager overnight, and the digest is the
   delivery for those alerts.
2. **Critical alerts are never held or rate-limited.** Before this PR, a burst
   could drop a critical alert once 20 alerts had been sent that minute. Now
   critical alerts are always sent immediately and do not count toward the
   limit. Critical alerts are rare (a few per week fleet-wide), so they cannot
   flood the pager.
3. **Duplicates are suppressed.** An alert whose source, key and text match one
   sent or held in the last 10 minutes returns `duplicate` and is not sent. This
   applies at every severity.
4. **Sliding rate limit.** The non-critical rate limit is now a sliding
   one-minute window instead of a fixed window that reset every 60 seconds.

## Details

- **Configuration.** Quiet hours are `HH:MM-HH:MM` in an IANA zone (operator
  config, e.g. `22:00-07:00` in `America/New_York`). The window is half-open,
  so an alert at exactly 07:00 is sent. Windows that cross midnight are the
  normal case.
- **Time handling.** `Notifier` passes UTC datetimes to `QuietHours.contains`,
  which converts to the window's zone before comparing.
- **Persistence.** The digest is persisted in
  `<state_dir>/quiet-hours-digest.json`, so held alerts survive a restart. The
  file is written via `tempfile.mkstemp` (mode 0600) and `os.replace`.
- **Delivery semantics.** Delivery is at-least-once. If the process dies after
  the digest is sent and before the cleared state is saved, the next start sends
  the digest again. A duplicate digest is acceptable; a lost held alert is not.
- **Digest ordering.** The digest lists the most severe alerts first, oldest
  first within a severity, and shows at most 20.
- **Dedupe hash.** The dedupe key is a SHA-1 of source, key and text. It only
  groups identical alerts in memory, is never exposed, and is not a security
  boundary. SHA-1 matches the alert IDs the rest of the alert pipeline already
  uses.
- **New CLI.** `notifyctl` has three read-only subcommands: `status`, `preview`
  and `check`.

`alerts.py` now holds the `Alert` record (moved out of `notify.py`).

## Testing

Unit tests cover:

- midnight-wrapping and same-day windows across a DST change;
- hold-then-flush;
- critical alerts during quiet hours and over the rate limit;
- dedupe expiry;
- the digest file mode.
