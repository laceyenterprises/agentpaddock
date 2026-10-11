# registry: publish change snapshots; queue webhook deliveries

Webhook delivery posted inside `Registry` operations, so a slow endpoint held up every create, update and remove for up to ten seconds per endpoint. `webhooks.deliver_change` now queues the body and the service posts it with `webhooks.flush` from its main loop between requests. Each endpoint receives changes in order; an endpoint that fails keeps its queued bodies for the next flush without holding up the others.

Queuing exposed a problem with the event payload. Events carried the live `Entry` object, so an update made before the queue drained changed what an earlier queued event would report. Change events now carry an immutable snapshot instead:

- `entry.created`, `entry.updated` and `entry.removed` are published with `Entry.snapshot()`: a dict with `name`, `settings` (a deep copy) and `tags` (a sorted list).
- `entry.updated` events also carry `before`, the snapshot taken before the change, so subscribers can report what changed.
- An update or tag operation that changes nothing no longer publishes an event or rewrites the file.
- New: `Registry.untag`.

`webhooks.deliver_change` builds its body from the snapshot; the posted JSON is unchanged apart from the new `before` field on updates.

The sandbox holds the registry package: `registry.py` and `webhooks.py` (changed) and `events.py`, `wiring.py` and `audit.py` (unchanged).
