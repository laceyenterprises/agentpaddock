"""Adversarial Eval Gym (GYM) harness.

Agent-behavior regression protection built on agent-os primitives: dispatch a
persona through the real worker pool, read the trajectory from the session
ledger, and grade it with an out-of-band verifier that never enters the agent
sandbox.

The core (``schema``, ``toolcalls``, ``verifier_runtime``, ``outcomes``,
``grader``) is pure stdlib. The real dispatch/ledger seam (``dispatch``,
``ledger``) imports ``cwp_dispatch`` / ``session_ledger`` lazily.
"""

from __future__ import annotations

from pathlib import Path as _Path

from gym.library import preload_library
from gym.library import start_identity as _start_identity

# GYMSKEW-02: hash the library before any other gym module loads, so the
# preload can prove every module this process imported came from one version.
_START_IDENTITY = _start_identity(_Path(__file__).resolve().parent)

__all__ = [
    "outcomes",
    "schema",
    "toolcalls",
    "verifier_runtime",
    "grader",
    "suite",
    "baseline",
    "alerting",
    "locks",
    "library",
    "preload_library",
]
