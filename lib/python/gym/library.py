"""Pin one gym-library version for the lifetime of a long-lived run (GYMSKEW-02).

A deploy rewrites ``modules/adversarial-eval-gym/`` under processes that are
already running. On 2026-10-06 an ``hq gym run`` cell started at 12:27Z, before
HRF-01 deployed at about 12:40Z. That process had imported ``gym.schema`` at
start, but ``gym.hidden_tests`` and ``gym.fix_audit`` are imported lazily, inside
functions. Their first import came after the deploy, so Python loaded the NEW
``hidden_tests.py``, which ran ``from gym.schema import is_fix_family`` against
the OLD ``gym.schema`` still in ``sys.modules``. All 6 fix cells graded
``error`` with an ``ImportError``.

:func:`preload_library` closes that window. Run entrypoints call it before they
dispatch any cell. It imports every module of the package, so each later lazy
``from gym.X import Y`` resolves from ``sys.modules`` at the start version. The
lazy imports themselves stay: they break import cycles and keep non-run verbs
light.

The preload also pins the library's content identity: a sha256 of every
``.py`` file. A finder on ``sys.meta_path`` then guards any ``gym.*`` import
that still reaches the disk, such as an :data:`EXCLUDED` module or a module
dropped from ``sys.modules``. It loads only the exact pinned bytes. If the file
is new or changed, it raises :class:`GymLibraryChanged` with the
:data:`REASON` prefix, and the suite runner records that cell as a harness
``error``. A mixed-version cell never grades as a model failure.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import pkgutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

REASON = "gym_library_changed_mid_run"

#: Modules the preload deliberately does not import, with the reason. The
#: identity guard still covers them: a later import must match the pinned bytes.
EXCLUDED: Mapping[str, str] = MappingProxyType(
    {
        "gym.matrix_report": (
            "imports the optional searchlight overlay (`sew`) at module level, and "
            "public clones may not hydrate tools/searchlight; only the short-lived "
            "`hq gym matrix report` verb uses it"
        ),
        "gym.toc_eval": (
            "imports the worker pool (`cwp_dispatch`) at module level; it is the "
            "`hq gym toc-matrix` driver and grades nothing in-process, because every "
            "attempt is a fresh `hq gym run` subprocess that preloads itself"
        ),
    }
)


class GymLibraryChanged(RuntimeError):
    """The gym library on disk no longer matches the version this run pinned."""


@dataclass(frozen=True)
class LibraryIdentity:
    """Content identity of the gym package: sha256 per ``.py`` file and overall."""

    root: Path
    digest: str
    files: Mapping[str, str]


_lock = threading.Lock()
_pinned: LibraryIdentity | None = None
_preloaded: tuple[str, ...] = ()


def package_root() -> Path:
    import gym

    return Path(gym.__file__).resolve().parent


def library_identity(root: Path | None = None) -> LibraryIdentity:
    """Hash every ``.py`` file under the package directory."""
    root = (root or package_root()).resolve()
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    overall = hashlib.sha256()
    for name, digest in files.items():
        overall.update(f"{name}\0{digest}\n".encode())
    return LibraryIdentity(root, overall.hexdigest(), MappingProxyType(files))


def start_identity(root: Path) -> LibraryIdentity | None:
    """Identity at package import, or None when the tree is unreadable then."""
    try:
        return library_identity(root)
    except OSError:
        return None


def module_names(root: Path | None = None) -> list[str]:
    """Every importable module of the package, without importing any of them."""
    root = (root or package_root()).resolve()
    names = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(("gym", *parts)))
    return names


def pinned() -> LibraryIdentity | None:
    """The identity pinned by :func:`preload_library`, or None before it ran."""
    return _pinned


def preloaded_modules() -> tuple[str, ...]:
    return _preloaded


def preload_library() -> LibraryIdentity:
    """Import every gym module once and pin the library identity (idempotent).

    Fails closed with :class:`GymLibraryChanged` when a deploy lands between
    ``import gym`` and the end of the preload, when the process-start identity
    could not be read, or when the grader's verifier
    support bundle (read when ``gym.grader`` was imported) does not match the
    pinned files.
    """
    global _pinned, _preloaded
    with _lock:
        if _pinned is not None:
            return _pinned
        import gym

        before = getattr(gym, "_START_IDENTITY", None)
        if before is None:
            raise GymLibraryChanged(
                f"{REASON}: gym library identity could not be verified at process start; rerun"
            )
        try:
            # Taken in gym/__init__ before any other gym module loaded, so an
            # unchanged digest covers the modules imported before this call too.
            loaded = []
            for info in pkgutil.walk_packages(gym.__path__, prefix="gym."):
                if info.name not in EXCLUDED:
                    importlib.import_module(info.name)
                    loaded.append(info.name)
            after = library_identity()
        except (OSError, ImportError, SyntaxError) as exc:
            raise GymLibraryChanged(
                f"{REASON}: gym library unreadable during preload: {exc}"
            ) from exc
        if after.digest != before.digest:
            raise GymLibraryChanged(
                f"{REASON}: the gym library changed on disk after this process started; rerun"
            )
        _check_support_bundle(after)
        sys.meta_path.insert(0, _PinnedLibraryFinder(after))
        _pinned, _preloaded = after, tuple(sorted(loaded))
        return after


def _check_support_bundle(identity: LibraryIdentity) -> None:
    from gym import grader

    for bundled, source in grader.SUPPORT_SOURCES.items():
        data = grader._SUPPORT_FILES.get(bundled)
        if data is None or hashlib.sha256(data).hexdigest() != identity.files.get(source):
            raise GymLibraryChanged(
                f"{REASON}: verifier support file {source} differs from the pinned library"
            )


def skew_reason(exc: BaseException) -> str | None:
    """The ``gym_library_changed_mid_run`` reason for ``exc``, or None.

    Matches a guard refusal anywhere in the exception chain. Also matches an
    ``ImportError`` from a gym module once the disk no longer matches the pin:
    that is the production failure, kept as defense in depth.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    import_error: ImportError | None = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, GymLibraryChanged):
            return str(current)
        if isinstance(current, ImportError) and import_error is None:
            name = current.name or ""
            if name == "gym" or name.startswith("gym."):
                import_error = current
        current = current.__cause__ or current.__context__
    identity = _pinned
    if import_error is None or identity is None:
        return None
    try:
        changed = library_identity(identity.root).digest != identity.digest
    except OSError:
        changed = True
    if not changed:
        return None
    return f"{REASON}: {type(import_error).__name__}: {import_error}"


class _PinnedSourceLoader(importlib.machinery.SourceFileLoader):
    """Execute the verified bytes, never a second read of the file."""

    def __init__(self, fullname: str, path: str, source: bytes):
        super().__init__(fullname, path)
        self._source = source

    def get_source(self, fullname: str):
        return importlib.util.decode_source(self._source)

    def get_code(self, fullname: str):
        return self.source_to_code(self._source, self.path)


class _PinnedLibraryFinder(importlib.abc.MetaPathFinder):
    """Guard ``gym.*`` imports that miss ``sys.modules`` after the pin."""

    def __init__(self, identity: LibraryIdentity):
        self.identity = identity

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "gym" and not fullname.startswith("gym."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.origin is None:
            return None
        origin = Path(spec.origin).resolve()
        try:
            relative = origin.relative_to(self.identity.root).as_posix()
        except ValueError:
            return None  # Another copy of a `gym` package; not this pin.
        try:
            source = origin.read_bytes()
        except OSError:
            source = None
        expected = self.identity.files.get(relative)
        if source is None or expected != hashlib.sha256(source).hexdigest():
            raise GymLibraryChanged(
                f"{REASON}: {fullname} ({relative}) differs from the gym library "
                f"pinned at run start ({self.identity.digest[:12]})"
            )
        return importlib.util.spec_from_file_location(
            fullname,
            origin,
            loader=_PinnedSourceLoader(fullname, str(origin), source),
            submodule_search_locations=spec.submodule_search_locations,
        )


__all__ = [
    "EXCLUDED",
    "REASON",
    "GymLibraryChanged",
    "LibraryIdentity",
    "library_identity",
    "module_names",
    "pinned",
    "preload_library",
    "preloaded_modules",
    "skew_reason",
]
