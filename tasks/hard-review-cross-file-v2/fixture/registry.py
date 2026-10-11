"""A registry of named entries, persisted as one JSON file.

Every change is published on the event bus as ``entry.<action>`` with a snapshot
of the entry (see ``Entry.snapshot``); updates also carry the prior snapshot.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from events import EventBus


class RegistryError(Exception):
    pass


@dataclass
class Entry:
    name: str
    settings: dict = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)

    def snapshot(self) -> dict:
        """A JSON-ready copy of this entry that later changes to it do not affect."""
        return {
            "name": self.name,
            "settings": copy.deepcopy(self.settings),
            "tags": sorted(self.tags),
        }


class Registry:
    def __init__(self, path: Path, bus: EventBus | None = None) -> None:
        self._path = Path(path)
        self._bus = bus
        self._entries = self._load()

    def _load(self) -> dict[str, Entry]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {
            name: Entry(name, item.get("settings", {}), set(item.get("tags", [])))
            for name, item in raw.items()
        }

    def _save(self) -> None:
        raw = {
            name: {"settings": entry.settings, "tags": sorted(entry.tags)}
            for name, entry in self._entries.items()
        }
        self._path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

    def _emit(self, kind: str, entry: Entry, *, before: dict | None = None) -> None:
        """Publish a snapshot of ``entry``, plus its prior snapshot for an update."""
        if self._bus is None:
            return
        payload = entry.snapshot()
        if before is not None:
            payload["before"] = before
        self._bus.publish(kind, payload)

    def get(self, name: str) -> Entry | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def create(self, name: str, settings: dict, tags=()) -> Entry:
        if name in self._entries:
            raise RegistryError(f"{name} already exists")
        entry = Entry(name, copy.deepcopy(settings), set(tags))
        self._entries[name] = entry
        self._save()
        self._emit("entry.created", entry)
        return entry

    def update(self, name: str, settings: dict) -> Entry:
        entry = self._entries.get(name)
        if entry is None:
            raise RegistryError(f"no entry named {name}")
        before = entry.snapshot()
        entry.settings.update(copy.deepcopy(settings))
        return self._changed(entry, before)

    def tag(self, name: str, *tags: str) -> Entry:
        entry = self._entries.get(name)
        if entry is None:
            raise RegistryError(f"no entry named {name}")
        before = entry.snapshot()
        entry.tags.update(tags)
        return self._changed(entry, before)

    def untag(self, name: str, *tags: str) -> Entry:
        entry = self._entries.get(name)
        if entry is None:
            raise RegistryError(f"no entry named {name}")
        before = entry.snapshot()
        entry.tags.difference_update(tags)
        return self._changed(entry, before)

    def _changed(self, entry: Entry, before: dict) -> Entry:
        """Save and publish an update; an update that changed nothing publishes nothing."""
        if entry.snapshot() == before:
            return entry
        self._save()
        self._emit("entry.updated", entry, before=before)
        return entry

    def remove(self, name: str) -> Entry:
        entry = self._entries.pop(name, None)
        if entry is None:
            raise RegistryError(f"no entry named {name}")
        self._save()
        self._emit("entry.removed", entry)
        return entry
