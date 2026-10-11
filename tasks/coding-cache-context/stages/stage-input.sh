#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/codec.py" <<'STAGED_INPUT'
import unicodedata

def wire_name(name):
    return unicodedata.normalize("NFC", name).casefold()

def cache_key(tenant, name, revision):
    return wire_name(name), revision
STAGED_INPUT
cat >"$sandbox_root/catalog.py" <<'STAGED_INPUT'
from codec import wire_name, cache_key

ROWS = {("red", "straße", 4): 17, ("blue", "strasse", 4): 29,
        ("red", "strasse", 4): 41}

def lookup(cache, tenant, name, revision):
    key = cache_key(tenant, name, revision)
    if key not in cache:
        # Backend uses NFC spelling, NOT the wire/display name.
        import unicodedata
        cache[key] = ROWS[(tenant, unicodedata.normalize("NFC", name), revision)]
    return cache[key]
STAGED_INPUT
cat >"$sandbox_root/scenario.py" <<'STAGED_INPUT'
from catalog import lookup

def replay():
    cache = {}
    return [lookup(cache, *request) for request in
            [("red", "straße", 4), ("blue", "strasse", 4),
             ("red", "strasse", 4), ("red", "straße", 4)]]

if __name__ == "__main__":
    print(replay())
STAGED_INPUT
