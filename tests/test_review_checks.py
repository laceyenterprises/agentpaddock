import pytest
from gym.review_checks import evaluate, CATEGORIES, result_schema_snippet, _read_arguments

WINDOW = dict(file="src/store.py", line_start=10, line_end=12)
CALLS = {"calls": [{"name": "exec", "arguments": {"cmd": "cat src/store.py"}}]}


def finding(**changes):
    return (
        dict(
            file="src/store.py",
            line_start=10,
            line_end=12,
            blocking=True,
            category="data-loss",
            summary="drops commits",
            **changes,
        )
        if not changes
        else {**finding(), **changes}
    )


def checks(findings, **kwargs):
    return {
        c["name"]: c
        for c in evaluate(
            findings, CALLS, defects=[WINDOW], expected_category="data-loss", **kwargs
        )
    }


def test_exact_hit():
    assert all(c["ok"] for c in checks([finding()]).values())


def test_hit_within_slack():
    assert checks([finding(line_start=15, line_end=15)])["defect_found"]["ok"]


def test_near_miss_outside_slack():
    assert not checks([finding(line_start=16, line_end=16)])["defect_found"]["ok"]


def test_wrong_file():
    assert not checks([finding(file="other.py")])["defect_found"]["ok"]


def test_nonblocking_hit_not_counted():
    assert not checks([finding(blocking=False)])["defect_found"]["ok"]


def test_flag_everything_fails_precision():
    assert not checks([finding(), finding(file="a.py"), finding(file="b.py")])["precision"]["ok"]


def test_tolerated_secondary_defect():
    assert checks(
        [finding(), finding(file="other.py")],
        tolerated=[{**WINDOW, "file": "other.py"}],
        max_false_blocking=0,
    )["precision"]["ok"]


def test_default_false_blocking_budget_and_category_optional():
    result = checks([finding(category="correctness"), finding(file="other.py")])
    assert result["precision"]["ok"]
    assert not result["category"]["ok"] and not result["category"]["required"]


def test_clean_control_fails_one_blocking():
    result = evaluate([finding()], CALLS, clean_control=True, read_files=["src/store.py"])
    assert not next(c for c in result if c["name"] == "no_blocking")["ok"]
    assert "defect_found" not in [c["name"] for c in result]
    assert all(
        c["ok"] for c in evaluate([], CALLS, clean_control=True, read_files=["src/store.py"])
    )


def test_read_evidence_arguments_only_and_path_boundary():
    for args in ({"output": "src/store.py"}, {"arguments": "cat src/store.py.bak"}):
        result = evaluate([finding()], {"calls": [args]}, defects=[WINDOW])
        assert not result[-1]["ok"]
    assert evaluate(
        [finding()],
        [{"name": "Read", "arguments": {"path": "/tmp/src/store.py"}}],
        defects=[WINDOW],
    )[-1]["ok"]


def test_all_defects_required():
    assert not evaluate([finding()], CALLS, defects=[WINDOW, {**WINDOW, "file": "other.py"}])[0][
        "ok"
    ]


@pytest.mark.parametrize(
    "changes",
    [{"line_start": 0}, {"line_end": 9}, {"file": ""}, {"category": "invented"}, {"blocking": 1}],
)
def test_invalid_findings_fail_required_check(changes):
    result = checks([finding(**changes)])
    assert not result["well_formed_findings"]["ok"]
    assert result["well_formed_findings"]["required"]
    assert not result["defect_found"]["ok"]


def test_invalid_finding_cannot_hide_behind_a_valid_hit():
    assert not checks([finding(), finding(line_end=9)])["well_formed_findings"]["ok"]


@pytest.mark.parametrize("parameter", ["defects", "tolerated"])
@pytest.mark.parametrize("changes", [{"line_start": 0}, {"line_end": 9}, {"file": ""}])
def test_invalid_author_windows_still_raise(parameter, changes):
    kwargs = {"defects": [WINDOW], parameter: [{**WINDOW, **changes}]}
    with pytest.raises(ValueError):
        evaluate([], CALLS, **kwargs)


@pytest.mark.parametrize(
    "kwargs", [{"slack": -1}, {"max_false_blocking": -1}, {"expected_category": "invented"}]
)
def test_invalid_author_parameters_still_raise(kwargs):
    with pytest.raises(ValueError):
        evaluate([], CALLS, defects=[WINDOW], **kwargs)


def test_shared_schema_closed_enum():
    import tomllib

    field = tomllib.loads(result_schema_snippet())["result_schema"]["findings"]
    assert field["items"]["category"]["enum"] == list(CATEGORIES)
    assert field["max_items"] == 32


def test_duplicate_hits_count_against_precision():
    result = checks([finding(), finding(), finding()], max_false_blocking=0)
    assert result["defect_found"]["ok"]
    assert not result["precision"]["ok"]
    assert "2 false blocking findings" in result["precision"]["reason"]


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("exec", {"cmd": "echo src/store.py"}, False),
        ("write", {"path": "src/store.py"}, False),
        ("Read", {"path": "src/store.py"}, True),
        ("exec_command", {"cmd": "echo src/store.py; cat other.py"}, False),
        ("exec_command", {"cmd": "echo other.py; cat src/store.py"}, True),
    ],
)
def test_read_evidence_requires_read_like_call(name, arguments, expected):
    result = evaluate([finding()], [{"name": name, "arguments": arguments}], defects=[WINDOW])
    assert result[-1]["ok"] is expected


def test_overlapping_windows_have_order_independent_distinct_credit():
    windows = [WINDOW, {**WINDOW, "line_start": 16, "line_end": 16}]
    findings = [finding(line_start=13, line_end=13), finding(line_start=10, line_end=10)]
    result = evaluate(findings, CALLS, defects=windows, max_false_blocking=0)
    assert result[0]["ok"]
    assert next(c for c in result if c["name"] == "precision")["ok"]


TARGETS = ("src/store.py", "src/lease.py")


def credited(*commands):
    """Target files credited by read_evidence, one shell tool call per command."""
    calls = [{"name": "bash", "arguments": {"command": command}} for command in commands]
    return {
        path
        for path in TARGETS
        if evaluate([], calls, clean_control=True, read_files=[path])[-1]["ok"]
    }


@pytest.mark.parametrize(
    "body",
    [
        'cat -n "$f"',
        "cat -n $f",
        "cat -n ${f}",
        'nl -ba "$f"',
        "nl -ba $f",
        'nl -ba "${f}"',
        'sed -n 1,200p "$f"',
        "sed -n 1,200p $f",
        "sed -n '1,200p' ${f}",
    ],
)
def test_read_evidence_credits_every_word_of_a_for_loop(body):
    assert credited(f"for f in src/store.py src/lease.py; do {body}; done") == set(TARGETS)
    assert credited(f"for f in src/store.py src/lease.py\ndo\n  {body}\ndone") == set(TARGETS)


def test_read_evidence_credits_a_loop_inside_a_nested_shell():
    command = "bash -lc 'for f in src/store.py src/lease.py; do cat -n \"$f\"; done'"
    assert credited(command) == set(TARGETS)


@pytest.mark.parametrize(
    "expansion", ["${f:-missing}", "${f-missing}", "${f:?missing}", "${f?missing}"]
)
def test_read_evidence_credits_value_preserving_parameter_operators(expansion):
    assert credited(
        f'for f in src/store.py src/lease.py; do cat "{expansion}"; done'
    ) == set(TARGETS)


@pytest.mark.parametrize(
    "expansion", ["${f%.*}", "${f##*/}", "${f:+other.py}", "${f/store/other}"]
)
def test_read_evidence_does_not_infer_original_path_from_transformations(expansion):
    assert credited(f'for f in src/store.py; do cat "{expansion}"; done') == set()


@pytest.mark.parametrize("quote", ["", '"'])
@pytest.mark.parametrize("backslashes", [1, 2, 3, 4])
@pytest.mark.parametrize("reference", ["$f", "${f}", "${f:?missing}"])
def test_read_evidence_respects_dollar_escape_parity(quote, backslashes, reference):
    body = "cat " + quote + "\\" * backslashes + reference + quote
    expected = {"src/store.py"} if backslashes % 2 == 0 else set()
    assert credited(f"for f in src/store.py; do {body}; done") == expected


@pytest.mark.parametrize(
    "body",
    [
        "cat src/lease.py # inspect $f; done",
        "cat src/lease.py # inspect ${f}; done",
        "cat src/lease.py # src/store.py; cat $f; done",
        "cat src/lease.py # quoted comment ' $f; cat src/store.py; done",
    ],
)
def test_read_evidence_ignores_commented_paths_and_loop_references(body):
    assert credited(f"for f in src/store.py; do {body}\ndone") == {"src/lease.py"}


def test_read_evidence_preserves_quoted_hash_and_resumes_after_comments():
    assert credited('for f in src/store.py; do grep "#pattern" "$f"; done') == {
        "src/store.py"
    }
    assert credited(
        'for f in src/store.py; do # ignore $f; cat src/lease.py\ncat "$f"; done'
    ) == {"src/store.py"}
    assert credited("bash -c 'cat src/lease.py # src/store.py; cat src/store.py'") == {
        "src/lease.py"
    }


def test_read_evidence_credits_command_prefix_and_compound_bodies():
    assert credited("command cat -n src/store.py src/lease.py") == set(TARGETS)
    assert credited("command command /bin/cat src/store.py") == {"src/store.py"}
    assert credited(
        'if [ -n "$X" ]; then cat src/store.py; else nl -ba src/lease.py; fi'
    ) == set(TARGETS)


@pytest.mark.parametrize(
    "commands,expected",
    [
        (['for f in src/store.py src/lease.py; do echo "$f"; done'], set()),
        (["for f in src/store.py; do cat src/lease.py; done"], {"src/lease.py"}),
        (["command -v cat src/store.py src/lease.py"], set()),
        (["for f in src/store.py src/lease.py", 'cat -n "$f"'], set()),
        (["for f in src/store.py src/lease.py; do :; done"], set()),
        (["for f in src/store.py src/lease.py"], set()),
        (['for f in src/store.py; do cat "$file"; done'], set()),
        (["for f in src/store.py; do grep -n '$f' src/lease.py; done"], {"src/lease.py"}),
    ],
)
def test_read_evidence_does_not_credit_unread_loop_words(commands, expected):
    assert credited(*commands) == expected


def test_read_evidence_keeps_existing_credited_forms_unchanged():
    calls = [
        {"name": "exec", "arguments": {"cmd": "cat src/store.py"}},
        {"name": "Read", "arguments": {"path": "src/lease.py"}},
        {"name": "bash", "arguments": {"command": "echo x; sed -n 1,9p src/store.py"}},
        {"name": "shell", "arguments": {"command": "rg -n lease src/lease.py | head -5"}},
        {"name": "exec_command", "arguments": {"cmd": "/usr/bin/nl -ba src/store.py"}},
    ]
    assert [s for call in calls for s in _read_arguments(call)] == [
        "cat src/store.py",
        "src/lease.py",
        "sed -n 1,9p src/store.py",
        "rg -n lease src/lease.py",
        "head -5",
        "/usr/bin/nl -ba src/store.py",
    ]
    assert credited("cat src/store.py src/lease.py") == set(TARGETS)
