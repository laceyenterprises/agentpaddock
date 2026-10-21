"""PDK-04: the static leaderboard site builds deterministically from results/ only."""

from __future__ import annotations

import re
import shutil
from copy import deepcopy
from pathlib import Path

import build_site
import leaderboard as lb
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "leaderboard"
# The repository name the site must never link: it was never created.
RETIRED_REPOSITORY = "laceyenterprises/paddock"  # publication-scan: allow(private-repo): absent
#: An href or src that is root-relative ("/x"); "//" and absolute URLs do not match.
ROOT_RELATIVE = re.compile(r"""\b(?:href|src)\s*=\s*["']?/(?!/)""", re.IGNORECASE)
ABSOLUTE_LINK = re.compile(r"""\b(?:href|src)\s*=\s*["']?(https?:)?//([^"'\s>]*)""", re.IGNORECASE)


def _row(page: str, label: str) -> str:
    """The board table row whose model cell is ``label``."""
    rows = [row for row in page.split("<tr>") if f"<td>{label}</td>" in row]
    assert rows, label
    return rows[0]


def test_site_builds_deterministically_from_fixtures(tmp_path: Path):
    first = build_site.build(FIXTURE)
    moved = tmp_path / "elsewhere" / "results"
    shutil.copytree(FIXTURE, moved)
    assert build_site.build(FIXTURE) == first
    assert build_site.build(moved) == first
    assert set(first) == {name for name, _ in build_site.PAGES}


def test_built_html_links_only_relatively_and_to_the_public_names():
    for files in (build_site.build(FIXTURE), build_site.build(lb.DEFAULT_RESULTS)):
        for name, page in files.items():
            assert "github.io" not in page, name
            assert RETIRED_REPOSITORY not in page, name
            assert not ROOT_RELATIVE.search(page), name
            for scheme, target in ABSOLUTE_LINK.findall(page):
                assert scheme, f"{name}: protocol-relative link //{target}"
                public_link = target == "agentpaddock.ai/" or target.startswith(
                    "github.com/laceyenterprises/agentpaddock"
                )
                vendor_evidence = name == "methodology.html" and target.startswith(
                    (
                        "developers.openai.com/codex/",
                        "code.claude.com/docs/en/",
                        "opencode.ai/docs/",
                        "www.antigravity.google/docs/",
                        "raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/",
                    )
                )
                assert public_link or vendor_evidence, f"{name}: {target}"
            canonical = re.findall(r'rel="canonical" href="([^"]+)"', page)
            assert canonical in ([], ["https://agentpaddock.ai/"]), name
        assert "https://github.com/laceyenterprises/agentpaddock" in files["methodology.html"]


def test_a_result_without_a_price_source_renders_cost_unknown():
    index = build_site.build(FIXTURE)["index.html"]
    unpriced = _row(index, "nova/coder-unpriced")
    unknown_source = _row(index, "local/coder-tiny")
    for row in (unpriced, unknown_source):
        assert "<td>unknown</td>" in row
        assert "$" not in row
    assert "$0.72" in _row(index, "acme/coder-large")
    assert "provider-invoice-reconciled" in _row(index, "bolt/coder-small")


def test_pages_show_boards_matrix_tasks_changelog_and_methodology():
    files = build_site.build(FIXTURE)
    index = files["index.html"]
    assert "review · task version 2" in index and "review · task version 1" in index
    assert "95% Wilson" in index
    assert "beta-cli" in index.split('class="banner"')[1].split("</p>")[0]
    assert "alpha-cli" not in index.split('class="banner"')[1].split("</p>")[0]
    matrix = files["matrix.html"]
    assert "Tokens per pass" in matrix and '<path class="bar"' in matrix
    assert "coding: 2/3" in matrix
    tasks = files["tasks.html"]
    assert "coding-alpha" in tasks and "discriminates" in tasks
    changelog = files["changelog.html"]
    assert "FIXGRADE-01" in changelog and "1 rows tagged <code>pre-FIXGRADE-01</code>" in changelog
    methodology = files["methodology.html"]
    assert "Price table 2026-01-02" in methodology and "provider-invoice-reconciled" in methodology
    assert 'id="fairness"' in methodology and "Vendor-default tools." in methodology


def test_committed_results_chart_says_tokens_are_not_measured():
    matrix = build_site.build(lb.DEFAULT_RESULTS)["matrix.html"]
    assert "No board row in results/ has complete token measurements yet." in matrix


def test_committed_fairness_findings_and_sources_are_rendered():
    results = lb.load_results()
    page = build_site.methodology_page(results)
    for audit in results.fairness["audits"]:
        assert f"{audit['harness']}: checklist evidence" in page
        for item in audit["evidence"]:
            assert f"<td>{item['id']}</td><td>{item['status']}</td>" in page
            assert build_site.esc(item["notes"]) in page
            for source in item["sources"]:
                assert build_site.esc(source) in page
    assert "maintainer sign-off" in page


def test_audited_without_sign_off_remains_provisional_and_escapes_evidence():
    results = deepcopy(lb.load_results(FIXTURE))
    audit = results.fairness["audits"][0]
    audit.update(
        status="audited",
        sign_off=None,
        evidence=[
            {
                "id": "tools",
                "status": "pass",
                "notes": '<script>"finding"</script>',
                "sources": [
                    "https://vendor.example/defaults?a=1&b=2",
                    "lib/python/gym/standalone.py:82",
                    "records[model.reasoning=<unknown>]",
                    "javascript:alert(1)",
                ],
            }
        ],
    )
    assert "alpha-cli" in build_site.fairness_banner(results)
    page = build_site.methodology_page(results)
    assert "<script>" not in page
    assert "&lt;script&gt;&quot;finding&quot;&lt;/script&gt;" in page
    assert 'href="https://vendor.example/defaults?a=1&amp;b=2"' in page
    assert '/blob/main/lib/python/gym/standalone.py#L82"' in page
    assert "records[model.reasoning=&lt;unknown&gt;]" in page
    assert 'href="javascript:' not in page
    audit.update(status="passed", sign_off="Fixture sign-off")
    assert "alpha-cli" not in build_site.fairness_banner(results)


def test_cli_writes_then_checks(tmp_path: Path, capsys):
    out = tmp_path / "site"
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out)]) == 0
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out), "--check"]) == 0
    (out / "index.html").write_text("stale", encoding="utf-8")
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out), "--check"]) == 1
    assert "index.html" in capsys.readouterr().err


def test_cli_refuses_invalid_results(tmp_path: Path, capsys):
    broken = tmp_path / "results"
    shutil.copytree(FIXTURE, broken)
    (broken / "2026-01-03-fixture-run.json").write_text("{}", encoding="utf-8")
    assert build_site.main(["--results", str(broken), "--out", str(tmp_path / "site")]) == 1
    assert "does not validate" in capsys.readouterr().err
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize("name", [name for name, _ in build_site.PAGES])
def test_every_page_links_to_every_other(name: str):
    page = build_site.build(FIXTURE)[name]
    for other, _ in build_site.PAGES:
        assert f'href="{other}"' in page


@pytest.mark.parametrize("values", [(0, 0), (1, 1000)])
def test_short_token_bars_have_nonnegative_forward_segments(monkeypatch, values):
    results = lb.load_results(FIXTURE)
    board = lb.boards(results.records)[0]
    rows = [dict(board["rows"][0], tokens_per_pass=value) for value in values]
    monkeypatch.setattr(lb, "boards", lambda records: [dict(board, rows=rows)])
    chart = build_site.tokens_chart(results)
    forward = re.findall(r'd="M[0-9.,]+h(-?[0-9.]+)a', chart)
    assert len(forward) == 2
    assert all(float(segment) >= 0 for segment in forward)
