# Retention planning and batch archival

Intentionally change retention: dirty terminal workers are deferred for an operator and omitted from the returned removed IDs. Archive every eligible clean terminal worker before beginning removal. If any archive fails, propagate the exception and remove none, even those already archived. An archive without removal is safe and idempotent; a later pass repeats it. Removal may fail partway through the removal phase and that exception must also propagate. Input order is stable and IDs are unique. Each worker has id, terminal and dirty keys. Callbacks do not mutate workers.

This refactor introduces an immutable plan and separates the two phases. Review correctness against these intentional behavior changes.
