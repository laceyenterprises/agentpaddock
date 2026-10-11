# Discriminates multi-file cache identity and normalization

Read codec.py, catalog.py and scenario.py. The synthetic backend's identity
contract is (tenant, NFC spelling, revision); wire names are for display only.
Select the smallest correct cache-key replacement. Predict the original replay
as a comma-separated string without spaces. Predict the replay after ONLY
prepending tenant to the current key. Give the red/strasse value under your
selected replacement, and the number of cache entries after that fixed replay.
Do not change backend identity or disable caching.

Do not edit the staged inputs. Write only your structured diagnosis to
`bench-result.json` in your worktree root, using these fields (integer values
are JSON numbers, string traces have no spaces):

```json
{
  "fix": "one of: tenant-wire-revision | tenant-nfc-revision | nfc-revision | tenant-name",
  "original": "<string>",
  "tenant_only": "<string>",
  "fixed_third": "<integer>",
  "fixed_entries": "<integer>"
}
```
