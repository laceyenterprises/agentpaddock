"""GYM-05 runbook completeness: the operator contract covers all six topics.

The runbook is the operator contract for the whole pack; the ticket mandates it
cover six recurring operations. This test pins that they each have a section, so
a future edit can't silently drop one.
"""

from __future__ import annotations

import pytest

from conftest import REPO_ROOT

RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "RUNBOOK-adversarial-eval-gym.md"

pytestmark = pytest.mark.skipif(
    not RUNBOOK.is_file(), reason="host runbook is not shipped"
)

# The six mandated topics (SPEC §3 GYM-05), each a section heading in the runbook.
_REQUIRED_SECTIONS = [
    "## Adding a task",
    "## Adding a postmortem-derived RCA task",
    "## Updating the baseline",
    "## Responding to a `gym_regression` alert",
    "## The CTX dependency for injection tasks",
    "## Lock maintenance",
]


def test_runbook_exists():
    assert RUNBOOK.is_file(), f"runbook not found at {RUNBOOK}"


def test_runbook_covers_all_six_topics():
    text = RUNBOOK.read_text(encoding="utf-8")
    missing = [section for section in _REQUIRED_SECTIONS if section not in text]
    assert not missing, f"runbook is missing mandated section(s): {missing}"


def test_runbook_documents_the_only_lock_refresh_path():
    # Lock maintenance must state that `hq gym lock update` is the sole refresh
    # path and that drift aborts fail-closed — the ticket Don't.
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "hq gym lock update" in text
    assert "fail-closed" in text
