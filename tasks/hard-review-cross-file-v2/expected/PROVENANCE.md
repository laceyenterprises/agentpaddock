# Maintainer provenance (never staged)

Source: synthetic, authored for HRX-02 as the reworked `hard-review-cross-file`. `Registry._emit` (`registry.py:58-65`) now publishes a dict snapshot instead of the `Entry` object. The diff updates the one consumer it knows about, `webhooks.deliver_change`. The unchanged `audit.record_change` (`audit.py:12-20`) still reads attributes, so it raises `AttributeError` on every event, and `EventBus.publish` (`events.py`) logs and swallows that.

The consumer is reached only through an indirection. `wiring.py` names subscribers as `module:function` strings and resolves them with `importlib`. No file imports `audit`, `record_change` does not appear in `change.diff`, and searching for the changed names (`_emit`, `publish`, `snapshot`, `Entry`) never reaches `audit.py`. The defect is credited any-of the contract change or the consumer; `read_files` covers all five staged modules, including the indirection file.
