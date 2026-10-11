"""Committed baseline + regression comparison for the scheduled gym run.

``gym-baseline.json`` is the last-known-good verdict per task. The scheduled
runner grades HEAD, diffs it against this baseline, and the *only* alerting
transition is a task that **passed at baseline and failed at HEAD** (SPEC §1,
§6, §10). Everything else — a task already failing at baseline that still fails,
a task absent from the baseline, a task that newly passes — is not an alert.

Read/write are deterministic: verdicts are sorted by task id so the committed
file and any ``hq gym baseline update`` round-trip are diff-stable. The initial
committed baseline ships *unestablished* (SPEC §7): the operator runs
``hq gym baseline update`` once after the first full green suite to establish it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from gym import outcomes

BASELINE_SCHEMA = "gym-baseline/v1"

#: The ``task_version`` of a verdict recorded before tasks were versioned
#: (PDK-03). Every task then sat at its first version, so a missing entry means 1.
DEFAULT_TASK_VERSION = 1

_UNESTABLISHED_NOTE = (
    "Unestablished baseline: run `hq gym baseline update` after the first full "
    "green suite to record the last-known-good verdicts (SPEC §7). Until then "
    "the regression gate has nothing to diff against and never alerts."
)


#: Separates the task id from the model in a baseline verdict key.
#:
#: A gym cell is only meaningful as (task, model): the same task graded against
#: qwen and against claude-code are two different results, and keying on task id
#: alone means the second run silently overwrites the first. Every field after
#: the model carries a fixed marker (``sha256:`` digest, ``compression=``,
#: ``attempt=``) and is parsed from the right, so model ids may contain this
#: separator.
VERDICT_KEY_SEPARATOR = "@"

#: Marker prefixes for the TOC-05 cohort axes that follow the skill digest.
COMPRESSION_KEY_MARKER = "compression="
ATTEMPT_KEY_MARKER = "attempt="

#: The uncompressed control arm. It keys identically to a run that never named
#: a compression mode, so an ``off`` cell compares against the existing baseline.
COMPRESSION_OFF = "off"
#: Marker for the reasoning-level axis. It sits between the model and any skill
#: digest. Because the model is the leftover text between the task id and the
#: marked axes, a model id with an ``@effort:`` segment would parse as a
#: different (model, level) cohort; :func:`validate_model_id` refuses those ids.
EFFORT_KEY_MARKER = "effort:"


def validate_model_id(model: str | None) -> str | None:
    """Refuse a model id that would collide with a reasoning-level cohort.

    Unlevelled keys must stay byte-identical to the pre-effort layout, so the
    level cannot be encoded in a way that is disjoint from every model string.
    Instead the reserved ``@effort:`` segment is kept out of model ids.
    """
    if model is None:
        return None
    # Every segment counts: a bare "effort:high" model would key like the
    # default-model cohort at level "high".
    for segment in model.split(VERDICT_KEY_SEPARATOR):
        if segment.startswith(EFFORT_KEY_MARKER):
            raise ValueError(
                f"model id {model!r} contains a reserved "
                f"'{EFFORT_KEY_MARKER}' cohort segment; "
                "use --reasoning-level to select a level"
            )
    return model


def cohort_key(model: str | None, reasoning_level: str | None = None) -> str | None:
    """CLI sweep identity; an unlevelled sweep retains its original slice."""
    validate_model_id(model)
    if reasoning_level is None:
        return model
    return f"{model or ''}{VERDICT_KEY_SEPARATOR}{EFFORT_KEY_MARKER}{reasoning_level}"


def verdict_key(
    task_id: str,
    model: str | None = None,
    skill_digest: str | None = None,
    compression: str | None = None,
    attempt: int | None = None,
    reasoning_level: str | None = None,
) -> str:
    """Baseline key for one (task, model, skill digest, compression, attempt) cohort cell.

    A run with no model override keeps the bare task id, so every baseline
    written before models were selectable still reads and compares unchanged.
    A run with no skill digest, no compression (or ``off``), and no attempt
    likewise preserves the old key.
    """
    validate_model_id(model)
    tail: list[str] = []
    if reasoning_level:
        tail.append(f"{EFFORT_KEY_MARKER}{reasoning_level}")
    if skill_digest:
        tail.append(skill_digest)
    if compression and compression != COMPRESSION_OFF:
        tail.append(f"{COMPRESSION_KEY_MARKER}{compression}")
    if attempt is not None:
        tail.append(f"{ATTEMPT_KEY_MARKER}{int(attempt)}")
    if not tail:
        if not model:
            return task_id
        return f"{task_id}{VERDICT_KEY_SEPARATOR}{model}"
    return VERDICT_KEY_SEPARATOR.join((task_id, model or "", *tail))


def split_verdict_key(key: str) -> tuple[str, str | None]:
    """Inverse of :func:`verdict_key`. Bare task id -> (task_id, None)."""
    task_id, model, *_rest = split_cohort_key(key)
    return task_id, model


def cohort_for_verdict_key(key: str) -> str | None:
    _, model, _, _, _, reasoning_level = split_cohort_key(key)
    return cohort_key(model, reasoning_level)


def split_cohort_key(
    key: str,
) -> tuple[str, str | None, str | None, str | None, int | None, str | None]:
    """Inverse of :func:`verdict_key`.

    Returns ``(task_id, model, skill_digest, compression, attempt, reasoning_level)``. The marked
    trailing axes are peeled off from the right in reverse emission order, then
    the task id is the text before the first separator and the model is
    everything left over, separators included.
    """
    rest = key
    attempt: int | None = None
    compression: str | None = None
    skill_digest: str | None = None
    reasoning_level: str | None = None

    head, separator, last = rest.rpartition(VERDICT_KEY_SEPARATOR)
    if separator and last.startswith(ATTEMPT_KEY_MARKER):
        value = last[len(ATTEMPT_KEY_MARKER) :]
        if value.isdigit():
            attempt = int(value)
            rest = head
    head, separator, last = rest.rpartition(VERDICT_KEY_SEPARATOR)
    if separator and last.startswith(COMPRESSION_KEY_MARKER):
        value = last[len(COMPRESSION_KEY_MARKER) :]
        if value:
            compression = value
            rest = head
    head, separator, last = rest.rpartition(VERDICT_KEY_SEPARATOR)
    if separator and last.startswith("sha256:"):
        skill_digest = last
        rest = head
    head, separator, last = rest.rpartition(VERDICT_KEY_SEPARATOR)
    if separator and last.startswith(EFFORT_KEY_MARKER):
        reasoning_level = last[len(EFFORT_KEY_MARKER) :]
        rest = head

    task_id, separator, model = rest.partition(VERDICT_KEY_SEPARATOR)
    if not separator:
        model = ""
    return task_id, model or None, skill_digest, compression, attempt, reasoning_level


class BaselineError(ValueError):
    """Raised when a baseline file is unreadable or malformed."""


@dataclass(frozen=True)
class Baseline:
    """A parsed ``gym-baseline.json``."""

    established: bool
    generated_at: str | None
    verdicts: dict[str, str] = field(default_factory=dict)
    slices: dict[str, str | None] = field(default_factory=dict)
    note: str | None = None
    #: verdict key -> ``task_version`` the verdict was graded at.
    task_versions: dict[str, int] = field(default_factory=dict)

    def version_for(self, key: str) -> int:
        return self.task_versions.get(key, DEFAULT_TASK_VERSION)

    def verdict_for(
        self,
        task_id: str,
        model: str | None = None,
        skill_digest: str | None = None,
        reasoning_level: str | None = None,
    ) -> str | None:
        return self.verdicts.get(
            verdict_key(task_id, model, skill_digest, reasoning_level=reasoning_level)
        )


def unestablished_baseline() -> dict[str, Any]:
    """The serializable shape shipped as the initial committed baseline."""
    return {
        "schema": BASELINE_SCHEMA,
        "established": False,
        "generated_at": None,
        "note": _UNESTABLISHED_NOTE,
        "slices": {},
        "verdicts": {},
    }


def baseline_document(
    verdicts: Mapping[str, str],
    *,
    generated_at: str,
    slices: Mapping[str, str | None] | None = None,
    task_versions: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Build an *established* baseline document from a verdict map.

    Verdicts are sorted by task id so the serialized file is deterministic.
    ``slices`` records which CLI model sweep produced each verdict. ``None`` is
    the default-suite slice, including task-level model pins; a model string is
    only for cells created by ``hq gym ... --model <model>``. ``task_versions``
    records the task version each verdict was graded at (default 1).
    """
    ordered = {task_id: verdicts[task_id] for task_id in sorted(verdicts)}
    normalized_slices = (
        slices
        if slices is not None
        else {task_id: cohort_for_verdict_key(task_id) for task_id in verdicts}
    )
    ordered_slices = {task_id: normalized_slices.get(task_id) for task_id in sorted(verdicts)}
    versions = task_versions or {}
    return {
        "schema": BASELINE_SCHEMA,
        "established": True,
        "generated_at": generated_at,
        "slices": ordered_slices,
        "task_versions": {
            task_id: versions.get(task_id, DEFAULT_TASK_VERSION) for task_id in sorted(verdicts)
        },
        "verdicts": ordered,
    }


def merge_model_slice(
    existing: Baseline | None,
    verdicts: Mapping[str, str],
    *,
    model: str | None,
    generated_at: str,
    verdict_slices: Mapping[str, str | None] | None = None,
    verdict_versions: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Replace one model's baseline slice while preserving other models.

    A model sweep only re-grades one CLI sweep slice. Task-level model pins are
    part of the default suite even though their verdict keys include a model, so
    key strings alone cannot identify what the operator just refreshed. The safe
    update is: remove old keys for the CLI slice just swept, then merge in fresh
    verdicts from that same slice.
    """
    merged: dict[str, str] = {}
    merged_slices: dict[str, str | None] = {}
    merged_versions: dict[str, int] = {}
    if existing and existing.established:
        for task_id, verdict in existing.verdicts.items():
            if existing.slices.get(task_id) == model:
                continue
            merged[task_id] = verdict
            merged_slices[task_id] = existing.slices.get(task_id)
            merged_versions[task_id] = existing.version_for(task_id)
    incoming_slices = (
        verdict_slices
        if verdict_slices is not None
        else {
            task_id: (None if model is None else cohort_for_verdict_key(task_id))
            for task_id in verdicts
        }
    )
    for task_id, verdict in verdicts.items():
        if incoming_slices.get(task_id) != model:
            continue
        merged[task_id] = verdict
        merged_slices[task_id] = model
        merged_versions[task_id] = (verdict_versions or {}).get(task_id, DEFAULT_TASK_VERSION)
    return baseline_document(
        merged, generated_at=generated_at, slices=merged_slices, task_versions=merged_versions
    )


def serialize_baseline(document: Mapping[str, Any]) -> str:
    """Deterministic JSON text (sorted keys, trailing newline) for committing."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def load_baseline(path: str | Path) -> Baseline:
    """Parse a ``gym-baseline.json`` into a :class:`Baseline`."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineError(f"baseline not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc
    return parse_baseline(raw, where=str(path))


def parse_baseline(raw: Any, *, where: str = "baseline") -> Baseline:
    if not isinstance(raw, dict):
        raise BaselineError(f"{where}: must be a JSON object")
    verdicts_raw = raw.get("verdicts", {})
    if not isinstance(verdicts_raw, dict):
        raise BaselineError(f"{where}: 'verdicts' must be an object")
    verdicts: dict[str, str] = {}
    for task_id, verdict in verdicts_raw.items():
        if not isinstance(task_id, str) or not isinstance(verdict, str):
            raise BaselineError(f"{where}: verdicts must map task-id string -> verdict string")
        if not outcomes.is_outcome(verdict):
            raise BaselineError(f"{where}: task {task_id!r} has non-outcome verdict {verdict!r}")
        verdicts[task_id] = verdict
    slices_raw = raw.get("slices", {})
    if not isinstance(slices_raw, dict):
        raise BaselineError(f"{where}: 'slices' must be an object")
    slices: dict[str, str | None] = {}
    for task_id in verdicts:
        raw_slice = slices_raw.get(task_id)
        if raw_slice is not None and not isinstance(raw_slice, str):
            raise BaselineError(f"{where}: slice for task {task_id!r} must be a string or null")
        if task_id in slices_raw:
            slices[task_id] = raw_slice
        else:
            # Backward compatibility for baselines written before explicit
            # slices existed: bare keys are default-suite cells; model-qualified
            # keys came from model-sweep support and are safest treated as sweep
            # cells until the next explicit baseline update rewrites metadata.
            try:
                slices[task_id] = cohort_for_verdict_key(task_id)
            except ValueError as exc:
                raise BaselineError(f"{where}: invalid cohort key {task_id!r}: {exc}") from exc
    versions_raw = raw.get("task_versions", {})
    if not isinstance(versions_raw, dict):
        raise BaselineError(f"{where}: 'task_versions' must be an object")
    task_versions: dict[str, int] = {}
    for task_id in verdicts:
        version = versions_raw.get(task_id, DEFAULT_TASK_VERSION)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise BaselineError(f"{where}: task_version for {task_id!r} must be a positive integer")
        task_versions[task_id] = version
    generated_at = raw.get("generated_at")
    if generated_at is not None and not isinstance(generated_at, str):
        raise BaselineError(f"{where}: 'generated_at' must be a string or null")
    established = bool(raw.get("established", bool(verdicts))) and bool(verdicts)
    note = raw.get("note")
    return Baseline(
        established=established,
        generated_at=generated_at,
        verdicts=verdicts,
        slices=slices,
        note=note if isinstance(note, str) else None,
        task_versions=task_versions,
    )


def write_baseline(path: str | Path, document: Mapping[str, Any]) -> None:
    """Write a baseline document deterministically to ``path``.

    Creates the parent directory: the established baseline lives under
    ``$HQ_ROOT/gym/`` (host state, not the repo -- see
    ``cli.default_baseline_path``), which does not exist on a fresh host.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialize_baseline(document), encoding="utf-8")


@dataclass(frozen=True)
class Regression:
    """A baseline-pass -> HEAD-fail transition: the one alerting signal."""

    task: str
    baseline_verdict: str
    head_verdict: str


@dataclass(frozen=True)
class NewPass:
    """A task that failed at baseline and passes at HEAD: logged, not alerted."""

    task: str
    baseline_verdict: str


@dataclass(frozen=True)
class RegressionReport:
    """The diff of HEAD verdicts against the committed baseline."""

    established: bool
    regressions: tuple[Regression, ...] = ()
    new_passes: tuple[NewPass, ...] = ()
    #: Baseline keys skipped because HEAD graded a different ``task_version``.
    version_changed: tuple[str, ...] = ()

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)


def compare(
    baseline: Baseline,
    head_verdicts: Mapping[str, str],
    *,
    slice_model: str | None = None,
    head_versions: Mapping[str, int] | None = None,
) -> RegressionReport:
    """Diff HEAD verdicts against the baseline.

    A **regression** is exactly ``baseline == passed`` and ``HEAD == failed`` —
    a task that used to pass and now fails a verifier (SPEC §1/§6). A baseline
    task that was already non-passing, or one absent from the baseline, never
    produces a regression. A **new pass** (baseline present + non-passing,
    HEAD passing) is recorded for logging only.

    HEAD degradations to a *non-fail* non-pass (``agent_error`` /
    ``no_submission`` / ``format_failed`` / a gate outcome) are deliberately not
    regressions: they are transient/harness signals, and paging on them is the
    noisy-gate failure mode SPEC §6 warns against.

    Results compare only within one ``task_version`` (PDK-03): a baseline
    verdict graded at another version than HEAD's is neither a regression nor a
    new pass, only listed in ``version_changed``. ``head_versions`` maps the
    same keys as ``head_verdicts``; a missing entry means version 1.
    """
    if not baseline.established:
        return RegressionReport(established=False)

    regressions: list[Regression] = []
    new_passes: list[NewPass] = []
    version_changed: list[str] = []
    for task_id in sorted(baseline.verdicts):
        if baseline.slices.get(task_id) != slice_model:
            continue
        base_verdict = baseline.verdicts[task_id]
        head_verdict = head_verdicts.get(task_id)
        if head_verdict is None:
            # Task dropped from the corpus at HEAD — not a behavior regression.
            continue
        head_version = (head_versions or {}).get(task_id, DEFAULT_TASK_VERSION)
        if baseline.version_for(task_id) != head_version:
            version_changed.append(task_id)
            continue
        base_passing = outcomes.is_passing(base_verdict)
        head_passing = outcomes.is_passing(head_verdict)
        if base_passing and head_verdict == outcomes.FAILED:
            regressions.append(
                Regression(
                    task=task_id,
                    baseline_verdict=base_verdict,
                    head_verdict=head_verdict,
                )
            )
        elif not base_passing and head_passing:
            new_passes.append(NewPass(task=task_id, baseline_verdict=base_verdict))
    return RegressionReport(
        established=True,
        regressions=tuple(regressions),
        new_passes=tuple(new_passes),
        version_changed=tuple(version_changed),
    )


__all__ = [
    "BASELINE_SCHEMA",
    "DEFAULT_TASK_VERSION",
    "BaselineError",
    "Baseline",
    "Regression",
    "NewPass",
    "RegressionReport",
    "unestablished_baseline",
    "baseline_document",
    "merge_model_slice",
    "serialize_baseline",
    "load_baseline",
    "parse_baseline",
    "write_baseline",
    "compare",
    "split_cohort_key",
    "validate_model_id",
]
