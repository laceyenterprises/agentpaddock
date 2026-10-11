#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/loader.py" <<'STAGED_INPUT'
class Loader:
    def __init__(self):
        self.generation = 0
        self.visible = None
        self.pending = {}

    def start(self, label):
        self.generation += 1
        self.pending[self.generation] = label
        return self.generation

    def finish(self, generation, value):
        self.pending.pop(generation, None)
        if not self.pending:
            self.visible = value
STAGED_INPUT
cat >"$sandbox_root/scenario.py" <<'STAGED_INPUT'
from loader import Loader

SCHEDULES = ((1, 2, 3), (1, 3, 2), (2, 1, 3),
             (2, 3, 1), (3, 1, 2), (3, 2, 1))

def replay(order):
    loader = Loader()
    for label in ("old", "middle", "new"):
        loader.start(label)
    trace = []
    for generation in order:
        loader.finish(generation, generation * 10)
        trace.append(loader.visible)
    return trace

if __name__ == "__main__":
    for order in SCHEDULES:
        print(order, replay(order))
STAGED_INPUT
