"""GYMENDPOINT-01: persisted leases, conservative GPU clock and fail-closed admission.

A provider expiry is armed before creation, independently of our reaper. The
local guard reserves two minutes for reaping/deletion; provider outages still
require operator intervention (no local process can guarantee a remote bill).
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import getpass
import json
import math
import logging
from pathlib import Path
import re
import sys
import time
import uuid

from gym import host_services
from gym.endpoint_api import API, EndpointError
from urllib.parse import urlparse

# Published on-demand GPU-hour rates effective 2026-09-01, verified 2026-10-05.
# Unknown hardware fails closed. Region-restricted placements are not requested.
# https://fireworks.ai/pricing
RATES = {
    "NVIDIA_H100_80GB": 8.0,
    "NVIDIA_H200_141GB": 8.0,
    "NVIDIA_B200_180GB": 13.0,
    "NVIDIA_B300_288GB": 15.0,
    "NVIDIA_GB300_288GB": 20.0,
}
RESERVE_SECONDS = 120
DELETE_PENDING_SECONDS = 600
PREFIX = "evalbench-gym-"
_log = logging.getLogger(__name__)


def reserved_route(model):
    route = str(model or "").rsplit("/", 1)[-1]
    return route if route.startswith(PREFIX) else None


def root_path(root=None):
    if root is not None:
        return Path(root) / "gym" / "endpoints"
    # The launching tree's root, the one the fleet dispatch shares (Agent OS:
    # $HQ_ROOT first).
    return host_services.current().data_dir(launched_tree=True) / "endpoints"


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise EndpointError("duration and cap must be finite and positive")
    return value


def duration(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h|d)?", value)
    if not match:
        raise argparse.ArgumentTypeError("duration must be e.g. 30m or 2h")
    return positive(match[1]) * {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]


def owned(name, account, annotations=None):
    valid = re.fullmatch(r"accounts/[\w-]+/deployments/[\w-]+", name) is not None
    in_account = name.startswith(f"accounts/{account}/deployments/")
    gym = (
        name.rsplit("/", 1)[-1].startswith("evalbench-")
        or (annotations or {}).get("agent-os-gym") == "true"
    )
    return valid and in_account and gym


def select_shape(shapes, requested="auto-min"):
    choices = []
    for version in shapes:
        snapshot = version.get("snapshot", {})
        rate = RATES.get(snapshot.get("acceleratorType"))
        if version.get("validated") is True and snapshot.get("acceleratorCount") == 1 and rate:
            name = version.get("name", "")
            if requested == "auto-min" or requested in {name, snapshot.get("name")}:
                choices.append((rate, name))
    if not choices:
        raise EndpointError("no validated single-GPU shape with a published rate")
    return min(choices)


class Manager:
    def __init__(self, root=None, api_factory=None, now=time.time, sleep=time.sleep):
        self.root = root_path(root)
        self.api_factory = api_factory or self._api
        self.now, self.sleep = now, sleep

    def _api(self, settings):
        return API(host_services.current().secrets(), settings)

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def save(self, lease):
        path = self.root / (lease["id"] + ".json")
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(lease, indent=2) + "\n")
        temp.replace(path)

    def load(self, ident):
        if not re.fullmatch(r"evalbench-[\w-]+", ident):
            raise EndpointError("invalid lease id")
        lease = json.loads((self.root / (ident + ".json")).read_text())
        if (
            not isinstance(lease, dict)
            or lease.get("id") != ident
            or not {
                "deployment",
                "settings",
                "route",
                "state",
                "cells",
                "created_at",
                "expires_at",
                "cap_usd",
                "rate_usd_per_gpu_hour",
            }.issubset(lease)
            or not isinstance(lease["settings"], dict)
            or not isinstance(lease["cells"], dict)
            or any(not isinstance(cell, dict) for cell in lease["cells"].values())
        ):
            raise EndpointError("invalid lease record")
        if not owned(lease["deployment"], lease["settings"]["account"]):
            raise EndpointError("lease deployment is not gym-owned")
        return lease

    def leases(self):
        leases = []
        for path in self.root.glob("evalbench-*.json"):
            try:
                leases.append(self.load(path.stem))
            except (OSError, ValueError, KeyError, TypeError, EndpointError) as exc:
                # Keep corrupt records for operator recovery; do not let one
                # unreadable lease block accounting for every healthy lease.
                _log.warning("cannot load endpoint lease %s (%s)", path.name, type(exc).__name__)
                try:
                    self.audit({"id": path.stem}, "lease_load", "failed")
                except OSError:
                    _log.warning("cannot audit malformed endpoint lease %s", path.name)
        return leases

    def audit(self, lease, action, outcome):
        # Only controlled identifiers/codes; never API bodies or exception text.
        with (self.root / "audit.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {"at": self.now(), "lease": lease["id"], "action": action, "outcome": outcome}
                )
                + "\n"
            )

    def cost(self, lease, at=None):
        start = lease.get("ready_at")
        end = lease.get("deleted_at", self.now() if at is None else at)
        return (
            max(0, end - start) / 3600 * lease["rate_usd_per_gpu_hour"]
            if start is not None
            else 0.0
        )

    def deadline(self, lease):
        # Conservative backstop counts creation time for provider expiry,
        # whereas reported accrued cost counts from first observed READY.
        return (
            min(
                lease["expires_at"],
                lease["created_at"] + lease["cap_usd"] / lease["rate_usd_per_gpu_hour"] * 3600,
            )
            - RESERVE_SECONDS
        )

    def _down(self, lease, api):
        lease["state"] = "closing"
        self.save(lease)
        failed = []
        if "deleted_at" not in lease:
            try:
                state = api.get(lease["deployment"]).get("state")
                if state not in {"DELETED", "STATE_DELETED"}:
                    if state not in {"DELETING", "STATE_DELETING"}:
                        api.delete(lease["deployment"])
                        state = api.get(lease["deployment"]).get("state")
                    if state not in {"DELETED", "STATE_DELETED"}:
                        lease.setdefault("delete_pending_since", self.now())
                        if self.now() - lease["delete_pending_since"] < DELETE_PENDING_SECONDS:
                            lease["cleanup_failures"] = ["deployment_delete_pending"]
                            self.save(lease)
                            return lease
                        raise EndpointError("provider deletion pending beyond 600s")
                lease["deleted_at"] = self.now()
                self.audit(lease, "deployment_delete", "ok")
            except Exception:
                failed.append("deployment_delete")
                self.audit(lease, "deployment_delete", "failed")
        if not lease.get("route_deleted"):
            try:
                # A deterministic ID is persisted BEFORE route registration.
                # Retry even when a create response was lost.
                api.route_down(lease)
                lease["route_deleted"] = True
                self.audit(lease, "route_delete", "ok")
            except Exception:
                failed.append("route_delete")
                self.audit(lease, "route_delete", "failed")
        lease["cleanup_failures"] = failed
        if not failed:
            lease["state"] = "closed"
            lease["closed_at"] = self.now()
            from gym.endpoint_results import finalize_costs

            finalize_costs(self, lease)
        self.save(lease)
        if failed:
            raise EndpointError("endpoint cleanup incomplete: " + ", ".join(failed))
        return lease

    def down(self, ident):
        with self.lock():
            lease = self.load(ident)
            if lease["state"] == "closed":
                return lease
            return self._down(lease, self.api_factory(lease["settings"]))

    def up(
        self,
        settings,
        model,
        cap,
        ttl,
        shape="auto-min",
        creation_timeout=600,
        capacity_timeout=180,
        route_timeout=600,
        cell_timeout=3600,
    ):
        settings = {
            key: settings[key] for key in ("account", "fireworks_ref", "litellm_ref", "litellm_url")
        }
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", settings["account"]):
            raise EndpointError("invalid Fireworks account")
        if not all(settings[key].startswith("op://") for key in ("fireworks_ref", "litellm_ref")):
            raise EndpointError("credentials must be secrets-bus references")
        url = urlparse(settings["litellm_url"])
        if url.username or url.password or url.query or url.fragment or not url.hostname:
            raise EndpointError("LiteLLM URL must not contain credentials or query parameters")
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}
        ):
            raise EndpointError("remote LiteLLM requires HTTPS")
        cap, ttl = positive(cap), positive(ttl)
        creation_timeout, capacity_timeout, route_timeout = map(
            positive, (creation_timeout, capacity_timeout, route_timeout)
        )
        if creation_timeout > 600 or route_timeout > 600:
            raise EndpointError("creation and route timeouts must not exceed 600s")
        api = self.api_factory(settings)
        rate, selected = select_shape(api.shapes(model), shape)
        now = self.now()
        ident = PREFIX + uuid.uuid4().hex[:16]
        deadline = now + min(ttl, cap / rate * 3600)
        if deadline - now <= RESERVE_SECONDS + creation_timeout + route_timeout + positive(
            cell_timeout
        ):
            raise EndpointError("cap/TTL cannot cover warm probe, one cell and teardown reserve")
        lease = {
            "id": ident,
            "owner": getpass.getuser(),
            "created_at": now,
            "expires_at": now + ttl,
            "cap_usd": cap,
            "rate_usd_per_gpu_hour": rate,
            "price_source": "https://fireworks.ai/pricing",
            "price_as_of": "2026-10-05",
            "deployment": f"accounts/{settings['account']}/deployments/{ident}",
            "route": ident,
            "route_id": str(uuid.uuid4()),
            "settings": settings,
            "inference_model": model + "#accounts/" + settings["account"] + "/deployments/" + ident,
            "shape": selected,
            "provider_expire_time": datetime.fromtimestamp(deadline, UTC).isoformat(),
            "route_timeout_seconds": route_timeout,
            "state": "creating",
            "cells": {},
        }
        with self.lock():
            registry = self.root / "accounts.json"
            accounts = json.loads(registry.read_text()) if registry.exists() else {}
            accounts[settings["account"]] = settings
            temp = registry.with_suffix(".tmp")
            temp.write_text(json.dumps(accounts, indent=2) + "\n")
            temp.replace(registry)
            self.save(lease)  # Crash recovery is armed before any paid call.
        self.audit(lease, "create", "started")
        try:
            api.create(lease, model, selected)
            capacity_since = None
            while True:
                state = api.get(lease["deployment"])
                if self.now() >= min(now + creation_timeout, self.deadline(lease)):
                    raise EndpointError("creation deadline exceeded")
                if state.get("status", {}).get("code") in {"RESOURCE_EXHAUSTED", 8, "8"}:
                    if capacity_since is None:
                        capacity_since = self.now()
                    if self.now() - capacity_since >= capacity_timeout:
                        raise EndpointError("capacity exhaustion deadline exceeded")
                else:
                    capacity_since = None
                if state.get("state") == "READY":
                    lease["ready_at"] = self.now()
                    lease["state"] = "warming"
                    with self.lock():
                        if self.load(ident)["state"] in {"closing", "closed"}:
                            raise EndpointError("endpoint reaped during setup")
                        self.save(lease)
                    break
                if state.get("state") in {"FAILED", "DELETED"}:
                    raise EndpointError("deployment failed before READY")
                self.sleep(5)
            self._admit(lease, route_timeout)
            api.warm(lease)
            self._admit(lease, 30)
            with self.lock():
                current = self.load(ident)
                if current["state"] == "closing" or current["state"] == "closed":
                    raise EndpointError("endpoint reaped during setup")
                self._admit(lease, 30)
                api.route_up(lease)
                lease["state"] = "ready"
                self.save(lease)
            self.audit(lease, "up", "ok")
            return lease
        except Exception:
            self.audit(lease, "up", "failed")
            with self.lock():
                lease = self.load(ident)
                self._down(lease, api)
            raise EndpointError("endpoint setup failed; deployment and route cleaned up") from None

    def _admit(self, lease, seconds):
        if (
            self.now() + seconds >= self.deadline(lease)
            or self.cost(lease, self.now() + seconds + RESERVE_SECONDS) >= lease["cap_usd"]
        ):
            raise EndpointError("endpoint cap or TTL would be exceeded")

    def guard(self, model, seconds, cell_id=None):
        # Reserved route names cannot silently fall back to unleased dispatch.
        route = reserved_route(model)
        if route is None:
            return None
        with self.lock():
            leases = [item for item in self.leases() if item["route"] == route]
            if not leases:
                raise EndpointError("gym route has no lease")
            lease = leases[0]
            try:
                if lease["state"] != "ready":
                    raise EndpointError("endpoint is not ready")
                self._admit(lease, positive(seconds))
            except EndpointError:
                # Refusing a new cell must not revoke existing admissions.
                # Deadline teardown belongs to the reaper/provider backstop.
                raise EndpointError("endpoint dispatch refused: cap, TTL or closed lease") from None
            if cell_id:
                lease["cells"].setdefault(cell_id, {"admitted_at": self.now()})
                self.save(lease)
            return lease

    def bind_cell(self, model, old_id, launch_id):
        route = reserved_route(model)
        if route is None:
            return
        with self.lock():
            for lease in self.leases():
                if lease["route"] == route and old_id in lease["cells"]:
                    lease["cells"][launch_id] = lease["cells"].pop(old_id)
                    self.save(lease)

    def cell_cost(self, model, cell_id):
        route = reserved_route(model)
        if route is None or not self.root.exists():
            return None
        with self.lock():
            for lease in self.leases():
                if lease["route"] == route and cell_id in lease["cells"]:
                    # Each cell takes the unallocated clock slice exactly once.
                    # Includes warm/idle time; concurrent cells never double bill.
                    cell = lease["cells"][cell_id]
                    if "cost_usd" not in cell:
                        total = self.cost(lease)
                        allocated = sum(c.get("cost_usd", 0) for c in lease["cells"].values())
                        cell["cost_usd"] = max(0, total - allocated)
                        cell["accounted_at"] = self.now()
                        self.save(lease)
                    return {
                        "cost_basis": "gpu_hours",
                        "cost_usd": cell["cost_usd"],
                        "cost_status": "estimated",
                        "endpoint_lease": lease["id"],
                        "gpu_hours": cell["cost_usd"] / lease["rate_usd_per_gpu_hour"],
                    }
        return None

    def status(self):
        with self.lock():
            rows = []
            for lease in self.leases():
                state = (
                    self.api_factory(lease["settings"]).get(lease["deployment"]).get("state")
                    if lease["state"] != "closed"
                    else "DELETED"
                )
                rows.append(
                    {
                        **lease,
                        "provider_state": state,
                        "accrued_usd": self.cost(lease),
                        "cap_tripped": self.now() >= self.deadline(lease)
                        or self.cost(lease) >= lease["cap_usd"],
                        "unallocated_usd": max(
                            0,
                            self.cost(lease)
                            - sum(c.get("cost_usd", 0) for c in lease["cells"].values()),
                        ),
                    }
                )
            return rows

    def reap(self):
        with self.lock():
            leases = self.leases()
            registry = self.root / "accounts.json"
            settings = json.loads(registry.read_text()) if registry.exists() else {}
            settings.update({item["settings"]["account"]: item["settings"] for item in leases})
            failures = 0
            for account, config in settings.items():
                api = self.api_factory(config)
                try:
                    deployments = api.deployments()
                except Exception:
                    self.audit({"id": "account-" + account}, "provider_list", "failed")
                    failures += 1
                    deployments = []
                for lease in leases:
                    if lease["settings"]["account"] != account or lease["state"] == "closed":
                        continue
                    if (
                        lease["state"] == "closing"
                        or self.now() >= self.deadline(lease)
                        or self.cost(lease) >= lease["cap_usd"]
                    ):
                        try:
                            self._down(lease, api)
                        except EndpointError:
                            failures += 1
                names = {item["deployment"] for item in leases if item["state"] != "closed"}
                for deployment in deployments:
                    name = deployment.get("name", "")
                    if (
                        owned(name, account, deployment.get("annotations"))
                        and name not in names
                        and deployment.get("state")
                        not in {"DELETED", "STATE_DELETED", "DELETING", "STATE_DELETING"}
                    ):
                        orphan = {"id": name.rsplit("/", 1)[-1]}
                        try:
                            api.delete(name)
                            self.audit(orphan, "orphan_delete", "ok")
                        except Exception:
                            self.audit(orphan, "orphan_delete", "failed")
                            failures += 1
            if failures:
                raise EndpointError(
                    f"endpoint reaper: {failures} cleanup/list failures; operator intervention required"
                )
            return {"accounts": len(settings), "failures": 0}


def main(argv=None):
    from gym.endpoint_cli import main as cli_main
    from gym.library import GymLibraryChanged, preload_library

    # `up` waits minutes for capacity, then lazily imports dispatch (GYMSKEW-02).
    try:
        preload_library()
    except GymLibraryChanged as exc:
        print(f"[hq gym] {exc}", file=sys.stderr)
        return 1
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
