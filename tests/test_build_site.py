"""PDK-04: the static leaderboard site builds deterministically from results/ only."""

from __future__ import annotations

import html
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import zlib
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import build_site
import leaderboard as lb
import pytest
import render_social_card
from paddock_site import data as site_data
from paddock_site import layout

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


#: The committed card template, captured before any test swaps in a synthetic one.
CARD_TEMPLATE = build_site.CARD_TEMPLATE
SYNTHETIC_CARD = (
    "<!doctype html><p>synthetic card</p><svg><!-- broadcast-mark --></svg>\n"
)


def _png(width: int, height: int) -> bytes:
    """A blank one-bit greyscale PNG built with the standard library."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    rows = (b"\0" + b"\0" * ((width + 7) // 8)) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def synthetic_card(tmp_path_factory, monkeypatch) -> Path:
    """A synthetic card template and a PNG stamped with its source's SHA-256."""
    card = tmp_path_factory.mktemp("card")
    template = card / "social_card.html"
    template.write_text(SYNTHETIC_CARD, encoding="utf-8")
    monkeypatch.setattr(build_site, "CARD_TEMPLATE", template)
    png = card / "social-card.png"
    png.write_bytes(
        build_site.stamp_png(
            _png(*build_site.CARD_SIZE),
            build_site.CARD_KEY,
            build_site.card_source_hash(build_site.card_source()),
        )
    )
    monkeypatch.setattr(build_site, "CARD_PNG", png)
    return card


def _text_files(files):
    return [(name, text) for name, text in files.items() if isinstance(text, str)]


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
        | {
            "paddock.css",
            "paddock.js",
            "standings.js",
            "favicon.svg",
            "social-card.png",
        }
        | {
            build_site.board_filename(b)
            for b in lb.boards(lb.load_results(FIXTURE).records)
        }
        | set(site_data.build(lb.load_results(FIXTURE), FIXTURE.parent / "tasks"))
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
        for name, page in _text_files(files):
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
    assert "Also: task version 1 · 1 task · 1 entry →" in index
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
    "stale_file",
    [
        "index.html",
        "paddock.css",
        "favicon.svg",
        "social-card.png",
        "board-review-v2.html",
    ],
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


def _resolve(name: str, link: str) -> str:
    """The output file a relative ``link`` on page ``name`` points at."""
    path = urlsplit(link).path
    if not path:
        return name
    return posixpath.normpath(posixpath.join(posixpath.dirname(name), path))


def _local_links(name: str, page: str) -> set[str]:
    return {
        _resolve(name, attrs["href"])
        for tag, attrs in Elements(page).items
        if tag == "a" and "href" in attrs and not urlsplit(attrs["href"]).scheme
    }


@pytest.mark.parametrize("name", [name for name, _ in build_site.PAGES])
def test_every_page_links_to_every_other(name: str):
    page = build_site.build(FIXTURE)[name]
    assert {other for other, _ in build_site.PAGES} <= _local_links(name, page)


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
    consent = """<!-- Google consent defaults: analytics storage denied in the EEA, UK, Switzerland and listed territories; no consent UI; ad storage off everywhere -->
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'granted'});
gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'denied',region:['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH','AX','GF','GP','MQ','RE','YT','MF','GI','JE','GG','IM']});</script>"""
    loader = """<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-P52SNL3D');</script>
<!-- End Google Tag Manager -->"""
    disclosure = (
        "This site uses Google Analytics to count visits. "
        "Google Tag Manager loads on every page; analytics cookies are disabled for visitors in the EEA, the UK, Switzerland and listed territories."
    )
    out = tmp_path / "site"
    assert build_site.GTM_CONTAINER_ID == "GTM-P52SNL3D"
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out)]) == 0
    pages = sorted(out.rglob("*.html"))
    assert {page.relative_to(out).as_posix() for page in pages} == {
        name for name in build_site.build(FIXTURE) if name.endswith(".html")
    }
    for path in pages:
        page = path.read_text(encoding="utf-8")
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        footer = page.split('<footer class="footer">', 1)[1].split("</footer>", 1)[0]
        for block in (consent, loader):
            assert page.count(block) == 1, path.name
        assert (
            head.index('<meta name="viewport"')
            < head.index("<title>")
            < head.index(consent)
            < head.index(loader)
        )
        assert head.index("<script>") == head.index(consent) + consent.index("<script>")
        assert page.count("GTM-P52SNL3D") == 1, path.name
        assert page.count("www.googletagmanager.com/gtm.js") == 1, path.name
        assert "<noscript>" not in page and "<iframe" not in page, path.name
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
            target = _resolve(name, attrs["href"])
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
        root = layout.up(name)
        assert (
            f'<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">' in page
        )
        assert f'<link rel="stylesheet" href="{root}paddock.css">' in page
        assert f'<script src="{root}paddock.js" defer></script>' in page
        assert 'aria-label="Agent Paddock home"' in page and "PADDOCK" in page
        assert page.count("fonts.googleapis.com/css2?") == 1 and "display=swap" in page
        # A page with its own view switches (PDW-07) loads one shared script for them.
        own = f'<script src="{root}{standings_pages.SCRIPT_NAME}" defer></script>'
        assert page.count(own) == (1 if name == "standings/index.html" else 0), name
        assert page.count("<script") == 3 + page.count(own) and "<style" not in page
        assert "<h1>" in page and page.count("<h1>") == 1
        assert {other for other, _ in build_site.PAGES} <= _local_links(name, page)
        # Every link marked current points at the page itself (a board page
        # belongs to the leaderboard), and the page is marked somewhere.
        current = {
            _resolve(name, a["href"])
            for t, a in elements
            if t == "a" and a.get("aria-current") == "page"
        }
        assert current == {"index.html" if name.startswith("board-") else name}
        assert f"{build_site.REPO_URL}/blob/main/LICENSE" in page
        assert "© 2026 Lacey Enterprises LLC" in page
    favicon = files["favicon.svg"]
    assert "prefers-color-scheme: dark" in favicon
    assert "skewX(-10)" in favicon
    assert all(color in favicon for color in ("#007BFF", "#00C27A", "#E0703A"))


def test_overview_features_one_board_per_suite_and_preserves_old_anchors():
    results = lb.load_results(FIXTURE)
    files = build_site.build(FIXTURE)
    index = files["index.html"]
    current = index.split('<h2 id="current-boards">')[1].split("</section>")[0]
    assert "<table" not in index
    assert "<h1>Leaderboard</h1>" in index and build_site.DESCRIPTION in index
    assert index.count(build_site.TASK_VERSIONS) == 1
    assert f"<b>{len(results.records)}</b><span>result rows</span>" in index
    boards = lb.boards(results.records)
    cards = re.findall(r'<article class="card".*?</article>', current)
    assert len(cards) == len({b["suite"] for b in boards})
    for board in boards:
        anchor = build_site.board_anchor(board)
        assert index.count(f'id="{anchor}"') == 1
        card = next(c for c in cards if f"<h3>{board['suite']}</h3>" in c)
        assert f'id="{anchor}"' in card
        assert f'href="{build_site.board_filename(board)}"' in card
    for card in cards:
        anchor = re.search(r'id="([^"]+)"', card)[1]
        board = next(b for b in boards if build_site.board_anchor(b) == anchor)
        assert card.count('<li class="tower-row">') == min(5, len(board["rows"]))
    assert "Earlier task versions" not in index and "earlier-boards" not in index


def _three_board_suite(tmp_path: Path) -> Path:
    """The fixture plus a synthetic suite with boards v1 (5 tasks, 1 entry),
    v2 (5 tasks, 2 entries) and v3 (1 task, 1 entry)."""
    results = tmp_path / "results"
    shutil.copytree(FIXTURE, results)
    (name,) = [p.name for p in FIXTURE.glob("*.json")]
    document = deepcopy(lb.load_results(FIXTURE).documents[name])
    first, other = document["records"][0], document["records"][3]
    plan = [(1, 5, (first,)), (2, 5, (first, other)), (3, 1, (first,))]
    records = []
    for version, tasks, templates in plan:
        for task in range(1, tasks + 1):
            for template in templates:
                record = deepcopy(template)
                record.update(
                    run_id=f"triad-{len(records):03d}",
                    suite="triad",
                    task=f"triad-{task}",
                    task_version=version,
                )
                records.append(record)
    document.update(id="triad-run", records=records)
    lb.write_document(results / f"{document['date']}-triad-run.json", document)
    return results


def test_featured_board_has_the_most_tasks_and_lists_the_others(tmp_path: Path):
    results = _three_board_suite(tmp_path)
    triad = [
        b for b in lb.boards(lb.load_results(results).records) if b["suite"] == "triad"
    ]
    assert build_site.featured_board(triad)["task_version"] == 2
    files = build_site.build(results)
    assert build_site.build(results) == files
    index = files["index.html"]
    (card,) = [
        c
        for c in re.findall(r'<article class="card".*?</article>', index)
        if "<h3>triad</h3>" in c
    ]
    assert card.startswith('<article class="card" id="triad-v2">')
    assert '<p class="kicker">task version 2 · 5 tasks · 2 entries</p>' in card
    also = re.findall(
        r'<li><a id="([^"]+)" href="([^"]+)">Also: ([^<]+)</a></li>', card
    )
    assert also == [
        ("triad-v3", "board-triad-v3.html", "task version 3 · 1 task · 1 entry →"),
        ("triad-v1", "board-triad-v1.html", "task version 1 · 5 tasks · 1 entry →"),
    ]
    assert card.index('<ul class="also">') > card.index('<div class="tower">')
    for version in (1, 2, 3):
        assert index.count(f'id="triad-v{version}"') == 1
    assert "<details><summary>1 task</summary>" in files["board-triad-v3.html"]
    assert "<details><summary>5 tasks</summary>" in files["board-triad-v1.html"]
    assert "Earlier task versions" not in index


def test_featured_board_tie_breaks():
    def board(version, tasks, rows):
        return {"task_version": version, "tasks": ["t"] * tasks, "rows": [{}] * rows}

    assert (
        build_site.featured_board([board(1, 5, 9), board(2, 5, 1)])["task_version"] == 2
    )
    assert (
        build_site.featured_board([board(1, 6, 1), board(2, 5, 9)])["task_version"] == 1
    )
    # Duplicate versions exercise the defensive fallback, outside lb.boards output.
    a, b = board(1, 5, 1), board(1, 5, 2)
    assert (
        build_site.featured_board([b, a]) is b
        and build_site.featured_board([a, b]) is b
    )
    assert build_site.plural(1, "task") == "1 task"
    assert build_site.plural(0, "entry", "entries") == "0 entries"


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


#: Where a generated page may say "paddock" without the product name "Agent Paddock".
PRODUCT_NAME_ALLOWLIST = re.compile(
    r"Agent\s+Paddock"  # the product name itself
    r'|>AGENT</span><span class="wordmark-name">PADDOCK<'  # the stacked lockup
    r"|\bpaddock (?:run|show|baseline|lock)\b"  # the command
    r"|\bpaddock\.css\b"  # the stylesheet path
    r"|\bpaddock\.js\b"  # the menu script path
    r'|<meta name="paddock-site" content="pdw-1">'  # the activation marker
    r"|agentpaddock"  # the domain, repository and distribution
    r"|\bPADDOCK_[A-Z0-9_]+"  # environment variables
    r'|"paddock-site-[a-z]+/v\d+"'  # site data schema ids (PDW-03)
    r'|"paddock-(?:run|bench)"'  # the trust level and its origin, as JSON values
    r"|>Independent and open source: about the paddock<"  # the results note (PDW-07)
)


def _stray_product_names(page: str) -> list[str]:
    return re.findall(
        r"(?i).{0,30}paddock.{0,30}", PRODUCT_NAME_ALLOWLIST.sub("", page)
    )


@pytest.mark.parametrize(
    ("text", "stray"),
    [
        ("the eval bench that preceded Agent Paddock", False),
        ("run <code>paddock run --suite coding</code>", False),
        ('<a href="https://agentpaddock.ai/">', False),
        ('<link rel="stylesheet" href="paddock.css">', False),
        ("set PADDOCK_NO_DOCKER=1", False),
        ("the eval bench that preceded Paddock", True),
        ('aria-label="Paddock home"', True),
        ("<title>Tasks · Paddock</title>", True),
        ('<span class="wordmark">PADDOCK</span>', True),
        ("the paddock commit", True),
    ],
)
def test_product_name_allowlist(text, stray):
    assert bool(_stray_product_names(text)) is stray


def test_no_page_names_the_product_bare_paddock():
    for name, page in _text_files(build_site.build(FIXTURE)):
        assert not _stray_product_names(page), (name, _stray_product_names(page))


#: Pages whose hero follows its prototype's eyebrow (PDW-07).
PROTOTYPE_HEROES = {"standings/index.html", "entries/index.html"}


def test_every_page_title_and_eyebrow_names_agent_paddock():
    files = build_site.build(FIXTURE)
    for name, page in files.items():
        if not name.endswith(".html"):
            continue
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        titles = re.findall(r"<title>(.*?)</title>", head)
        assert len(titles) == 1 and titles[0].endswith(" · Agent Paddock"), name
    for name, _ in build_site.PAGES:
        if name in PROTOTYPE_HEROES:
            # Pages rebuilt from a prototype carry its own eyebrow over the h1.
            assert re.search(r'<p class="st-eyebrow">.*?</p><h1>', files[name]), name
            continue
        assert '<span class="eyebrow">Agent Paddock</span><h1>' in files[name], name
    methodology = files["methodology.html"]
    assert "the eval bench that preceded Agent Paddock," in methodology
    assert "Agent Paddock commit it ran at" in methodology


def _css_rules(css: str) -> dict[str, str]:
    rules: dict[str, str] = {}
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        rules.setdefault(selector.strip(), "")
        rules[selector.strip()] += body
    return rules


def test_header_and_footer_render_the_one_stacked_lockup(monkeypatch):
    lockup = build_site.brand()
    assert re.fullmatch(
        r'<a class="brand" href="index.html" aria-label="Agent Paddock home">'
        r'<svg viewBox="0 0 100 100" aria-hidden="true"><use href="#broadcast"/></svg>'
        r'<span class="wordmark"><span class="wordmark-agent">AGENT</span>'
        r'<span class="wordmark-name">PADDOCK</span></span></a>',
        lockup,
    )
    for name, page in build_site.build(FIXTURE).items():
        if not name.endswith(".html"):
            continue
        # A nested page's lockup links home through its relative prefix.
        lockup = layout.brand(layout.up(name))
        header = page.split('<header class="nav">', 1)[1].split("</header>", 1)[0]
        footer = page.split('<footer class="footer">', 1)[1].split("</footer>", 1)[0]
        assert header.count(lockup) == 1 and footer.count(lockup) == 1, name
        assert page.count("wordmark-agent") == 2, name
    # Both renderings come from layout.brand(): replacing it replaces both.
    monkeypatch.setattr(layout, "brand", lambda root="": "<i>synthetic lockup</i>")
    page = build_site.build(FIXTURE)["index.html"]
    assert (
        page.count("<i>synthetic lockup</i>") == 2
        and "wordmark" not in page.split("</head>")[1]
    )


def test_lockup_styles_follow_the_brand_spec():
    style = layout.CSS
    rules = _css_rules(style)
    phone = _css_rules(
        style.split("@media (max-width: 640px) {", 1)[1].split("\n}\n", 1)[0]
    )

    def declares(rules: dict[str, str], selector: str, *declarations: str) -> None:
        body = " ".join(rules[selector].split())
        for declaration in declarations:
            assert declaration in body, (selector, declaration, body)

    declares(
        rules,
        ".wordmark",
        "flex-direction: column;",
        "align-items: flex-start;",
        "font-style: italic;",
        "line-height: .95;",
        '"Saira"',
    )
    declares(
        rules,
        ".wordmark-agent",
        "font-weight: 500;",
        "letter-spacing: .04em;",
        "font-size: 13px;",
    )
    declares(
        rules,
        ".wordmark-name",
        "font-weight: 800;",
        "letter-spacing: .01em;",
        "font-size: 21px;",
    )
    declares(rules, ".brand svg", "width: 34px;", "height: 34px;")
    declares(rules, ".footer .brand svg", "width: 46px;", "height: 46px;")
    declares(rules, ".footer .wordmark-agent", "font-size: 17px;")
    declares(rules, ".footer .wordmark-name", "font-size: 28px;")
    declares(phone, ".nav .brand svg", "width: 32px;", "height: 32px;")
    declares(phone, ".nav .wordmark-agent", "font-size: 12px;")
    declares(phone, ".nav .wordmark-name", "font-size: 19px;")
    # AGENT is 0.6 x PADDOCK at every size, within a pixel.
    for agent, name in ((13, 21), (17, 28), (12, 19)):
        assert abs(agent - name * 0.6) < 1
    # Both lines inherit the brand text colour (ink in the header and the light footer).
    for selector, body in rules.items():
        if "wordmark" in selector:
            assert "color" not in body, selector
            assert not re.search(r"--(muted|tower-mute|ink-2)", body), selector
    declares(rules, ".brand", "color: var(--ink);")
    declares(rules, ".footer .brand", "color: var(--ink);")
    page = build_site.build(FIXTURE)["index.html"]
    assert "family=Saira:ital,wght@1,500;1,800&amp;" in page
    assert page.count("fonts.googleapis.com/css2?") == 1


def _meta(page: str) -> list[tuple[str, str]]:
    head = page.split("<head>", 1)[1].split("</head>", 1)[0]
    return [
        (attrs.get("property") or attrs.get("name"), attrs["content"])
        for tag, attrs in Elements(head).items
        if tag == "meta"
        and ("property" in attrs or "name" in attrs)
        and attrs.get("name") != "viewport"
    ]


def test_every_page_carries_the_social_card_meta():
    files = build_site.build(FIXTURE)
    pages = [name for name in files if name.endswith(".html")]
    route_urls = {route.file: route.url for route in build_site.ROUTES}
    urls = set()
    for name in pages:
        page = files[name]
        meta = _meta(page)
        tags = dict(meta)
        assert len(tags) == len(meta), name
        title = html.unescape(re.search(r"<title>(.*?)</title>", page)[1])
        url = route_urls.get(name, "https://agentpaddock.ai/" + name)
        alt = build_site.CARD_ALT
        assert tags == {
            "paddock-site": "pdw-1",
            "og:type": "website",
            "og:site_name": "Agent Paddock",
            "og:title": title,
            "og:description": build_site.DESCRIPTION,
            "og:url": url,
            "og:image": "https://agentpaddock.ai/social-card.png",
            "og:image:width": "1200",
            "og:image:height": "630",
            "og:image:type": "image/png",
            "og:image:alt": alt,
            "twitter:card": "summary_large_image",
            "twitter:title": title,
            "twitter:description": build_site.DESCRIPTION,
            "twitter:image": "https://agentpaddock.ai/social-card.png",
            "twitter:image:alt": alt,
        }, name
        for value in tags.values():
            if "://" in value:
                assert value.startswith("https://agentpaddock.ai/"), (name, value)
        urls.add(tags["og:url"])
        # The tags sit in the head, after the title and before the Tag Manager.
        head = page.split("</head>", 1)[0]
        assert head.index("<title>") < head.index('property="og:type"')
        assert head.index('name="twitter:image:alt"') < head.index("<script>")
    assert len(urls) == len(pages)
    assert any(name.startswith("board-") for name in pages)


def test_social_card_png_is_written_at_card_size_and_deterministically(tmp_path):
    outs = [tmp_path / "first", tmp_path / "second"]
    for out in outs:
        assert build_site.main(["--results", str(FIXTURE), "--out", str(out)]) == 0
    data = (outs[0] / "social-card.png").read_bytes()
    assert data == build_site.CARD_PNG.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    assert struct.unpack(">II", data[16:24]) == (1200, 630)
    first = sorted(
        path.relative_to(outs[0]).as_posix()
        for path in outs[0].rglob("*")
        if path.is_file()
    )
    assert first == sorted(
        path.relative_to(outs[1]).as_posix()
        for path in outs[1].rglob("*")
        if path.is_file()
    )
    for name in first:
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes(), name


def test_committed_social_card_matches_its_source():
    assert build_site.card_problem(build_site.CARD_PNG.read_bytes()) == ""


@pytest.mark.parametrize("check", [False, True])
def test_missing_card_reports_how_to_render_it(tmp_path, capsys, synthetic_card, check):
    build_site.CARD_PNG.unlink()
    argv = ["--results", str(FIXTURE), "--out", str(tmp_path / "site")]
    assert build_site.main([*argv, *(["--check"] if check else [])]) == 1
    assert capsys.readouterr().err == (
        "social-card.png is missing; run python3 scripts/render_social_card.py\n"
    )
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize("length", range(8, 16))
def test_truncated_png_header_is_reported_as_invalid(length):
    png = build_site.PNG_SIGNATURE + b"\0" * (length - 8)
    assert build_site.card_problem(png) == "social-card.png is not a PNG"


def test_check_reports_a_card_rendered_from_another_source(
    tmp_path, capsys, monkeypatch, synthetic_card
):
    out = tmp_path / "site"
    argv = ["--results", str(FIXTURE), "--out", str(out)]
    assert build_site.main(argv) == 0
    assert "warning" not in capsys.readouterr().err
    assert build_site.main([*argv, "--check"]) == 0
    capsys.readouterr()
    template = synthetic_card / "social_card.html"
    template.write_text(SYNTHETIC_CARD.replace("synthetic", "edited"), "utf-8")
    assert build_site.main([*argv, "--check"]) == 1
    err = capsys.readouterr().err
    assert "social-card.png was rendered from a different card source" in err
    assert "scripts/render_social_card.py" in err
    # Re-stamping the PNG for the edited source makes the card current again.
    png = synthetic_card / "social-card.png"
    source_hash = build_site.card_source_hash(build_site.card_source())
    png.write_bytes(
        build_site.stamp_png(png.read_bytes(), build_site.CARD_KEY, source_hash)
    )
    assert build_site.main(argv) == 0
    assert build_site.main([*argv, "--check"]) == 0
    # The mark is part of the card source: changing it stales the card too.
    monkeypatch.setattr(build_site, "MARK", "<rect/>")
    assert build_site.main([*argv, "--check"]) == 1
    assert "different card source" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("png", "problem"),
    [
        (b"not a png", "social-card.png is not a PNG"),
        (_png(600, 315), "social-card.png is 600x315, not 1200x630"),
        (_png(1200, 630), "social-card.png was rendered from a different card source"),
    ],
)
def test_check_names_the_card_problem(tmp_path, capsys, synthetic_card, png, problem):
    out = tmp_path / "site"
    argv = ["--results", str(FIXTURE), "--out", str(out)]
    assert build_site.main(argv) == 0
    (synthetic_card / "social-card.png").write_bytes(png)
    assert build_site.main(argv) == 0
    assert problem in capsys.readouterr().err
    assert build_site.main([*argv, "--check"]) == 1
    assert problem in capsys.readouterr().err


def test_stamp_replaces_the_card_hash_and_keeps_the_image():
    blank = _png(1200, 630)
    once = build_site.stamp_png(blank, build_site.CARD_KEY, "a" * 64)
    twice = build_site.stamp_png(once, build_site.CARD_KEY, "b" * 64)
    assert build_site.png_text(twice) == {build_site.CARD_KEY: "b" * 64}
    assert [kind for kind, _ in build_site.png_chunks(twice)] == [
        b"IHDR",
        b"tEXt",
        b"IDAT",
        b"IEND",
    ]
    assert build_site.png_size(twice) == (1200, 630)
    assert (
        twice.replace(twice[33 : 33 + 12 + len(build_site.CARD_KEY) + 65], b"") == blank
    )


@pytest.mark.parametrize(
    "override", ["missing-browser", "", "not-executable", "directory"]
)
def test_invalid_chrome_override_never_falls_back(tmp_path, monkeypatch, override):
    browser = tmp_path / "browser"
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    browser.chmod(0o755)
    (tmp_path / "not-executable").touch()
    (tmp_path / "directory").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CHROME", override)
    monkeypatch.setattr(render_social_card, "CANDIDATES", (str(browser),))
    with pytest.raises(SystemExit, match="CHROME does not name an executable browser"):
        render_social_card.find_chrome()


@pytest.mark.parametrize("mode", ["path", "command", "automatic"])
def test_chrome_discovery_accepts_executables(tmp_path, monkeypatch, mode):
    browser = tmp_path / "browser"
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    browser.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(render_social_card, "CANDIDATES", ("browser",))
    if mode == "automatic":
        monkeypatch.delenv("CHROME", raising=False)
    else:
        monkeypatch.setenv("CHROME", str(browser) if mode == "path" else "browser")
    assert render_social_card.find_chrome() == str(browser)


@pytest.mark.parametrize("returncode", [0, 17])
def test_render_failure_includes_bounded_browser_diagnostics(monkeypatch, returncode):
    popen = subprocess.Popen

    def failing_browser(command, **kwargs):
        return popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('x' * 131072 + '\\nlaunch failed\\n'); "
                f"sys.exit({returncode})",
            ],
            **kwargs,
        )

    monkeypatch.setattr(render_social_card.subprocess, "Popen", failing_browser)
    with pytest.raises(SystemExit) as error:
        render_social_card.render("<p>card</p>", "browser", timeout=5)
    message = str(error.value)
    assert f"Chrome exited ({returncode}) without a complete screenshot" in message
    assert message.endswith("launch failed")
    assert len(message) < 8300


class Text(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.skip = 0
        self.text = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("style", "script", "title")

    def handle_endtag(self, tag):
        self.skip -= tag in ("style", "script", "title")

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def test_card_template_holds_the_stacked_lockup_and_no_data(monkeypatch):
    monkeypatch.setattr(build_site, "CARD_TEMPLATE", CARD_TEMPLATE)
    source = build_site.card_source()
    template = CARD_TEMPLATE.read_text(encoding="utf-8")
    # The mark comes from the site's one MARK, never a copy in the template.
    assert template.count(build_site.CARD_MARK_SLOT) == 1
    assert "translate(9 0) skewX(-10)" not in template
    assert source.count(build_site.MARK) == 1
    assert '<svg viewBox="0 0 100 100" aria-hidden="true">' + build_site.MARK in source
    assert (
        '<div class="wordmark"><span class="wordmark-agent">AGENT</span>'
        '<span class="wordmark-name">PADDOCK</span></div>' in source
    )
    css = " ".join(source.split("<style>", 1)[1].split("</style>", 1)[0].split())
    for declaration in (
        ".lockup svg { width: 176px; height: 176px;",
        ".wordmark-agent { font-weight: 500; font-size: 60px;",
        ".wordmark-name { font-weight: 800; font-size: 100px;",
        'font-family: "Saira", sans-serif; font-style: italic; color: #FFFFFF;',
        'font-family: "JetBrains Mono", monospace;',
        "background: #0E1116;",
        "width: 1200px; height: 630px;",
    ):
        assert declaration in css, declaration
    text = " ".join(" ".join(Text(source).text).split())
    assert "An open leaderboard for coding agents" in text
    assert (
        "Model × harness on real repository tasks, graded by a checker the agent "
        "never sees." in text
    )
    assert "agentpaddock.ai" in text
    # Decoration, not data: the tower positions are the only digits on the card.
    assert re.findall(r"\d", text) == ["1", "2", "3", "4", "5"]


# ---------- PDW-02: the shared layout and the page registry ----------

from paddock_site import glyphs as site_glyphs  # noqa: E402
from paddock_site import routes as site_routes  # noqa: E402

WEB_DESIGN = Path(__file__).resolve().parents[1] / "design" / "web"
PUBLISHED_GLYPHS = set(site_glyphs.published_ids())


def _html_pages(files):
    return {name: page for name, page in files.items() if name.endswith(".html")}


def _between(page: str, start: str, end: str) -> str:
    assert page.count(start) == 1, start
    return page.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.parametrize(
    ("active", "header_marks"),
    [
        ("index.html", 0),
        ("learn/index.html", 2),
        ("contact/index.html", 1),
        ("methodology.html", 0),
    ],
)
@pytest.mark.parametrize(
    "filename", ["board-example.html", "boards/example/index.html"]
)
def test_document_uses_output_path_for_links_and_active_route_for_marks(
    active, header_marks, filename
):
    page = build_site.document("Example", active, "<h1>Example</h1>", filename=filename)
    root = "../" * filename.count("/")
    for asset in ("paddock.css", "paddock.js", "favicon.svg"):
        assert f'"{root}{asset}"' in page
    assert f'property="og:url" content="{build_site.SITE_URL}{filename}"' in page
    header = _between(page, '<header class="nav">', "</header>")
    footer = _between(page, '<footer class="footer">', "</footer>")
    assert {r.file for r in build_site.ROUTES} <= _local_links(filename, footer)
    for section, count in ((header, header_marks), (footer, 1)):
        assert f'class="brand" href="{root}index.html"' in section
        marked = re.findall(r'<a href="([^"]+)" aria-current="page"', section)
        assert marked == [root + active] * count


def test_sprite_reads_and_parses_each_path_once_and_returns_independent_lists(
    tmp_path, monkeypatch
):
    paths = [tmp_path / "first.svg", tmp_path / "second.svg"]
    for path, glyph_id in zip(paths, ("first", "second")):
        path.write_text(
            f'<svg><symbol id="{glyph_id}"></symbol></svg>', encoding="utf-8"
        )
    reads = []
    read_text = Path.read_text

    def counted_read(path, *args, **kwargs):
        reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read)
    for path, glyph_id in zip(paths, ("first", "second")):
        first = site_glyphs.sprite(path=path)
        symbols = site_glyphs.published_symbols(path)
        symbols.clear()
        assert site_glyphs.sprite(path=path) == first
        assert site_glyphs.published_ids(path) == [glyph_id]
        assert reads.count(path) == 1


def test_every_registered_route_builds_into_the_output(tmp_path):
    out = tmp_path / "site"
    assert build_site.main(["--results", str(FIXTURE), "--out", str(out)]) == 0
    assert (
        build_site.main(["--results", str(FIXTURE), "--out", str(out), "--check"]) == 0
    )
    for route in build_site.ROUTES:
        path = out / route.file
        assert path.is_file(), route.path
        page = path.read_text(encoding="utf-8")
        # Today's board pages keep the titles their builders give them.
        if route.builder is None:
            assert f"<title>{html.escape(route.title)} · Agent Paddock</title>" in page
    # The registry names each route once, by path and by output file.
    assert len({r.path for r in build_site.ROUTES}) == len(build_site.ROUTES)
    assert len({r.file for r in build_site.ROUTES}) == len(build_site.ROUTES)


def test_every_page_carries_the_site_marker_once_in_its_head():
    for name, page in _html_pages(build_site.build(FIXTURE)).items():
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        assert head.count('<meta name="paddock-site" content="pdw-1">') == 1, name
        assert page.count('name="paddock-site"') == 1, name


def test_every_page_has_the_nav_the_menu_panel_and_the_footer():
    routes = build_site.ROUTES
    bar = site_routes.nav_routes(routes, site_routes.BAR)
    menu = site_routes.nav_routes(routes, site_routes.BAR, site_routes.PANEL)
    assert [r.label for r in bar] == [
        "Learn",
        "Races",
        "Standings",
        "Garage",
        "Pit wall",
    ]
    assert [r.label for r in menu] == [
        "Learn",
        "Races",
        "Standings",
        "Garage",
        "Pit wall",
        "Community",
        "Drivers",
        "An entry's season",
        "Rulebook",
        "Dictionary",
        "Race it yourself",
        "Contact and about",
    ]
    for name, page in _html_pages(build_site.build(FIXTURE)).items():
        header = _between(page, '<header class="nav">', "</header>")
        wide = _between(header, '<nav class="nav-wide" aria-label="Main">', "</nav>")
        panel = _between(
            header,
            '<nav class="nav-panel" id="site-menu" aria-label="Menu" hidden>',
            "</nav>",
        )
        footer = _between(page, '<footer class="footer">', "</footer>")
        assert _local_links(name, wide) == {r.file for r in bar}, name
        assert _local_links(name, panel) == {r.file for r in menu}, name
        # The panel draws one glyph per item.
        assert panel.count("<use href=") == len(menu), name
        # The menu button controls the panel and starts closed; it draws both strokes.
        button = _between(
            header, '<button type="button" class="nav-burger"', "</button>"
        )
        assert (
            'aria-expanded="false"' in button and 'aria-controls="site-menu"' in button
        )
        assert layout.MENU_STROKES in button and layout.CLOSE_STROKES in button
        # Without JavaScript the footer still reaches every page.
        assert {r.file for r in routes} <= _local_links(name, footer), name
        assert (
            layout.FOOTER_DISCLAIMER in footer and layout.FOOTER_INDEPENDENCE in footer
        )
        # The current page is marked in the bar and the panel when it is in them.
        for route in menu:
            mark = f'href="{layout.href(route.file, name)}" aria-current="page"'
            expected = 1 if route.file == name else 0
            assert panel.count(mark) == expected, (name, route.path)
            if route in bar:
                assert wide.count(mark) == expected, (name, route.path)


def test_every_nav_glyph_is_a_published_symbol():
    for route in site_routes.nav_routes(
        build_site.ROUTES, site_routes.BAR, site_routes.PANEL
    ):
        assert route.nav_glyph in PUBLISHED_GLYPHS, route.path
    expected = {
        "learn/": "track-map",
        "races/": "race-calendar",
        "standings/": "timing-tower",
        "garage/": "setup-sheet",
        "pit-wall/": "pit-wall",
        "community/": "entry",
        "drivers/": "driver",
        "entries/": "podium",
        "rulebook/": "rulebook",
        "dictionary/": "briefing",
        "race-it-yourself/": "pit-lane",
        "contact/": "pit-board",
    }
    assert {r.path: r.nav_glyph for r in build_site.ROUTES if r.nav_glyph} == expected


def test_every_page_inlines_the_sprite_exactly_once_and_uses_only_its_symbols():
    published = site_glyphs.published_symbols()
    for name, page in _html_pages(build_site.build(FIXTURE)).items():
        assert page.count('<svg class="symbols"') == 1, name
        sprite = _between(page, '<svg class="symbols"', "</svg>")
        assert all(page.count(symbol) == 1 for symbol in published), name
        ids = re.findall(r'<symbol id="([^"]+)"', page)
        assert len(ids) == len(set(ids)), name
        uses = set(re.findall(r'<use href="#([^"]+)"', page))
        assert uses <= set(ids), (name, uses - set(ids))
        assert "<symbol" not in page.replace(sprite, ""), name


def test_the_stylesheet_carries_the_design_tokens_once_and_pages_none():
    files = build_site.build(FIXTURE)
    css = files["paddock.css"]
    tokens = (Path(__file__).resolve().parents[1] / "design" / "tokens.css").read_text(
        encoding="utf-8"
    )
    assert css.startswith(tokens) and css.count(tokens) == 1
    # One definition of each token: the page styles carry no copy of their own.
    assert css.count("--paper: #FFFFFF;") == 1
    for name, page in _html_pages(files).items():
        assert page.count("paddock.css") == 1, name
        assert "--paper:" not in page, name


def test_registry_rejects_a_builder_for_an_unregistered_route():
    with pytest.raises(KeyError, match="unregistered"):
        site_routes.registry({"nowhere/": lambda results: ""})
    replaced = site_routes.registry({"learn/": lambda results: "built"})
    learn = next(r for r in replaced if r.path == "learn/")
    assert learn.builder(None) == "built" and learn.file == "learn/index.html"
    assert all(r.builder is None for r in replaced if r.path != "learn/")


def test_placeholders_carry_no_numbers_and_link_home():
    files = build_site.build(FIXTURE)
    for route in build_site.ROUTES:
        if route.builder is not None:
            continue
        page = files[route.file]
        main = _between(page, '<main class="wrap">', "</main>")
        text = " ".join(" ".join(Text(main).text).split())
        assert not re.search(r"\d", text), (route.path, text)
        assert "index.html" in _local_links(route.file, main), route.path
        assert f"<h1>{html.escape(route.title)}</h1>" in main


@pytest.mark.parametrize(
    ("race_type", "mark", "red"),
    [
        ("coding", "coding-worker", False),
        ("fix", "diff", False),
        ("hard-fix", "ci-failed", True),
        ("review", "review", False),
        ("hard-review", "red-team", True),
        ("docs", "documents", False),
        ("rca", "search", False),
    ],
)
def test_race_type_roundel(race_type, mark, red):
    roundel = site_glyphs.roundel(race_type)
    assert '<use href="#roundel"/>' in roundel and f'<use href="#{mark}"/>' in roundel
    assert ("roundel-red" in roundel) is red
    label = site_glyphs.RACE_TYPES[race_type][0]
    assert f'role="img" aria-label="{label}"' in roundel
    assert 'aria-hidden="true"' in site_glyphs.roundel(race_type, labelled=False)
    sprite = layout.sprite()
    symbol = re.search(rf'<symbol id="{mark}"[^>]*>(.*?)</symbol>', sprite)
    assert symbol, mark
    assert ("--pdk-red" in symbol[1]) is red
    # The frame is the published roundel glyph; the marks are not published ones.
    assert "roundel" in PUBLISHED_GLYPHS and mark not in PUBLISHED_GLYPHS


def test_menu_script_toggles_the_panel_and_ships_once():
    files = build_site.build(FIXTURE)
    script = files["paddock.js"]
    for needle in ("aria-expanded", "site-menu", "Escape", "(min-width: 720px)"):
        assert needle in script
    css = files["paddock.css"]
    assert "@media (min-width: 720px)" in layout.CSS
    assert ".nav-burger[hidden], .nav-panel[hidden] { display: none; }" in css


def test_design_reference_is_committed_and_never_shipped():
    readme = (WEB_DESIGN / "README.md").read_text(encoding="utf-8")
    prototypes = sorted(path.name for path in WEB_DESIGN.glob("*.dc.html"))
    assert prototypes and "Main.dc.html" in prototypes
    for prototype in prototypes:
        assert f"`{prototype.removesuffix('.dc.html')}" in readme, prototype
    assert (WEB_DESIGN / "data" / "live.example.json").is_file()
    route_map = readme.split("## Route map in the site generator", 1)[1]
    for route in build_site.ROUTES:
        assert f"| `/{route.path}` | `{route.file}` |" in route_map, route.path
        if route.nav_glyph:
            assert f"`{route.nav_glyph}`" in route_map, route.path
    for name, content in build_site.build(FIXTURE).items():
        assert not name.endswith(".dc.html"), name
        if isinstance(content, str):
            assert ".dc.html" not in content and "support.js" not in content, name
            assert "DCLogic" not in content, name


# ---------------------------------------------------------------- site data (PDW-03)
#
# A small synthetic results tree: a paddock bench document (trusted by default),
# a fork document (self-reported by default) and four local tasks. The golden
# files under tests/fixtures/site-data/ are its whole data output; regenerate
# them after an intended change with PADDOCK_WRITE_SITE_GOLDEN=1.

SITE_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "site-data"
SENTINELS = (
    "COUNT_CHECK_SENTINEL",
    "COUNT_LABEL_SENTINEL",
    "HIDDEN_CHECK_SENTINEL",
    "HIDDEN_LABEL_SENTINEL",
    "FACT_SENTINEL",
    "FACT_LABEL_SENTINEL",
    "UNLISTED_SENTINEL",
)
BENCH = "2026-01-05-bench.json"
FORK = "2026-01-06-fork.json"
REVIEW_CARD = """name = "Alpha circuit"
pitch = "Review a proposed change."
challenge = "hard-review"
infographic = "hard-review"

[[checkpoints]]
check = "read_files"
label = "Read the code"
disclose = "label"

[[checkpoints]]
check = "COUNT_CHECK_SENTINEL"
label = "COUNT_LABEL_SENTINEL"
disclose = "count"

[[checkpoints]]
check = "fact:FACT_SENTINEL"
label = "FACT_LABEL_SENTINEL"
disclose = "label"

[[checkpoints]]
check = "verdict_right"
label = "Right verdict"
disclose = "label"

[[checkpoints]]
check = "HIDDEN_CHECK_SENTINEL"
label = "HIDDEN_LABEL_SENTINEL"
disclose = "hidden"
"""


def _checks(**failed: bool) -> list[dict]:
    names = {
        "read": "read_files",
        "count": "COUNT_CHECK_SENTINEL",
        "fact": "fact:FACT_SENTINEL",
        "verdict": "verdict_right",
        "hidden": "HIDDEN_CHECK_SENTINEL",
    }
    checks = [
        {"name": name, "ok": not failed.get(short, False), "required": True}
        for short, name in names.items()
    ]
    return checks + [{"name": "UNLISTED_SENTINEL", "ok": False, "required": False}]


def _site_tree(tmp_path: Path, trust: dict | None = None) -> Path:
    """Write the synthetic tree; returns its results directory."""
    results = tmp_path / "results"
    shutil.copytree(FIXTURE, results)
    (results / "2026-01-03-fixture-run.json").unlink()
    raw = json.loads((results / "prices" / "2026-01-02.json").read_text())
    table = lb.PriceTable(raw["date"], raw["models"], raw["note"])
    runner = lb.PADDOCK_BENCH_RUNNER
    configs = {
        name: {"harness": name, "runner": runner}
        for name in ("alpha-cli", "beta-cli", "gamma-cli")
    }
    hashes = {name: lb.config_hash(descriptor) for name, descriptor in configs.items()}
    identities = {
        "A": ("alpha-cli", "acme/coder-large", "high"),
        "C": ("alpha-cli", "acme/coder-large", "low"),
        "D": ("beta-cli", "acme/coder-large", "high"),
        "B": ("beta-cli", "bolt/coder-small", None),
        "F": ("alpha-cli", "local/coder-tiny", None),
        "E": ("gamma-cli", "bolt/coder-small", "max"),
    }
    counter = iter(range(1, 1000))

    def record(who, task, suite, version, verdict, attempt, wall, checks=None, **extra):
        harness, provider, reasoning = identities[who]
        tokens = {"input": 1000, "cache": 4000, "output": 100 * attempt}
        row = {
            "run_id": f"synthetic-{next(counter):03d}",
            "suite": suite,
            "task": task,
            "task_version": version,
            "attempt": attempt,
            "harness": {
                "name": harness,
                "version": None,
                "config_hash": hashes[harness],
            },
            "model": {"provider_id": provider, "reasoning": reasoning},
            "verdict": verdict,
            "score": None,
            "tokens": tokens,
            "wall_seconds": wall,
            "paddock_commit": None,
            "excludedBecause": None,
            "cost": lb.price_cost(tokens, provider, table),
        }
        if checks is not None:
            row["checks"] = checks
        row.update(extra)
        return row

    review = ("hard-review-alpha", "hard-review", 2)
    docs = ("docs-beta", "docs", 1)
    bench = [
        record("A", *review, "passed", 1, 100.0, _checks()),
        record("A", *review, "passed", 2, 120.0, _checks()),
        record("A", *review, "failed", 3, 90.0, _checks(read=True)),
        record("A", *review, "error", 4, None, excludedBecause="infra:error"),
        record("D", *review, "passed", 1, 80.0, _checks()),
        record("D", *review, "failed", 2, 85.0, _checks(verdict=True)),
        record("D", *review, "passed", 3, 70.0),
        record("B", *review, "failed", 1, 200.0, _checks(read=True)),
        record("B", *review, "passed", 2, 210.0, _checks()),
        record("B", *review, "passed", 3, 190.0, _checks()),
        record("C", *review, "failed", 1, 300.0, _checks(hidden=True)),
        record("C", *review, "failed", 2, 310.0, _checks(count=True)),
        record("C", *review, "failed", 3, 320.0, _checks()),
        record("F", *review, "failed", 1, 400.0, _checks(fact=True)),
        record("F", *review, "passed", 2, 410.0, _checks()),
        record("A", *docs, "passed", 1, 60.0),
        record("A", *docs, "passed", 2, 61.0),
        record("A", *docs, "failed", 3, 62.0),
        record("D", *docs, "passed", 1, 50.0),
        record("D", *docs, "failed", 2, 51.0),
        record("D", *docs, "passed", 3, 52.0),
        # Out of attempt order on purpose: the first result is attempt 1.
        record("B", *docs, "failed", 2, 40.0),
        record("B", *docs, "passed", 1, 41.0),
        record("B", *docs, "failed", 3, 42.0),
        record("C", *docs, "failed", 1, 30.0),
        record("C", *docs, "passed", 2, 31.0),
        record("C", *docs, "passed", 3, 32.0),
        record("A", "fix-gamma", "fix", 1, "passed", 1, 20.0),
        record("B", "fix-gamma", "fix", 1, "failed", 1, 21.0),
        record("C", "fix-gamma", "fix", 1, "passed", 1, None),
        # A superseded task version and a task that is not local: never races.
        record("A", "coding-old", "coding", 1, "passed", 1, 10.0),
        record("A", "coding-elsewhere", "coding", 1, "passed", 1, 10.0),
    ]
    fork = [
        record("E", *review, "passed", 1, 10.0, _checks()),
        record("E", *docs, "passed", 1, 10.0),
    ]

    def document(name, records, **source):
        date, doc_id = name[:10], name[11:].removesuffix(".json")
        used = {row["harness"]["name"] for row in records}
        lb.write_document(
            results / name,
            {
                "schema": lb.SCHEMA,
                "id": doc_id,
                "date": date,
                "source": {
                    "description": "Synthetic site-data fixture.",
                    "evidence": "tests/test_build_site.py",
                    **source,
                },
                "price_table": "2026-01-02",
                "harness_configs": {
                    hashes[name]: configs[name] for name in sorted(used)
                },
                "records": records,
            },
        )

    document(BENCH, bench, kind="import")
    document(
        FORK,
        fork,
        kind="import",
        fork={
            "repository": "https://example.com/community/paddock",
            "commit": "a" * 40,
        },
    )
    if trust is not None:
        (results / "meta" / "trust.json").write_text(
            json.dumps({"schema": "paddock-trust/v1", "documents": trust})
        )
    tasks = tmp_path / "tasks"
    for task_id, suite, version, title, card in (
        ("hard-review-alpha", "hard-review", 2, "Synthetic review race", REVIEW_CARD),
        ("docs-beta", "docs", 1, "Synthetic docs race", None),
        ("fix-gamma", "fix", 1, "Synthetic fix race", None),
        ("coding-old", "coding", 2, "Synthetic bumped race", None),
    ):
        (tasks / task_id).mkdir(parents=True)
        (tasks / task_id / "task.toml").write_text(
            f'id = "{task_id}"\ntask_version = {version}\n'
            f'title = "{title}"\nsuite = "{suite}"\n'
        )
        if card:
            (tasks / task_id / "track.toml").write_text(card)
    return results


def _site_files(results: Path) -> dict[str, str]:
    return site_data.build(lb.load_results(results), results.parent / "tasks")


def _site_json(files: dict[str, str], name: str):
    return json.loads(files[f"data/{name}"])


def test_site_data_matches_the_golden_file_per_schema(tmp_path, monkeypatch):
    files = _site_files(_site_tree(tmp_path))
    if os.environ.get("PADDOCK_WRITE_SITE_GOLDEN") == "1":
        shutil.rmtree(SITE_GOLDEN, ignore_errors=True)
        for name, text in files.items():
            path = SITE_GOLDEN / name.removeprefix("data/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    golden = {
        "data/" + path.relative_to(SITE_GOLDEN).as_posix(): path.read_text("utf-8")
        for path in sorted(SITE_GOLDEN.rglob("*.json"))
    }
    assert files == golden
    schemas = {
        _site_json(files, name.removeprefix("data/"))["schema"] for name in files
    }
    assert schemas == set(site_data.SCHEMAS.values())
    for name, text in files.items():
        value = json.loads(text)
        assert {"schema", "generatedAt", "basis"} <= set(value), name
        assert value["generatedAt"] == "2026-01-06", name
    # The same tree elsewhere builds the same bytes.
    assert _site_files(_site_tree(tmp_path / "again")) == files


def test_site_data_counts_only_trusted_documents(tmp_path):
    files = _site_files(_site_tree(tmp_path))
    community = _site_json(files, "community.json")
    by_document = {row["document"] + ".json": row for row in community["documents"]}
    assert by_document[BENCH]["trust"] == "paddock-run"
    assert by_document[BENCH]["trustOrigin"] == "paddock-bench"
    assert by_document[FORK]["trust"] == "self-reported"
    assert by_document[FORK]["counted"] is False
    listed = {(row["car"], row["trust"]) for row in community["entries"]}
    assert ("gamma-cli", "self-reported") in listed
    assert ("alpha-cli", "paddock-run") in listed
    for name, text in files.items():
        if name != "data/community.json":
            assert "gamma-cli" not in text, name
            assert FORK.removesuffix(".json") not in text, name
    standings = _site_json(files, "standings.json")
    assert standings["state"] == "live"
    assert standings["basis"]["documents"] == [BENCH.removesuffix(".json")]
    # A maintainer sign-off elevates the fork; it then counts in standings.
    signed = {
        "level": "reproduced",
        "evidence": ["https://example.com/evidence"],
        "sign_off": "maintainer",
    }
    elevated = _site_files(_site_tree(tmp_path / "signed", {FORK: signed}))
    rows = _site_json(elevated, "standings.json")["entries"]
    fork_rows = [row for row in rows if row["car"] == "gamma-cli"]
    assert [row["trust"] for row in fork_rows] == ["reproduced"]
    assert fork_rows[0]["place"] == 1


@pytest.mark.parametrize("copy_record", [dict, deepcopy], ids=["shallow", "deep"])
def test_site_data_resolves_trust_for_copied_first_results(
    tmp_path, monkeypatch, copy_record
):
    signed = {
        "level": "reproduced",
        "evidence": ["https://example.com/evidence"],
        "sign_off": "maintainer",
    }
    results = _site_tree(tmp_path, {FORK: signed})
    expected = _site_files(results)
    first_results = lb.first_results
    monkeypatch.setattr(
        lb,
        "first_results",
        lambda documents: [copy_record(record) for record in first_results(documents)],
    )
    assert _site_files(results) == expected


def test_an_empty_trusted_set_awaits_sign_off_and_never_falls_back(tmp_path):
    demoted = {BENCH: {"level": "self-reported", "evidence": [], "sign_off": None}}
    files = _site_files(_site_tree(tmp_path, demoted))
    standings = _site_json(files, "standings.json")
    assert standings["state"] == "awaiting-trust-sign-off"
    assert standings["entries"] == [] and standings["partial"] == []
    assert _site_json(files, "races.json")["races"] == []
    drivers = _site_json(files, "drivers.json")
    assert drivers["ladders"] == [] and drivers["sameDriverDifferentCar"] == []
    assert not [
        name for name in files if name.startswith(("data/races/", "data/entries/"))
    ]
    for name in ("standings.json", "races.json", "drivers.json"):
        assert _site_json(files, name)["state"] == "awaiting-trust-sign-off"
    community = _site_json(files, "community.json")
    assert community["state"] == "live"
    assert {row["trust"] for row in community["documents"]} == {"self-reported"}
    assert community["entries"]
    # The committed fixture's single run document is not the bench: awaiting too.
    fixture = site_data.build(lb.load_results(FIXTURE), FIXTURE.parent / "tasks")
    assert json.loads(fixture["data/standings.json"])["state"] == (
        "awaiting-trust-sign-off"
    )


def test_site_data_discloses_checkpoints_only_as_the_track_card_says(tmp_path):
    results = _site_tree(tmp_path)
    files = _site_files(results)
    for name, text in files.items():
        for sentinel in SENTINELS:
            assert sentinel not in text, (name, sentinel)
        assert "fact:" not in text, name
    race = _site_json(files, "races/hard-review-alpha.json")
    assert race["checkpoints"] == ["Read the code", "Right verdict"]
    # Count checkpoint, docs fact (forced to count) and the unlisted check.
    assert race["moreChecks"] == 3
    # 13 counted runs record checks (one does not; the infra row never counts).
    assert race["funnel"]["all"] == [13, 11, 8, 6]
    assert race["funnel"]["byCar"]["beta-cli"][0] == 5
    assert race["classification"]["retirements"] == {
        "checkpoints": [
            {"checkpoint": 1, "label": "Read the code", "runs": 1},
            {"checkpoint": 2, "label": "Right verdict", "runs": 0},
        ],
        "undisclosed": 2,
        "unclassified": 0,
        "unrecorded": 0,
    }
    assert [row["lapSeconds"] for row in race["classification"]["finishers"]] == [
        80.0,
        100.0,
    ]
    stages = next(
        row
        for row in _site_json(files, "races.json")["races"]
        if row["id"] == "hard-review-alpha"
    )["stages"]
    assert [row["label"] for row in stages["checkpoints"]] == [
        "Read the code",
        "Right verdict",
    ]
    assert stages["moreChecks"] == 3
    # A race whose runs record no checks has no checkpoints at all.
    docs = _site_json(files, "races/docs-beta.json")
    assert docs["checkpoints"] is None and docs["funnel"] is None
    assert docs["classification"]["retirements"] is None
    # Turning the hidden checkpoint into a counted one adds one to the count
    # and names nothing new.
    card = results.parent / "tasks" / "hard-review-alpha" / "track.toml"
    card.write_text(
        card.read_text().replace('disclose = "hidden"', 'disclose = "count"')
    )
    recount = _site_json(_site_files(results), "races/hard-review-alpha.json")
    assert recount["moreChecks"] == 4
    assert recount["checkpoints"] == race["checkpoints"]


def test_site_data_ranges_places_and_ties_equal_leaderboard(tmp_path):
    results = _site_tree(tmp_path)
    files = _site_files(results)
    loaded = lb.load_results(results)
    calendar = {("hard-review", "hard-review-alpha", 2), ("docs", "docs-beta", 1)}
    assert [
        (row["challenge"], row["id"], row["version"])
        for row in json.loads(files["data/races.json"])["races"]
        if row["calendar"]
    ] == sorted(calendar, key=lambda key: key[1])
    grouped: dict = {}
    for record in lb.first_results({BENCH: loaded.documents[BENCH]}):
        if (record["suite"], record["task"], record["task_version"]) in calendar:
            grouped.setdefault(site_data.identity(record), []).append(record)
    expected = [
        {"key": key, **lb.summarize(records)}
        for key, records in grouped.items()
        if len(records) == len(calendar)
    ]
    expected.sort(key=lb.standing_key)
    standings = _site_json(files, "standings.json")["entries"]
    assert [(row["car"], row["driver"], row["map"]) for row in standings] == [
        (key[0], key[2], key[3]) for key in (row["key"] for row in expected)
    ]
    for row, want, (place, tie) in zip(standings, expected, lb.places(expected)):
        assert (row["k"], row["n"], row["rate"]) == (
            want["passed"],
            want["n"],
            want["rate"],
        )
        assert (
            (row["lo"], row["hi"]) == want["ci"] == lb.wilson(want["passed"], want["n"])
        )
        assert (row["place"], row["tie"]) == (place, tie)
        assert row["costPerPass"] == want["cost_per_pass"]["usd"]
    assert [(row["place"], row["tie"]) for row in standings] == [
        (1, True),
        (1, True),
        (3, False),
        (4, False),
    ]
    partial = _site_json(files, "standings.json")["partial"]
    assert [(row["driver"], row["place"]) for row in partial] == [
        ("local/coder-tiny", None)
    ]
    assert partial[0]["costPerPass"] is None
    # Races count every counted run; ladders match leaderboard on the same runs.
    alpha = next(
        row
        for row in _site_json(files, "races.json")["races"]
        if row["id"] == "hard-review-alpha"
    )
    review_runs = [
        record
        for record in loaded.documents[BENCH]["records"]
        if record["task"] == "hard-review-alpha" and lb.included(record)
    ]
    summary = lb.summarize(review_runs)
    assert (alpha["k"], alpha["n"]) == (summary["passed"], summary["n"]) == (7, 14)
    assert alpha["difficulty"] == lb.difficulty(7, 14) == 4
    ladder = _site_json(files, "drivers.json")["ladders"]
    assert [(row["driver"], row["car"]) for row in ladder] == [
        ("acme/coder-large", "alpha-cli")
    ]
    assert [level["map"] for level in ladder[0]["levels"]] == ["low", "high"]
    high = [
        r
        for r in review_runs
        if site_data.identity(r)[0] == "alpha-cli" and r["model"]["reasoning"] == "high"
    ]
    assert ladder[0]["levels"][1]["costPerRun"] == lb.cost_per_run(high)["usd"]
    assert (ladder[0]["levels"][1]["lo"], ladder[0]["levels"][1]["hi"]) == lb.wilson(
        2, 3
    )
    sets = _site_json(files, "drivers.json")["sameDriverDifferentCar"]
    assert [
        (row["driver"], row["map"], [c["car"] for c in row["cars"]]) for row in sets
    ] == [("acme/coder-large", "high", ["alpha-cli", "beta-cli"])]


def test_site_data_entry_lists_every_first_result(tmp_path):
    files = _site_files(_site_tree(tmp_path))
    standings = _site_json(files, "standings.json")
    for row in standings["entries"] + standings["partial"]:
        entry = _site_json(files, f"entries/{row['id']}.json")
        assert entry["totals"] == {"k": row["k"], "n": row["n"], "rate": row["rate"]}
        assert (entry["place"], entry["tie"]) == (row["place"], row["tie"])
    alpha = next(row for row in standings["entries"] if row["car"] == "alpha-cli")
    entry = _site_json(files, f"entries/{alpha['id']}.json")
    assert [(r["race"], r["calendar"], r["passed"]) for r in entry["races"]] == [
        ("docs-beta", True, True),
        ("fix-gamma", False, True),
        ("hard-review-alpha", True, True),
    ]
    assert entry["medianLapSeconds"] == 80.0
    assert entry["fullCalendar"] is True


def test_build_writes_site_data_beside_the_pages(tmp_path):
    results = _site_tree(tmp_path)
    files = build_site.build(results)
    assert files["data/standings.json"] == _site_files(results)["data/standings.json"]
    assert "data/races/hard-review-alpha.json" in files


# ---------------------------------------------------------------- standings and entries (PDW-07)

from paddock_site import standings as standings_pages  # noqa: E402

ROW = re.compile(
    r'<li class="st-item" data-entry="([^"]+)".*?</details></li>', re.DOTALL
)


def _rows(page: str) -> list[tuple[str, str]]:
    """``(entry id, row markup)`` for every standings row, in page order."""
    return [(match[1], match[0]) for match in ROW.finditer(page)]


def _expected_standings(results: Path) -> list[dict]:
    """The placed rows as leaderboard.py orders them, computed here from scratch."""
    loaded = lb.load_results(results)
    calendar = {("hard-review", "hard-review-alpha", 2), ("docs", "docs-beta", 1)}
    grouped: dict = {}
    for record in lb.first_results({BENCH: loaded.documents[BENCH]}):
        if (record["suite"], record["task"], record["task_version"]) in calendar:
            grouped.setdefault(site_data.identity(record), []).append(record)
    rows = [
        {"key": key, **lb.summarize(records)}
        for key, records in grouped.items()
        if len(records) == len(calendar)
    ]
    rows.sort(key=lb.standing_key)
    return rows


def test_standings_carries_the_results_note_after_the_hero(tmp_path):
    for files in (build_site.build(_site_tree(tmp_path)), build_site.build(FIXTURE)):
        page = files["standings/index.html"]
        main = _between(page, '<main class="wrap">', "</main>")
        hero = main.index('<section class="st-hero">')
        note = main.index('data-pdk="results-note"')
        assert main.count('data-pdk="results-note"') == 1
        assert hero < note
        assert main.index("<h1>The timing tower.</h1>") < note
        # Nothing but the hero comes before the note.
        assert "<section" not in main[main.index("</section>", hero) + 10 : note - 30]
        block = main[note : main.index("</section>", note)]
        assert "How to read these results" in block
        assert "the same automated checks grade every run" in block
        assert "It is not a comprehensive ranking" in block
        assert '<use href="#rulebook"/>' in block
        links = _local_links("standings/index.html", block)
        assert links == {"contact/index.html", "rulebook/index.html"}
        assert ">Independent and open source: about the paddock<" in block
        assert ">Read the method<" in block
    live = build_site.build(_site_tree(tmp_path / "again"))["standings/index.html"]
    assert "AI agents race a fixed calendar of 2 tasks" in live


def test_standings_order_and_ties_equal_leaderboard(tmp_path):
    results = _site_tree(tmp_path)
    page = build_site.build(results)["standings/index.html"]
    expected = _expected_standings(results)
    ids = site_data.entry_ids(row["key"] for row in expected)
    rows = _rows(page)
    assert [entry for entry, _ in rows] == [ids[row["key"]] for row in expected]
    labels = [
        re.search(r'<span class="st-by-pass">([^<]+)</span>', markup)[1]
        for _, markup in rows
    ]
    want = [f"={place}" if tie else str(place) for place, tie in lb.places(expected)]
    assert labels == want == ["=1", "=1", "3", "4"]
    for (_, markup), row in zip(rows, expected):
        value = re.search(
            r'<span class="st-value num"><span class="st-by-pass">([^<]+)<', markup
        )
        assert value[1] == f"{row['rate'] * 100:.1f}%"
        assert (
            f"honest range {row['ci'][0] * 100:.1f}% to {row['ci'][1] * 100:.1f}%"
            in markup
        )
    # The photo finish names the tie leaderboard.py found.
    assert "<b>Photo finish.</b> The top two share" in page
    # The rows ranked by fuel bill: cheapest per race passed first, no price last.
    standings = json.loads(build_site.build(results)["data/standings.json"])["entries"]
    fuel = standings_pages.fuel_ranks(standings)
    for (_, markup), (order, label) in zip(rows, fuel):
        assert f'data-fuel-rank="{order}"' in markup
        assert f'<span class="st-by-fuel">{label}</span>' in markup
    by_fuel = sorted(range(len(standings)), key=lambda i: fuel[i][0])
    priced = [
        standings[i]["costPerPass"]
        for i in by_fuel
        if standings[i]["costPerPass"] is not None
    ]
    assert priced == sorted(priced)


def test_fuel_ranks_tie_equal_bills_and_put_no_price_last():
    entries = [
        {"costPerPass": 0.2},
        {"costPerPass": None},
        {"costPerPass": 0.1},
        {"costPerPass": 0.2},
        {"costPerPass": None},
    ]
    assert standings_pages.fuel_ranks(entries) == [
        (1, "=2"),
        (3, "–"),
        (0, "1"),
        (2, "=2"),
        (4, "–"),
    ]


def test_top_rows_keep_a_tie_at_the_cut_whole():
    places = [1, 1, 3, 3, 3, 6]
    rows = [{"place": place} for place in places]
    assert standings_pages.top_by_pass(rows, top=3) == 5
    assert standings_pages.top_by_pass(rows, top=2) == 2
    assert standings_pages.top_by_pass(rows, top=10) == 6


def test_every_standings_row_links_to_a_built_entry_page(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    standings = json.loads(files["data/standings.json"])
    rows = _rows(files["standings/index.html"])
    assert rows
    for entry, markup in rows:
        links = _local_links("standings/index.html", markup)
        target = f"entries/{entry}/index.html"
        assert links == {target}, entry
        assert target in files
        assert "See its whole season, race by race" in markup
    # One page per standings entry, placed or partial, and no others.
    built = {name for name in files if re.fullmatch(r"entries/[^/]+/index\.html", name)}
    assert built == {
        f"entries/{row['id']}/index.html"
        for row in standings["entries"] + standings["partial"]
    }
    # The landing page lists every one of them.
    landing = _local_links("entries/index.html", files["entries/index.html"])
    assert built <= landing
    for name in built:
        page = files[name]
        # Entry pages mark the entries route in the nav and resolve every link.
        assert 'href="../../entries/index.html" aria-current="page"' in page
        for link in _local_links(name, page):
            assert link in files, (name, link)


def _left(style: str, prop: str) -> float:
    return float(re.search(rf"{prop}:([0-9.]+)%", style)[1])


def test_scale_positions_match_values(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    page = files["standings/index.html"]
    standings = json.loads(files["data/standings.json"])["entries"]

    def at(value: float, low: int, high: int) -> float:
        return min(100.0, max(0.0, (value * 100 - low) / (high - low) * 100))

    # The scale and the rows share one grid, so the ticks sit over the bars.
    scale = _between(page, '<div class="st-scale st-grid', "</div>")
    ticks = re.findall(r'style="left:([0-9.]+)%" data-value="(\d+)"', scale)
    assert [int(value) for _, value in ticks] == [75, 80, 85, 90, 95, 100]
    for left, value in ticks:
        assert float(left) == pytest.approx(at(int(value) / 100, 75, 100), abs=0.05)
    assert '<summary class="st-row st-grid">' in page
    for (_, markup), row in zip(_rows(page), standings):
        bar = re.search(
            r'<span class="st-band" style="(left:[^"]+)"></span>'
            r'<span class="st-dot" style="(left:[^"]+)"></span>',
            markup,
        )
        band, dot = bar[1], bar[2]
        assert _left(band, "left") == pytest.approx(at(row["lo"], 75, 100), abs=0.05)
        assert _left(band, "width") == pytest.approx(
            at(row["hi"], 75, 100) - at(row["lo"], 75, 100), abs=0.1
        )
        assert _left(dot, "left") == pytest.approx(at(row["rate"], 75, 100), abs=0.05)
    # A value below the scale is held to its left end, never drawn outside.
    assert standings_pages.scale_pos(0.5) == 0.0
    assert standings_pages.scale_pos(1.0) == 100.0
    # The entry hero draws its range on 50-100%.
    for row in standings:
        hero = files[f"entries/{row['id']}/index.html"]
        bar = re.search(
            r'<span class="en-range"[^>]*><span class="st-band" style="([^"]+)"></span>'
            r'<span class="st-dot" style="([^"]+)"></span>',
            hero,
        )
        assert _left(bar[1], "left") == pytest.approx(at(row["lo"], 50, 100), abs=0.05)
        assert _left(bar[2], "left") == pytest.approx(
            at(row["rate"], 50, 100), abs=0.05
        )
        assert "scale 50% to 100%" in hero


def test_entry_grid_has_one_flag_per_calendar_race(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    standings = json.loads(files["data/standings.json"])
    calendar = [
        r for r in json.loads(files["data/races.json"])["races"] if r["calendar"]
    ]
    assert len(calendar) == 2
    for row in standings["entries"] + standings["partial"]:
        page = files[f"entries/{row['id']}/index.html"]
        entry = json.loads(files[f"data/entries/{row['id']}.json"])
        grid = _between(page, "data-en-season>", '<div class="en-pick"')
        cells = re.findall(r'class="en-flag (pass|dnf|none)"', grid)
        assert len(cells) == len(calendar), row["id"]
        ran = {race["race"]: race for race in entry["races"] if race["calendar"]}
        for race in calendar:
            if race["id"] in ran:
                result = "pass" if ran[race["id"]]["passed"] else "dnf"
                assert f'class="en-flag {result}"' in grid
                assert f'data-race="{race["id"]}"' in grid
            else:
                assert f'aria-label="{race["name"]}, not raced"' in grid
        # Races off the calendar never get a flag.
        for race in entry["races"]:
            if not race["calendar"]:
                assert f'data-race="{race["race"]}"' not in grid
        assert f"{row['k']} of {row['n']} races passed" in page
    # A tapped flag carries its lap and fuel.
    alpha = next(r for r in standings["entries"] if r["car"] == "alpha-cli")
    page = files[f"entries/{alpha['id']}/index.html"]
    assert 'data-line="Checkered flag · lap 1:40 · fuel' in page
    partial = standings["partial"][0]
    assert "Not placed" in files[f"entries/{partial['id']}/index.html"]


def test_standings_and_entries_await_trust_sign_off(tmp_path):
    demoted = {BENCH: {"level": "self-reported", "evidence": [], "sign_off": None}}
    files = build_site.build(_site_tree(tmp_path, demoted))
    for name in ("standings/index.html", "entries/index.html"):
        page = files[name]
        assert 'data-state="awaiting-trust-sign-off"' in page, name
        assert 'class="st-item"' not in page and "en-list-row" not in page, name
        assert "gamma-cli" not in page, name
    assert not [name for name in files if re.match(r"entries/[^/]+/index\.html", name)]


def test_standings_and_entry_pages_name_no_hidden_check(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    pages = {
        name: text
        for name, text in files.items()
        if name.startswith(("standings/", "entries/"))
    }
    assert len(pages) > 2
    for name, text in pages.items():
        for sentinel in SENTINELS:
            assert sentinel not in text, (name, sentinel)
        assert "fact:" not in text, name
        assert "faster" not in text.lower(), name
        # Only the published sprite and the race-type marks are drawn.
        uses = set(re.findall(r'<use href="#([^"]+)"', text))
        marks = {mark for _l, mark, _b in site_glyphs.RACE_TYPES.values()}
        assert uses <= PUBLISHED_GLYPHS | marks | {"broadcast"}, (name, uses)
