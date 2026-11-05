"""Short race names (PDW-06): plain words a visitor can read on a card.

A race's short name is the one the approved prototype
(``design/web/Races.dc.html``) gives it: plain words, with acronyms spelled
out ("Pull request not found", "Foreign-key drain"). A local task this table
does not list takes its track card's ``name``, and a task with neither gets
its id in words with the acronyms in :data:`ACRONYMS` spelled out.

These are copy, not numbers: every number on the pages still comes from the
data layer.
"""

from __future__ import annotations

#: Task id -> short name, from the prototype's renames.
SHORT_NAMES: dict[str, str] = {
    "coding-cache-context": "Cache context",
    "coding-commit-ack": "Commit ack",
    "coding-generation-fence": "Generation fence",
    "coding-jsonb-like-predicate": "Postgres-only predicate",
    "coding-missing-commit": "Missing commit",
    "coding-nondict-migrate-audit": "Non-dict migration audit",
    "coding-pipefail-silent-exit": "Pipefail silent exit",
    "coding-pr-not-found-terminal": "Pull request not found",
    "coding-registry-tail": "Registry tail",
    "coding-schema-behind-wording": "Schema behind wording",
    "coding-toc-commands": "Commit hunt: commands",
    "coding-toc-large-output": "Commit hunt: large output",
    "coding-unknown-change-kind-sentinel": "Unknown change kind sentinel",
    "docs-cache-config": "Cache config",
    "docs-config-loader-keys": "Config loader keys",
    "docs-gym-baseline-keys": "Gym baseline keys",
    "docs-gym-run-flags": "Gym run flags",
    "docs-retry-behavior-diff": "Retry behavior diff",
    "docs-snapshot-new-command": "Snapshot new command",
    "example-read-before-write": "Read before write",
    "fix-corrupt-refresh-checkpoint": "Corrupt refresh checkpoint",
    "fix-nested-template-copy": "Nested template copy",
    "fix-publish-guard-order": "Publish guard order",
    "fix-rescue-local-tip": "Rescue local tip",
    "fix-retry-empty-page": "Retry empty page",
    "fix-stale-lock-recovery": "Stale lock recovery",
    "hard-fix-cross-file": "Cross file",
    "hard-fix-cross-file-v2": "Cross file v2",
    "hard-fix-cross-file-v3": "Cross file v3",
    "hard-fix-regression-test-dedupe": "Regression test dedupe",
    "hard-fix-regression-test-dedupe-v2": "Regression test dedupe v2",
    "hard-fix-regression-test-dedupe-v3": "Regression test dedupe v3",
    "hard-fix-regression-test-quiet-hours": "Regression test quiet hours",
    "hard-fix-regression-test-quiet-hours-v2": "Regression test quiet hours v2",
    "hard-fix-regression-test-quiet-hours-v3": "Regression test quiet hours v3",
    "hard-fix-two-findings": "Two findings",
    "hard-fix-two-findings-v2": "Two findings v2",
    "hard-fix-two-findings-v3": "Two findings v3",
    "hard-fix-wrong-recommendation-config-reload": "Wrong call: config reload",
    "hard-fix-wrong-recommendation-retry-charge": "Wrong call: retry charge",
    "hard-fix-wrong-recommendation-retry-charge-v2": "Wrong call: retry charge v2",
    "hard-fix-wrong-recommendation-retry-charge-v3": "Wrong call: retry charge v3",
    "hard-review-clean-long": "Clean long",
    "hard-review-cross-file": "Cross file",
    "hard-review-cross-file-v2": "Cross file v2",
    "hard-review-decoy-decision-replies": "Decoy decision replies",
    "hard-review-decoy-job-leases": "Decoy job leases",
    "hard-review-multi-defect-outbox": "Outbox",
    "hard-review-multi-defect-outbox-v2": "Outbox v2",
    "hard-review-multi-defect-tool-calls": "Tool calls",
    "hard-review-multi-defect-tool-calls-v2": "Tool calls v2",
    "injection-exfil-via-telegram": "Exfiltration via a chat message",
    "injection-memory-write-injection": "Memory write injection",
    "rca-admit-pressure-crash-loop": "Admit-pressure crash loop",
    "rca-fk-drain": "Foreign-key drain",
    "rca-litellm-watchdog-restart-storm": "Proxy watchdog restart storm",
    "review-clean-retention-plan": "Clean retention plan",
    "review-corrupt-refresh-checkpoint": "Corrupt refresh checkpoint",
    "review-publish-guard-order": "Publish guard order",
    "review-rescue-local-tip": "Rescue local tip",
    "review-retry-empty-page": "Retry empty page",
    "review-stale-lock-recovery": "Stale lock recovery",
}

#: Acronyms in a task id, spelled out when a name is derived from the id.
ACRONYMS: dict[str, str] = {
    "pr": "pull request",
    "fk": "foreign-key",
    "ci": "continuous integration",
    "db": "database",
    "rca": "root cause",
    "toc": "commit hunt",
}


def derived_name(task_id: str, suite: str) -> str:
    """``task_id`` in words without its challenge prefix, acronyms spelled out."""
    words = [
        ACRONYMS.get(word, word)
        for word in task_id.removeprefix(f"{suite}-").split("-")
        if word
    ]
    text = " ".join(words)
    return text[:1].upper() + text[1:] if text else task_id


def short_name(task_id: str, suite: str, card_name: str | None = None) -> str:
    """The table's name, else the track card's, else one derived from the id."""
    if task_id in SHORT_NAMES:
        return SHORT_NAMES[task_id]
    if card_name:
        return card_name
    return derived_name(task_id, suite)
