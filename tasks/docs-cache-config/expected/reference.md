## Cache
cache.max_entries defaults to 128.
cache.ttl_seconds defaults to 60.
cache.ttl_seconds=0 disables caching; negative values are invalid (exit 2).
Eviction is least-recently-used. --no-cache bypasses reads and writes.
