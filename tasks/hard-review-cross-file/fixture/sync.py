"""Converge the registry on a desired-state document from the provisioning repo."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from registry import Registry

LOGGER = logging.getLogger(__name__)


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def sync(registry: Registry, desired: dict[str, dict[str, object]], *, prune: bool) -> SyncReport:
    """Create, update and (with ``prune``) remove entries to match ``desired``."""
    report = SyncReport()
    for name, settings in sorted(desired.items()):
        try:
            entry = registry.find_entry(name)
        except KeyError:
            registry.create_entry(name, settings)
            report.created.append(name)
            continue
        if entry.settings == settings:
            report.unchanged.append(name)
        else:
            registry.update_settings(name, settings)
            report.updated.append(name)
    if prune:
        for name in registry.names():
            if name not in desired:
                registry.remove(name)
                report.removed.append(name)
    return report


def run(
    registry_path: Path, desired: dict[str, dict[str, object]], *, prune: bool = False
) -> SyncReport:
    registry = Registry(registry_path)
    report = sync(registry, desired, prune=prune)
    registry.save()
    LOGGER.info(
        "registry.sync created=%d updated=%d removed=%d unchanged=%d",
        len(report.created),
        len(report.updated),
        len(report.removed),
        len(report.unchanged),
    )
    return report
