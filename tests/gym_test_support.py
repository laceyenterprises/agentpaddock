"""Explicit shared Docker readiness checks for gym tests."""

import pytest


def require_docker_backend():
    """Socket absence skips explicitly; a reachable but unusable engine fails."""
    import shutil
    import subprocess

    def unavailable(message):
        import os

        if os.environ.get("GYM_REQUIRE_REAL_ENGINE") == "1":
            pytest.fail(message)
        pytest.skip(message)

    docker = shutil.which("docker")
    if not docker:
        unavailable("Docker executable unavailable; host real-engine validation required")
    try:
        probe = subprocess.run([docker, "info"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        unavailable(f"Docker socket unavailable: {exc}; host validation required")
    if probe.returncode:
        unavailable("Docker socket unavailable: " + probe.stderr.decode(errors="replace")[-500:])


#: Set by `ci / fast`: Actions never runs Docker containment (SPEC R1).
NO_DOCKER_ENV = "PADDOCK_NO_DOCKER"


def hide_docker(monkeypatch):
    """Make the Docker engine invisible, as on a machine without Docker.

    A GitHub runner has Docker, so without this the Docker-backed tests and any
    hidden-test run would start real containers in CI. With it they skip, or
    take the no-engine path, exactly as on a Docker-less laptop.
    """
    import shutil
    from pathlib import Path

    which = shutil.which

    def without_docker(cmd, *args, **kwargs):
        return None if Path(str(cmd)).name == "docker" else which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", without_docker)
