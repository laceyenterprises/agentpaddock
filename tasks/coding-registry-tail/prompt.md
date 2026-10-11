# Discriminates long-file reading, late rebinding and snapshots

Read consumer.py, scenario.py and the entire ordered registry.py (including
its tail). This discriminates a deep read from trusting early defaults or aliases.
Which concrete profile does SELECTED bind to, and what is its limit before any
request? Each request must own its tags list and may override its own limit;
the template must stay unmodified. Select the smallest fix inside request.
Give first-request tag count after both original requests and the template tag
count after both fixed requests. Changing registration/default semantics is out
of scope; all current registry values are ints or the tags list.

Do not edit the staged inputs. Write only your structured diagnosis to
`bench-result.json` in your worktree root, using these fields (integer values
are JSON numbers, string traces have no spaces):

```json
{
  "profile": "<string>",
  "template_limit": "<integer>",
  "fix": "one of: copy-dict | copy-tags-list | clear-tags-after-return | use-default-limit",
  "original_first_tags": "<integer>",
  "fixed_template_tags": "<integer>"
}
```
