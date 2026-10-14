import pytest
from gym.doc_checks import checks


def failures(text, **kwargs):
    return {c["name"] for c in checks(text, **kwargs) if not c["ok"]}


def test_all_patterns_required_and_values_bound_to_claim():
    contract = dict(
        required_facts={"limit": [r"--limit[^\n]*default 20", r"exit code 2"]},
        forbidden_claims={},
    )
    assert not failures(
        "--limit default 20; invalid input exit code 2",
        known_flags=["--limit"],
        **contract,
    )
    assert "fact:limit" in failures(
        "--limit default 20", known_flags=["--limit"], **contract
    )
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
    contract = dict(
        required_facts={}, forbidden_claims={}, preserved_headings=["## Usage"]
    )
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
    contract = dict(
        required_facts={}, forbidden_claims={}, config_key_prefixes=["cache."]
    )
    assert not failures(
        "e.g. i.e. surface.py guide.md change.diff bench-result.json", **contract
    )
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
    contract = dict(
        required_facts={}, forbidden_claims={}, preserved_headings=["## Usage"]
    )
    assert "heading:## Usage" in failures(
        fence + "python\n## Usage\n" + fence, **contract
    )
    assert not failures(
        fence + "python\n# comment\n" + fence + "\n## Usage\n", **contract
    )


def test_facts_share_one_bounded_section_or_paragraph():
    contract = dict(
        required_facts={"levels": [r"\A(?=.*flag)(?=.*low)(?=.*high)"]},
        forbidden_claims={},
    )
    assert not failures("## Levels\nflag\n\n- low\n- high", **contract)
    assert not failures("flag\nlow\nhigh", **contract)
    assert "fact:levels" in failures("## Flag\nflag low\n## Other\nhigh", **contract)
    assert "fact:levels" in failures("flag low\n\nhigh", **contract)
    assert "fact:levels" in failures(
        "## Levels\nflag low\n\n" + ("padding\n" * 1200) + "\nhigh", **contract
    )


def test_config_namespace_ignores_source_attributes():
    contract = dict(
        required_facts={}, forbidden_claims={}, known_config_keys=["cache.ttl"]
    )
    assert not failures(
        "self.models `obj.attr` argparse.ArgumentParser --help -h", **contract
    )
    assert "known_config_keys" in failures("`cache.invented`", **contract)


def unit_failures(text, patterns, **spec):
    return failures(
        text,
        required_facts={"claim": {"patterns": patterns, "scope": "unit", **spec}},
        forbidden_claims={},
    )


def test_self_scoped_rejection_retains_subject_across_semicolon():
    spec = dict(
        reject=[r"--json\b(?:(?!--[\w-]+)[^|.])*\bdefault is true"],
        reject_subject=[],
    )
    assert "fact:claim" in unit_failures(
        "--json is boolean; its default is true.", ["--json", "boolean"], **spec
    )
    assert "fact:claim" not in unit_failures(
        "--json is boolean; --limit default is true.",
        ["--json", "boolean"],
        **spec,
    )


@pytest.mark.parametrize(
    "text",
    [
        "The cache default is 128.",
        "128 is the default for the cache.",
        "- The cache's maximum entries. Default: 128.",
        "- Cache default:\n  128 entries.",
        "### Cache\n- Default: 128 entries.",
        "| Subject | Default |\n| --- | --- |\n| Cache | 128 |",
    ],
)
def test_unit_tokens_can_appear_in_any_order(text):
    assert not unit_failures(text, [r"\bcache\b", r"\bdefault\b", r"\b128\b"])


@pytest.mark.parametrize(
    "text",
    [
        "Cache default is 129. Another default is 128.",
        "- Cache default is 129.\n- Another default is 128.",
        "| Subject | Default |\n| Cache | 129 |\n| Other | 128 |",
        "### Cache default 128",
    ],
)
def test_units_do_not_borrow_tokens(text):
    assert "fact:claim" in unit_failures(text, [r"\bcache\b", r"\b128\b"])


def test_table_header_context_and_default_cell_are_local():
    text = "| Key | Default | Allowed values |\n| worker | codex | plan, codex |"
    assert not unit_failures(text, ["worker", "allowed values", "plan"])
    assert "fact:claim" in unit_failures(
        text, ["worker", "default", "plan"], default_column=True
    )
    assert not unit_failures(
        text.replace("| codex |", "| plan |"),
        ["worker", "default", "plan"],
        default_column=True,
    )
    # A named default in one table does not suppress another headerless table.
    assert not unit_failures(
        text + "\n\n| other | plan |", ["other", "plan"], default_column=True
    )


def test_default_cell_keeps_its_label_and_row_rejections():
    text = "| Flag | Default / Output |\n| mode | JSON |"
    assert not unit_failures(
        text,
        ["mode", "JSON"],
        default_column=True,
        reject=[r"mode default JSON"],
    )
    text = "| Flag | Default | Description |\n| mode | off | takes a value |"
    assert "fact:claim" in unit_failures(
        text,
        ["mode", "off"],
        default_column=True,
        reject=[r"mode.*takes a value"],
        reject_subject=[],
    )


@pytest.mark.parametrize("kind", ["sentence", "list", "table", "enum"])
def test_overlong_logical_units_fail_closed(kind):
    from gym.doc_checks import MAX_FACT_UNIT

    padding = "x" * (MAX_FACT_UNIT // 2)
    text = {
        "sentence": f"cache {padding}\n{padding} default 128.",
        "list": f"- cache {padding}\n  {padding} default 128.",
        "table": f"| Subject | {padding} | Default |\n| cache | {padding} | 128 |",
        "enum": "cache values:\n"
        + "\n".join("- " + padding for _ in range(3))
        + "\n- 128",
    }[kind]
    assert "fact:claim" in unit_failures(text, ["cache", "128"])


def test_literal_list_context_does_not_join_independent_claims():
    assert not unit_failures(
        "Allowed values:\n- plan.\n- codex.\n- claude-code.",
        ["allowed", "plan", "codex", "claude-code"],
    )
    assert "fact:claim" in unit_failures(
        "Limits:\n- Cache default is 129.\n- Other default is 128.", ["cache", "128"]
    )


def test_explicit_section_scope_remains_available():
    text = "## Values\nflag low\n\n- high"
    assert not failures(
        text,
        required_facts={
            "claim": {"patterns": ["flag", "low", "high"], "scope": "section"}
        },
        forbidden_claims={},
    )
    assert "fact:claim" in unit_failures(text, ["flag", "low", "high"])


def test_unit_authoring_errors():
    with pytest.raises(ValueError):
        unit_failures("text", [])
    with pytest.raises(ValueError):
        unit_failures("text", ["text"], scope="document")


def test_enum_with_correct_prefix_and_overlong_tail_fails_closed():
    text = "Allowed values:\n- plan\n- codex\n- claude-code\n" + "- padding\n" * 400
    assert "fact:claim" in unit_failures(text, ["plan", "codex", "claude-code"])


def test_long_paragraph_keeps_individually_bounded_sentences():
    text = "Short unrelated sentence. " * 200 + "Cache default is 128."
    # Use wrapped lines so document_shape does not reject a long physical line.
    assert not unit_failures(text.replace(". ", ".\n"), ["cache", "default", "128"])


def test_arbitrary_table_headers_with_markdown_rule_supply_context():
    text = "| Setting | Initial value |\n| :--- | ---: |\n| Cache | 128 |"
    assert not unit_failures(text, ["cache", "initial value", "128"])
    assert "fact:claim" in unit_failures(text + "\n| Other | 129 |", ["cache", "129"])


@pytest.mark.parametrize(
    "text",
    [
        "Cache is configured. Its default is 128.",
        "- Cache is configured.\n  Its default is 128.\n  - Nested explanation.",
        "### Cache\n\nThe default is 128.",
        "### Cache\n- Default is 128.\n  - Nested explanation.",
    ],
)
def test_paragraph_scope(text):
    assert not unit_failures(text, ["cache", "128"], scope="paragraph")


@pytest.mark.parametrize(
    "text",
    [
        "### Cache default 128",
        "Cache.\n\nDefault 128.",
        "- Cache.\n- Default 128.",
        "### Cache\nOther block.\n\nDefault 128.",
        "Cache " + ("padding\n" * 600) + "default 128.",
    ],
)
def test_paragraph_boundaries_and_limit(text):
    assert "fact:claim" in unit_failures(text, ["cache", "128"], scope="paragraph")


@pytest.mark.parametrize(
    "text,ok",
    [
        ("Other default is 128, no update needed; cache default is 128.", True),
        ("Other default is 128, no update needed. Cache default is 128.", True),
        ("Cache default is 128, no update needed.", False),
    ],
)
def test_reject_is_clause_local(text, ok):
    assert (
        "fact:claim"
        not in unit_failures(
            text, ["cache", "128"], scope="paragraph", reject=["no update"]
        )
    ) is ok


def test_heading_plus_first_list_scope():
    assert not unit_failures(
        "### Cache\n- Initial configuration.\n- Default 128.",
        ["cache", "128"],
        scope="paragraph",
    )
    assert "fact:claim" in unit_failures(
        "### Cache\nFirst paragraph.\n\n- Default 128.",
        ["cache", "128"],
        scope="paragraph",
    )


@pytest.mark.parametrize(
    "body",
    ["Cache is configured.\n\n- Default 128.", "Cache is configured.\n\nDefault 128."],
)
def test_section_scope_adjacent_blocks(body):
    assert not unit_failures("### Settings\n" + body, ["cache", "128"], scope="section")
    assert "fact:claim" in unit_failures(
        "### Settings\n" + body.replace("\n\n", "\n### Other\n"),
        ["cache", "128"],
        scope="section",
    )
    assert "fact:claim" in unit_failures(
        "### Settings\n" + body.replace("\n\n", "\n\n" + "padding\n" * 1200 + "\n"),
        ["cache", "128"],
        scope="section",
    )


def test_section_scope_keeps_clause_reject():
    assert not unit_failures(
        "### Settings\nOther default needs no update.\n\nCache default is 128.",
        ["cache", "128"],
        scope="section",
        reject=["no update"],
    )
    assert "fact:claim" in unit_failures(
        "### Settings\nCache default is 128.\n\nCache needs no update.",
        ["cache", "128"],
        scope="section",
        reject=["no update"],
    )


@pytest.mark.parametrize("verb", ["specifies the worker", "controls worker dispatch"])
def test_declared_key_introduces_punctuated_enum(verb):
    text = f"`roles.worker` {verb}.\n- plan,\n- codex;\n- claude-code."
    assert not failures(
        text,
        known_config_keys=["roles.worker"],
        required_facts={
            "values": {
                "scope": "unit",
                "patterns": [r"roles\.worker", r"plan codex claude-code"],
            }
        },
        forbidden_claims={},
    )


def test_escaped_table_pipe_keeps_default_column_aligned():
    text = "| Key | Values | Default |\n| --- | --- | --- |\n| cache.mode | low\\|high | 20 |"
    assert not failures(
        text,
        known_config_keys=["cache.mode"],
        required_facts={
            "default": {
                "scope": "unit",
                "default_column": True,
                "patterns": [r"cache\.mode default 20"],
            }
        },
        forbidden_claims={},
    )


@pytest.mark.parametrize("condition", ["if", "when", "with", "otherwise"])
def test_outcome_conditions_do_not_join_swapped_codes(condition):
    assert "fact:claim" in unit_failures(
        f"exit codes 0 on invalid and 2 {condition} success",
        [r"0.*success"],
    )
