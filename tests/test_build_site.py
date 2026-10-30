"""PDK-04: the static leaderboard site builds deterministically from results/ only."""

from __future__ import annotations

import re
import shutil
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import build_site
import leaderboard as lb
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "leaderboard"
# The repository name the site must never link: it was never created.
RETIRED_REPOSITORY = (
    "laceyenterprises/paddock"  # publication-scan: allow(private-repo): absent
)
#: An href or src that is root-relative ("/x"); "//" and absolute URLs do not match.
ROOT_RELATIVE = re.compile(r"""\b(?:href|src)\s*=\s*["']?/(?!/)""", re.IGNORECASE)
ABSOLUTE_LINK = re.compile(
    r"""\b(?:href|src)\s*=\s*["']?(https?:)?//([^"'\s>]*)""", re.IGNORECASE
)


def _row(page: str, label: str) -> str:
    """The board table row whose model cell is ``label``."""
    rows = [row for row in page.split("<tr>") if f"<b>{label}</b>" in row]
    assert rows, label
    return rows[0]


def test_site_builds_deterministically_from_fixtures(tmp_path: Path):
    first = build_site.build(FIXTURE)
    moved = tmp_path / "elsewhere" / "results"
    shutil.copytree(FIXTURE, moved)
    assert build_site.build(FIXTURE) == first
    assert build_site.build(moved) == first
    assert set(first) == (
        {name for name, _ in build_site.PAGES}
        | {"paddock.css", "favicon.svg"}
        | {
            build_site.board_filename(b)
            for b in lb.boards(lb.load_results(FIXTURE).records)
        }
    )


def test_site_from_gzip_matches_site_from_plain_results(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURE, results)
    expected = build_site.build(results)
    for path in results.glob("*.json"):
        document = lb.load_results(results).documents[path.name]
        lb.write_document(path.with_suffix(".json.gz"), document)
        path.unlink()
    assert build_site.build(results) == expected


def _is_tag_manager_link(scheme: str, target: str) -> bool:
    return scheme == "https:" and target.startswith("www.googletagmanager.com/")


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://www.googletagmanager.com/gtm.js?id=GTM-P52SNL3D", True),
        ("https://www.googletagmanager.com/ns.html?id=GTM-P52SNL3D", True),
        ("http://www.googletagmanager.com/gtm.js", False),
        ("//www.googletagmanager.com/gtm.js", False),
        ("https://www.googletagmanager.com.example/gtm.js", False),
        ("https://www.googletagmanager.com@other.example/gtm.js", False),
        ("https://www.google-analytics.com/analytics.js", False),
    ],
)
def test_tag_manager_external_link_allowance(url, allowed):
    scheme, target = ABSOLUTE_LINK.search(f'src="{url}"').groups()
    assert _is_tag_manager_link(scheme, target) is allowed


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
                font_link = scheme == "https:" and (
                    target.startswith("fonts.googleapis.com/")
                    or target == "fonts.gstatic.com"
                )
                assert (
                    public_link
                    or vendor_evidence
                    or font_link
                    or _is_tag_manager_link(scheme, target)
                ), f"{name}: {target}"
            canonical = re.findall(r'rel="canonical" href="([^"]+)"', page)
            assert canonical in ([], ["https://agentpaddock.ai/"]), name
        assert (
            "https://github.com/laceyenterprises/agentpaddock"
            in files["methodology.html"]
        )


def test_a_result_without_a_price_source_renders_cost_unknown():
    index = build_site.build(FIXTURE)["board-coding-v1.html"]
    unpriced = _row(index, "nova/coder-unpriced")
    unknown_source = _row(index, "local/coder-tiny")
    for row in (unpriced, unknown_source):
        assert '<td class="cost">unknown</td>' in row
        assert "$" not in row
    assert "$0.72" in _row(index, "acme/coder-large")
    assert '<span class="tag">invoice</span>' in _row(index, "bolt/coder-small")
    assert '<span class="tag">list</span>' in _row(index, "acme/coder-large")
    assert "not measured (0/1)" in build_site.build(FIXTURE)["board-review-v1.html"]
    assert "list = published-rate · invoice = provider-invoice-reconciled" in index


def test_pages_show_boards_matrix_tasks_changelog_and_methodology():
    files = build_site.build(FIXTURE)
    index = files["index.html"]
    assert "<h3>review</h3>" in index and "task version 2" in index
    assert "review · task version 1" in index
    assert "95% Wilson" in index
    assert "not measured" in files["board-review-v1.html"]
    assert "beta-cli" in index.split('class="caveat"')[1].split("</p>")[0]
    assert "alpha-cli" not in index.split('class="caveat"')[1].split("</p>")[0]
    matrix = files["matrix.html"]
    assert "Tokens per pass" in matrix and '<path class="bar"' in matrix
    assert "coding: 2/3" in matrix
    tasks = files["tasks.html"]
    assert "coding-alpha" in tasks and "discriminates" in tasks
    changelog = files["changelog.html"]
    assert (
        "FIXGRADE-01" in changelog
        and "1 rows tagged <code>pre-FIXGRADE-01</code>" in changelog
    )
    methodology = files["methodology.html"]
    assert (
        "Price table 2026-01-02" in methodology
        and "provider-invoice-reconciled" in methodology
    )
    assert 'id="fairness"' in methodology and "Vendor-default tools." in methodology


def test_committed_results_chart_shows_measured_tokens():
    matrix = build_site.build(lb.DEFAULT_RESULTS)["matrix.html"]
    assert "No board row in results/ has complete token measurements yet." not in matrix
    assert 'aria-label="Tokens per pass by board row"' in matrix
    assert '<path class="bar"' in matrix


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
    assert (
        "<script>" not in page.split('<main class="wrap">', 1)[1].split("</main>", 1)[0]
    )
    assert "&lt;script&gt;&quot;finding&quot;&lt;/script&gt;" in page
    assert (
        'href="https://vendor.example/defaults?a=1&amp;b=2" rel="noopener noreferrer"'
        in page
    )
    assert '/blob/main/lib/python/gym/standalone.py#L82"' in page
    assert "records[model.reasoning=&lt;unknown&gt;]" in page
    assert 'href="javascript:' not in page
    audit.update(status="passed", sign_off="Fixture sign-off")
    assert "alpha-cli" not in build_site.fairness_banner(results)


@pytest.mark.parametrize(
    "stale_file", ["index.html", "paddock.css", "favicon.svg", "board-review-v2.html"]
)
def test_cli_writes_then_checks(tmp_path: Path, capsys, stale_file):
    out = tmp_path / "site"
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out)]) == 0
    assert (
        build_site.main(["--results", str(FIXTURE), "--out", str(out), "--check"]) == 0
    )
    (out / stale_file).write_text("stale", encoding="utf-8")
    assert (
        build_site.main(["--results", str(FIXTURE), "--out", str(out), "--check"]) == 1
    )
    assert stale_file in capsys.readouterr().err


def test_cli_refuses_invalid_results(tmp_path: Path, capsys):
    broken = tmp_path / "results"
    shutil.copytree(FIXTURE, broken)
    (broken / "2026-01-03-fixture-run.json").write_text("{}", encoding="utf-8")
    assert (
        build_site.main(["--results", str(broken), "--out", str(tmp_path / "site")])
        == 1
    )
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


@pytest.mark.parametrize(
    "source",
    [
        "scripts/../../leaderboard.py:1",
        "scripts/./leaderboard.py:1",
        "scripts//leaderboard.py:1",
        "scripts/leaderboard.py:0",
        "scripts/leaderboard.py:1junk",
    ],
)
def test_fairness_source_does_not_link_unnormalized_repository_paths(source):
    assert (
        build_site.fairness_source(source) == f"<code>{build_site.esc(source)}</code>"
    )


def test_fairness_repository_link_preserves_selector_context():
    source = "results/fixture.json:45 records[harness.name=codex].model.provider_id"
    rendered = build_site.fairness_source(source)
    assert '/blob/main/results/fixture.json#L45"' in rendered
    assert source in rendered


class Elements(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.items = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def test_every_page_has_tag_manager_consent_defaults_and_disclosure(tmp_path):
    consent = """<!-- Google consent defaults: analytics off in the EEA, UK and Switzerland until consent; ad storage off everywhere -->
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'denied',region:['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH']});
gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'granted'});</script>"""
    loader = """<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-P52SNL3D');</script>
<!-- End Google Tag Manager -->"""
    noscript = """<!-- Google Tag Manager (noscript) -->
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-P52SNL3D" height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
<!-- End Google Tag Manager (noscript) -->"""
    disclosure = (
        "This site uses Google Analytics to count visits. "
        "Analytics is off by default for visitors in the EEA, the UK and Switzerland."
    )
    out = tmp_path / "site"
    assert build_site.GTM_CONTAINER_ID == "GTM-P52SNL3D"
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out)]) == 0
    pages = sorted(out.glob("*.html"))
    assert {page.name for page in pages} == {
        name for name in build_site.build(FIXTURE) if name.endswith(".html")
    }
    for path in pages:
        page = path.read_text(encoding="utf-8")
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        footer = page.split('<footer class="footer">', 1)[1].split("</footer>", 1)[0]
        for block in (consent, loader, noscript):
            assert page.count(block) == 1, path.name
        assert head.index(consent) < head.index(loader)
        assert head.index("<script>") == head.index(consent) + consent.index("<script>")
        assert page.split("<body>", 1)[1].lstrip().startswith(noscript), path.name
        assert page.count("GTM-P52SNL3D") == 2, path.name
        assert page.count("www.googletagmanager.com/gtm.js") == 1, path.name
        assert page.count("<noscript>") == page.count("<iframe") == 1, path.name
        assert footer.count(disclosure) == 1, path.name
        assert "gtag/js" not in page and "googletagmanager.com/gtag" not in page


def test_every_local_link_and_anchor_resolves():
    files = build_site.build(FIXTURE)
    elements = {
        name: Elements(text).items
        for name, text in files.items()
        if name.endswith(".html")
    }
    for name, items in elements.items():
        for _, attrs in items:
            if "href" not in attrs:
                continue
            link = urlsplit(attrs["href"])
            if link.scheme:
                continue
            target = link.path or name
            assert target in files, (name, target)
            if link.fragment:
                assert any(a.get("id") == link.fragment for _, a in elements[target]), (
                    name,
                    link,
                )


def test_brand_assets_and_navigation_on_every_page():
    files = build_site.build(FIXTURE)
    for name, page in files.items():
        if not name.endswith(".html"):
            continue
        elements = Elements(page).items
        assert page.count('<symbol id="broadcast"') == 1
        assert "translate(9 0) skewX(-10)" in page
        for color in ("#007BFF", "#00C27A", "#E0703A"):
            assert color in page
        assert 'fill="var(--ink)"' in page and page.count('fill="var(--paper)"') == 3
        assert '<link rel="icon" href="favicon.svg" type="image/svg+xml">' in page
        assert '<link rel="stylesheet" href="paddock.css">' in page
        assert 'aria-label="Paddock home"' in page and "PADDOCK" in page
        assert page.count("fonts.googleapis.com/css2?") == 1 and "display=swap" in page
        assert page.count("<script") == 2 and "<style" not in page
        assert "<h1>" in page and page.count("<h1>") == 1
        for other, _ in build_site.PAGES:
            assert f'href="{other}"' in page
        current = [a["href"] for t, a in elements if t == "a" and "aria-current" in a]
        assert current == ["index.html" if name.startswith("board-") else name]
        assert f"{build_site.REPO_URL}/blob/main/LICENSE" in page
        assert "© 2026 Lacey Enterprises LLC" in page
    favicon = files["favicon.svg"]
    assert "prefers-color-scheme: dark" in favicon
    assert "skewX(-10)" in favicon
    assert all(color in favicon for color in ("#007BFF", "#00C27A", "#E0703A"))


def test_overview_selects_current_boards_and_preserves_old_anchors():
    results = lb.load_results(FIXTURE)
    files = build_site.build(FIXTURE)
    index = files["index.html"]
    current, earlier = index.split('<h2 id="current-boards">')[1].split(
        '<h2 id="earlier-boards">'
    )
    assert "<table" not in index
    assert "<h1>Leaderboard</h1>" in index and build_site.DESCRIPTION in index
    assert f"<b>{len(results.records)}</b><span>result rows</span>" in index
    boards = lb.boards(results.records)
    for board in boards:
        anchor = build_site.board_anchor(board)
        assert index.count(f'id="{anchor}"') == 1
        latest = max(b["task_version"] for b in boards if b["suite"] == board["suite"])
        section = current if board["task_version"] == latest else earlier
        assert f'id="{anchor}"' in section
        assert f'href="{build_site.board_filename(board)}"' in section
    for card in re.findall(r'<article class="card".*?</article>', current):
        anchor = re.search(r'id="([^"]+)"', card)[1]
        board = next(b for b in boards if build_site.board_anchor(b) == anchor)
        assert card.count('<li class="tower-row">') == min(5, len(board["rows"]))


def test_each_board_page_preserves_its_own_values_and_tasks():
    results = lb.load_results(FIXTURE)
    files = build_site.build(FIXTURE)
    for board in lb.boards(results.records):
        page = files[build_site.board_filename(board)]
        assert page.count('<table class="tower-table">') == 1
        assert 'viewBox="0 0 120 14"' in page
        assert page.count("<section id=") == 1
        assert f"<h1>{board['suite']}</h1>" in page
        assert f"Board · task version {board['task_version']}" in page
        assert "<details><summary>" in page
        assert build_site.COMPARABILITY in page
        for task in board["tasks"]:
            assert f"<li>{task}</li>" in page
        rows = page.split("<tbody>")[1].split("</tbody>")[0].split("<tr>")[1:]
        assert len(rows) == len(board["rows"])
        for html_row, row in zip(rows, board["rows"]):
            assert (
                f"<b>{build_site.esc(build_site.model_label(row['key']))}</b>"
                in html_row
            )
            assert build_site.finish(row) in html_row
            low, high = row["ci"]
            assert f"{build_site.pct(low)}–{build_site.pct(high)}" in html_row
            assert f"<td>{row['tasks']}</td>" in html_row
            assert build_site.tokens_cell(row) in html_row
            assert build_site.cost_cell(row) in html_row
            if row["mean_score"] is not None:
                assert f"<td>{row['mean_score']:.2f}</td>" in html_row
            assert f">{sum(row['excluded'].values())}</td>" in html_row
            assert f'title="{build_site.excluded_text(row["excluded"])}"' in html_row
        headers = [a for t, a in Elements(page).items if t == "th"]
        assert len(headers) == 10 and all(a.get("scope") == "col" for a in headers)


def test_matrix_shades_each_suite_cell_by_its_pass_rate():
    results = lb.load_results(FIXTURE)
    page = build_site.matrix_page(results)
    assert '<table class="grid matrix">' in page and '<td class="empty">' in page
    assert 'aria-label="No rows">·' in page
    for by_suite in lb.matrix(results.records)["cells"].values():
        for (suite, version), summary in by_suite.items():
            shade = round((summary["rate"] or 0) * 24)
            label = f"{suite}{'' if version == 1 else f' v{version}'}"
            assert (
                f'style="--shade:{shade}%">{label}: {summary["passed"]}/{summary["n"]}'
                in page
            )


def test_committed_results_overview_size():
    assert (
        len(build_site.build(lb.DEFAULT_RESULTS)["index.html"].encode("utf-8"))
        < 150_000
    )


def test_board_filenames_cannot_escape_output_directory():
    board = {"suite": "../synthetic suite", "task_version": 1}
    name = build_site.board_filename(board)
    assert "/" not in name and name.startswith("board-") and name.endswith("-v1.html")
