#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/surface.py" <<'RFD_INPUT'
def verdicts(self) -> dict[str, str]:
        """(task, model) key -> resolved outcome, the shape the gate diffs on.

        Unmodelled runs keep the bare task id, so this is wire-compatible with
        every baseline written before models were selectable.
        """
        return {
            baseline.verdict_key(g.cell.task_id, self.models.get(g.cell.task_id)): g.cell.outcome
            for g in self.graded
        }


# Model resolution in run_suite:
# models[t.id] = t.dispatch.get("model") or model

# baseline key helper excerpt:
VERDICT_KEY_SEPARATOR = "@"
def verdict_key(task_id: str, model: str | None = None) -> str:
    """Baseline key for one (task, model) cell.

    A run with no model override keeps the bare task id, so every baseline
    written before models were selectable still reads and compares unchanged.
    """
    if not model:
        return task_id
    return f"{task_id}{VERDICT_KEY_SEPARATOR}{model}"
RFD_INPUT
cat >"$sandbox_root/guide.md" <<'RFD_INPUT'
## Baseline keys


        Unmodelled runs keep the bare task id, so this is wire-compatible with
        every baseline written before models were selectable.
RFD_INPUT
