#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/store.py" <<'STAGED_INPUT'
class AckLost(Exception):
    pass

class Store:
    def __init__(self):
        self.balance = 0
        self.receipts = set()
        self.pending = None
        self.lose_ack = True

    def begin(self):
        self.pending = (self.balance, self.receipts.copy())

    def apply(self, event, amount):
        balance, receipts = self.pending
        self.pending = (balance + amount, receipts)

    def mark(self, event):
        self.pending[1].add(event)

    def commit(self):
        self.balance, self.receipts = self.pending
        self.pending = None
        if self.lose_ack:
            self.lose_ack = False
            raise AckLost()

    def rollback(self):
        self.pending = None  # Does not undo an already durable commit.
STAGED_INPUT
cat >"$sandbox_root/consumer.py" <<'STAGED_INPUT'
from store import AckLost

def consume(store, event, amount):
    for attempt in range(2):
        store.begin()
        try:
            store.apply(event, amount)
            store.commit()
            store.begin()
            store.mark(event)
            store.commit()
            return
        except AckLost:
            store.rollback()
STAGED_INPUT
cat >"$sandbox_root/scenario.py" <<'STAGED_INPUT'
from store import Store
from consumer import consume

def replay():
    store = Store()
    consume(store, "e7", 13)
    consume(store, "e7", 13)  # Redelivery uses the same event id.
    return store.balance, len(store.receipts)

if __name__ == "__main__":
    print(replay())
STAGED_INPUT
