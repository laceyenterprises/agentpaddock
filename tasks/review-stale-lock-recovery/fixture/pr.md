# Serialize registry updates across processes

Add a synchronous lock shared between processes so read-modify-write updates to a registry file happen one at a time. Several independent processes use the same lock path, and a holder can crash while holding it. Callers keep the returned descriptor while they read and replace the registry, then close it and remove the lock. No other mutex exists.

Trimmed excerpt: imports are reduced and the module's sleep helper is shown as its stdlib equivalent. The acquire function and its constants are complete. Review the acquire change; the omitted update and release paths are unchanged context. Line numbers refer to file-lock.mjs.
