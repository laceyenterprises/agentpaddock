"""Outbox dispatcher: claim pending messages and deliver them."""

from __future__ import annotations

import logging
import os
import socket
import time

from sender import DeliveryError, PermanentFailure
from store import Message, Outbox

LOGGER = logging.getLogger(__name__)

# A message is attempted at most this many times before it is dead-lettered.
MAX_ATTEMPTS = 5


def exhausted(message: Message) -> bool:
    """True when the attempt that just failed was the message's last one.

    ``attempts`` counts the message's earlier failed attempts.
    """
    return message.attempts >= MAX_ATTEMPTS


def worker_name() -> str:
    """A name unique to this dispatcher process, recorded on its claims."""
    return f"{socket.gethostname()}:{os.getpid()}"


class Dispatcher:
    def __init__(self, outbox: Outbox, sender, *, batch_size: int = 50) -> None:
        self._outbox = outbox
        self._sender = sender
        self._batch_size = batch_size
        self._worker = worker_name()

    def run_once(self) -> int:
        """Deliver one claimed batch; return how many messages it held."""
        batch = self._outbox.claim_batch(self._worker, limit=self._batch_size)
        for message in batch:
            self._deliver(message)
        return len(batch)

    def run_forever(self, *, idle_sleep: float = 1.0) -> None:
        while True:
            if not self.run_once():
                time.sleep(idle_sleep)

    def _deliver(self, message: Message) -> None:
        try:
            self._sender.send(message.topic, message.body)
        except DeliveryError as exc:
            if exhausted(message):
                LOGGER.error("outbox.dead_letter id=%s error=%s", message.id, exc)
                self._outbox.dead_letter(message.id, str(exc))
            else:
                LOGGER.warning("outbox.retry id=%s error=%s", message.id, exc)
                self._outbox.release(message.id, str(exc))
        except PermanentFailure as exc:
            LOGGER.error("outbox.rejected id=%s error=%s", message.id, exc)
            self._outbox.dead_letter(message.id, str(exc))
        else:
            self._outbox.mark_sent(message.id)
