"""Standalone runner: one harness CLI in a Docker container per task (PDK-01).

``StandaloneTrajectorySource`` is the :class:`~gym.grader.TrajectorySource`
for a machine without an Agent OS host. For each task it:

1. stages the task's public inputs into a fresh local sandbox
   (:mod:`gym.staging`, the same stage scripts and fixture manifest the fleet
   runs) and writes the guarded prompt;
2. runs the harness headless in ``docker run`` with the sandbox bind-mounted
   at ``/workspace`` as the working directory, all capabilities dropped, no
   privilege escalation, a read-only root filesystem with a tmpfs ``/tmp`` as
   the harness home, and the host user's uid so the sandbox stays writable;
3. parses the harness's stdout into the ``harness_toolcalls`` shapes and reads
   ``bench-result.json`` back from the sandbox.

Credentials come only from the process environment or the user config's
``[credentials]`` table. They reach the container as ``-e NAME`` (the value
is in the docker client's environment, never its argv), and every credential
value is redacted from the error text and tool calls the Trajectory carries,
so results never hold one.

The image must already contain the harness CLI (``--pull=never``): building
it is the operator's choice, not the runner's. Gemini is not supported yet; its
CLI has not been verified to install and run headless in a container.

Stdlib and ``gym`` only: this module ships in the standalone package.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable

from gym import harness_toolcalls, staging
from gym.grader import Trajectory
from gym.schema import Task

WORKSPACE = "/workspace"
HOME = "/tmp/paddock-home"
DEFAULT_TIMEOUT_SECONDS = 3600.0
DEFAULT_NETWORK = "bridge"
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPUS = 2.0
DEFAULT_PIDS_LIMIT = 256
_CONTROL_TIMEOUT_SECONDS = 5
_POLL_SECONDS = 0.1
_log = logging.getLogger(__name__)
_REDACTED = "<redacted>"
_MAX_ERROR_CHARS = 2000
_MAX_ARGUMENT_STRING = 2000
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_EVENT_BYTES = 1024 * 1024
_CONTAINER_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]")


@dataclass(frozen=True)
class Harness:
    """How to run one harness CLI headless and read its tool calls."""

    name: str
    argv: Callable[[str, str | None, str | None], list[str]]
    parse: Callable[[list[str]], list[dict[str, Any]]]
    #: Credential variables forwarded from the host when set.
    credential_env: tuple[str, ...]
    #: Non-secret variables that point the harness's state into the tmpfs home.
    home_env: Mapping[str, str] = field(default_factory=dict)


def _claude_argv(prompt: str, model: str | None, reasoning: str | None) -> list[str]:
    argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    # The container is the boundary; a headless run cannot answer a prompt.
    argv += ["--permission-mode", "bypassPermissions"]
    if model:
        argv += ["--model", model]
    if reasoning:
        argv += ["--effort", reasoning]
    return argv


def _codex_argv(prompt: str, model: str | None, reasoning: str | None) -> list[str]:
    argv = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral"]
    # Codex's own sandbox cannot nest inside an unprivileged container.
    argv += ["--dangerously-bypass-approvals-and-sandbox"]
    if model:
        argv += ["-m", model]
    if reasoning:
        argv += ["-c", f"model_reasoning_effort={reasoning}"]
    return [*argv, "--", prompt]


def _opencode_argv(prompt: str, model: str | None, reasoning: str | None) -> list[str]:
    if reasoning is not None:
        raise StandaloneConfigError("opencode does not support reasoning overrides")
    argv = ["opencode", "run", "--format", "json"]
    if model:
        argv += ["--model", model]
    return [*argv, prompt]


def _pi_argv(prompt: str, model: str | None, reasoning: str | None) -> list[str]:
    argv = ["pi", "--no-session", "--mode", "json"]
    if model:
        argv += ["--model", model]
    if reasoning:
        argv += ["--thinking", reasoning]
    return [*argv, "-p", prompt]


_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
)

HARNESSES: dict[str, Harness] = {
    "claude-code": Harness(
        name="claude-code",
        argv=_claude_argv,
        parse=harness_toolcalls.calls_from_claude_stream_json,
        credential_env=(
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
        ),
        home_env={"CLAUDE_CONFIG_DIR": f"{HOME}/.claude"},
    ),
    "codex": Harness(
        name="codex",
        argv=_codex_argv,
        parse=harness_toolcalls.calls_from_codex_exec_stdout,
        credential_env=("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"),
        home_env={"CODEX_HOME": f"{HOME}/.codex"},
    ),
    "opencode": Harness(
        name="opencode",
        argv=_opencode_argv,
        parse=harness_toolcalls.calls_from_opencode_json,
        credential_env=_PROVIDER_KEYS,
        home_env={
            "XDG_CONFIG_HOME": f"{HOME}/.config",
            "XDG_DATA_HOME": f"{HOME}/.local/share",
            "XDG_STATE_HOME": f"{HOME}/.local/state",
        },
    ),
    "pi": Harness(
        name="pi",
        argv=_pi_argv,
        parse=harness_toolcalls.calls_from_pi_json,
        credential_env=_PROVIDER_KEYS,
        home_env={"PI_CODING_AGENT_DIR": f"{HOME}/.pi/agent"},
    ),
}


class StandaloneConfigError(ValueError):
    """The standalone runner's configuration is incomplete or invalid."""


def redact(value: Any, secrets: tuple[str, ...]) -> Any:
    """``value`` with every secret substring replaced, recursively."""
    if not secrets:
        return value
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, _REDACTED)
        return value
    if isinstance(value, dict):
        return {redact(key, secrets): redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    return value


def _cap_arguments(value: Any) -> Any:
    """Bound tool argument bodies only after credential redaction."""
    if isinstance(value, str) and len(value) > _MAX_ARGUMENT_STRING:
        return (
            value[:_MAX_ARGUMENT_STRING]
            + f"...[truncated {len(value) - _MAX_ARGUMENT_STRING} chars]"
        )
    if isinstance(value, dict):
        return {key: _cap_arguments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cap_arguments(item) for item in value]
    return value


class _OutputCollector:
    """Drain both pipes continuously, retaining only bounded complete evidence.

    Readers keep draining after overflow so containment cannot deadlock on a
    full pipe. Overflow evidence is never parsed or graded.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self.exceeded = Event()
        self._lock = Lock()
        self._total = 0
        self._buffers = [bytearray(), bytearray()]
        self._threads = []
        for index, stream in enumerate((proc.stdout, proc.stderr)):
            thread = Thread(target=self._drain, args=(index, stream), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _drain(self, index: int, stream: Any) -> None:
        pending = 0
        try:
            while chunk := stream.read(65536):
                with self._lock:
                    self._total += len(chunk)
                    pieces = chunk.split(b"\n")
                    sizes = [pending + len(pieces[0]), *(len(p) for p in pieces[1:])]
                    pending = sizes[-1] if len(pieces) == 1 else len(pieces[-1])
                    if self._total > _MAX_OUTPUT_BYTES or max(sizes) > _MAX_EVENT_BYTES:
                        self.exceeded.set()
                    if not self.exceeded.is_set():
                        self._buffers[index].extend(chunk)
        finally:
            stream.close()

    def finish(self) -> tuple[str, str]:
        for thread in self._threads:
            thread.join()
        return tuple(bytes(b).decode("utf-8", errors="replace") for b in self._buffers)


@dataclass
class StandaloneTrajectorySource:
    """Run each task's harness CLI in Docker against its staged sandbox."""

    wait_for_interruption_cleanup = True

    harness: str
    image: str
    model: str | None = None
    reasoning_level: str | None = None
    docker_bin: str = "docker"
    network: str = DEFAULT_NETWORK
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    cpus: float = DEFAULT_CPUS
    pids_limit: int = DEFAULT_PIDS_LIMIT
    #: Where sandboxes are created; a system temp directory when None.
    work_dir: Path | None = None
    #: Keep each sandbox after the run (for debugging a cell).
    keep_sandbox: bool = False
    #: Credentials from the user config, by variable name. The environment
    #: wins over the config for the same name.
    config_credentials: Mapping[str, str] = field(default_factory=dict, repr=False)
    #: Extra variable names to forward, beyond the harness's defaults.
    pass_env: tuple[str, ...] = ()
    env: Mapping[str, str] | None = field(default=None, repr=False)
    stop_event: Event = field(default_factory=Event, init=False, repr=False)
    admission_halted: Event = field(default_factory=Event, init=False, repr=False)
    _active: set[str] = field(default_factory=set, init=False, repr=False)
    _active_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _sandboxes: list[Path] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.harness not in HARNESSES:
            known = ", ".join(sorted(HARNESSES))
            raise StandaloneConfigError(f"unknown harness {self.harness!r}; known: {known}")
        if not self.image:
            raise StandaloneConfigError("standalone runner needs a Docker image (--image)")
        if self.harness == "opencode" and self.reasoning_level is not None:
            raise StandaloneConfigError("opencode does not support reasoning overrides")
        for name in ("memory_mb", "pids_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise StandaloneConfigError(f"{name} must be a positive integer")
        for name in ("cpus", "timeout_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise StandaloneConfigError(f"{name} must be finite and positive")
        # The grader reads sandbox_root after run() returns (fix-task workspace
        # capture), so sandboxes outlive run(). The suite closes only forked
        # sources, so a finalizer also removes them when this one is dropped.
        self._finalizer = weakref.finalize(self, _remove_all, self._sandboxes)
        if self.keep_sandbox:
            self._finalizer.detach()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        env: Mapping[str, str] | None = None,
        harness: str | None = None,
        image: str | None = None,
        model: str | None = None,
        reasoning_level: str | None = None,
        **fleet_options: Any,
    ) -> StandaloneTrajectorySource:
        """Build from the user config's ``[standalone]`` and ``[credentials]`` tables.

        Explicit arguments win over the config. Fleet-only options (worker
        class, priority, compression) have no meaning without a host, so a
        value for one is refused rather than silently ignored.
        """
        ignored = sorted(key for key, value in fleet_options.items() if value is not None)
        if ignored:
            raise StandaloneConfigError(
                "standalone runner does not support "
                + ", ".join(f"--{k.replace('_', '-')}" for k in ignored)
                + "; they need an Agent OS host (--runner agent-os)"
            )
        section = config.get("standalone") or {}
        credentials = config.get("credentials") or {}
        if not isinstance(section, Mapping) or not isinstance(credentials, Mapping):
            raise StandaloneConfigError("[standalone] and [credentials] must be tables")
        harness = harness or section.get("harness")
        if not harness:
            known = ", ".join(sorted(HARNESSES))
            raise StandaloneConfigError(f"standalone runner needs a harness (--harness: {known})")
        harness_section = section.get(harness) if isinstance(section.get(harness), Mapping) else {}
        work_dir = section.get("work_dir")
        return cls(
            harness=str(harness),
            image=str(image or harness_section.get("image") or section.get("image") or ""),
            model=model or harness_section.get("model") or section.get("model"),
            reasoning_level=reasoning_level or section.get("reasoning_level"),
            docker_bin=str(section.get("docker_bin") or "docker"),
            network=str(section.get("network") or DEFAULT_NETWORK),
            timeout_seconds=section.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            memory_mb=section.get("memory_mb", DEFAULT_MEMORY_MB),
            cpus=section.get("cpus", DEFAULT_CPUS),
            pids_limit=section.get("pids_limit", DEFAULT_PIDS_LIMIT),
            work_dir=Path(str(work_dir)).expanduser() if work_dir else None,
            config_credentials={str(k): str(v) for k, v in credentials.items()},
            pass_env=tuple(str(name) for name in section.get("pass_env") or ()),
            env=env,
        )

    # -- credentials ---------------------------------------------------------

    def _credentials(self) -> dict[str, str]:
        """Every forwarded variable that has a value, by name."""
        spec = HARNESSES[self.harness]
        environ = os.environ if self.env is None else self.env
        names = dict.fromkeys((*spec.credential_env, *self.pass_env, *self.config_credentials))
        resolved: dict[str, str] = {}
        for name in names:
            value = environ.get(name) or self.config_credentials.get(name)
            if value:
                resolved[name] = value
        return resolved

    # -- docker --------------------------------------------------------------

    def _docker_argv(
        self, name: str, sandbox: Path, credential_names: list[str], command: list[str]
    ) -> list[str]:
        spec = HARNESSES[self.harness]
        argv = [
            self.docker_bin,
            "run",
            "--rm",
            f"--name={name}",
            "--pull=never",
            "--init",
            f"--network={self.network}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            f"--memory={self.memory_mb}m",
            f"--memory-swap={self.memory_mb}m",
            f"--cpus={self.cpus}",
            f"--pids-limit={self.pids_limit}",
            "--tmpfs=/tmp:rw,exec,mode=1777",
            f"--volume={sandbox}:{WORKSPACE}",
            f"--workdir={WORKSPACE}",
        ]
        if hasattr(os, "getuid"):
            argv.append(f"--user={os.getuid()}:{os.getgid()}")
        for name, value in {"HOME": HOME, **spec.home_env}.items():
            argv.append(f"--env={name}={value}")
        # Name only: docker reads the value from its own environment.
        for name in credential_names:
            argv.append(f"--env={name}")
        return [*argv, self.image, *command]

    def _client_env(self, credentials: Mapping[str, str]) -> dict[str, str]:
        """The docker client's environment: what it needs to reach the engine, plus credentials."""
        environ = os.environ if self.env is None else self.env
        keep = ("PATH", "HOME", "TMPDIR", "XDG_RUNTIME_DIR")
        client = {k: v for k, v in environ.items() if k in keep or k.startswith("DOCKER_")}
        client.update(credentials)
        return client

    def _kill(self, name: str, client_env: Mapping[str, str]) -> bool:
        """Bounded cancellation attempts; only engine state confirms containment."""
        for attempt in range(3):
            try:
                killed = subprocess.run(
                    [self.docker_bin, "kill", name],
                    env=dict(client_env),
                    capture_output=True,
                    text=True,
                    timeout=_CONTROL_TIMEOUT_SECONDS,
                )
                state = subprocess.run(
                    [self.docker_bin, "inspect", "--format={{.State.Running}}", name],
                    env=dict(client_env),
                    capture_output=True,
                    text=True,
                    timeout=_CONTROL_TIMEOUT_SECONDS,
                )
                if state.returncode == 0 and state.stdout.strip() == "false":
                    return True
                if state.returncode != 0 and re.search(
                    rf"^(?:error: |error response from daemon: )?no such (?:object|container): {re.escape(name)}$",
                    state.stderr.strip(),
                    flags=re.IGNORECASE | re.MULTILINE,
                ):
                    return True  # --rm removed our uniquely named container.
                _log.warning(
                    "container %s termination unconfirmed (kill exit=%s, inspect exit=%s)",
                    name,
                    killed.returncode,
                    state.returncode,
                )
            except (OSError, subprocess.SubprocessError):
                _log.warning("container %s termination control failed", name)
            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))
        return False

    def _contain(self, name: str, client_env: Mapping[str, str], proc: subprocess.Popen) -> None:
        """Keep supervision and the sandbox until both engine and client stop.

        A failed control batch permanently halts this source's admissions. The
        paid run stays owned here, with bounded control calls, until the engine
        confirms termination; grading never observes a writable sandbox.
        """
        while True:
            if self._kill(name, client_env):
                # A still-running docker run client may be in container creation.
                # Do not abandon it on a premature 'no such object' response.
                try:
                    proc.wait(timeout=_POLL_SECONDS)
                except subprocess.TimeoutExpired:
                    self.admission_halted.set()
                    continue
                # Confirm again after client exit to cover late creation.
                if self._kill(name, client_env):
                    return
            self.admission_halted.set()
            _log.error("container %s remains supervised; suite admissions halted", name)
            time.sleep(1)

    def prepare_concurrent(self, stop_event: Event) -> None:
        self.stop_event = stop_event

    # -- TrajectorySource ----------------------------------------------------

    def run(self, task: Task) -> Trajectory:
        spec = HARNESSES[self.harness]
        credentials = self._credentials()
        secrets = tuple(sorted({v for v in credentials.values() if v}, key=len, reverse=True))
        launch_id = f"paddock-{_CONTAINER_NAME_UNSAFE.sub('-', task.id)}-{uuid.uuid4().hex[:12]}"
        parent = Path(tempfile.mkdtemp(prefix="paddock-", dir=self.work_dir))
        sandbox = parent / "workspace"
        self._sandboxes.append(parent)
        model = task.dispatch.get("model") or self.model

        def finish(status: str, *, error: str | None = None, **fields: Any) -> Trajectory:
            # metrics stay None: they are the ledger's MEG-02 shape, and the
            # suite judges model mismatch from them.
            return Trajectory(
                terminal_status=status,
                launch_request_id=launch_id,
                resolved_reasoning_level=self.reasoning_level
                if self.harness != "opencode"
                else None,
                error=redact(error, secrets)[:_MAX_ERROR_CHARS] if error else None,
                sandbox_root=sandbox,
                execution_provenance={
                    "runner": "standalone",
                    "harness": self.harness,
                    "image": self.image,
                    "model": str(model) if model else None,
                },
                result_redactor=lambda value: redact(value, secrets),
                **fields,
            )

        try:
            staging.run_stage_scripts(task, sandbox)
            staging.verify_task_inputs(sandbox)
            prompt = staging.guarded_prompt(task, sandbox).read_text(encoding="utf-8")
        except staging.InputFixturesMissing as exc:
            return finish("input_fixtures_missing", error=str(exc))
        command = spec.argv(prompt, str(model) if model else None, self.reasoning_level)
        argv = self._docker_argv(launch_id, sandbox, sorted(credentials), command)
        client_env = self._client_env(credentials)
        with self._active_lock:
            if self.stop_event.is_set() or self.admission_halted.is_set():
                return finish("canceled", error="standalone suite admissions halted")
            self._active.add(launch_id)
        proc = None
        status = None
        collector = None
        try:
            proc = subprocess.Popen(
                argv,
                env=client_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            collector = _OutputCollector(proc)
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if collector.exceeded.is_set():
                    status = "output_limit_exceeded"
                    break
                if self.stop_event.is_set() or self.admission_halted.is_set():
                    status = "canceled"
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                try:
                    proc.wait(timeout=min(_POLL_SECONDS, max(0.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
            # Every exit, including a failed client, must establish containment.
            self._contain(launch_id, client_env, proc)
            stdout, stderr = collector.finish()
            if collector.exceeded.is_set():
                status = "output_limit_exceeded"
            if status is not None:
                return finish(status, error=f"{spec.name} {status}; container stopped")
        except FileNotFoundError as exc:
            return finish("failed", error=f"docker client not found: {exc}")
        except BaseException:
            self.stop_event.set()
            if proc is not None:
                self._contain(launch_id, client_env, proc)
            if collector is not None:
                collector.finish()
            raise
        finally:
            with self._active_lock:
                self._active.discard(launch_id)
        calls = redact(spec.parse(stdout.splitlines()), secrets)
        if self.harness in {"pi", "opencode"}:
            for call in calls:
                call["arguments"] = _cap_arguments(call["arguments"])
        submitted = redact(staging.read_submitted_result(sandbox), secrets)
        if proc.returncode != 0:
            detail = (redact(stderr, secrets).strip() or redact(stdout, secrets).strip())[
                -_MAX_ERROR_CHARS:
            ]
            return finish(
                "failed",
                error=f"{spec.name} exited {proc.returncode}: {detail or '<no output>'}",
                harness_calls=calls,
                submitted_result=submitted,
            )
        return finish("succeeded", harness_calls=calls, submitted_result=submitted)

    def close(self) -> None:
        """Remove every sandbox this source created, unless ``keep_sandbox``."""
        with self._active_lock:
            if self._active:
                raise RuntimeError("cannot remove sandboxes while containers remain supervised")
            if not self.keep_sandbox:
                _remove_all(self._sandboxes)


def _remove_all(sandboxes: list[Path]) -> None:
    while sandboxes:
        shutil.rmtree(sandboxes.pop(), ignore_errors=True)


__all__ = ["HARNESSES", "Harness", "StandaloneConfigError", "StandaloneTrajectorySource", "redact"]
