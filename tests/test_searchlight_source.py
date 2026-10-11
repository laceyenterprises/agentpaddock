"""The gym consumes Searchlight even while the legacy copy still exists."""

from pathlib import Path


def test_sew_imports_are_from_searchlight():
    from sew import cost_model, report, bakeoff_report

    root = Path(__file__).resolve().parents[3] / "tools" / "searchlight"
    for module in (cost_model, report, bakeoff_report):
        assert Path(module.__file__).resolve().is_relative_to(root)
