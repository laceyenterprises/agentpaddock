"""Task/verifier schema for the eval gym.

A gym task is a self-contained directory ``tasks/<id>/`` with:

  * ``task.toml``   — this schema: id, suite, persona, stages, result_schema, verifier
  * ``verifier.py`` — the out-of-band verifier (reads BENCH_RESULT + BENCH_TOOLCALLS)
  * ``expected/``   — ground truth, NEVER staged to the agent and NEVER visible to
                      the verifier (the grader runs the verifier out of band).

Parsing is deliberately *strict*: an unknown key anywhere fails loudly rather
than being silently dropped, and ``result_schema`` / ``[verifier]`` are
mandatory. A silently-dropped key is how a corpus task drifts into never
actually asserting what its author thought it did.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gym.baseline import validate_model_id

# Suites a task may belong to. "all" is a run-time selector over every task, not
# a task-level membership value (SPEC §10).
SUITES: frozenset[str] = frozenset(
    {"coding", "injection", "rca", "review", "fix", "docs", "hard-review", "hard-fix"}
)

# Suites whose tasks run agent code under the hidden-test containment. Every
# fix-only safeguard (hidden-test permission, activation seam, original-bytes
# snapshot, integrity hold, sealed fix_audit envelope) keys on is_fix_family,
# never on a literal suite name (HRF SPEC §3, HRF-01).
FIX_FAMILY_SUITES: frozenset[str] = frozenset({"fix", "hard-fix"})


# Reserved regression variants shared by schema validation and execution.
REGRESSION_VARIANTS = ("original", "insufficient", "reference", "submission")


def is_fix_family(suite: str | None) -> bool:
    """True for the suites whose tasks run agent code: ``fix`` and ``hard-fix``."""
    return suite in FIX_FAMILY_SUITES


# result_schema field types. "enum" is the closed-set case the deterministic
# verifiers lean on (SPEC §5/§6): the agent must submit one of a fixed set.
FIELD_TYPES: frozenset[str] = frozenset({"string", "integer", "number", "boolean", "enum", "list"})

_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "suite",
        "task_version",
        "persona",
        "dispatch",
        "stages",
        "result_schema",
        "verifier",
        "capture",
        "hidden_tests",
        "regression_test",
        "required_large_output_commands",
    }
)
_DISPATCH_KEYS: frozenset[str] = frozenset(
    {"persona", "worker_class", "prompt", "model", "temperature"}
)
_STAGE_KEYS: frozenset[str] = frozenset({"run", "description"})
_VERIFIER_KEYS: frozenset[str] = frozenset({"entrypoint", "description"})
_FIELD_SPEC_KEYS: frozenset[str] = frozenset(
    {"type", "enum", "required", "description", "items", "max_items"}
)


class TaskSchemaError(ValueError):
    """Raised when a task.toml violates the schema. Never swallowed."""


@dataclass(frozen=True)
class FieldSpec:
    """One result field; programmatic lists with max_items=None are unbounded.

    Parsed task schemas require an explicit nonnegative integer list bound.
    """

    name: str
    type: str
    enum: tuple[Any, ...] | None = None
    required: bool = True
    description: str | None = None
    items: tuple[FieldSpec, ...] = ()
    max_items: int | None = None


@dataclass(frozen=True)
class Stage:
    """One staging step that prepares the agent's sandbox before the run."""

    run: str
    description: str | None = None


@dataclass(frozen=True)
class Task:
    """A parsed, validated gym task."""

    id: str
    result_schema: tuple[FieldSpec, ...]
    verifier_entrypoint: str
    suite: str | None = None
    title: str | None = None
    #: PDK-03: bumped whenever a task's graded content changes, so results are
    #: only compared within one version. Every shipped task declares it.
    task_version: int = 1
    persona: str | None = None
    dispatch: dict[str, Any] = field(default_factory=dict)
    stages: tuple[Stage, ...] = ()
    verifier_description: str | None = None
    task_dir: Path | None = None
    required_large_output_commands: tuple[str, ...] = ()
    capture_files: tuple[str, ...] = ()
    hidden_tests_entrypoint: str | None = None
    hidden_tests_timeout_seconds: int = 120
    hidden_tests_expected_tests: int | None = None
    #: HRF-03: the agent-written unittest file graded against four variants.
    regression_test_path: str | None = None
    regression_test_timeout_seconds: int = 60
    regression_test_must_fail_variants: tuple[str, ...] = ()

    def field_spec(self, name: str) -> FieldSpec | None:
        for spec in self.result_schema:
            if spec.name == name:
                return spec
        return None

    @property
    def verifier_path(self) -> Path | None:
        if self.task_dir is None:
            return None
        return self.task_dir / self.verifier_entrypoint

    @property
    def expected_dir(self) -> Path | None:
        """Ground-truth dir. Present for verifier authors; NEVER staged to the
        agent and NEVER reachable from the out-of-band verifier env."""
        if self.task_dir is None:
            return None
        return self.task_dir / "expected"


def _require_mapping(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskSchemaError(f"{where} must be a table, got {type(value).__name__}")
    return value


def _reject_unknown(mapping: dict[str, Any], allowed: frozenset[str], *, where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise TaskSchemaError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _parse_field_spec(name: str, raw: Any, *, scalar_only: bool = False) -> FieldSpec:
    spec = _require_mapping(raw, where=f"result_schema.{name}")
    _reject_unknown(spec, _FIELD_SPEC_KEYS, where=f"result_schema.{name}")
    ftype = spec.get("type")
    if not isinstance(ftype, str) or ftype not in FIELD_TYPES:
        raise TaskSchemaError(
            f"result_schema.{name}.type must be one of {sorted(FIELD_TYPES)}, got {ftype!r}"
        )
    items: tuple[FieldSpec, ...] = ()
    max_items = None
    if ftype == "list":
        if scalar_only:
            raise TaskSchemaError("nested lists are not supported")
        raw_items = _require_mapping(spec.get("items"), where=f"{name}.items")
        items = tuple(_parse_field_spec(k, v, scalar_only=True) for k, v in raw_items.items())
        max_items = spec.get("max_items")
        if type(max_items) is not int or max_items < 0:
            raise TaskSchemaError(f"{name}.max_items must be a nonnegative integer")
    elif "items" in spec or "max_items" in spec:
        raise TaskSchemaError("items and max_items require type = 'list'")
    enum_values: tuple[Any, ...] | None = None
    if ftype == "enum":
        raw_enum = spec.get("enum")
        if not isinstance(raw_enum, list) or not raw_enum:
            raise TaskSchemaError(
                f"result_schema.{name}: enum type requires a non-empty 'enum' list"
            )
        enum_values = tuple(raw_enum)
    elif "enum" in spec:
        raise TaskSchemaError(f"result_schema.{name}: 'enum' is only valid when type = 'enum'")
    required = spec.get("required", True)
    if not isinstance(required, bool):
        raise TaskSchemaError(f"result_schema.{name}.required must be a boolean")
    description = spec.get("description")
    if description is not None and not isinstance(description, str):
        raise TaskSchemaError(f"result_schema.{name}.description must be a string")
    return FieldSpec(
        name=name,
        type=ftype,
        enum=enum_values,
        required=required,
        description=description,
        items=items,
        max_items=max_items,
    )


def _parse_stages(raw: Any) -> tuple[Stage, ...]:
    if not isinstance(raw, list):
        raise TaskSchemaError("stages must be an array")
    stages: list[Stage] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            stages.append(Stage(run=item))
            continue
        table = _require_mapping(item, where=f"stages[{i}]")
        _reject_unknown(table, _STAGE_KEYS, where=f"stages[{i}]")
        run = table.get("run")
        if not isinstance(run, str) or not run:
            raise TaskSchemaError(f"stages[{i}].run must be a non-empty string")
        description = table.get("description")
        if description is not None and not isinstance(description, str):
            raise TaskSchemaError(f"stages[{i}].description must be a string")
        stages.append(Stage(run=run, description=description))
    return tuple(stages)


def parse_task_dict(data: dict[str, Any], *, task_dir: Path | None = None) -> Task:
    """Validate task data; check variant directories only when task_dir is provided."""
    _require_mapping(data, where="task.toml")
    _reject_unknown(data, _TOP_LEVEL_KEYS, where="task.toml")

    task_id = data.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise TaskSchemaError("task.toml: 'id' is required and must be a non-empty string")

    if "result_schema" not in data:
        raise TaskSchemaError("task.toml: 'result_schema' table is required")
    raw_schema = _require_mapping(data["result_schema"], where="result_schema")
    if not raw_schema:
        raise TaskSchemaError("result_schema must declare at least one field")
    result_schema = tuple(_parse_field_spec(name, spec) for name, spec in raw_schema.items())

    if "verifier" not in data:
        raise TaskSchemaError("task.toml: '[verifier]' table is required")
    raw_verifier = _require_mapping(data["verifier"], where="verifier")
    _reject_unknown(raw_verifier, _VERIFIER_KEYS, where="verifier")
    entrypoint = raw_verifier.get("entrypoint", "verifier.py")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise TaskSchemaError("verifier.entrypoint must be a non-empty string")

    suite = data.get("suite")
    if suite is not None and suite not in SUITES:
        raise TaskSchemaError(f"suite must be one of {sorted(SUITES)} (or omitted), got {suite!r}")

    task_version = data.get("task_version", 1)
    if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version < 1:
        raise TaskSchemaError(f"task_version must be a positive integer, got {task_version!r}")

    for optional_str in ("title", "persona"):
        value = data.get(optional_str)
        if value is not None and not isinstance(value, str):
            raise TaskSchemaError(f"{optional_str} must be a string")

    dispatch: dict[str, Any] = {}
    if "dispatch" in data:
        dispatch = _require_mapping(data["dispatch"], where="dispatch")
        _reject_unknown(dispatch, _DISPATCH_KEYS, where="dispatch")
        pinned_model = dispatch.get("model")
        if isinstance(pinned_model, str):
            try:
                validate_model_id(pinned_model)
            except ValueError as exc:
                raise TaskSchemaError(f"dispatch.model: {exc}") from None

    capture = _require_mapping(data.get("capture", {}), where="capture")
    _reject_unknown(capture, frozenset({"files"}), where="capture")
    capture_files = capture.get("files", [])
    if not isinstance(capture_files, list) or len(capture_files) > 8:
        raise TaskSchemaError("capture.files must be an array of at most 8 paths")
    for path in capture_files:
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or path == "."
        ):
            raise TaskSchemaError("capture.files paths must be workspace-relative without '..'")

    hidden_entrypoint = None
    hidden_timeout = 120
    hidden_expected_tests = None
    if "hidden_tests" in data:
        if not is_fix_family(suite):
            raise TaskSchemaError(
                f"hidden_tests requires suite in {sorted(FIX_FAMILY_SUITES)}, got {suite!r}"
            )
        hidden = _require_mapping(data["hidden_tests"], where="hidden_tests")
        _reject_unknown(
            hidden,
            frozenset({"entrypoint", "timeout_seconds", "expected_tests"}),
            where="hidden_tests",
        )
        hidden_entrypoint = hidden.get("entrypoint")
        if (
            not isinstance(hidden_entrypoint, str)
            or not hidden_entrypoint.startswith("hidden/")
            or len(Path(hidden_entrypoint).parts) != 2
            or ".." in Path(hidden_entrypoint).parts
            or not hidden_entrypoint.endswith(".json")
        ):
            raise TaskSchemaError("hidden_tests.entrypoint must be hidden/<file>.json")
        hidden_timeout = hidden.get("timeout_seconds", 120)
        if type(hidden_timeout) is not int or not 1 <= hidden_timeout <= 120:
            raise TaskSchemaError("hidden_tests.timeout_seconds must be an integer from 1 to 120")
        hidden_expected_tests = hidden.get("expected_tests")
        if type(hidden_expected_tests) is not int or hidden_expected_tests < 1:
            raise TaskSchemaError("hidden_tests.expected_tests must be a positive integer")

    regression_path = None
    regression_timeout = 60
    must_fail_variants = []
    if "regression_test" in data:
        if not is_fix_family(suite):
            raise TaskSchemaError(
                f"regression_test requires suite in {sorted(FIX_FAMILY_SUITES)}, got {suite!r}"
            )
        if hidden_entrypoint is None:
            # The test runs only after the hidden cases pass.
            raise TaskSchemaError("regression_test requires [hidden_tests]")
        regression = _require_mapping(data["regression_test"], where="regression_test")
        _reject_unknown(
            regression,
            frozenset({"path", "timeout_seconds", "must_fail_variants"}),
            where="regression_test",
        )
        must_fail_variants = regression.get("must_fail_variants", [])
        if (
            not isinstance(must_fail_variants, list)
            or len(must_fail_variants) > 3
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or name in REGRESSION_VARIANTS
                for name in must_fail_variants
            )
            or len(set(must_fail_variants)) != len(must_fail_variants)
        ):
            raise TaskSchemaError(
                "regression_test.must_fail_variants must be at most 3 distinct variant names "
                "without built-in collisions"
            )
        for name in must_fail_variants:
            if task_dir is not None and not (task_dir / "hidden/variants" / name).is_dir():
                raise TaskSchemaError(f"regression_test: missing variant directory {name!r}")
        regression_path = regression.get("path")
        if (
            not isinstance(regression_path, str)
            or not re.fullmatch(r"test_\w+\.py", regression_path)
            or regression_path not in capture_files
        ):
            raise TaskSchemaError(
                "regression_test.path must be a captured top-level test_<name>.py file"
            )
        regression_timeout = regression.get("timeout_seconds", 60)
        if type(regression_timeout) is not int or not 1 <= regression_timeout <= 60:
            raise TaskSchemaError("regression_test.timeout_seconds must be an integer from 1 to 60")

    required_commands = data.get("required_large_output_commands", [])
    if "required_large_output_commands" in data:
        # TOC is a host integration: the host that owns the seam names the
        # command labels. A host without one (standalone) checks the shape only,
        # so one TOC task cannot stop the rest of the corpus loading.
        from gym import host_services

        commands = host_services.current().large_output_commands()
        if (
            not isinstance(required_commands, list)
            or not required_commands
            or any(
                not isinstance(command, str)
                or not command
                or (commands is not None and command not in commands)
                for command in required_commands
            )
            or len(set(required_commands)) != len(required_commands)
        ):
            raise TaskSchemaError(
                "required_large_output_commands must be a nonempty array of distinct TOC command labels"
            )

    stages = _parse_stages(data["stages"]) if "stages" in data else ()

    return Task(
        id=task_id,
        required_large_output_commands=tuple(required_commands),
        result_schema=result_schema,
        verifier_entrypoint=entrypoint,
        suite=suite,
        title=data.get("title"),
        task_version=task_version,
        persona=data.get("persona"),
        dispatch=dict(dispatch),
        stages=stages,
        verifier_description=raw_verifier.get("description"),
        task_dir=task_dir,
        capture_files=tuple(capture_files),
        hidden_tests_entrypoint=hidden_entrypoint,
        hidden_tests_timeout_seconds=hidden_timeout,
        hidden_tests_expected_tests=hidden_expected_tests,
        regression_test_path=regression_path,
        regression_test_timeout_seconds=regression_timeout,
        regression_test_must_fail_variants=tuple(must_fail_variants),
    )


def load_task(task_dir: str | Path) -> Task:
    """Parse ``<task_dir>/task.toml`` into a validated Task."""
    task_dir = Path(task_dir)
    toml_path = task_dir / "task.toml"
    try:
        raw = toml_path.read_bytes()
    except OSError as exc:
        raise TaskSchemaError(f"cannot read {toml_path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise TaskSchemaError(f"{toml_path}: invalid TOML: {exc}") from exc
    task = parse_task_dict(data, task_dir=task_dir)
    if task.verifier_path is not None and not task.verifier_path.exists():
        raise TaskSchemaError(
            f"{toml_path}: verifier entrypoint {task.verifier_entrypoint!r} not found"
        )
    return task


def validate_result(task: Task, result: Any) -> str | None:
    """Check a submitted result against the task's result_schema.

    Returns None if the result conforms; otherwise a short reason string. A
    non-None return means the cell resolves to ``format_failed`` — closed-set
    grading, so an out-of-enum value or an unknown field is a format failure,
    not a silent pass.
    """
    if not isinstance(result, dict):
        return f"result must be a JSON object, got {type(result).__name__}"

    return _validate_fields(task.result_schema, result)


def _validate_fields(specs: tuple[FieldSpec, ...], result: dict[str, Any]) -> str | None:
    allowed = {spec.name for spec in specs}
    extra = sorted(set(result) - allowed)
    if extra:
        return f"result carries unknown field(s) {extra}"

    for spec in specs:
        if spec.name not in result:
            if spec.required:
                return f"missing required field {spec.name!r}"
            continue
        value = result[spec.name]
        reason = _check_field_value(spec, value)
        if reason is not None:
            return reason
    return None


def _check_field_value(spec: FieldSpec, value: Any) -> str | None:
    if spec.type == "list":
        if not isinstance(value, list):
            return f"field {spec.name!r} must be a list"
        if spec.max_items is not None and len(value) > spec.max_items:
            return f"field {spec.name!r} exceeds max_items"
        for item in value:
            if not isinstance(item, dict):
                return f"field {spec.name!r} items must be objects"
            reason = _validate_fields(spec.items, item)
            if reason:
                return f"field {spec.name!r}: {reason}"
        return None
    if spec.type == "enum":
        assert spec.enum is not None
        if value not in spec.enum:
            return f"field {spec.name!r} value {value!r} not in closed set {list(spec.enum)}"
        return None
    if spec.type == "string":
        if not isinstance(value, str):
            return f"field {spec.name!r} must be a string"
    elif spec.type == "integer":
        # bool is a subclass of int; reject it as a distinct type.
        if isinstance(value, bool) or not isinstance(value, int):
            return f"field {spec.name!r} must be an integer"
    elif spec.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"field {spec.name!r} must be a number"
    elif spec.type == "boolean":
        if not isinstance(value, bool):
            return f"field {spec.name!r} must be a boolean"
    return None


__all__ = [
    "SUITES",
    "FIX_FAMILY_SUITES",
    "is_fix_family",
    "FIELD_TYPES",
    "TaskSchemaError",
    "FieldSpec",
    "Stage",
    "Task",
    "parse_task_dict",
    "load_task",
    "validate_result",
]
