"""PDK-02 guard: modules that ship in paddock import no Agent OS package.

Every ``lib/python/gym/*.py`` outside :data:`HOST_PLUGIN_MODULES`, and every
``scripts/*.py``, ships in the standalone package. None of them may import an
Agent OS package (at any depth, lazily or not), load one by name through
``importlib``, load an Agent OS source file by path, or import a host-plugin
module, which stays in agent-os when the rest leaves (PDK-07). What a shipped
module needs from its host comes from :mod:`gym.host_services`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
GYM_DIR = MODULE_ROOT / "lib" / "python" / "gym"
SCRIPTS_DIR = MODULE_ROOT / "scripts"
REPO_ROOT = MODULE_ROOT

#: The host plugin: fleet runner and Agent OS host services. These drive the
#: worker pool and the session ledger, and leave the gym with the plugin.
HOST_PLUGIN_MODULES = frozenset(
    {
        "acceptance_recovery",
        "agent_os_host",
        "agent_os_services",
        "dispatch",
        "ledger",
        "matrix",
        "matrix_report",
        "toc_eval",
    }
)

#: Agent OS packages, named even when the checkout is not an agent-os tree.
KNOWN_AGENT_OS_PACKAGES = frozenset(
    {
        "agent_os_config",
        "agent_os_core",
        "canonical_content",
        "cwp_dispatch",
        "dispatch_core",
        "session_ledger",
    }
)

_IMPORT_CALLS = frozenset({"import_module", "__import__", "spec_from_file_location"})
_NOT_PACKAGES = frozenset({"conftest", "test", "tests"})


def _package_name(path: Path) -> str | None:
    if path.name.endswith(".egg-info") or path.name.startswith((".", "_")):
        return None
    if path.is_dir() and (path / "__init__.py").is_file():
        name = path.name
    elif path.is_file() and path.suffix == ".py":
        name = path.stem
    else:
        return None
    return None if name in _NOT_PACKAGES else name


def agent_os_sources(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Agent OS top-level packages in this checkout, by name, with their source dir."""
    found: dict[str, str] = {}
    gym_lib = (MODULE_ROOT / "lib" / "python").resolve()
    roots = [
        *sorted(repo_root.glob("platform/*/src")),
        *sorted(repo_root.glob("platform")),
        *sorted(repo_root.glob("modules/*/lib/python")),
    ]
    for root in roots:
        if root.resolve() == gym_lib or not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            name = _package_name(child)
            if name is not None:
                found.setdefault(name, root.relative_to(repo_root).as_posix())
    return found


def forbidden_packages() -> frozenset[str]:
    return KNOWN_AGENT_OS_PACKAGES | frozenset(agent_os_sources())


def forbidden_paths() -> frozenset[str]:
    """Agent OS source dirs a shipped module must not load files from."""
    paths = {
        source
        for source in agent_os_sources().values()
        # `platform` alone holds canonical_content; name its package dir instead.
        if source != "platform"
    }
    paths |= {
        f"platform/{name}"
        for name, source in agent_os_sources().items()
        if source == "platform"
    }
    return frozenset(
        paths | {"platform/agent-os-config/src", "platform/session-ledger/src"}
    )


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ids.add(id(node.value))
    return ids


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def violations(
    source: str,
    *,
    packages: frozenset[str],
    paths: frozenset[str],
    host_modules: frozenset[str] = HOST_PLUGIN_MODULES,
    filename: str = "<module>",
) -> list[str]:
    """Each forbidden import in ``source``, as ``file:line: reason``."""
    tree = ast.parse(source, filename=filename)
    docstrings = _docstring_ids(tree)
    host_dotted = {f"gym.{name}" for name in host_modules}
    found: list[str] = []

    def flag(node: ast.AST, reason: str) -> None:
        found.append(f"{filename}:{getattr(node, 'lineno', 0)}: {reason}")

    def check_module(node: ast.AST, dotted: str) -> None:
        top = dotted.split(".")[0]
        if top in packages:
            flag(node, f"imports Agent OS package {dotted!r}")
        elif any(
            dotted == host or dotted.startswith(host + ".") for host in host_dotted
        ):
            flag(node, f"imports host-plugin module {dotted!r}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_module(node, alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            check_module(node, node.module)
            if node.module == "gym":
                for alias in node.names:
                    if alias.name in host_modules:
                        flag(node, f"imports host-plugin module 'gym.{alias.name}'")
        elif isinstance(node, ast.Call) and _call_name(node) in _IMPORT_CALLS:
            for arg in node.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    check_module(node, arg.value)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            for path in paths:
                if path in node.value:
                    flag(node, f"names Agent OS source path {path!r}")
    return found


def shipped_files() -> list[Path]:
    modules = [
        path
        for path in sorted(GYM_DIR.glob("*.py"))
        if path.stem not in HOST_PLUGIN_MODULES
    ]
    return modules + sorted(SCRIPTS_DIR.glob("*.py"))


def test_host_plugins_are_not_shipped():
    on_disk = {path.stem for path in GYM_DIR.glob("*.py")}
    assert not (HOST_PLUGIN_MODULES & on_disk), sorted(HOST_PLUGIN_MODULES & on_disk)


def test_known_packages_are_discovered_in_an_agent_os_checkout():
    if not (REPO_ROOT / "platform").is_dir():
        pytest.skip("not an agent-os checkout")
    discovered = set(agent_os_sources())
    assert {
        "agent_os_config",
        "cwp_dispatch",
        "session_ledger",
        "canonical_content",
    } <= discovered
    assert "gym" not in discovered
    assert "sew" not in discovered  # Searchlight is its own repo, not Agent OS.


@pytest.mark.parametrize("path", shipped_files(), ids=lambda path: path.name)
def test_shipped_module_imports_no_agent_os_package(path):
    found = violations(
        path.read_text(encoding="utf-8"),
        packages=forbidden_packages(),
        paths=forbidden_paths(),
        filename=path.relative_to(MODULE_ROOT).as_posix(),
    )
    assert not found, "\n".join(found)


@pytest.mark.parametrize(
    "source",
    [
        "import session_ledger\n",
        "import cwp_dispatch.reasoning_level as r\n",
        "def f():\n    from agent_os_config.roots import hq_root\n",
        "from canonical_content import hash_file\n",
        "from gym import dispatch\n",
        "from gym.ledger import LedgerReader\n",
        "import gym.matrix\n",
        "import importlib\nimportlib.import_module('agent_os_config.roots')\n",
        "p = 'platform/agent-os-config/src/agent_os_config/backoff.py'\n",
    ],
)
def test_guard_flags_each_violation(source):
    assert violations(
        source,
        packages=KNOWN_AGENT_OS_PACKAGES,
        paths=frozenset({"platform/agent-os-config/src"}),
    )


def test_guard_allows_stdlib_gym_and_prose():
    source = (
        '"""Mentions agent_os_config and platform/agent-os-config/src in prose."""\n'
        "import json\n"
        "from gym import host_services, locks\n"
        "from gym.host_services import current\n"
        "from . import outcomes\n"
        "HOSTS = ('gym.agent_os_host:plugin',)\n"
    )
    assert not violations(
        source,
        packages=KNOWN_AGENT_OS_PACKAGES,
        paths=frozenset({"platform/agent-os-config/src"}),
    )


_BLOCKED_IMPORT_PROBE = """
import importlib, json, sys
from importlib.abc import MetaPathFinder

blocked = set(json.loads(sys.argv[2]))

class Block(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ImportError(f"blocked Agent OS package {name}")
        return None

sys.meta_path.insert(0, Block())
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
from gym import cli, host_services, taint
services = host_services.current()
print(json.dumps({
    "services": type(services).__name__,
    "baseline": str(cli.default_baseline_path()),
    "taint": taint.load_live_taint_mode(),
}))
"""


def test_shipped_modules_import_and_run_standalone_with_agent_os_blocked(tmp_path):
    import json
    import os
    import subprocess
    import sys

    modules = [f"gym.{path.stem}" for path in shipped_files() if path.parent == GYM_DIR]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PADDOCK_DATA_DIR": str(tmp_path / "data"),
        "PADDOCK_CONFIG": str(tmp_path / "absent.toml"),
        "PYTHONPATH": str(GYM_DIR.parent),
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _BLOCKED_IMPORT_PROBE,
            json.dumps(modules),
            json.dumps(sorted(forbidden_packages())),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "services": "StandaloneServices",
        "baseline": str(tmp_path / "data" / "gym-baseline.json"),
        "taint": "unavailable",
    }
