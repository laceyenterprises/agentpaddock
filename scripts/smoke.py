"""Bounded synthetic Paddock entry points; no live runtime/credential fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib/python"))


class InvocationTimeout(BaseException):
    """Abort the invocation through step and owner-level exception handlers."""


def exercise(step, scratch):
    from gym import host_services, taint
    from gym.grader import Trajectory
    from gym.suite import ALL_SUITE, discover_tasks, run_suite

    fixtures = ROOT / "tests/fixtures/mini-suite"
    tasks = discover_tasks(fixtures, suite=ALL_SUITE)

    class Source:
        def run(self, task):
            if step == "suite.timeout":
                return Trajectory(terminal_status="timeout", error="synthetic deadline")
            return Trajectory(
                terminal_status="succeeded",
                submitted_result={"answer": "ok" if task.id == "mini-pass" else "bad"},
            )

    if step in {"suite.grade", "suite.timeout"}:
        previous = host_services.install(
            host_services.StandaloneServices(env={}, config={}, root=scratch)
        )
        try:
            result = run_suite(
                tasks, Source(), suite=ALL_SUITE, taint_mode=taint.ENFORCE
            )
            verdicts = result.verdicts()
            if step == "suite.grade":
                assert verdicts == {"mini-pass": "passed", "mini-fail": "failed"}
            else:
                assert set(verdicts.values()) == {"agent_error"}
                from gym.standalone import StandaloneTrajectorySource

                runner = StandaloneTrajectorySource(
                    harness="codex", image="synthetic", work_dir=scratch, env={}
                )
                runner.stop_event.set()
                try:
                    assert runner.run(tasks[0]).terminal_status == "canceled"
                finally:
                    runner.close()
            return {
                "verdicts": verdicts,
                "dispatch": "synthetic trajectory",
                "standaloneHalt": step == "suite.timeout",
            }
        finally:
            host_services.install(previous)
    if step == "lease.lifecycle":
        from gym.endpoints import Manager
        from gym.endpoint_api import EndpointError
        from unittest.mock import Mock

        api = Mock()
        api.shapes.return_value = [
            {
                "name": "synthetic",
                "validated": True,
                "snapshot": {
                    "acceleratorType": "NVIDIA_H100_80GB",
                    "acceleratorCount": 1,
                },
            }
        ]
        api.get.return_value = {"state": "READY"}

        def delete(name):
            api.get.return_value = {"state": "DELETED"}

        api.delete.side_effect = delete
        clock = [1000.0]
        manager = Manager(
            scratch / "leases",
            api_factory=lambda settings: api,
            now=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
        settings = {
            "account": "fixture",
            "fireworks_ref": "op://synthetic/unused/key",  # publication-scan: allow(op-reference): unused synthetic API fixture
            "litellm_ref": "op://synthetic/unused/key",  # publication-scan: allow(op-reference): unused synthetic API fixture
            "litellm_url": "https://provider.invalid",
        }  # publication-scan: allow(op-reference): unused synthetic API fixture
        lease = manager.up(settings, "fixture-model", 20, 7200, route_timeout=60)
        assert manager.load(lease["id"])["state"] == "ready"
        manager.guard(lease["route"], 1, "fixture-cell")
        assert "fixture-cell" in manager.load(lease["id"])["cells"]
        assert manager.down(lease["id"])["state"] == "closed"
        manager.down(lease["id"])
        assert api.delete.call_count == 1
        # Setup deadline also traverses real cleanup with a synthetic clock.
        api.get.return_value = {"state": "CREATING"}
        try:
            manager.up(
                settings,
                "fixture-model",
                20,
                7200,
                route_timeout=60,
                creation_timeout=1,
            )
        except EndpointError:
            pass
        else:
            raise AssertionError("deadline accepted")
        assert {x["state"] for x in manager.leases()} == {"closed"}
        return {
            "closed": len(manager.leases()),
            "provider": "synthetic",
            "replayDeletes": 1,
        }
    raise ValueError("missing step: " + step)


def verifier_process(argv, *, cwd, env, capture_output, text, timeout):
    """Bound only the real synthetic verifier process; no Docker/host commands."""
    assert argv[1:] == ["-S", "verifier.py"]
    assert Path(cwd).name.startswith("gym-verify-")
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=min(timeout, 15))
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def run(mode="sandbox", commit="synthetic", fail_step=None):
    doc = json.loads((ROOT / "post-merge-verify.json").read_text())
    started = time.monotonic()
    rows = []

    def deny(*args, **kwargs):
        raise RuntimeError("sandbox attempted network")

    with (
        tempfile.TemporaryDirectory(prefix="paddock-smv-") as tmp,
        patch.object(socket.socket, "connect", deny),
        patch.object(subprocess, "run", verifier_process),
    ):
        for step in doc["steps"]:
            tick = time.monotonic()
            verdict = "pass" if mode == "sandbox" else "unavailable"
            try:
                if mode != "sandbox":
                    evidence = {"reason": doc["modes"]["host"]["exclusions"][0]}
                elif fail_step == step["id"]:
                    raise RuntimeError("synthetic required-step failure")
                else:
                    evidence = exercise(step["id"], Path(tmp))
            except Exception as exc:
                verdict, evidence = "fail", {"error": f"{type(exc).__name__}: {exc}"}
            rows.append(
                {
                    **{k: step[k] for k in ("id", "promise", "edges")},
                    "verdict": verdict,
                    "observed": evidence,
                    "expected": "README entry-point assertions",
                    "evidence": evidence,
                    "durationSeconds": time.monotonic() - tick,
                    "exclusions": doc["modes"].get(mode, doc["modes"]["host"])[
                        "exclusions"
                    ],
                }
            )
    return {
        "module": doc["module"],
        "mode": mode,
        "commit": commit,
        "verdict": "pass" if all(r["verdict"] == "pass" for r in rows) else "fail",
        "durationSeconds": time.monotonic() - started,
        "steps": rows,
    }


def main():
    mode = os.environ.get("SMV_MODE", "sandbox")
    commit = (
        os.environ.get("SMV_COMMIT")
        or subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout.strip()
    )
    # Allowlist before importing owner code. The harness itself never inherits
    # DSNs, provider/model keys, notification URLs or an operator HOME.
    clean = {
        k: os.environ[k]
        for k in ("PATH", "LANG", "LC_ALL", "PYTHONUSERBASE")
        if k in os.environ
    }

    def overdue(*_):
        raise InvocationTimeout("180-second invocation budget exceeded")

    previous = signal.signal(signal.SIGALRM, overdue)
    signal.setitimer(signal.ITIMER_REAL, 175)
    try:
        with (
            tempfile.TemporaryDirectory(prefix="paddock-home-") as home,
            patch.dict(
                os.environ,
                {**clean, "HOME": home, "AGENT_OS_SESSION_LEDGER_VDB": "false"},
                clear=True,
            ),
        ):
            report = run(mode=mode, commit=commit)
        for row in report["steps"]:
            print(
                f"{row['verdict'].upper()} {report['module']}/{row['id']} promise={row['promise']} edges={','.join(row['edges'])}"
            )
        print(json.dumps(report))
        return 0 if report["verdict"] == "pass" else 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
