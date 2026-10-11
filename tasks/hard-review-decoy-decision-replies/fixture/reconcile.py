"""Periodic decision upkeep: replay lost dispatches and page overdue decisions."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from decisions import DecisionStore
from replies import Dispatch, locked, reply

LOGGER = logging.getLogger(__name__)

# An open decision pages the operator once at each of these ages.
PAGE_THRESHOLDS_MINUTES = (15, 60, 240)


def reconcile_answered(
    store: DecisionStore, root: Path, dispatch: Dispatch, *, now: float
) -> list[str]:
    """Dispatch answers that were recorded while the dispatcher was down."""
    replayed = []
    for decision in store.answered_undispatched():
        with locked(root, decision.id):
            # reply() would take this same lock; pass that it is held.
            reply(store, root, decision.id, decision.answer, dispatch, now=now, _lock_held=True)
        replayed.append(decision.id)
    return replayed


def _load_receipts(path: Path) -> dict[str, list[int]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_receipts(path: Path, receipts: dict[str, list[int]]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(receipts, sort_keys=True))
    os.replace(temp, path)


def page_deadlines(
    store: DecisionStore, receipts_path: Path, *, now: float
) -> list[tuple[str, int]]:
    """Page each open decision once for every threshold its age has crossed.

    The receipt for a page is written only after enqueue_page returns, so a
    failed enqueue is retried on the next tick rather than suppressed.
    """
    receipts = _load_receipts(receipts_path)
    paged = []
    for decision in store.open_decisions():
        age_minutes = (now - decision.raised_at) / 60
        for threshold in PAGE_THRESHOLDS_MINUTES:
            if age_minutes < threshold:
                break
            if decision.id in receipts:
                continue
            text = f"Decision {decision.id} from {decision.worker} open {int(age_minutes)}m: {decision.question}"
            store.enqueue_page(decision.id, threshold, text, now=now)
            receipts.setdefault(decision.id, []).append(threshold)
            _save_receipts(receipts_path, receipts)
            paged.append((decision.id, threshold))
    return paged


def tick(store: DecisionStore, root: Path, dispatch: Dispatch, *, now: float) -> dict[str, int]:
    """One reconcile pass. A failed replay is logged and retried next tick."""
    try:
        replayed = reconcile_answered(store, root, dispatch, now=now)
    except Exception:
        LOGGER.exception("decisions.replay_failed")
        replayed = []
    paged = page_deadlines(store, root / "decisions" / "page-receipts.json", now=now)
    return {"replayed": len(replayed), "paged": len(paged)}


def _print_dispatch(worker: str, answer: str) -> str:
    """Dry-run dispatcher: print the answer instead of delivering it."""
    print(f"would dispatch to {worker}: {answer}")
    return f"dry-run:{worker}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="decisions-reconcile")
    parser.add_argument("--db", required=True, help="decisions SQLite database")
    parser.add_argument("--root", required=True, type=Path, help="HQ state root")
    parser.add_argument("--dry-run", action="store_true", help="print instead of dispatching")
    parser.add_argument("--now", type=float, default=None, help="override the clock (tests)")
    args = parser.parse_args(argv)
    if not args.dry_run:
        print("decisions-reconcile: live dispatch runs inside the HQ daemon", file=sys.stderr)
        return 2
    store = DecisionStore(args.db)
    try:
        now = time.time() if args.now is None else args.now
        counts = tick(store, args.root, _print_dispatch, now=now)
    finally:
        store.close()
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
