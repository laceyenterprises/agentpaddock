"""Account registry: named entries persisted as one JSON document."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


class RegistryError(Exception):
    """The registry document is unreadable or an operation is invalid."""


@dataclass
class Entry:
    name: str
    settings: dict[str, object]
    created_at: float
    updated_at: float
    tags: list[str] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = time.time()


class Registry:
    """Entries keyed by name. Callers hold the instance for one operation."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, Entry] = {}
        if path.exists():
            try:
                document = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                raise RegistryError(f"{path}: {exc}") from exc
            for raw in document.get("entries", []):
                entry = Entry(**raw)
                self._entries[entry.name] = entry

    def names(self) -> list[str]:
        return sorted(self._entries)

    def find_entry(self, name: str) -> Entry | None:
        """Return the entry called ``name``, or None when there is none."""
        return self._entries.get(name)

    def create_entry(self, name: str, settings: dict[str, object]) -> Entry:
        if name in self._entries:
            raise RegistryError(f"entry {name!r} already exists")
        now = time.time()
        entry = Entry(name=name, settings=dict(settings), created_at=now, updated_at=now)
        self._entries[name] = entry
        return entry

    def update_settings(self, name: str, settings: dict[str, object]) -> Entry:
        entry = self.find_entry(name)
        if entry is None:
            raise RegistryError(f"no entry {name!r}")
        entry.settings = dict(settings)
        entry.touch()
        return entry

    def rename(self, old: str, new: str) -> Entry:
        entry = self.find_entry(old)
        if entry is None:
            raise RegistryError(f"no entry {old!r}")
        if self.find_entry(new) is not None:
            raise RegistryError(f"entry {new!r} already exists")
        del self._entries[old]
        entry.name = new
        entry.touch()
        self._entries[new] = entry
        return entry

    def remove(self, name: str) -> bool:
        """Remove ``name``; return False when it was already absent."""
        return self._entries.pop(name, None) is not None

    def tag(self, name: str, tag: str) -> None:
        entry = self.find_entry(name)
        if entry is None:
            raise RegistryError(f"no entry {name!r}")
        if tag not in entry.tags:
            entry.tags.append(tag)
            entry.touch()

    def save(self) -> None:
        """Atomically replace the registry document."""
        document = {"entries": [asdict(self._entries[name]) for name in self.names()]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".registry-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._path)
        except BaseException:
            os.unlink(temp)
            raise
