"""Rejoin final GPU-hour lease attribution into saved cell results."""

from __future__ import annotations

import json
from pathlib import Path

from gym.endpoint_api import EndpointError
from gym.grader import economics_view


def finalize_costs(manager, lease):
    """Assign final idle/teardown overhead to the last collected cell once."""
    collected = [cell for cell in lease["cells"].values() if "cost_usd" in cell]
    if collected and "deleted_at" in lease and not lease.get("cost_finalized"):
        tail = max(collected, key=lambda cell: cell["accounted_at"])
        tail["cost_usd"] += max(0, manager.cost(lease) - sum(c["cost_usd"] for c in collected))
        lease["cost_finalized"] = True


def rejoin(manager, ident, path):
    """Rewrite a suite result or matrix state JSON after endpoint teardown.

    No remote calls; only result rows linked to this lease's admitted LRQs are
    updated. Re-running a matrix report then calculates fully loaded $/pass.
    """
    with manager.lock():
        lease = manager.load(ident)
        if "deleted_at" not in lease:
            raise EndpointError("cost rejoin requires confirmed provider deletion")
        finalize_costs(manager, lease)
        manager.save(lease)
        source = Path(path)
        data = json.loads(source.read_text())
        count = 0

        def walk(node):
            nonlocal count
            if isinstance(node, list):
                for value in node:
                    walk(value)
            elif isinstance(node, dict):
                for value in list(node.values()):
                    walk(value)
                launch = node.get("launchRequestId") or node.get("launch_request_id")
                cell = lease["cells"].get(launch)
                if (
                    cell
                    and "cost_usd" in cell
                    and ("metrics" in node or "verdict" in node or "outcome" in node)
                ):
                    economics = {
                        "cost_basis": "gpu_hours",
                        "endpoint_lease": ident,
                        "cost_usd": cell["cost_usd"],
                        "cost_status": "estimated",
                        "gpu_hours": cell["cost_usd"] / lease["rate_usd_per_gpu_hour"],
                    }
                    node.setdefault("metrics", {})
                    if node["metrics"] is None:
                        node["metrics"] = {}
                    node["metrics"].update(economics)
                    node.update(economics_view(economics))
                    count += 1

        walk(data)
        if not count:
            raise EndpointError("result file contains no collected cells from this lease")
        temp = source.with_name(source.name + ".endpoint-costs.tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n")
        temp.replace(source)
        manager.audit(lease, "cost_results", "ok")
        return {"updated_cells": count, "lease_accrued_usd": manager.cost(lease)}
