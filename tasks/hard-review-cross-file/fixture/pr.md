# registry: find_entry returns None for an unknown name

`Registry.find_entry` raised `KeyError` for an unknown name, which forced a `try`/`except` around every lookup in `registry.py`. It now returns `None`, matching `dict.get`, and the lookups in `update_settings`, `rename` and `tag` check for `None` instead. `remove` uses `dict.pop` with a default. No user-visible behaviour changes: each operation raises the same `RegistryError` as before.

The sandbox holds both modules of the registry package: `registry.py` (changed) and `sync.py` (the provisioning sync, unchanged by this PR).
