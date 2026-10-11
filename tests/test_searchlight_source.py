"""The gym consumes Searchlight even while the legacy copy still exists."""

from pathlib import Path

import pytest


def test_sew_imports_are_from_searchlight():
    pytest.importorskip(
        "sew", reason="host price and report integration is not shipped"
    )
    from sew import cost_model, report, bakeoff_report

    root = Path(__file__).resolve().parents[1] / "tools" / "searchlight"
    for module in (cost_model, report, bakeoff_report):
        assert Path(module.__file__).resolve().is_relative_to(root)
