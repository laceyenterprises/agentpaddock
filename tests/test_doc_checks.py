import pytest
from gym.doc_checks import checks


def failures(text, **kwargs):
    return {c["name"] for c in checks(text, **kwargs) if not c["ok"]}


def test_all_patterns_required_and_values_bound_to_claim():
    contract = dict(
        required_facts={"limit": [r"--limit[^\n]*default 20", r"exit code 2"]}, forbidden_claims={}
    )
    assert not failures(
        "--limit default 20; invalid input exit code 2", known_flags=["--limit"], **contract
    )
    assert "fact:limit" in failures("--limit default 20", known_flags=["--limit"], **contract)
    assert "fact:limit" in failures(
        "## Limit\n--limit\n## Other\nrandom keywords: default 20 exit code 2",
        known_flags=["--limit"],
        **contract,
    )


def test_stale_padding_rejected_even_with_correct_facts():
    assert "stale:old" in failures(
        "default 20\ndefault 50",
        required_facts={"limit": ["default 20"]},
        forbidden_claims={"old": ["default 50"]},
    )


def test_flags_and_dotted_keys_anywhere_and_explicit_literals():
    contract = dict(
        required_facts={},
        forbidden_claims={},
        known_flags=["--limit"],
        known_config_keys=["cache.ttl"],
        non_config_literals=["surface.py"],
    )
    assert not failures("`--limit=20` cache.ttl surface.py", **contract)
    assert failures("```sh\nhq new --invented=3\n```\ncache.secret", **contract) == {
        "known_flags",
        "known_config_keys",
    }
    assert "known_flags" in failures("--limit-extra", **contract)
    assert "known_config_keys" in failures("cache.ttl.extra", **contract)


def test_exact_headings_and_missing_capture():
    contract = dict(required_facts={}, forbidden_claims={}, preserved_headings=["## Usage"])
    assert not failures("## Usage\ntext", **contract)
    assert "heading:## Usage" in failures("## usage\ntext", **contract)
    assert "document_captured" in failures(None, **contract)


@pytest.mark.parametrize("field", ["required_facts", "forbidden_claims"])
def test_empty_or_string_regex_sets_are_authoring_errors(field):
    for patterns in ([], "default 20"):
        contract = dict(required_facts={}, forbidden_claims={})
        contract[field] = {"invalid": patterns}
        with pytest.raises(ValueError):
            checks("", **contract)


def test_dotted_key_before_sentence_punctuation_is_checked():
    assert "known_config_keys" in failures(
        "Set cache.invented.",
        required_facts={},
        forbidden_claims={},
        config_key_prefixes=["cache."],
    )
    assert "known_config_keys" in failures(
        "path/cache.invented",
        required_facts={},
        forbidden_claims={},
        config_key_prefixes=["cache."],
    )


def test_prose_abbreviations_and_task_filenames_are_not_keys():
    contract = dict(required_facts={}, forbidden_claims={}, config_key_prefixes=["cache."])
    assert not failures("e.g. i.e. surface.py guide.md change.diff bench-result.json", **contract)
    # Arbitrary extensions are not exempt: a config key can end in 'json'.
    assert "known_config_keys" in failures("cache.json", **contract)


def test_document_shape_fails_before_fact_regexes_run():
    assert failures(
        "x" * 2049,
        required_facts={"invalid_regex_never_evaluated": ["("]},
        forbidden_claims={},
    ) == {"document_shape"}


def test_other_line_separators_cannot_bypass_regex_line_limit():
    assert "document_shape" in failures(
        ("x" * 1024 + "\v") * 3,
        required_facts={},
        forbidden_claims={},
    )


@pytest.mark.parametrize("ending", ["\r\n", "  \n", "\t\n"])
def test_headings_ignore_trailing_whitespace(ending):
    assert not failures(
        "## Usage" + ending,
        required_facts={},
        forbidden_claims={},
        preserved_headings=["## Usage"],
    )


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_headings_inside_fences_do_not_count(fence):
    contract = dict(required_facts={}, forbidden_claims={}, preserved_headings=["## Usage"])
    assert "heading:## Usage" in failures(fence + "python\n## Usage\n" + fence, **contract)
    assert not failures(fence + "python\n# comment\n" + fence + "\n## Usage\n", **contract)


def test_facts_share_one_bounded_section_or_paragraph():
    contract = dict(
        required_facts={"levels": [r"\A(?=.*flag)(?=.*low)(?=.*high)"]}, forbidden_claims={}
    )
    assert not failures("## Levels\nflag\n\n- low\n- high", **contract)
    assert not failures("flag\nlow\nhigh", **contract)
    assert "fact:levels" in failures("## Flag\nflag low\n## Other\nhigh", **contract)
    assert "fact:levels" in failures("flag low\n\nhigh", **contract)
    assert "fact:levels" in failures(
        "## Levels\nflag low\n\n" + ("padding\n" * 1200) + "\nhigh", **contract
    )


def test_config_namespace_ignores_source_attributes():
    contract = dict(required_facts={}, forbidden_claims={}, known_config_keys=["cache.ttl"])
    assert not failures("self.models `obj.attr` argparse.ArgumentParser --help -h", **contract)
    assert "known_config_keys" in failures("`cache.invented`", **contract)
