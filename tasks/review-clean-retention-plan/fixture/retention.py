from dataclasses import dataclass


@dataclass(frozen=True)
class SweepPlan:
    ready: tuple
    deferred: tuple


def plan_sweep(workers):
    ready = []
    deferred = []
    for worker in workers:
        if not worker["terminal"]:
            continue
        # Unsaved work is retained for a later operator decision.
        target = deferred if worker["dirty"] else ready
        target.append(worker)
    return SweepPlan(tuple(ready), tuple(deferred))


def sweep(workers, archive, remove):
    plan = plan_sweep(workers)
    archived = []
    for worker in plan.ready:
        archive(worker)
        archived.append(worker)
    removed = []
    for worker in archived:
        remove(worker)
        removed.append(worker["id"])
    return removed
