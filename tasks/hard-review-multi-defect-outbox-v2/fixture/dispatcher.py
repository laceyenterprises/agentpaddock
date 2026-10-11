"""Delivers outbox messages: claim a batch, send each one, record the outcome.

Several dispatchers may run against one outbox. Each claims its own batch, and
each outcome is recorded only while the dispatcher still holds the claim.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, fields

import backoff
from sender import DeliveryError, PermanentFailure, RetryLater
from store import MAX_ATTEMPTS, Message, Outbox

LOGGER = logging.getLogger(__name__)

# How often each dispatcher releases claims left behind by crashed dispatchers.
RECLAIM_EVERY_SECONDS = 60.0


def worker_name() -> str:
    """A name unique to this dispatcher process, recorded on its claims."""
    return f"{socket.gethostname()}:{os.getpid()}"


class StopFlag:
    """Set by SIGTERM or SIGINT; the dispatcher finishes its batch, then stops."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def __call__(self) -> bool:
        return self._event.is_set()

    def set(self, *_: object) -> None:
        self._event.set()

    def install(self) -> StopFlag:
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, self.set)
        return self


@dataclass
class Stats:
    sent: int = 0
    retried: int = 0
    dead: int = 0
    lost_claims: int = 0

    def add(self, other: Stats) -> None:
        for field in fields(self):
            setattr(self, field.name, getattr(self, field.name) + getattr(other, field.name))

    @property
    def handled(self) -> int:
        return self.sent + self.retried + self.dead + self.lost_claims


class Dispatcher:
    def __init__(
        self,
        outbox: Outbox,
        sender,
        *,
        batch_size: int = 50,
        clock: Callable[[], float] = time.time,
        worker: str | None = None,
    ) -> None:
        self._outbox = outbox
        self._sender = sender
        self._batch_size = batch_size
        self._clock = clock
        self._worker = worker or worker_name()
        self.totals = Stats()

    @property
    def worker(self) -> str:
        return self._worker

    def run_once(self) -> Stats:
        """Claim one batch and attempt each message in it once."""
        stats = Stats()
        batch = self._outbox.claim_batch(self._worker, now=self._clock(), limit=self._batch_size)
        for message in batch:
            self._deliver(message, stats)
        self.totals.add(stats)
        return stats

    def run_forever(
        self, *, idle_sleep: float = 1.0, stop: Callable[[], bool] = lambda: False
    ) -> None:
        last_reclaim = float("-inf")
        while not stop():
            now = self._clock()
            if now - last_reclaim >= RECLAIM_EVERY_SECONDS:
                released = self._outbox.reclaim_expired(now=now)
                if released:
                    LOGGER.warning("released %d expired claims", released)
                last_reclaim = now
            try:
                stats = self.run_once()
            except Exception:
                LOGGER.exception("outbox batch failed; its claims expire and are released")
                stats = Stats()
            if not stats.handled:
                time.sleep(idle_sleep)

    def _deliver(self, message: Message, stats: Stats) -> None:
        try:
            self._sender.send(message.topic, message.body)
        except RetryLater as exc:
            self._failed(message, stats, error=str(exc), wait_at_least=exc.retry_after)
        except DeliveryError as exc:
            self._failed(message, stats, error=str(exc))
        except PermanentFailure as exc:
            self._dead(message, stats, error=f"rejected: {exc}")
        else:
            held = self._outbox.mark_sent(message.id, self._worker, now=self._clock())
            self._count(stats, "sent", held, message)

    def _dead(self, message: Message, stats: Stats, *, error: str) -> None:
        LOGGER.error("dead-lettering %s after %d attempts: %s", message.id, message.attempts, error)
        held = self._outbox.dead_letter(message.id, self._worker, error=error, now=self._clock())
        self._count(stats, "dead", held, message)

    def _count(self, stats: Stats, outcome: str, held: bool, message: Message) -> None:
        if held:
            setattr(stats, outcome, getattr(stats, outcome) + 1)
            return
        # The claim expired and another dispatcher may have the message now.
        LOGGER.warning("lost the claim on %s before recording %s", message.id, outcome)
        stats.lost_claims += 1

    def _failed(
        self, message: Message, stats: Stats, *, error: str, wait_at_least: float = 0.0
    ) -> None:
        if message.attempts >= MAX_ATTEMPTS:
            self._dead(message, stats, error=error)
            return
        wait = max(backoff.delay(message.attempts), wait_at_least)
        LOGGER.warning(
            "attempt %d of %s failed, next in %.0fs: %s", message.attempts, message.id, wait, error
        )
        now = self._clock()
        held = self._outbox.retry(
            message.id, self._worker, error=error, next_attempt_at=now + wait, now=now
        )
        self._count(stats, "retried", held, message)
