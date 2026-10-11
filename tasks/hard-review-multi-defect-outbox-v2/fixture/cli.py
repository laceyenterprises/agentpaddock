"""The ``outbox`` operator command."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from db import Database
from dispatcher import Dispatcher, StopFlag
from sender import HttpSender
from store import Message, Outbox

DAY_SECONDS = 86400.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outbox")
    parser.add_argument("--db", default="outbox.db")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="create or upgrade the outbox table")
    commands.add_parser("counts", help="print message counts by state")
    status = commands.add_parser("status", help="print counts and the delivery backlog")
    status.add_argument("--max-lag", type=float, default=300.0, metavar="SECONDS")
    run = commands.add_parser("run", help="deliver messages until interrupted")
    run.add_argument("--host", required=True)
    run.add_argument("--port", type=int, default=80)
    run.add_argument("--batch-size", type=int, default=50)
    run.add_argument("--worker", help="claim name; defaults to host:pid")
    dead = commands.add_parser("dead-letters", help="list dead-lettered messages")
    dead.add_argument("--limit", type=int, default=100)
    replay = commands.add_parser("replay", help="requeue dead letters by id")
    replay.add_argument("ids", type=int, nargs="+")
    discard = commands.add_parser("discard", help="delete dead letters by id")
    discard.add_argument("ids", type=int, nargs="+")
    purge = commands.add_parser("purge", help="delete old delivered messages")
    purge.add_argument("--older-than", type=float, required=True, metavar="DAYS")
    commands.add_parser("reclaim", help="release claims held by crashed dispatchers")
    return parser


def describe(message: Message) -> dict:
    return {
        "id": message.id,
        "topic": message.topic,
        "attempts": message.attempts,
        "last_error": message.last_error,
    }


def emit(value) -> None:
    json.dump(value, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None, *, clock=time.time) -> int:
    args = build_parser().parse_args(argv)
    outbox = Outbox(Database(args.db))
    if args.command == "migrate":
        outbox.migrate()
    elif args.command == "counts":
        emit(outbox.counts())
    elif args.command == "run":
        logging.basicConfig(level=logging.INFO)
        sender = HttpSender(args.host, args.port)
        dispatcher = Dispatcher(outbox, sender, batch_size=args.batch_size, worker=args.worker)
        dispatcher.run_forever(stop=StopFlag().install())
        emit(vars(dispatcher.totals))
    elif args.command == "status":
        counts = outbox.counts()
        lag = outbox.oldest_due(now=clock())
        healthy = (lag is None or lag <= args.max_lag) and not counts.get("dead")
        emit({"counts": counts, "lag_seconds": lag, "healthy": healthy})
        return 0 if healthy else 1
    elif args.command == "dead-letters":
        emit([describe(message) for message in outbox.dead_letters(limit=args.limit)])
    elif args.command == "replay":
        missing = [i for i in args.ids if not outbox.replay(i, now=clock())]
        if missing:
            print(f"not dead-lettered: {missing}", file=sys.stderr)
            return 1
    elif args.command == "discard":
        missing = [i for i in args.ids if not outbox.discard(i)]
        if missing:
            print(f"not dead-lettered: {missing}", file=sys.stderr)
            return 1
    elif args.command == "purge":
        if args.older_than < 1:
            print("--older-than must be at least one day", file=sys.stderr)
            return 2
        emit({"purged": outbox.purge(older_than=args.older_than * DAY_SECONDS, now=clock())})
    elif args.command == "reclaim":
        emit({"released": outbox.reclaim_expired(now=clock())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
