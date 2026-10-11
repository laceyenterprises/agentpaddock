"""Which subscribers receive which registry events.

Subscribers are named as ``module:function`` so that this table is the one place
a deployment edits; each module is imported when the bus is wired at startup.
"""

from __future__ import annotations

import importlib

from events import EventBus

SUBSCRIBERS: dict[str, tuple[str, ...]] = {
    "entry.*": ("audit:record_change", "webhooks:deliver_change"),
}


def resolve(target: str):
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def wire(bus: EventBus, table: dict[str, tuple[str, ...]] = SUBSCRIBERS) -> EventBus:
    for pattern, targets in table.items():
        for target in targets:
            bus.subscribe(pattern, resolve(target))
    return bus
