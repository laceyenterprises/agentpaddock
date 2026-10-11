# Maintainer provenance (never staged)

Source: synthetic; authored for HRF-04. `registry.find_entry` changes from raising `KeyError` to returning `None` (`registry.py:47-49`). The unchanged caller `sync.py:26-32` still catches `KeyError` to create missing entries, so every new desired entry now raises `AttributeError` at `entry.settings`, and the sync aborts before saving. `sync.py` is staged but absent from `change.diff`. The defect is any-of the contract change or the caller; `read_files` includes both.
