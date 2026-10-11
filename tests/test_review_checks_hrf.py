"""HRF-02 review grading: recall thresholds, decoy budgets, any-of defects."""

import math

import pytest
from gym.review_checks import evaluate

FILES = ("ledger.py", "writer.py", "caller.py")
CALLS = [{"name": "Read", "arguments": {"path": path}} for path in FILES]


def window(file, start, end=None, **extra):
    return dict(file=file, line_start=start, line_end=end or start, **extra)


def finding(file, start, end=None, **changes):
    return {
        **window(file, start, end),
        "blocking": True,
        "category": "correctness",
        "summary": "defect",
        **changes,
    }


def checks(findings, **kwargs):
    return {c["name"]: c for c in evaluate(findings, CALLS, **kwargs)}


FOUR = [
    window("ledger.py", 10),
    window("ledger.py", 40),
    window("writer.py", 20),
    window("writer.py", 80),
]


def test_recall_passes_at_threshold_and_fails_one_below():
    hits = [finding(d["file"], d["line_start"]) for d in FOUR]
    at = checks(hits[:3], defects=FOUR, min_recall=0.75)
    assert at["recall"] == dict(
        name="recall", ok=True, reason="found 3 of 4 defects; required 3", required=True
    )
    assert "defect_found" not in at
    below = checks(hits[:2], defects=FOUR, min_recall=0.75)
    assert not below["recall"]["ok"]
    assert below["recall"]["reason"] == "found 2 of 4 defects; required 3"


def test_recall_threshold_is_exact_for_fractions():
    three = FOUR[:3]
    hits = [finding(d["file"], d["line_start"]) for d in three]
    assert checks(hits[:2], defects=three, min_recall=2 / 3)["recall"]["ok"]
    assert not checks(hits[:1], defects=three, min_recall=2 / 3)["recall"]["ok"]
    full = checks(hits, defects=three, min_recall=1)
    assert full["recall"]["reason"] == "found 3 of 3 defects; required 3"


def test_recall_still_needs_distinct_findings():
    # Two findings on one defect earn one credit; the second is excess.
    result = checks(
        [finding("ledger.py", 10), finding("ledger.py", 11)],
        defects=FOUR[:2],
        min_recall=1,
        max_false_blocking=0,
    )
    assert result["recall"]["reason"] == "found 1 of 2 defects; required 2"
    assert not result["precision"]["ok"]


@pytest.mark.parametrize("value", [0, -0.5, 1.01, True, "0.5", math.nan])
def test_invalid_min_recall_raises(value):
    with pytest.raises(ValueError):
        evaluate([], CALLS, defects=FOUR, min_recall=value)


def test_min_recall_on_clean_control_raises():
    with pytest.raises(ValueError):
        evaluate([], CALLS, clean_control=True, read_files=["ledger.py"], min_recall=1)


DEFECT = window("ledger.py", 10)
DECOYS = [window("ledger.py", 30, 32), window("writer.py", 5)]


def test_decoy_hit_within_budget_and_one_over():
    hits = [finding("ledger.py", 10), finding("ledger.py", 31)]
    within = checks(
        hits, defects=[DEFECT], decoys=DECOYS, max_decoy_blocking=1, max_false_blocking=0
    )
    assert within["decoy_resistance"] == dict(
        name="decoy_resistance",
        ok=True,
        reason="1 blocking findings on 2 decoys; budget 1",
        required=True,
    )
    # Decoy hits have their own budget; they never count toward precision.
    assert within["precision"]["ok"]
    assert "0 false blocking findings" in within["precision"]["reason"]
    over = checks(
        [*hits, finding("writer.py", 7)],
        defects=[DEFECT],
        decoys=DECOYS,
        max_decoy_blocking=1,
        max_false_blocking=0,
    )
    assert not over["decoy_resistance"]["ok"]
    assert over["decoy_resistance"]["reason"] == "2 blocking findings on 2 decoys; budget 1"
    assert over["precision"]["ok"]


def test_default_decoy_budget_is_zero_and_nonblocking_decoy_notes_are_free():
    result = checks(
        [finding("ledger.py", 10), finding("ledger.py", 31)], defects=[DEFECT], decoys=DECOYS
    )
    assert not result["decoy_resistance"]["ok"]
    note = finding("ledger.py", 31, blocking=False)
    assert checks([finding("ledger.py", 10), note], defects=[DEFECT], decoys=DECOYS)[
        "decoy_resistance"
    ]["ok"]


def test_decoy_hit_on_clean_control():
    result = checks(
        [finding("ledger.py", 31)], clean_control=True, read_files=["ledger.py"], decoys=DECOYS
    )
    assert not result["no_blocking"]["ok"]
    assert not result["decoy_resistance"]["ok"]


def test_broad_finding_that_spans_a_decoy_counts_as_a_decoy_hit():
    result = checks([finding("ledger.py", 1, 400)], defects=[DEFECT], decoys=DECOYS)
    assert result["defect_found"]["ok"]
    assert not result["decoy_resistance"]["ok"]


def test_decoy_hits_never_displace_a_clean_finding():
    # The broad finding overlaps the defect and a decoy; the precise finding
    # keeps the credit whichever order the reviewer listed them in.
    broad, precise = finding("ledger.py", 8, 31), finding("ledger.py", 10)
    for order in ([broad, precise], [precise, broad]):
        result = checks(
            order, defects=[DEFECT], decoys=DECOYS, max_decoy_blocking=1, max_false_blocking=0
        )
        assert all(c["ok"] for c in result.values()), result


@pytest.mark.parametrize(
    "decoy",
    [
        window("ledger.py", 10),  # on the defect
        window("ledger.py", 13),  # within slack of the defect
        window("caller.py", 50),  # on the tolerated window
        window("writer.py", 22),  # on an any_of alternative
    ],
)
def test_decoy_overlapping_an_author_window_raises(decoy):
    with pytest.raises(ValueError, match="decoy windows must not overlap"):
        evaluate(
            [],
            CALLS,
            defects=[DEFECT, {"any_of": [window("ledger.py", 60), window("writer.py", 20)]}],
            tolerated=[window("caller.py", 50)],
            decoys=[decoy],
        )


def test_decoy_just_outside_slack_is_accepted():
    evaluate([], CALLS, defects=[DEFECT], decoys=[window("ledger.py", 14)])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_decoy_blocking": -1, "decoys": DECOYS},
        {"max_decoy_blocking": True, "decoys": DECOYS},
        {"max_decoy_blocking": 1},
        {"decoys": [window("ledger.py", 0)]},
    ],
)
def test_invalid_decoy_parameters_raise(kwargs):
    with pytest.raises(ValueError):
        evaluate([], CALLS, defects=[DEFECT], **kwargs)


CROSS = {
    "any_of": [window("writer.py", 20, 22), window("caller.py", 40)],
    "expected_category": "api-contract",
}


@pytest.mark.parametrize("location", [("writer.py", 21), ("caller.py", 41)])
def test_any_of_defect_credited_at_either_alternative(location):
    result = checks([finding(*location, category="api-contract")], defects=[CROSS])
    assert all(c["ok"] for c in result.values()), result


def test_any_of_defect_credited_only_once_when_both_alternatives_are_flagged():
    both = [finding("writer.py", 21), finding("caller.py", 40)]
    result = checks(both, defects=[CROSS, DEFECT], min_recall=1, max_false_blocking=1)
    assert result["recall"]["reason"] == "found 1 of 2 defects; required 2"
    assert "1 false blocking findings" in result["precision"]["reason"]
    strict = checks(both, defects=[CROSS], max_false_blocking=0)
    assert strict["defect_found"]["ok"]
    assert not strict["precision"]["ok"]


def test_any_of_assignment_is_order_free():
    # One finding overlaps an any_of alternative and a plain defect; the other
    # only the plain defect. Credit must cover both however they are listed.
    defects = [
        {"any_of": [window("ledger.py", 10), window("caller.py", 90)]},
        window("ledger.py", 16),
    ]
    findings = [finding("ledger.py", 13), finding("ledger.py", 17)]
    for order in (findings, findings[::-1]):
        result = checks(order, defects=defects, max_false_blocking=0)
        assert result["defect_found"]["ok"] and result["precision"]["ok"], result
    for order in (findings, findings[::-1]):
        result = checks(order, defects=defects[::-1], max_false_blocking=0)
        assert result["defect_found"]["ok"] and result["precision"]["ok"], result


@pytest.mark.parametrize("reverse_defects", [False, True])
@pytest.mark.parametrize("reverse_findings", [False, True])
def test_category_enforcement_distinguishes_overlapping_defects(reverse_defects, reverse_findings):
    defects = [
        {
            "any_of": [window("ledger.py", 10), window("ledger.py", 40)],
            "expected_category": "security",
        },
        window("ledger.py", 41, expected_category="api-contract"),
    ]
    hits = [
        finding("ledger.py", 10, category="security"),
        finding("ledger.py", 40, category="security"),
    ]
    if reverse_defects:
        defects.reverse()
    if reverse_findings:
        hits.reverse()
    # Existing tasks retain advisory category matching by default.
    assert checks(hits, defects=defects)["defect_found"]["ok"]
    strict = checks(hits, defects=defects, enforce_category=True)
    assert not strict["defect_found"]["ok"]
    assert strict["precision"]["ok"]  # The tolerated duplicate must not earn recall.
    correct = [
        finding("ledger.py", 40, category="security"),
        finding("ledger.py", 41, category="api-contract"),
    ]
    if reverse_findings:
        correct.reverse()
    strict = checks(correct, defects=defects, enforce_category=True, max_false_blocking=0)
    assert strict["defect_found"]["ok"] and strict["precision"]["ok"]


@pytest.mark.parametrize("category_source", ["task", "defect", "unspecified"])
def test_category_enforcement_uses_declared_category(category_source):
    defect = window("ledger.py", 10)
    kwargs = {}
    if category_source == "task":
        kwargs["expected_category"] = "security"
    elif category_source == "defect":
        defect["expected_category"] = "security"
        kwargs["expected_category"] = "correctness"
    result = checks([finding("ledger.py", 10)], defects=[defect], enforce_category=True, **kwargs)
    assert result["defect_found"]["ok"] == (category_source == "unspecified")


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_invalid_category_enforcement_raises(value):
    with pytest.raises(ValueError, match="enforce_category must be a boolean"):
        evaluate([], CALLS, defects=FOUR, enforce_category=value)


def test_any_of_category_is_set_on_the_defect():
    result = checks([finding("caller.py", 40, category="correctness")], defects=[CROSS])
    assert not result["category"]["ok"]
    assert not result["category"]["required"]


@pytest.mark.parametrize(
    "defect",
    [
        {"any_of": []},
        {"any_of": window("writer.py", 20)},
        {"any_of": [window("writer.py", 20)], "file": "writer.py"},
        {"any_of": [{"any_of": [window("writer.py", 20)]}]},
        {"any_of": [window("writer.py", 20, expected_category="correctness")]},
        {"any_of": [window("writer.py", 0)]},
        {"any_of": [window("writer.py", 20)], "expected_category": "invented"},
    ],
)
def test_invalid_any_of_defects_raise(defect):
    with pytest.raises(ValueError):
        evaluate([], CALLS, defects=[defect])


def test_any_of_in_tolerated_raises():
    with pytest.raises(ValueError):
        evaluate([], CALLS, defects=[DEFECT], tolerated=[CROSS])


def test_read_evidence_requires_every_listed_file():
    hit = [finding("writer.py", 21, category="api-contract")]
    files = ["writer.py", "caller.py"]
    assert checks(hit, defects=[CROSS], read_files=files)["read_evidence"]["ok"]
    partial = evaluate(hit, CALLS[1:2], defects=[CROSS], read_files=files)
    assert not partial[-1]["ok"]
    # Without read_files, an any_of defect needs every alternative's file read.
    assert not evaluate(hit, CALLS[1:2], defects=[CROSS])[-1]["ok"]
    assert evaluate(hit, CALLS[1:3], defects=[CROSS])[-1]["ok"]


def long_diff_task():
    """A 3-file, 400-line review: four defects (one cross-file) and four decoys."""
    return dict(
        defects=[
            window("ledger.py", 40, 42),
            window("ledger.py", 190),
            window("writer.py", 75),
            {"any_of": [window("writer.py", 120, 124), window("caller.py", 60)]},
        ],
        decoys=[
            window("ledger.py", 100, 104),
            window("writer.py", 30),
            window("writer.py", 160),
            window("caller.py", 20, 22),
        ],
        tolerated=[window("ledger.py", 250, 251)],
        min_recall=1,
        max_false_blocking=1,
    )


def test_long_diff_reference_passes_and_obvious_only_fails():
    task = long_diff_task()
    reference = [
        finding("ledger.py", 41),
        finding("ledger.py", 190),
        finding("writer.py", 75),
        finding("caller.py", 60),
    ]
    assert all(c["ok"] for c in checks(reference, **task).values())
    obvious = checks(reference[:1], **task)
    assert not obvious["recall"]["ok"]
    assert obvious["recall"]["reason"] == "found 1 of 4 defects; required 4"


def test_flag_everything_fails_on_a_long_diff():
    task = long_diff_task()
    spray = [finding(file, line) for file in FILES for line in range(1, 400, 5)]
    result = checks(spray, **task)
    assert result["recall"]["ok"]
    assert not result["decoy_resistance"]["ok"]
    assert not result["precision"]["ok"]
    # One whole-file finding per defect still lands on the decoys.
    broad = [finding(file, 1, 400) for file in ("ledger.py", "ledger.py", "writer.py", "caller.py")]
    result = checks(broad, **task)
    assert result["recall"]["ok"]
    assert not result["decoy_resistance"]["ok"]
