"""Pytest bootstrap for the gym unit suite.

Puts the gym package on sys.path so the hermetic core (schema/toolcalls/grader/
verifier_runtime/outcomes) imports without an install and without the worker
pool or session ledger present.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB_PYTHON = Path(__file__).resolve().parents[1] / "lib" / "python"
if str(_LIB_PYTHON) not in sys.path:
    sys.path.insert(0, str(_LIB_PYTHON))

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from gym_test_support import NO_DOCKER_ENV, hide_docker, require_docker_backend  # noqa: E402

MODULE_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = MODULE_ROOT / "tasks"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SCRIPTS_DIR = MODULE_ROOT / "scripts"
# modules/adversarial-eval-gym -> modules -> repo root.
REPO_ROOT = MODULE_ROOT.parents[1]
WORKER_POOL_PYTHON = REPO_ROOT / "modules" / "worker-pool" / "lib" / "python"
if str(WORKER_POOL_PYTHON) not in sys.path:
    sys.path.insert(0, str(WORKER_POOL_PYTHON))
AGENT_OS_CONFIG_PYTHON = REPO_ROOT / "platform" / "agent-os-config" / "src"
if str(AGENT_OS_CONFIG_PYTHON) not in sys.path:
    sys.path.insert(0, str(AGENT_OS_CONFIG_PYTHON))
# The ledger reads (gym.ledger) resolve against this checkout's session ledger,
# not whichever tree an inherited PYTHONPATH names.
SESSION_LEDGER_PYTHON = REPO_ROOT / "platform" / "session-ledger" / "src"
if str(SESSION_LEDGER_PYTHON) not in sys.path:
    sys.path.insert(0, str(SESSION_LEDGER_PYTHON))
CANONICAL_CONTENT_PARENT = REPO_ROOT / "platform"
if str(CANONICAL_CONTENT_PARENT) not in sys.path:
    sys.path.insert(0, str(CANONICAL_CONTENT_PARENT))

# gym.cost prices through SEW's one price table (sew.cost_model).
SEW_PYTHON = REPO_ROOT / "tools" / "searchlight" / "lib" / "python"
if str(SEW_PYTHON) not in sys.path:
    sys.path.insert(0, str(SEW_PYTHON))

# Make the scaffolding generator (scripts/rca_from_postmortem.py) importable by
# the GYM-02 generator tests without an install.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.append(str(SCRIPTS_DIR))


def _importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# An exported paddock checkout has no Agent OS beside it (PDK-05), so the Agent
# OS fixtures below apply only in an agent-os tree.
AGENT_OS_HOST = _importable("agent_os_config") and _importable("gym.agent_os_services")


@pytest.fixture(autouse=True)
def no_docker_in_ci(monkeypatch):
    """`ci / fast` sets PADDOCK_NO_DOCKER=1: no container starts in Actions (SPEC R1)."""
    import os

    if os.environ.get(NO_DOCKER_ENV) == "1":
        hide_docker(monkeypatch)


@pytest.fixture(autouse=True)
def reset_agent_os_roots_cache():
    if not AGENT_OS_HOST:
        yield
        return
    from agent_os_config.roots import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def agent_os_host_services(tmp_path_factory):
    """Run the suite on the Agent OS host services, as `bin/hq-gym` does (PDK-02).

    A user's paddock config must not move tests onto standalone state; tests of
    the standalone services install their own. Outside Agent OS the suite runs
    on standalone services with an empty config and a temporary data directory.
    """
    from gym import host_services

    if AGENT_OS_HOST:
        from gym.agent_os_services import AgentOSServices

        services = AgentOSServices()
    else:
        services = host_services.StandaloneServices(
            tmp_path_factory.mktemp("paddock-data"), env={}, config={}
        )
    previous = host_services.install(services)
    yield
    host_services.install(previous)


@pytest.fixture
def protocol_backend(monkeypatch):
    """Only synthetic test bytes use this seam; it proves no OS containment."""
    import gym.hidden_tests as runner

    def command(work, args):
        # Synthetic protocol tests do not claim container enforcement.
        if "memory.swap.max" in args[-1]:
            return [
                *args[:-1],
                "from pathlib import Path; "
                + args[-1][args[-1].index("print('gym-ready'") :].replace(
                    "/work/", str(work) + "/"
                ),
            ]
        if sys.platform != "linux" and args[-1] in (runner._WRAPPER, runner._REGRESSION_WRAPPER):
            return [*args[:-1], args[-1].replace("protect_parent()\nrequest", "request")]
        return args

    monkeypatch.setattr(runner, "_command", command)


@pytest.fixture
def docker_backend(monkeypatch, request):
    """Intentionally use the VM-shared HQ tmp parent; children are isolated and removed.

    The canonical shared_work_root may create/chmod that parent. Ordinary pytest
    TMPDIR is not necessarily mounted in the Docker VM and cannot prove containment.
    """
    require_docker_backend()
    from gym import hidden_tests

    # HQ scratch is VM-shared; pytest tmp_path/default TMPDIR need not be.
    import tempfile
    from agent_os_config import roots

    test_hq = tempfile.TemporaryDirectory(
        prefix="gym-test-hq-", dir=hidden_tests.shared_work_root()
    )
    request.addfinalizer(test_hq.cleanup)
    monkeypatch.setattr(roots, "hq_root", lambda: test_hq.name)
    work_root = hidden_tests.shared_work_root()
    original = hidden_tests.run

    def run(*args, **kwargs):
        kwargs.setdefault("work_root", work_root)
        return original(*args, **kwargs)

    monkeypatch.setattr(hidden_tests, "run", run)
    # Some tests imported run before fixture setup.
    for module in list(sys.modules.values()):
        if (
            getattr(module, "__name__", "").startswith("test_")
            and getattr(module, "run", None) is original
        ):
            monkeypatch.setattr(module, "run", run)


@pytest.fixture(autouse=True)
def fake_host_headroom(monkeypatch):
    # Unit tests must not let live host pressure decide whether fake dispatches run.
    if AGENT_OS_HOST:
        monkeypatch.setattr("cwp_dispatch.resource_admission.sample_headroom_pause", lambda: None)


@pytest.fixture(scope="session")
def audit_keys(tmp_path_factory):
    import subprocess

    root = tmp_path_factory.mktemp("audit-keys")
    certificate, key = root / "recipient.pem", root / "private.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=gym-test-only",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    return certificate, key


@pytest.fixture(autouse=True)
def provision_test_audit_recipient(monkeypatch, audit_keys):
    # Synthetic recipient only. Real operator private keys never enter workers.
    monkeypatch.setenv("GYM_FIX_AUDIT_CERT", str(audit_keys[0]))
