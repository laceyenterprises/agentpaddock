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
from collections import defaultdict
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
            "learn/learn.js",
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
    index = files["leaderboard.html"]
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
        assert page.count("<script") == 3 + page.count(own) + 2 * (name == "learn/index.html") and "<style" not in page
        if name == "learn/index.html":
            assert page.count('<script type="application/json" id="learn-data">') == 1
            assert page.count('<script src="learn.js" defer></script>') == 1
        assert "<h1>" in page and page.count("<h1>") == 1
        assert {other for other, _ in build_site.PAGES} <= _local_links(name, page)
        # Every link marked current points at the page itself (a board page
        # belongs to the leaderboard), and the page is marked somewhere.
        current_links = [
            a
            for t, a in elements
            if t == "a" and "aria-current" in a
        ]
        assert all(a["aria-current"] == "page" for a in current_links), name
        current = {_resolve(name, a["href"]) for a in current_links}
        if name.startswith("board-"):
            expected = {build_site.LEADERBOARD}
        elif name == "index.html":
            # Home is linked by the lockup, which carries no current mark.
            expected = set()
        else:
            expected = {name}
        assert current == expected, name
        assert f"{build_site.REPO_URL}/blob/main/LICENSE" in page
        assert "© 2026 Lacey Enterprises LLC" in page
    favicon = files["favicon.svg"]
    assert "prefers-color-scheme: dark" in favicon
    assert "skewX(-10)" in favicon
    assert all(color in favicon for color in ("#007BFF", "#00C27A", "#E0703A"))


def test_overview_features_one_board_per_suite_and_preserves_old_anchors():
    results = lb.load_results(FIXTURE)
    files = build_site.build(FIXTURE)
    index = files["leaderboard.html"]
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
    index = files["leaderboard.html"]
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
        len(build_site.build(lb.DEFAULT_RESULTS)["leaderboard.html"].encode("utf-8"))
        < 150_000
    )
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
    r"|'paddock-live/v\d+'"  # the live feed schema id the home script checks (PDW-04)
    r'|"paddock-(?:run|bench)"'  # the trust level and its origin, as JSON values
    r"|>Independent and open source: about the paddock<"  # the results note (PDW-07)
    r"|</svg>Paddock run</span>"  # the trust badge's label (PDW-09)
    r"|(?s:<pre\b[^>]*><code>.*?</code></pre>)"  # commands quoted verbatim (PDW-09)
    r"|<code>[^<]*</code>"  # inline code: paths and commands
    r"|Learn the paddock in 60 seconds\."
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
        ("<h2>Learn the paddock in 60 seconds.</h2>", False),
        ("Learn the paddock commit", True),
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
        if name == "index.html":
            # Home's eyebrow names the product and what it is.
            assert re.search(
                r'<span class="eyebrow">Agent Paddock: [^<]+</span><h1>', files[name]
            )
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
        ("leaderboard.html", 0),
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


def test_data_page_builders_follow_the_registry_interface():
    files = build_site.build(FIXTURE)
    inputs = site_routes.Build(
        None,
        {name: json.loads(text) for name, text in files.items() if name.startswith("data/")},
        build_site.ROUTES,
    )
    for route in build_site.ROUTES:
        if route.path in build_site.DATA_PAGES:
            assert route.builder(inputs) == files[route.file]


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
        assert build_site.LEADERBOARD in _local_links(route.file, main), route.path
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


#: The rest of the review race's task.toml: enough for track-facts to stage it.
ALPHA_TASK = """
[dispatch]
prompt = "prompt.md"

[[stages]]
run = "stage.sh"

[result_schema]
verdict = { type = "string" }

[verifier]
entrypoint = "verifier.py"
"""
#: Each synthetic task's public prompt. The docs prompt's first paragraph runs
#: past the excerpt's word limit.
PROMPTS = {
    "hard-review-alpha": "# Review the alpha change\n\n"
    "Read change.diff and pr.md before you judge the change.\n\n"
    "Write bench-result.json with your findings.\n",
    "docs-beta": "Bring the guide up to date. "
    + " ".join(f"word{index}" for index in range(70))
    + "\n\nDOCS_SECOND_PARAGRAPH stays out of an excerpt.\n",
    "fix-gamma": "Fix the gamma bug.\n",
    "coding-old": "Solve the old race.\n",
}


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
            + (ALPHA_TASK if task_id == "hard-review-alpha" else "")
        )
        (tasks / task_id / "prompt.md").write_text(PROMPTS[task_id])
        if card:
            (tasks / task_id / "track.toml").write_text(card)
    # The review race is a whole task, so track-facts stages it: two files.
    alpha = tasks / "hard-review-alpha"
    (alpha / "stage.sh").write_text(
        "set -eu\nprintf 'one\\ntwo\\nthree\\n' > \"$1/change.diff\"\n"
        "printf 'notes\\n' > \"$1/pr.md\"\n"
    )
    (alpha / "verifier.py").write_text("# synthetic\n")
    for directory in ("expected", "hidden"):
        (alpha / directory).mkdir()
        (alpha / directory / "answer.txt").write_text("ANSWER_SENTINEL\n")
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


@pytest.mark.parametrize(
    ("costs", "top", "expected"),
    [
        ([0.2, None, 0.1, 0.2, None], 2, 3),
        ([0.2, None, 0.1, 0.2, None], 4, 4),
        ([None, None, None], 2, 2),
        ([0.1], 16, 1),
        ([], 16, 0),
    ],
)
def test_top_fuel_rows_keep_priced_ties_whole(costs, top, expected):
    entries = [{"costPerPass": cost} for cost in costs]
    assert standings_pages.top_by_fuel(entries, top=top) == expected


def test_board_keeps_fuel_tie_at_sixteen_visible(tmp_path):
    files = _site_files(_site_tree(tmp_path))
    standings = _site_json(files, "standings.json")
    template = standings["entries"][0]
    costs = list(range(1, 16)) + [16, 16, 18, None]
    standings["entries"] = [
        {
            **template,
            "id": f"entry-{i}",
            "place": i + 1,
            "tie": False,
            "costPerPass": cost,
        }
        for i, cost in enumerate(reversed(costs))
    ]
    markup = standings_pages.board("../", standings, _site_json(files, "races.json"))
    rows = _rows(markup)
    assert len(rows) == 19
    for (_, row), entry in zip(rows, standings["entries"]):
        expected = entry["costPerPass"] is not None and entry["costPerPass"] <= 16
        assert f'data-top-fuel="{int(expected)}"' in row
    assert sum('data-top-fuel="1"' in row for _, row in rows) == 17
    assert sum('data-top-pass="1"' in row for _, row in rows) == 16
    assert markup.count('<span class="st-by-fuel">=16</span>') == 2


def test_entry_chips_disambiguate_duplicate_setups(tmp_path):
    files = _site_files(_site_tree(tmp_path))
    standings = _site_json(files, "standings.json")
    first = standings["entries"][0]
    first["configHash"] = "sha256:" + "a" * 64
    second = {**first, "id": "other-setup", "configHash": "sha256:" + "b" * 64}
    unique = {**first, "id": "unique", "driver": "other/driver"}
    standings["entries"] = [first, second, unique]
    chips = standings_pages._chips(first["id"], standings, "../../")
    links = re.findall(r"<a\b.*?</a>", chips)
    assert len(links) == 3
    assert 'aria-current="page"' in links[0]
    assert 'aria-current="page"' not in links[1]
    assert "setup <code>aaaaaaaa</code>" in links[0]
    assert "setup <code>bbbbbbbb</code>" in links[1]
    assert "setup" not in links[2]


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


@pytest.mark.parametrize("bucket", ["entries", "partial"])
def test_entry_pages_ignore_rows_awaiting_trust_sign_off(bucket):
    # No race or entry files: the awaiting state must be checked before loading them.
    standings = {
        "state": standings_pages.AWAITING,
        "entries": [],
        "partial": [],
    }
    standings[bucket] = [{"id": "untrusted-entry"}]
    build = site_routes.Build(
        results=None, data={"data/standings.json": standings}, routes=()
    )
    assert standings_pages.entry_pages(build) == {}


@pytest.mark.parametrize("ran", [0, 1, 2])
def test_clean_sweep_requires_a_complete_calendar(tmp_path, ran):
    files = _site_files(_site_tree(tmp_path))
    standings = _site_json(files, "standings.json")
    row = standings["entries"][0]
    entry = _site_json(files, f"entries/{row['id']}.json")
    entry["races"] = [race for race in entry["races"] if race["calendar"]][:ran]
    for race in entry["races"]:
        race["passed"] = True
    entry["place"] = row["place"] if ran == 2 else None
    entry["challenges"] = {"docs": {"k": ran, "n": ran}}
    entry["totals"].update(k=ran, n=ran, rate=1.0 if ran else 0.0)
    page = standings_pages.entry_body(
        entry, standings, _site_json(files, "races.json"), "../../"
    )
    if ran == 2:
        assert 'data-default-title="A clean sweep"' in page
        assert "a clean sweep" in page
    else:
        assert "clean sweep" not in page.lower()
        title = "Passed all 1 race run" if ran else "No calendar races run"
        assert f'data-default-title="{title}"' in page
        assert "so it is not placed" in page
        note = "all races run passed" if ran else "no calendar races run"
        assert note in page


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


# ---------------------------------------------------------------- races (PDW-06)
#
# The races grid and one page per calendar race, built from the synthetic
# site tree above. Every number on them is checked against the data files.

from paddock_site import names as site_names  # noqa: E402
from paddock_site import races as site_races  # noqa: E402


def _race_site(tmp_path: Path) -> tuple[Path, dict]:
    results = _site_tree(tmp_path)
    return results, build_site.build(results)


def _attr_json(page: str, name: str):
    """The JSON a page holds in its one ``data-<name>`` attribute."""
    (raw,) = re.findall(rf'data-{name}="([^"]*)"', page)
    return json.loads(html.unescape(raw))


def _texts(page: str, css_class: str) -> list[str]:
    """The text of every ``<span class="<css_class>">`` on the page."""
    return [
        html.unescape(text)
        for text in re.findall(rf'<span class="{css_class}">([^<]*)</span>', page)
    ]


def test_races_build_one_page_per_calendar_race(tmp_path):
    _, files = _race_site(tmp_path)
    races = json.loads(files["data/races.json"])["races"]
    calendar = [race["id"] for race in races if race["calendar"]]
    assert calendar and len(calendar) < len(races)  # one raced race is off it
    pages = {name for name in files if re.fullmatch(r"races/[^/]+/index\.html", name)}
    assert pages == {site_races.race_file(race) for race in calendar}
    grid = files["races/index.html"]
    cards = re.findall(r'<a class="rc-card" href="([^"]+)"', grid)
    assert [_resolve("races/index.html", href) for href in cards] == [
        site_races.race_file(race["id"])
        for race in sorted(
            (race for race in races if race["calendar"]),
            key=lambda race: (race["rate"], race["shortName"].lower(), race["id"]),
        )
    ]
    assert f"<h1>{len(calendar)} races. Pick one.</h1>" in grid
    for race in races:
        if not race["calendar"]:
            continue
        card = grid.split(f'href="{race["id"]}/index.html"', 1)[1].split("</a>", 1)[0]
        assert f'data-rate="{race["rate"]:.6f}"' in card
        assert f"{race['k']} of {race['n']} runs" in card
        assert f"{race['entries']} entries" in card
        assert html.escape(race["shortName"]) in card
        page = files[site_races.race_file(race["id"])]
        # The page links back to the grid and marks Races as the current route.
        assert '<a href="../index.html">All 2 races</a>' in page
        assert 'href="../../races/index.html" aria-current="page">Races</a>' in page
        assert f"<h1>{html.escape(race['shortName'])}</h1>" in page
    # The challenge chips count the calendar's races per challenge.
    for challenge in {race["challenge"] for race in races if race["calendar"]}:
        chip = grid.split(f'data-challenge="{challenge}">', 1)[1].split("</button>", 1)[
            0
        ]
        count = sum(r["calendar"] and r["challenge"] == challenge for r in races)
        assert f'<span class="rc-chip-n">{count}</span>' in chip
        assert 'class="roundel' in chip
    # The committed results build a page for every one of their calendar races.
    real = build_site.build(lb.DEFAULT_RESULTS)
    real_calendar = [
        race["id"]
        for race in json.loads(real["data/races.json"])["races"]
        if race["calendar"]
    ]
    assert real_calendar
    for race in real_calendar:
        assert site_races.race_file(race) in real


def test_race_pages_hold_the_site_invariants(tmp_path):
    _, files = _race_site(tmp_path)
    pages = {
        name: page
        for name, page in _html_pages(files).items()
        if name.startswith("races/")
    }
    assert len(pages) == 3
    published = set(re.findall(r'<symbol id="([^"]+)"', layout.sprite()))
    for name, page in pages.items():
        assert page.count("<script") == 3 and "<style" not in page, name
        assert page.count("<h1>") == 1, name
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        assert re.search(r"<title>[^<]+ · Agent Paddock</title>", head), name
        assert set(re.findall(r'<use href="#([^"]+)"', page)) <= published, name
        for _, attrs in Elements(page).items:
            if "href" in attrs and not urlsplit(attrs["href"]).scheme:
                target = _resolve(name, attrs["href"])
                assert target in files, (name, target)
    script = files["paddock.js"]
    assert site_races.SCRIPT in script and script.startswith(layout.SCRIPT)
    assert "getAttribute('data-' + other + '-second')" in script
    assert "(other === 'lap' ? ' lap' : '')" not in script
    assert str(site_races.PLAYBACK_MS) in pages["races/hard-review-alpha/index.html"]
    assert site_races.PLAYBACK_MS == 1600


def test_race_page_funnel_comes_from_the_data(tmp_path):
    _, files = _race_site(tmp_path)
    race = json.loads(files["data/races/hard-review-alpha.json"])
    page = files["races/hard-review-alpha/index.html"]
    views = _attr_json(page, "funnel")
    funnel = race["funnel"]
    assert [(v["mode"], v["key"]) for v in views] == (
        [("all", "")]
        + [("car", car) for car in funnel["byCar"]]
        + [
            ("driver", driver)
            for driver, counts in funnel["byDriver"].items()
            if counts[0] >= site_races.DRIVER_MIN_RUNS
        ]
    )
    # A driver below the five-run line is in the data and not on the page.
    assert any(
        counts[0] < site_races.DRIVER_MIN_RUNS for counts in funnel["byDriver"].values()
    )
    everyone = views[0]
    counts = funnel["all"]
    assert everyone["n"] == counts[0] == funnel["runs"]
    assert everyone["widths"] == [
        round(site_races.RIBBON_PX * c / counts[0], 1) for c in counts
    ]
    # Stations: lights out, each named checkpoint, the finish.
    names = _texts(page, "rc-station-name")
    assert names == ["Lights out", *race["checkpoints"], "Checkered flag"]
    stats = _texts(page, "rc-station-stat")
    assert stats[0] == f"{counts[0]} runs start"
    for k in range(1, len(race["checkpoints"]) + 1):
        before, after = counts[k - 1], counts[k]
        assert stats[k] == f"{after / before:.0%} clear · {before - after} retire"
    assert stats[-1] == f"{counts[-1]} of {counts[0]} finish"
    # The worst checkpoint is the one most runs retire at, called out in red.
    drops = [counts[k - 1] - counts[k] for k in range(1, len(race["checkpoints"]) + 1)]
    worst = drops.index(max(drops)) + 1
    assert everyone["worst"] == worst
    assert f'<li class="rc-station is-lit is-worst" data-station="{worst}">' in page
    assert f"The worst checkpoint is <b>{race['checkpoints'][worst - 1]}</b>" in page
    # Every car's view adds up to the same lap as its own data.
    for view in views[1:]:
        data = funnel["byCar" if view["mode"] == "car" else "byDriver"][view["key"]]
        assert view["n"] == data[0]
        assert view["stats"][-1] == f"{data[-1]} of {data[0]} finish"


@pytest.mark.parametrize(
    ("lap_seconds", "lap_label"),
    [(None, "lap n/a"), (0, "0:00 lap"), (95, "1:35 lap")],
)
@pytest.mark.parametrize(
    ("cost_usd", "fuel_label"),
    [(None, "no price"), (0, "$0.000"), (0.25, "$0.250")],
)
def test_classification_secondary_lap_reads_like_the_race_card(
    lap_seconds, lap_label, cost_usd, fuel_label
):
    row = {
        "lapSeconds": lap_seconds,
        "costUsd": cost_usd,
        "driver": "d",
        "car": "c",
        "map": "default",
    }
    html_row = site_races._finisher(1, row)
    # Sorting by fuel shows the lap as the secondary value: "lap n/a", never "n/a lap".
    attrs = Elements(html_row).items[0][1]
    assert attrs["data-lap-second"] == lap_label
    assert attrs["data-usd-second"] == attrs["data-usd-text"] == fuel_label
    assert _texts(html_row, "rc-second") == [attrs["data-usd-second"]]


def test_race_page_classification_comes_from_the_data(tmp_path):
    _, files = _race_site(tmp_path)
    race = json.loads(files["data/races/hard-review-alpha.json"])
    page = files["races/hard-review-alpha/index.html"]
    classification = race["classification"]
    finishers = classification["finishers"]
    rows = re.findall(r'<li class="rc-finisher"[^>]*>.*?</li>', page)
    assert len(rows) == len(finishers)
    for position, (row, finisher) in enumerate(zip(rows, finishers), 1):
        assert f">P{position}</span>" in row
        assert f'data-lap="{finisher["lapSeconds"]}"' in row
        assert f'data-usd="{finisher["costUsd"]}"' in row
        assert f"{html.escape(finisher['driver'])} <span>in " in row
        assert site_races.lap_text(finisher["lapSeconds"]) in row
    assert (
        f'<h2 class="rc-h2">{len(finishers)} finished. '
        f"{classification['retired']} did not.</h2>"
    ) in page
    retirements = classification["retirements"]
    dnf = page.split('<ul class="rc-dnf-rows">', 1)[1].split("</ul>", 1)[0]
    shown = re.findall(
        r'<span class="rc-dnf-name">([^<]*)</span>.*?<span class="rc-dnf-n">(\d+)</span>',
        dnf,
    )
    expected = [(c["label"], str(c["runs"])) for c in retirements["checkpoints"]]
    if retirements["undisclosed"]:
        expected.append(
            ("At checks the track card does not name", str(retirements["undisclosed"]))
        )
    assert shown == expected
    # Over ten finishers: the first ten, then "Show all" (the script hides the rest).
    many = dict(race)
    many["classification"] = dict(
        classification, finishers=[finishers[0]] * 12, retired=0
    )
    more = site_races.race_page(many, None, 2, build_site.ROUTES)
    assert "Show all 12 finishers" in more and 'data-first="10"' in more


def test_race_page_shows_the_briefing_as_the_track_card_allows(tmp_path):
    results = _site_tree(tmp_path)
    tasks = results.parent / "tasks"
    card = tasks / "docs-beta" / "track.toml"
    base = (
        'name = "Beta circuit"\npitch = "Update the guide."\n'
        'challenge = "docs"\ninfographic = "docs"\ncheckpoints = []\n'
    )
    prompt = PROMPTS["docs-beta"]
    first, second = prompt.split("\n\n")

    def page(show: str | None) -> str:
        if show is None:
            card.unlink(missing_ok=True)
        else:
            card.write_text(base + f'[briefing]\nshow = "{show}"\n')
        return build_site.build(results)["races/docs-beta/index.html"]

    def brief(text: str) -> str:
        return f'<div class="rc-brief-text">{html.escape(text)}</div>'

    for show in (None, "full"):
        full = page(show)
        assert brief(first) in full, show
        assert brief(second.strip()) in full, show
        assert "<summary>Read it all</summary>" in full
    excerpt = page("excerpt")
    words = first.split()
    assert len(words) > site_data.EXCERPT_WORDS
    cut = " ".join(words[: site_data.EXCERPT_WORDS]) + " …"
    assert brief(cut) in excerpt
    assert words[site_data.EXCERPT_WORDS] not in excerpt
    assert "DOCS_SECOND_PARAGRAPH" not in excerpt
    assert "Read it all" not in excerpt
    hidden = page("none")
    assert "word0" not in hidden and "DOCS_SECOND_PARAGRAPH" not in hidden
    assert "keeps this race's briefing off the site" in hidden
    # No data file ever holds the prompt's text.
    for name, text in build_site.build(results).items():
        if name.startswith("data/"):
            assert "word0" not in text and "Read change.diff" not in text, name
    # A heading opens the review prompt: the lead runs through the first paragraph.
    alpha = build_site.build(results)["races/hard-review-alpha/index.html"]
    head, lead, rest = PROMPTS["hard-review-alpha"].strip().split("\n\n")
    assert brief(f"{head}\n\n{lead}") in alpha and brief(rest) in alpha


@pytest.mark.parametrize("show", ["excerpt", "full"])
@pytest.mark.parametrize("separator", ["\n\n\n\n", "\n \n\t\n\n"])
def test_briefing_skips_blank_paragraphs_after_heading(tmp_path, show, separator):
    results = _site_tree(tmp_path)
    task = results.parent / "tasks" / "hard-review-alpha"
    heading, lead, rest = PROMPTS["hard-review-alpha"].strip().split("\n\n")
    (task / "prompt.md").write_text(heading + separator + lead + "\n\n" + rest)
    card = task / "track.toml"
    card.write_text(card.read_text() + f'\n[briefing]\nshow = "{show}"\n')

    files = build_site.build(results)
    page = files["races/hard-review-alpha/index.html"]
    assert '<div class="rc-brief-text"></div>' not in page
    if show == "excerpt":
        assert f'<div class="rc-brief-text">{html.escape(lead)}</div>' in page
        assert "Read it all" not in page
        assert rest not in page
    else:
        opening, disclosure = page.split("<details", 1)
        assert html.escape(lead) in opening
        assert html.escape(rest) in disclosure


@pytest.mark.parametrize("prompt_bytes", [None, b"\xff"])
def test_site_build_tolerates_unreadable_track_prompt(tmp_path, prompt_bytes):
    results = _site_tree(tmp_path)
    prompt = results.parent / "tasks" / "hard-review-alpha" / "prompt.md"
    if prompt_bytes is None:
        prompt.unlink()
    else:
        prompt.write_bytes(prompt_bytes)

    files = build_site.build(results)
    race = json.loads(files["data/races/hard-review-alpha.json"])
    assert race["track"] is None
    assert race["briefing"] == {"show": "full", "words": None}
    page = files["races/hard-review-alpha/index.html"]
    assert "On the track ·" not in page
    assert '<div class="rc-brief-text">' not in page
    for chip in site_races.sealed_circuit(lb.track_conditions()):
        assert f"<li>{html.escape(chip)}</li>" in page


def test_no_page_names_a_checkpoint_the_track_card_hides(tmp_path):
    results, files = _race_site(tmp_path)
    for name, text in _text_files(files):
        for sentinel in (*SENTINELS, "ANSWER_SENTINEL"):
            assert sentinel not in text, (name, sentinel)
        if name.endswith(".html"):
            assert "fact:" not in text, name
    # Disclosing a counted checkpoint by label names it; hidden and counted
    # checkpoints stay unnamed on every page.
    card = results.parent / "tasks" / "hard-review-alpha" / "track.toml"
    card.write_text(
        card.read_text().replace(
            'label = "COUNT_LABEL_SENTINEL"\ndisclose = "count"',
            'label = "COUNT_LABEL_SENTINEL"\ndisclose = "label"',
        )
    )
    shown = build_site.build(results)["races/hard-review-alpha/index.html"]
    assert "COUNT_LABEL_SENTINEL" in shown
    assert "HIDDEN_LABEL_SENTINEL" not in shown and "FACT_LABEL_SENTINEL" not in shown


def test_race_page_track_files_and_sealed_circuit_come_from_track_facts(tmp_path):
    results, files = _race_site(tmp_path)
    page = files["races/hard-review-alpha/index.html"]
    facts = lb.track_facts(
        results.parent / "tasks" / "hard-review-alpha", lb.load_results(results)
    )
    staged = facts["staged"]
    assert (staged["files"], staged["lines"]) == (2, 4)
    assert f"On the track · {staged['files']} files, {staged['lines']} lines" in page
    chips = site_races.sealed_circuit(facts["conditions"])
    assert facts["conditions"] == lb.track_conditions()
    assert chips[:4] == [
        "2 processor cores",
        "2 GiB memory",
        "256 processes at most",
        "60-minute time limit",
    ]
    for chip in chips:
        assert f"<li>{html.escape(chip)}</li>" in page
    # A race without a card has no staged counts, and the same sealed circuit.
    docs = files["races/docs-beta/index.html"]
    assert "On the track ·" not in docs
    for chip in chips:
        assert f"<li>{html.escape(chip)}</li>" in docs


def test_races_grid_awaits_trust_sign_off_without_cards(tmp_path):
    demoted = {BENCH: {"level": "self-reported", "evidence": [], "sign_off": None}}
    results = _site_tree(tmp_path, demoted)
    files = build_site.build(results)
    grid = files["races/index.html"]
    assert "<h1>Races are on their way.</h1>" in grid
    assert "rc-card" not in grid and "data-races-grid" not in grid
    assert not [name for name in files if re.fullmatch(r"races/.+/index\.html", name)]


def test_card_stage_bars_mark_the_weakest_in_red():
    race = {
        "id": "hard-review-x",
        "challenge": "hard-review",
        "shortName": "X",
        "title": "T",
        "rate": 0.4,
        "k": 4,
        "n": 10,
        "entries": 3,
        "medianLapSeconds": 125.0,
        "medianCostUsd": 0.0421,
        "stages": {
            "checkpoints": [
                {"label": "Read", "recorded": 10, "clearRate": 0.9},
                {"label": "Verdict", "recorded": 10, "clearRate": 0.5},
                {"label": "Clean", "recorded": 10, "clearRate": 1.0},
            ],
            "moreChecks": 2,
        },
    }
    card = site_races.race_card(race, "x/index.html")
    assert re.findall(r'<li class="rc-stage([^"]*)">', card) == ["", " is-weak", ""]
    assert '<span class="rc-rate is-red">40%</span>' in card
    assert "and 2 more checks" in card
    assert "2:05 lap" in card and "$0.042 a run" in card
    race["stages"] = None
    assert "Stages not recorded yet." in site_races.race_card(race, "x/index.html")
    race.update(rate=None, k=0, n=0)
    uncounted = site_races.race_card(race, "x/index.html")
    assert 'data-rate=""' in uncounted
    assert '<span class="rc-rate">n/a</span>' in uncounted
    assert "0 of 0 runs" in uncounted


@pytest.mark.parametrize(
    ("task_id", "suite", "card", "name"),
    [
        ("coding-pr-not-found-terminal", "coding", None, "Pull request not found"),
        ("rca-fk-drain", "rca", None, "Foreign-key drain"),
        (
            "hard-review-decoy-job-leases",
            "hard-review",
            "Job lease circuit",
            "Decoy job leases",
        ),
        ("community-thing", "coding", "Thing circuit", "Thing circuit"),
        ("coding-pr-db-fix", "coding", None, "Pull request database fix"),
    ],
)
def test_short_names_spell_out_acronyms(task_id, suite, card, name):
    assert site_names.short_name(task_id, suite, card) == name


def test_every_local_task_has_a_short_name():
    for path in (Path(__file__).resolve().parents[1] / "tasks").glob("*/task.toml"):
        assert path.parent.name in site_names.SHORT_NAMES, path.parent.name
# ---------------------------------------------------------------- explainer pages (PDW-09)

from paddock_site import community as site_community  # noqa: E402
from paddock_site import contact as site_contact  # noqa: E402
from paddock_site import dictionary as site_dictionary  # noqa: E402
from paddock_site import pit_lane as site_pit_lane  # noqa: E402
from paddock_site import rulebook as site_rulebook  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CODE_BOX = re.compile(
    r'<div class="code"><pre[^>]*><code>(.*?)</code></pre>', re.DOTALL
)


def _main(page: str) -> str:
    return _between(page, '<main class="wrap">', "</main>")


def _indented_in(code: str, source: str) -> bool:
    """``code`` appears in ``source`` line for line, at one common indent."""
    lines = code.split("\n")
    source_lines = source.splitlines()
    for start, line in enumerate(source_lines):
        indent = line[: len(line) - len(line.lstrip())]
        if line != indent + lines[0]:
            continue
        window = source_lines[start : start + len(lines)]
        if window == [(indent + x) if x else x.rstrip() for x in lines] or window == [
            indent + x for x in lines
        ]:
            return True
    return False


def test_pit_lane_commands_equal_the_readme_and_contributing_text():
    page = build_site.build(FIXTURE)["race-it-yourself/index.html"]
    boxes = [html.unescape(code) for code in CODE_BOX.findall(page)]
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    runners = (REPO / "design" / "guide" / "race-it-yourself.md").read_text("utf-8")
    # Clone and install, the standalone config, the run, from-run, validate.
    assert len(boxes) == 5
    install, config, run, from_run, validate = boxes
    assert install.startswith(
        "git clone https://github.com/laceyenterprises/agentpaddock"
    )
    assert _indented_in(install, readme.split("## Quick start", 1)[1])
    # The README's runner section lives in the guide the README links to.
    assert "design/guide/race-it-yourself.md#runners-pdk-01" in readme
    assert config.startswith('runner = "standalone"') and "[standalone]" in config
    assert _indented_in(config, runners)
    bundle = contributing.split("## Submit results: a run bundle", 1)[1].split(
        "\n## ", 1
    )[0]
    assert "python3 -m gym.cli run" in run and _indented_in(run, bundle)
    assert "scripts/leaderboard.py from-run" in from_run and _indented_in(
        from_run, bundle
    )
    assert "leaderboard.py validate" in validate and _indented_in(validate, bundle)
    # Six steps, one copy button per code box, and the fallback in the script.
    main = _main(page)
    assert main.count('<li class="step"') == 6
    assert main.count('class="copy"') == len(boxes)
    script = build_site.build(FIXTURE)["paddock.js"]
    assert "Select and copy" in script and "execCommand('copy')" in script
    # A tap on the card advances, except inside a code box.
    assert "closest('.code, a, button, summary')" in script


def test_pit_lane_refuses_a_missing_source_block(tmp_path):
    (tmp_path / "README.md").write_text("# Title\n\n## Quick start\n\nNo code.\n")
    with pytest.raises(site_pit_lane.SourceError):
        site_pit_lane.command(site_pit_lane.COMMANDS["install"], tmp_path)
    text = "## A\n\n1. Step:\n\n   ```bash\n   one \\\n     two\n   ```\n\n## B\n\n```\nthree\n```\n"
    assert site_pit_lane.fenced_blocks(text, "## A") == ["one \\\n  two"]


def test_pit_lane_citations_use_heading_anchors_and_are_unique_per_step():
    page = build_site.build(FIXTURE)["race-it-yourself/index.html"]
    repo = "https://github.com/laceyenterprises/agentpaddock/blob/main/"
    expected = (
        "README.md#quick-start",
        "design/guide/race-it-yourself.md#runners-pdk-01",
        "design/guide/race-it-yourself.md#runners-pdk-01",
        "CONTRIBUTING.md#submit-results-a-run-bundle",
        "CONTRIBUTING.md#submit-results-a-run-bundle",
        "CONTRIBUTING.md#trust-levels",
    )
    for number, target in enumerate(expected, start=1):
        step = _between(page, f'id="step-{number}"', '<div class="step-foot">')
        citations = re.findall(r'<p class="from">From <a href="([^"]+)">', step)
        assert citations == [repo + target]
    results_step = _between(page, 'id="step-5"', "</li>")
    assert len(CODE_BOX.findall(results_step)) == 2


def test_every_dictionary_term_has_a_sprite_symbol_and_the_index_is_covered():
    index = json.loads(
        (REPO / "design" / "glyphs" / "index.json").read_text(encoding="utf-8")
    )
    terms = site_dictionary.terms(54)
    assert [t.glyph for t in terms] == [
        item["id"]
        for item in index
        if item["group"] not in site_dictionary.SKIPPED_GROUPS
    ]
    assert {t.glyph for t in terms} <= PUBLISHED_GLYPHS
    page = build_site.build(FIXTURE)["dictionary/index.html"]
    main = _main(page)
    for term in terms:
        card = _between(main, f'id="term-{term.glyph}">', "</li>")
        assert f'<use href="#{term.glyph}"/>' in card, term.glyph
        assert f"<h3>{html.escape(term.term)}</h3>" in card, term.glyph
    # The contact glyph is a term of its own, and it leads to the contact page.
    pit_board = _between(main, 'id="term-pit-board">', "</li>")
    assert 'href="../contact/index.html"' in pit_board
    assert "Where you meet it: Contact and about" in pit_board
    # Search and group chips ride on the script; the content reads without it.
    assert 'type="search"' in main and 'data-chips="group"' in main
    assert "Race calendar" in main


def test_dictionary_calendar_count_comes_from_the_site_data():
    assert "All 7 races" in site_dictionary.terms(7)[15].meaning
    assert not re.search(r"\d", site_dictionary.terms(0)[15].meaning)


@pytest.mark.parametrize(
    "meaning",
    ["Literal {name}", "Literal {", "Literal }", "Literal {calendar}"],
)
def test_dictionary_fallback_meanings_preserve_braces(tmp_path, meaning):
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            [{"id": "new-term", "group": "cast", "meaning": f"New term: {meaning}"}]
        ),
        encoding="utf-8",
    )
    (term,) = site_dictionary.terms(7, index)
    assert term.term == "New term"
    assert term.meaning == meaning


def test_every_rule_restates_a_source_section_that_exists():
    rules = site_rulebook.rules(54)
    assert len(rules) == 16
    assert {r.glyph for r in rules} <= PUBLISHED_GLYPHS
    methodology = build_site.build(FIXTURE)["methodology.html"]
    for rule in rules:
        source = rule.source
        if source.document == site_rulebook.METHODOLOGY:
            assert f"<h3>{source.section}</h3>" in methodology, rule.title
            continue
        text = (REPO / source.document).read_text(encoding="utf-8")
        headings = {
            line.lstrip("#").strip()
            for line in text.splitlines()
            if line.startswith("#")
        }
        assert source.section in headings, (rule.title, source)
    main = _main(build_site.build(FIXTURE)["rulebook/index.html"])
    assert main.count('<details class="rule">') == 16
    assert main.count('<p class="from">Restates <a href=') == 16
    assert "contributing-md" not in main
    assert "CONTRIBUTING.md#submit-results-a-run-bundle" in main
    assert 'href="../methodology.html"' in main


def test_contact_page_has_the_pit_board_the_mailto_and_the_challenge_subject():
    page = build_site.build(FIXTURE)["contact/index.html"]
    main = _main(page)
    hero = _between(main, '<div class="page-hero explain-hero">', "</div>")
    assert '<use href="#pit-board"/>' in hero
    assert '<use href="#team-radio"/>' not in main
    assert 'href="mailto:hello@agentpaddock.ai"' in main
    assert 'href="mailto:hello@agentpaddock.ai?subject=Result%20challenge"' in main
    assert 'href="https://github.com/laceyenterprises/agentpaddock/issues"' in main
    assert "Lacey Enterprises runs it." in main
    for title in (
        "Ask about the program",
        "Challenge a result",
        "Suggest a race or an entry",
        "Race it yourself",
    ):
        assert f"<h3>{title}</h3>" in main, title
    assert 'id="about"' in main
    # The footer's two lines are on this page too, and it links Contact and about.
    footer = _between(page, '<footer class="footer">', "</footer>")
    assert layout.FOOTER_DISCLAIMER in footer and layout.FOOTER_INDEPENDENCE in footer
    assert ">Contact and about</a>" in footer


def test_community_shows_examples_until_real_records_exist(tmp_path):
    results = _site_tree(tmp_path)
    files = build_site.build(results)
    main = _main(files["community/index.html"])
    # The fork document is self-reported: a real card with its badge, no empty state.
    assert "gamma-cli · bolt/coder-small" in main
    assert "The board is empty for now" not in main
    assert '<span class="badge badge-self badge-sm">' in main
    community = json.loads(files["data/community.json"])
    real = [e for e in community["entries"] if e["trust"] != "paddock-run"]
    assert main.count('class="tile result"') == len(real) + 1  # board + fork tab
    for entry in real:
        assert f"<b>{entry['k']} of {entry['n']}</b>" in main
    # Without a community record: the honest empty state and examples only.
    data = {
        "data/community.json": {
            **community,
            "documents": [
                d for d in community["documents"] if d["trust"] == "paddock-run"
            ],
            "entries": [e for e in community["entries"] if e["trust"] == "paddock-run"],
        }
    }
    route = next(r for r in build_site.ROUTES if r.path == "community/")
    empty = site_community.body(route, data)
    assert "The board is empty for now" in empty
    assert 'class="tile result"' not in empty
    assert empty.count('<span class="tag-example">EXAMPLE</span>') == 3
    assert 'href="../race-it-yourself/index.html"' in empty
    # Tabs and the trust ladder with every badge.
    for key in ("entries", "tracks", "forks"):
        assert f'role="tabpanel" id="tab-{key}"' in empty
    ladder = _between(empty, '<ol class="ladder">', "</ol>")
    assert [label for label, _c, _m in site_community.kit.TRUST.values()] == [
        "Self-reported",
        "Re-graded",
        "Reproduced",
        "Paddock run",
    ]
    for label, _css, _meaning in site_community.kit.TRUST.values():
        assert f"</svg>{label}</span>" in ladder


def test_explainer_page_numbers_come_from_the_site_data(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    races = json.loads(files["data/races.json"])["basis"]["calendarRaces"]
    assert races > 0
    assert f"a fixed calendar of {races} tasks" in files["contact/index.html"]
    assert f"{races} races is a small sample." in files["rulebook/index.html"]
    assert f"All {races} races, grouped by challenge" in files["dictionary/index.html"]
    assert site_contact.CHALLENGE_SUBJECT == "Result challenge"
# ---------------------------------------------------------------- home (PDW-04)

from paddock_site import home as site_home  # noqa: E402
from paddock_site import site_config  # noqa: E402

#: Numbers and names the prototype shows as samples; the real page computes its own.
PROTOTYPE_SAMPLES = (
    "96.3%",
    "52 of the 54",
    "17.9%",
    "Tool calls v2",
    "$0.035",
    "$0.285",
    "GPT-6.1 Sol",
    "Gemini 3.8 Flash",
    "Qwen 3.8 27B",
    "87.5% to 99.0%",
)


def _home(files) -> str:
    return _between(files["index.html"], '<main class="wrap">', "</main>")


def _readme_footer_lines() -> list[str]:
    readme = (WEB_DESIGN / "README.md").read_text(encoding="utf-8")
    bullet = readme.split("**Every page's footer**", 1)[1].split("\n- **", 1)[0]
    lines = re.findall(r'"([^"]+)"', " ".join(bullet.split()))
    assert len(lines) == 2, lines
    return lines


def test_home_builds_from_the_site_data_fixtures(tmp_path):
    results = _site_tree(tmp_path)
    files = build_site.build(results)
    assert build_site.build(results) == files
    main = _home(files)
    standings = json.loads(files["data/standings.json"])
    races = json.loads(files["data/races.json"])
    # The photo finish names the tied leaders from the data.
    leaders = [e for e in standings["entries"] if e["place"] == 1]
    assert len(leaders) == 2 and all(e["tie"] for e in leaders)
    finish = _between(main, '<section class="home-pf">', "</section>")
    assert "A photo finish." in finish
    assert (
        f"Two entries finished {leaders[0]['k']} of the {leaders[0]['n']} races."
        in finish
    )
    for leader in leaders:
        assert html.escape(site_home.entry_name(leader)) in finish
    assert finish.count('<a class="home-leader" href="standings/index.html">') == 2
    assert site_home.pct(leaders[0]["rate"]) in finish
    # The hardest race and the challenge row read races.json.
    calendar = [r for r in races["races"] if r["calendar"]]
    hardest = min(calendar, key=lambda r: r["rate"])
    card = _between(main, '<a class="home-card home-card-dark"', "</a>")
    assert (
        html.escape(hardest["name"]) in card and site_home.pct(hardest["rate"]) in card
    )
    assert f"of {hardest['n']} runs finished" in card
    for row in races["challenges"]:
        members = [r for r in calendar if r["challenge"] == row["challenge"]]
        assert row["races"] == len(members)
        assert (row["k"], row["n"]) == (
            sum(r["k"] for r in members),
            sum(r["n"] for r in members),
        )
        label = site_glyphs.RACE_TYPES[row["challenge"]][0]
        assert f"{label} · {site_home.pct(row['rate'])} average pass rate" in main
    # The hardest challenge is preselected and named as such.
    pressed = re.findall(
        r'<button type="button" class="challenge-card" data-pick="challenge" '
        r'aria-pressed="true".*?<span class="challenge-name">([^<]+)</span>',
        main,
    )
    assert pressed == ["Hard review"]
    assert "The hardest challenge on the calendar." in main
    # No prototype sample value reaches the page.
    text = " ".join(" ".join(Text(main).text).split())
    for sample in PROTOTYPE_SAMPLES:
        assert sample not in text, sample
    assert "Learn the paddock in 60 seconds." in main
    assert "Seven challenges. One is brutal." in main


def test_home_fuel_card_compares_a_shared_finish(tmp_path):
    standings = {
        "entries": [
            {
                "id": "a",
                "driver": "d/one",
                "car": "pi",
                "map": "default",
                "place": 1,
                "costPerPass": 0.04,
            },
            {
                "id": "b",
                "driver": "d/two",
                "car": "pi",
                "map": "high",
                "place": 1,
                "costPerPass": 0.32,
            },
            {
                "id": "c",
                "driver": "d/three",
                "car": "codex",
                "map": "low",
                "place": 3,
                "costPerPass": 0.01,
            },
        ]
    }
    card = site_home.fuel_card(standings)
    assert "Same finish. 8× the mileage." in card
    assert "d/one in pi passes a race for $0.040." in card
    assert "d/two in pi · high, tied on the finish, pays $0.320." in card
    # Without two priced entries sharing a place, the card makes no comparison.
    alone = site_home.fuel_card({"entries": standings["entries"][1:]})
    assert "Same finish" not in alone and "The fuel bill." in alone
    free = site_home.fuel_card({"entries": [dict(standings["entries"][0], costPerPass=0.0)]})
    assert "best-placed priced entry" in free


def test_home_without_trusted_results_says_so(tmp_path):
    demoted = {BENCH: {"level": "self-reported", "evidence": [], "sign_off": None}}
    main = _home(build_site.build(_site_tree(tmp_path, demoted)))
    assert "The grid is forming." in main and "home-leader" not in main
    assert "Not decided yet" in main
    assert "a fixed calendar of tasks" in main
    # The committed leaderboard fixture is not trusted either, and still builds.
    assert "The grid is forming." in _home(build_site.build(FIXTURE))


@pytest.mark.parametrize(
    ("finishes", "dnfs", "expected"),
    [
        (1, 3, ["passed", "dnf", "dnf", "dnf"]),
        (0, 3, ["dnf", "dnf", "dnf"]),
        (3, 0, ["passed", "passed", "passed"]),
        (0, 0, []),
        (3, 1, ["passed", "dnf", "passed", "passed"]),
        (3, 3, ["passed", "dnf", "passed", "dnf"]),
        (1, 6, ["passed", "dnf", "dnf", "dnf"]),
    ],
)
def test_home_sample_ticks_fill_budget_with_unpaired_results(finishes, dnfs, expected):
    entries = {}
    for i in range(max(finishes, dnfs)):
        entries[str(i)] = {
            "driver": f"Driver {i}",
            "car": "Car",
            "races": [
                {
                    "calendar": True,
                    "passed": passed,
                    "challenge": "coding",
                    "name": f"Race {i}",
                }
                for passed, count in ((True, finishes), (False, dnfs))
                if i < count
            ],
        }
    standings = {"entries": [{"id": key} for key in entries]}
    ticks = site_home.sample_ticks(standings, entries)
    assert [tick["kind"] for tick in ticks] == expected + ["rerun"]
    assert len({(tick["who"], tick["kind"]) for tick in ticks}) == len(ticks)


def test_home_ticker_drops_prototype_kinds_and_preserves_supported_flags():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the ticker JavaScript")
    # Execute the emitted normalizer, using its actual alias and flag tables.
    tables = site_home.SCRIPT.split("  var FLAGS = ", 1)[1].split("  var stamp =", 1)[0]
    normalizer = site_home.SCRIPT.split("  var text = function", 1)[1].split(
        "  var samples = [];", 1
    )[0]
    script = (
        "var FLAGS = "
        + tables
        + "var each = function (list, fn) { list.forEach(fn); };\n"
        + "var text = function"
        + normalizer
        + """
var tick = function (kind, extra) {
  return Object.assign({kind: kind, who: 'Driver in Car', what: 'Run'}, extra);
};
var invalid = Object.getOwnPropertyNames(Object.prototype).concat(['unknown']);
var rejected = clean(invalid.map(function (kind) { return tick(kind); }));
var aliases = clean(Object.keys(KINDS).map(function (kind) { return tick(kind.toUpperCase()); }));
var reruns = clean([
  tick('dnf', {infra: true}), tick('constructor', {infrastructure: true}),
  tick('__proto__', {rerun: true}), tick('unknown', {flagName: 'RED · RERUN'})
]);
var mixed = clean([tick('passed'), tick('constructor'), tick('__proto__'), tick('dnf')]);
console.log(JSON.stringify({rejected: rejected, aliases: aliases, reruns: reruns, mixed: mixed}));
"""
    )
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True, timeout=10
    )
    normalized = json.loads(result.stdout)
    assert normalized["rejected"] == []
    assert [t["kind"] for t in normalized["aliases"]] == list(
        site_home.TICK_ALIASES.values()
    )
    assert [t["kind"] for t in normalized["reruns"]] == ["rerun"] * 4
    assert [t["kind"] for t in normalized["mixed"]] == ["passed", "dnf"]
    assert all(
        tick["kind"] in site_home.TICK_FLAGS
        for ticks in normalized.values()
        for tick in ticks
    )


def test_home_ticker_reads_the_configured_feed_and_falls_back_to_labelled_samples(
    tmp_path,
):
    results = _site_tree(tmp_path)
    main = _home(build_site.build(results))
    ticker = _between(main, '<section class="home-ticker"', "</section>")
    feed = re.search(r'data-feed="([^"]*)"', ticker)[1]
    assert (
        feed == site_config.DEFAULT_LIVE_FEED_URL == "https://api.agentpaddock.ai/live"
    )
    assert 'data-source="sample"' in ticker
    assert (
        f'<span class="home-ticker-stamp" data-stamp>{site_home.SAMPLE_STAMP}</span>'
        in ticker
    )
    assert site_home.SAMPLE_STAMP == "Sample runs"
    samples = json.loads(html.unescape(re.search(r'data-samples="([^"]*)"', ticker)[1]))
    kinds = [tick["kind"] for tick in samples]
    assert set(kinds) == {"passed", "dnf", "rerun"} and kinds[-1] == "rerun"
    standings = json.loads(build_site.build(results)["data/standings.json"])
    names = {site_home.entry_name(e) for e in standings["entries"]}
    assert all(t["who"] in names for t in samples if t["kind"] != "rerun")
    # The first sample is drawn on the page without JavaScript.
    assert html.escape(samples[0]["who"]) in ticker
    # An infrastructure rerun is the red flag, never DNF.
    assert site_home.TICK_FLAGS["rerun"] == ("flag-red", "RED · RERUN")
    for word in ("rerun", "red", "infra", "infrastructure"):
        assert site_home.TICK_ALIASES[word] == "rerun"
    script = build_site.build(results)["paddock.js"]
    assert script.count(site_home.SCRIPT) == 1
    for needle in (
        "'paddock-live/v1'",
        "AbortController",
        "4000",
        "t.infra === true",
        "Live from the bench",
        "useSamples()",
        "textContent",
    ):
        assert needle in site_home.SCRIPT, needle
    assert "innerHTML" not in site_home.SCRIPT
    assert site_home.SCRIPT.index("t.infra === true") < site_home.SCRIPT.index("KINDS[")
    # The feed URL is a setting: overridden, turned off or refused.
    other = _home(build_site.build(results, {"live_feed_url": "live.json"}))
    assert 'data-feed="live.json"' in other
    off = _home(build_site.build(results, {"live_feed_url": ""}))
    assert 'data-feed=""' in off


@pytest.mark.parametrize(
    "value",
    [
        "http://api.agentpaddock.ai/live",
        "https://user:" + "secret@api.example/live",  # built from fragments
        "/live",
        "javascript:alert(1)",
        'live.json" onload="x',
        "//api.agentpaddock.ai/live",
        3,
    ],
)
def test_site_config_refuses_a_bad_feed_url(value):
    with pytest.raises(site_config.ConfigError):
        site_config.resolve({"live_feed_url": value})


def test_site_config_cli_override_and_refusals(tmp_path, capsys):
    assert site_config.resolve() == {"live_feed_url": site_config.DEFAULT_LIVE_FEED_URL}
    with pytest.raises(site_config.ConfigError, match="unknown"):
        site_config.resolve({"live_feed": "x"})
    config = tmp_path / "site.json"
    config.write_text('{"live_feed_url": "data/live.json"}', encoding="utf-8")
    out = tmp_path / "out"
    args = ["--results", str(FIXTURE), "--out", str(out), "--config", str(config)]
    assert build_site.main(args) == 0
    assert 'data-feed="data/live.json"' in (out / "index.html").read_text("utf-8")
    assert build_site.main(args + ["--check"]) == 0
    config.write_text('["not an object"]', encoding="utf-8")
    assert build_site.main(args) == 1
    assert "site config refused" in capsys.readouterr().err


def test_home_tiles_and_roundels_use_glyph_set_symbols_only(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    main = _home(files)
    marks = {mark for _label, mark, _body in site_glyphs.RACE_TYPES.values()}
    uses = re.findall(r'<use href="#([^"]+)"', main)
    assert set(uses) <= PUBLISHED_GLYPHS | marks
    # Race-type marks appear only inside a roundel frame.
    for mark in marks:
        assert main.count(f'<use href="#{mark}"/>') == main.count(
            f'<use href="#roundel"/><g transform="translate(5.4 5.4) scale(0.55)"><use href="#{mark}"/>'
        )
    learn = _between(main, '<div class="cast-grid"', "</div>")
    tiles = re.findall(r'<button type="button" class="cast-tile".*?</button>', learn)
    assert [re.search(r'<use href="#([^"]+)"', t)[1] for t in tiles] == [
        "driver",
        "car",
        "engine-map",
        "tires",
        "team-radio",
        "stewards",
        "fuel",
    ]
    assert [re.search(r"<span>([^<]+)</span>", t)[1] for t in tiles] == [
        term for term, _glyph, _body in site_home.CAST
    ]
    assert "The board does not measure skills yet." in main
    row = _between(main, "data-challenge-row>", "</div>")
    roundels = re.findall(r'<svg class="roundel roundel-([a-z-]+)', row)
    assert roundels == list(site_glyphs.RACE_TYPES) and len(roundels) == 7
    for glyph, _name in site_home.TICK_FLAGS.values():
        assert glyph in PUBLISHED_GLYPHS
    for glyph in (
        "start-lights",
        "flag-green",
        "photo-finish",
        "rulebook",
        "fuel-bill",
    ):
        assert f'<use href="#{glyph}"/>' in main, glyph
    # The tiles are 4 columns below 720 px and 7 across wider.
    css = files["paddock.css"]
    assert (
        ".cast-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));"
        in css
    )
    wide = site_home.CSS.split("@media (min-width: 720px) {", 1)[1].split("\n}\n", 1)[0]
    assert ".cast-grid { grid-template-columns: repeat(7, minmax(0, 1fr)); }" in wide


def test_home_swipe_cue_shows_below_720_px_only(tmp_path):
    main = _home(build_site.build(_site_tree(tmp_path)))
    cue = _between(
        main, '<div class="swipe-cue swipe-only" aria-hidden="true">', "</div>"
    )
    assert "Swipe for all seven" in cue and cue.count("data-dot") == 7
    assert '<span class="challenge-fade swipe-only" aria-hidden="true">' in main
    css = site_home.CSS
    wide = css.split("@media (min-width: 720px) {", 1)[1].split("\n}\n", 1)[0]
    assert ".swipe-only { display: none; }" in wide
    assert ".swipe-only" not in css.replace(wide, "")
    # Below 720 px the row scrolls sideways with narrower cards so the next peeks.
    rules = _css_rules(css.replace(wide, ""))
    assert "overflow-x: auto" in rules[".challenge-row"]
    assert "flex: 0 0 92px" in rules[".challenge-card"]
    assert "overflow: visible" in wide and "repeat(7, minmax(0, 1fr))" in wide
    # Touch targets: every home button and tile is at least 44 px tall.
    for selector in (
        ".cast-tile",
        ".challenge-card",
        ".home-start",
        ".home-why summary",
    ):
        height = re.search(r"min-height: (\d+)px", rules[selector])
        assert height and int(height[1]) >= 44, selector


def test_home_results_note_follows_the_photo_finish(tmp_path):
    main = _home(build_site.build(_site_tree(tmp_path)))
    note_at = main.index('data-pdk="results-note"')
    assert (
        main.index('<section class="home-pf">')
        < note_at
        < main.index('<section class="home-learn">')
    )
    note = _between(main, 'data-pdk="results-note"', "</section>")
    assert '<use href="#rulebook"/>' in note
    assert site_home.RESULTS_NOTE_TITLE == "How to read these results"
    assert ">How to read these results</h2>" in note
    assert (
        "These are AI-run benchmarks: AI agents race a fixed calendar of 2 tasks, and the "
        "same automated checks grade every run. The board shows how each entry did on "
        "these races. It is not a comprehensive ranking, and different tasks or settings "
        "can give a different order." in note
    )
    assert (
        '<a href="contact/index.html">Independent and open source: about the paddock</a>'
        in note
    )
    assert '<a href="rulebook/index.html">Read the method</a>' in note


def test_every_footer_carries_both_optics_lines_verbatim_and_links_contact(tmp_path):
    lines = _readme_footer_lines()
    assert lines == [layout.FOOTER_DISCLAIMER, layout.FOOTER_INDEPENDENCE]
    for name, page in _html_pages(build_site.build(_site_tree(tmp_path))).items():
        footer = _between(page, '<footer class="footer">', "</footer>")
        for line in lines:
            assert footer.count(line) == 1, (name, line)
        contact = layout.href("contact/index.html", name)
        assert re.search(
            rf'<a href="{re.escape(contact)}"( aria-current="page")?>Contact and about</a>',
            footer,
        ), name

# ---------------------------------------------------------------- the Learn page (PDW-05)
#
# The labs render from a drivers.json: the golden fixture above, or variants of
# it built here. Every number on the page must come from that file or from
# leaderboard.wilson.

from paddock_site import learn  # noqa: E402

DRIVERS_FIXTURE = SITE_GOLDEN / "drivers.json"


def _drivers() -> dict:
    return json.loads(DRIVERS_FIXTURE.read_text(encoding="utf-8"))


def _rung(engine_map: str, k: int, n: int, cost: float | None) -> dict:
    lo, hi = lb.wilson(k, n)
    return {
        "map": engine_map,
        "k": k,
        "n": n,
        "rate": k / n,
        "lo": lo,
        "hi": hi,
        "costPerRun": cost,
    }


def _car(car: str, k: int, n: int, place: int, cost: float | None = 0.01) -> dict:
    lo, hi = lb.wilson(k, n)
    return {
        "id": f"{car}-x",
        "car": car,
        "configHash": "sha256:" + "0" * 64,
        "k": k,
        "n": n,
        "rate": k / n,
        "lo": lo,
        "hi": hi,
        "place": place,
        "costPerPass": cost,
    }


def _embedded_view(page: str) -> dict:
    raw = re.search(
        r'<script type="application/json" id="learn-data">(.*?)</script>', page
    )
    return json.loads(raw[1])


def _main_text(page: str) -> str:
    main = _between(page, '<main class="wrap">', "</main>")
    return " ".join(" ".join(Text(main).text).split())


def test_learn_labs_render_from_the_drivers_fixture():
    drivers = _drivers()
    route = next(r for r in build_site.ROUTES if r.path == "learn/")
    page = learn.page(route, build_site.ROUTES, drivers)
    view = _embedded_view(page)
    assert view == learn.view(drivers)

    # The engine map: the fixture's one ladder, five levels always on the dial.
    ladder = drivers["ladders"][0]
    rungs = {rung["map"]: rung for rung in ladder["levels"]}
    (shown,) = view["engineMap"]["ladders"]
    assert list(shown["levels"]) == list(learn.LEVELS)
    for level in learn.LEVELS:
        tile = re.search(rf'<button[^>]*data-level="{level}"[^>]*>', page)[0]
        if level in rungs:
            assert 'aria-disabled="false"' in tile
            words = shown["levels"][level]
            rung = rungs[level]
            assert words["runs"] == f"{rung['k']} of {rung['n']} runs passed"
            assert words["range"] == (
                f"{rung['lo'] * 100:.1f}% to {rung['hi'] * 100:.1f}%"
            )
        else:
            assert 'aria-disabled="true"' in tile and "has not raced at" in tile
            assert shown["levels"][level] is None
    assert "Greyed out: medium, xhigh and max" in shown["caption"]
    # Best is the highest lower bound: high (0/3 at low has a range from 0%).
    assert shown["best"] == "high"
    best = shown["levels"]["high"]
    assert f'data-bind="em-pct" class="big">{best["pct"]}<' in page
    assert best["pct"] == f"{rungs['high']['rate'] * 100:.0f}%"
    assert (
        '<button type="button" class="level" data-level="high" aria-pressed="true"'
        in page
    )

    # Same driver, different car: both of the fixture's cars, bars from 80%.
    (cars,) = view["cars"]["sets"]
    assert [car["car"] for car in cars["cars"]] == ["alpha-cli", "beta-cli"]
    assert cars["floor"] == 80 and "Bars start at 80%" in cars["caption"]
    assert cars["cars"][0]["of"] == "2 of 2"
    assert cars["cars"][0]["usd"] == "$0.004"
    assert "coder-large at a high engine map, across all 2 races" in cars["sub"]

    # The honest ranges start from the lab's top result, held at its rate.
    ranges = view["ranges"]
    assert ranges["title"] == "How sure is 100%?"
    assert ranges["sizes"] == [9, 54, 162] and ranges["sizes"][ranges["start"]] == 54
    assert ranges["k"] == 54

    text = _main_text(page)
    for words in (
        "Does thinking harder help?",
        "Same driver. Different car.",
        "How sure is 100%?",
    ):
        assert words in text
    assert not re.search(r"(?i)\b(faster|fastest|quicker)\b", page)


def test_the_built_learn_page_embeds_the_view_of_its_own_drivers_json(tmp_path):
    files = build_site.build(_site_tree(tmp_path))
    drivers = json.loads(files["data/drivers.json"])
    assert _embedded_view(files["learn/index.html"]) == learn.view(drivers)
    assert files["learn/learn.js"] == learn.SCRIPT
    assert learn.CSS in files["paddock.css"]
    # The committed results build too, with real ladders and cars.
    real = build_site.build(lb.DEFAULT_RESULTS)
    view = _embedded_view(real["learn/index.html"])
    assert view["engineMap"]["ladders"] and view["cars"]["sets"]


def test_best_level_is_the_highest_lower_bound_not_the_highest_rate():
    dial = {
        "low": _rung("low", 18, 20, 0.01),  # 90%, a tight range
        "high": _rung("high", 3, 3, 0.05),  # 100%, a wide one
    }
    assert dial["high"]["rate"] > dial["low"]["rate"]
    assert dial["low"]["lo"] > dial["high"]["lo"]
    assert learn.best_level(dial) == "low"
    # A tie on the lower bound goes to the higher rate, then the cheaper run.
    same = {"low": _rung("low", 5, 9, 0.02), "high": _rung("high", 5, 9, 0.01)}
    assert learn.best_level(same) == "high"
    unpriced = {"low": _rung("low", 5, 9, 0.02), "high": _rung("high", 5, 9, None)}
    assert learn.best_level(unpriced) == "low"


def test_engine_map_notes_are_written_from_the_numbers():
    dial = {
        "low": _rung("low", 2, 9, 0.01),
        "medium": _rung("medium", 8, 9, 0.012),
        "high": _rung("high", 9, 10, 0.05),
    }
    best = learn.best_level(dial)
    assert best == "high"
    low = learn.level_note("low", dial, best)
    assert low.startswith("At low, 22% of runs pass, for $0.010 a run.")
    # Ranges that do not overlap: clearly behind; the best costs 5x as much.
    assert dial["low"]["hi"] < dial["high"]["lo"]
    assert "Clearly behind high." in low
    assert "High costs 5.0 times as much per run." in low
    medium = learn.level_note("medium", dial, best)
    assert medium.startswith("Medium adds 67 points over low, for $0.002 more a run.")
    # Overlapping ranges are a statistical tie, and the cost ratio is named.
    assert dial["medium"]["hi"] >= dial["high"]["lo"]
    assert "overlaps high's, so the two are a statistical tie." in medium
    assert "High costs 4.2 times as much per run." in medium
    high = learn.level_note("high", dial, best)
    assert "within a few points of medium" in high
    assert "This is its best level here" in high
    assert "times as much" not in high
    # Under double the cost, no ratio is written.
    near = {"low": _rung("low", 8, 9, 0.010), "high": _rung("high", 9, 10, 0.019)}
    assert "times as much" not in learn.level_note("low", near, learn.best_level(near))
    # An unpriced level says so instead of inventing a cost.
    bare = {"low": _rung("low", 4, 9, None), "high": _rung("high", 9, 10, None)}
    note = learn.level_note("low", bare, "high")
    assert "no price on record" in note and "times as much" not in note


def test_the_level_on_screen_is_held_across_drivers():
    drivers = _drivers()
    full = [
        _rung(level, k, 9, 0.01 * (i + 1))
        for i, (level, k) in enumerate(zip(learn.LEVELS, (3, 5, 6, 8, 7)))
    ]
    drivers["ladders"] = [
        {"driver": "acme/coder-large", "car": "alpha-cli", "levels": full},
        {
            "driver": "bolt/coder-small",
            "car": "beta-cli",
            "levels": [_rung("default", 4, 9, 0.01)]
            + [_rung("low", 4, 9, 0.01), _rung("medium", 7, 9, 0.02)],
        },
        # One dial level is no ladder: it never gets a chip.
        {"driver": "local/coder-tiny", "car": "alpha-cli", "levels": full[:1]},
    ]
    lab = learn.engine_map_lab(drivers)
    first, second = lab["ladders"]
    assert [ladder["name"] for ladder in lab["ladders"]] == [
        "coder-large",
        "coder-small",
    ]
    # Before any pick, each shows its best.
    assert learn.shown_level(first, None) == first["best"] == "xhigh"
    assert learn.shown_level(second, None) == second["best"] == "medium"
    # A held level the next driver has stays on screen ...
    for level in ("low", "medium"):
        assert learn.shown_level(second, level) == level
    # ... and one it lacks falls back to its best, with a note.
    for level in ("high", "xhigh", "max"):
        assert learn.shown_level(second, level) == "medium"
        assert second["fell"][level] == (
            f"It has not raced at {level} in this car, so this shows its best level, medium."
        )
        assert (
            second["why"][level] == f"coder-small has not raced at {level} in beta-cli"
        )
    assert set(first["show"]) == {"", *learn.LEVELS}
    assert all(first["show"][level] == level for level in learn.LEVELS)
    assert "It also raced at the car's own default, off this dial." in second["caption"]
    assert "Greyed out: high, xhigh and max" in second["caption"]
    # The script only looks the held level up; it holds what was on screen.
    assert "show[held || '']" in learn.SCRIPT
    assert "held = held || shown();" in learn.SCRIPT


def test_the_car_on_screen_is_held_across_drivers_and_falls_back_with_a_note():
    drivers = _drivers()
    drivers["sameDriverDifferentCar"] = [
        {
            "driver": "acme/coder-large",
            "map": "high",
            "cars": [_car("alpha-cli", 52, 54, 1), _car("beta-cli", 50, 54, 3)],
        },
        {
            "driver": "bolt/coder-small",
            "map": "default",
            "cars": [_car("gamma-cli", 51, 54, 2), _car("alpha-cli", 40, 54, 9, None)],
        },
    ]
    lab = learn.car_lab(drivers)
    first, second = lab["sets"]
    assert lab["start"] == 0 and first["driver"] == "acme/coder-large"
    assert learn.shown_car(first, None) == first["best"] == 0
    # Held alpha-cli: both drivers raced in it.
    assert first["cars"][learn.shown_car(first, "alpha-cli")]["car"] == "alpha-cli"
    assert second["cars"][learn.shown_car(second, "alpha-cli")]["car"] == "alpha-cli"
    # Held beta-cli: the second driver never raced in it, so its best car shows.
    assert learn.shown_car(second, "beta-cli") == second["best"] == 0
    assert second["fell"]["beta-cli"] == (
        "It has not raced in beta-cli, so this shows its best car."
    )
    assert "alpha-cli" not in second["fell"]
    assert second["cars"][1]["usd"] == "no price"
    assert "at the car's own default engine map, across all 54 races" in second["sub"]
    assert "heldCar = heldCar || cars.sets[set].cars[carIndex()].car;" in learn.SCRIPT


@pytest.mark.parametrize(
    ("rates", "floor"),
    [
        ((0.963, 0.944, 0.889), 80),
        ((0.963, 0.82), 80),
        ((0.963, 0.81), 75),
        ((0.9, 0.5), 45),
        ((1.0, 0.01), 0),
    ],
)
def test_the_car_bar_floor_drops_below_80_only_when_a_value_needs_it(rates, floor):
    assert learn.bar_floor(rates) == floor
    assert all(rate * 100 - floor >= 0 for rate in rates)
    caption = learn.floor_caption(floor, "beta-cli")
    assert caption.startswith(f"Bars start at {floor}%")
    if 0 < floor < 80:
        assert "lower than usual, so beta-cli still shows a bar" in caption


def test_car_bars_start_at_the_floor_in_the_view():
    drivers = _drivers()
    drivers["sameDriverDifferentCar"] = [
        {
            "driver": "acme/coder-large",
            "map": "high",
            "cars": [_car("alpha-cli", 50, 54, 1), _car("beta-cli", 30, 54, 2)],
        }
    ]
    (cars,) = learn.car_lab(drivers)["sets"]
    assert cars["floor"] == 50
    assert [car["w"] for car in cars["cars"]] == [
        f"{(50 / 54 * 100 - 50) / 50 * 100:.0f}%",
        f"{(30 / 54 * 100 - 50) / 50 * 100:.0f}%",
    ]
    assert cars["caption"].startswith("Bars start at 50%, lower than usual")


def test_honest_range_numbers_equal_leaderboard_wilson():
    table = learn.wilson_table()
    assert [len(rows) for rows in table] == [n + 1 for n in learn.SIZES]
    for rows, n in zip(table, learn.SIZES):
        for k, bounds in enumerate(rows):
            assert tuple(bounds) == lb.wilson(k, n)
    view = learn.view(_drivers())
    assert view["ranges"]["table"] == table
    assert view["ranges"]["z"] == f"{lb.Z_95:.2f}" == "1.96"
    for n in learn.SIZES:
        for k in (0, n // 2, n - 1, n):
            words = learn.range_view(k, n)
            lo, hi = lb.wilson(k, n)
            assert words["range"] == f"{lo * 100:.1f}% to {hi * 100:.1f}%"
            assert words["centre"] == f"{(lo + hi) / 2:.3f}"
            assert words["half"] == f"{(hi - lo) / 2:.3f}"
            assert words["p"] == f"{k / n:.3f}"
            # A perfect score's range: from its lower bound to 100%, never flat.
            assert words["perfect"] == f"{lb.wilson(n, n)[0] * 100:.1f}%"
            assert lb.wilson(n, n)[1] == 1.0 and lb.wilson(n, n)[0] < 1.0
    # The page shows the starting state's numbers and the perfect score's range.
    page = learn.body(_drivers(), "learn/index.html")
    start = learn.range_view(54, 54)
    assert f'data-bind="hr-range">{start["range"]}<' in page
    assert f'data-bind="hr-perfect">{start["perfect"]}<' in page
    assert "half-width = z × √(p(1 − p)/n + z²/4n²)" in page
    # The script reads the table; it computes no interval of its own.
    assert "ranges.table[size][passes]" in learn.SCRIPT
    assert "Math.sqrt" not in learn.SCRIPT


def test_the_honest_ranges_start_at_an_example_without_a_trusted_result():
    drivers = _drivers()
    drivers.update(
        state="awaiting-trust-sign-off", ladders=[], sameDriverDifferentCar=[]
    )
    view = learn.view(drivers)
    assert view["ranges"]["title"] == "How sure is a pass rate?"
    assert view["ranges"]["k"] == round(learn.EXAMPLE_RATE * learn.START_SIZE)
    page = learn.body(drivers, "learn/index.html")
    assert page.count("No trusted results yet.") == 2
    assert "data-ladder" not in page and "data-set" not in page


def test_the_sector_stepper_and_flags_spell_out_dnf_at_first_use():
    page = learn.body(_drivers(), "learn/index.html")
    text = " ".join(" ".join(Text(page).text).split())
    first = text.index("DNF")
    assert text[first : first + len("DNF, short for did not finish")] == (
        "DNF, short for did not finish"
    )
    assert '<abbr title="did not finish">DNF</abbr>' in page
    assert "DNF, short for did not finish: it missed a check" in page
    # Four sectors, each with a jump target at least 44 px across.
    assert page.count('data-sector-panel="') == 4
    for panel, count in (("sector", 4), ("flag", 6)):
        panels = re.findall(rf'<div[^>]*data-{panel}-panel="\d+"[^>]*>', page)
        assert len(panels) == count
        # First paint matches the initial script state, before JS loads.
        assert [" hidden" in tag for tag in panels] == [False] + [True] * (count - 1)
    assert re.findall(r'class="hit" cx="\d+" cy="\d+" r="(\d+)"', page) == ["22"] * 4
    # After sector 4 a tap starts another lap.
    assert "(sector + 1) % stepper.points.length" in learn.SCRIPT
    view = learn.view(_drivers())["stepper"]
    assert view["last"] == "Another lap" and len(view["points"]) == 4
    # Six flags, every glyph from the published set.
    flags = re.findall(r'class="flag" data-flag="\d"', page)
    assert len(flags) == 6
    glyph_uses = set(re.findall(r'<use href="#([^"]+)"', page))
    assert glyph_uses <= PUBLISHED_GLYPHS
    # No number in the sector or flag copy: numbers come from the data.
    for _title, _glyph, body, notice in learn.SECTORS:
        assert not re.search(r"\d", body + notice)
    for _name, _glyph, counted, body in learn.FLAGS:
        assert not re.search(r"\d", counted + body)


def test_learn_driver_chips_keep_two_providers_of_one_model_apart():
    names = learn.driver_names(["fireworks/kimi-k3", "moonshot/kimi-k3", "acme/coder"])
    assert names == {
        "fireworks/kimi-k3": "fireworks/kimi-k3",
        "moonshot/kimi-k3": "moonshot/kimi-k3",
        "acme/coder": "coder",
    }
    assert learn.embed({"a": "</script><b>&"}) == (
        '{"a":"\\u003c/script\\u003e\\u003cb\\u003e\\u0026"}'
    )

# ---------------------------------------------------------------- garage, pit wall and drivers (PDW-08)
#
# The data pages read only the data files above. They are checked on the
# synthetic tree, whose drivers cover every garage state, and on the committed
# results.

from paddock_site import drivers as site_drivers  # noqa: E402
from paddock_site import garage as site_garage  # noqa: E402
from paddock_site import pit_wall as site_pit_wall  # noqa: E402
from paddock_site import views as site_views  # noqa: E402


def _data_pages(results: Path):
    files = build_site.build(results)
    return files, site_views.SiteData.parse(files)


@pytest.fixture(scope="module")
def committed_pages():
    return _data_pages(lb.DEFAULT_RESULTS)


@pytest.fixture
def data_page_site():
    return site_views.SiteData.parse(
        {
            "data/" + path.relative_to(SITE_GOLDEN).as_posix(): path.read_text("utf-8")
            for path in SITE_GOLDEN.rglob("*.json")
        }
    )


def _data_page_names(files) -> list[str]:
    return [
        name
        for name in files
        if name.startswith(("garage/", "pit-wall/", "drivers/"))
        and name.endswith(".html")
    ]


def test_garage_level_availability_comes_from_the_data(tmp_path):
    files, site = _data_pages(_site_tree(tmp_path))
    view = site_garage.model(site)
    drivers = {driver["id"]: driver for driver in view["drivers"]}

    def open_levels(driver: str) -> list[str]:
        return [level for level, why in drivers[driver]["levels"].items() if not why]

    # Every level a driver has a counted run at, in any car and any document
    # (trusted or not), is open; the rest are closed with a reason.
    raced = defaultdict(set)
    for entry in site.placed + site.partial + list(site.community["entries"]):
        raced[entry["driver"]].add(entry["map"])
    assert set(drivers) == set(raced)
    for driver, maps in raced.items():
        assert set(open_levels(driver)) == maps & set(site_views.LEVELS), driver
        assert list(drivers[driver]["levels"]) == list(site_views.LEVELS)
    assert open_levels("acme/coder-large") == ["low", "high"]
    assert drivers["acme/coder-large"]["levels"]["max"] == (
        "No counted run has Coder Large at max; it has raced at low and high."
    )
    # A level seen only in a self-reported document still opens the level.
    assert open_levels("bolt/coder-small") == ["default", "max"]
    assert drivers["local/coder-tiny"]["levels"]["high"] == (
        "Coder Tiny has raced at one engine map only: provider default."
    )
    assert "It runs at one setting only." in drivers["local/coder-tiny"]["note"]
    # Featured drivers (a placed entry) first; a partial-only driver has no page.
    assert [d["id"] for d in view["drivers"] if d["featured"]] == [
        "acme/coder-large",
        "bolt/coder-small",
    ]
    assert drivers["local/coder-tiny"]["page"] is None

    # A raced entry carries its standings result; an unchecked one only its badge.
    key = site_garage.key
    top = site.placed[0]
    raced_entry = view["entries"][key(top["driver"], top["car"], top["map"])]
    assert (raced_entry["k"], raced_entry["n"], raced_entry["place"]) == (
        top["k"],
        top["n"],
        "=1st",
    )
    assert view["unverified"] == {
        key("bolt/coder-small", "gamma-cli", "max"): "self-reported"
    }
    assert key("bolt/coder-small", "gamma-cli", "max") not in view["entries"]
    # Open but unraced: acme at high in a car it never raced.
    unraced = key("acme/coder-large", "gamma-cli", "high")
    assert unraced not in view["entries"] and unraced not in view["unverified"]
    partial = view["entries"][key("local/coder-tiny", "alpha-cli", "default")]
    assert partial["place"] == "not placed" and not partial["placed"]

    page = files["garage/index.html"]
    main = _between(page, '<main class="wrap">', "</main>")
    assert view["start"] == [top["driver"], top["car"], top["map"]]
    assert main.count('aria-disabled="true"') == 4  # acme opens on low and high only
    assert 'data-state="unraced" hidden' in main
    assert f'href="../{site_garage.RACE_IT_YOURSELF}"' in main
    assert "Nobody has raced this yet." in main
    assert f'data-slot="pct">{site_views.pct(top["rate"])}<' in main
    embedded = json.loads(_between(page, 'id="garage-data">', "</script>"))
    assert embedded["drivers"] == view["drivers"]
    assert set(embedded["entries"]) == set(view["entries"])


def test_garage_offers_every_committed_driver_and_its_levels(committed_pages):
    files, site = committed_pages
    view = site_garage.model(site)
    assert [level[0] for level in view["levels"]] == [
        "default",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    for driver in view["drivers"]:
        for level, why in driver["levels"].items():
            assert bool(why) != any(
                key.startswith(f"{driver['id']}|") and key.endswith(f"|{level}")
                for key in [*view["entries"], *view["unverified"]]
            ), (driver["id"], level)
    for entry in site.placed:
        raced = view["entries"][
            site_garage.key(entry["driver"], entry["car"], entry["map"])
        ]
        assert raced["placed"] and raced["pct"] == site_views.pct(entry["rate"])


@pytest.mark.parametrize("source", ["entries", "partial", "community", "none"])
def test_garage_start_skips_unsupported_maps(data_page_site, source):
    site = data_page_site
    candidate = deepcopy(site.placed[-1])
    for entry in site.placed + site.partial + site.community["entries"]:
        entry["map"] = "ultra"
    if source != "none":
        candidate["map"] = "high"
        if source == "community":
            candidate["trust"] = "self-reported"
            site.community["entries"].append(candidate)
        else:
            site.standings[source].append(candidate)
    view = site_garage.model(site)
    page = site_garage.body(site, "garage/index.html")
    if source == "none":
        assert view["start"] is None
        assert "data-garage" not in page
    else:
        assert view["start"] == [candidate["driver"], candidate["car"], "high"]
        assert 'data-map="high" aria-pressed="true"' in page
        assert 'data-slot="map">high<' in page


def test_driver_labels_include_community_name_collisions(data_page_site):
    site = data_page_site
    community = deepcopy(site.community["entries"][0])
    community.update(driver="other/coder-large", trust="self-reported")
    site.community["entries"].append(community)
    name = "Coder Large · Acme"
    garage = {d["id"]: d["name"] for d in site_garage.model(site)["drivers"]}
    assert garage["other/coder-large"] == "Coder Large · Other"
    assert garage["acme/coder-large"] == name
    assert site_drivers.model(site)[0]["name"] == name
    assert site_pit_wall.model(site)["rows"][0]["driverName"] == name


def test_pit_wall_links_resolve_when_driver_slugs_collide(data_page_site):
    site = data_page_site
    replacements = {
        "acme/coder-large": "vendor/model_v1",
        "bolt/coder-small": "vendor/model-v1",
    }
    for entry in site.placed + site.partial + site.community["entries"]:
        entry["driver"] = replacements.get(entry["driver"], entry["driver"])
    route = next(r for r in build_site.ROUTES if r.file == site_drivers.INDEX)
    pages = site_drivers.pages(site, route, build_site.ROUTES)
    rows = site_pit_wall.model(site)["rows"]
    links = {row["driverPage"] for row in rows}
    assert len(links) == 2
    assert "drivers/vendor-model-v1/index.html" not in links
    assert links <= pages.keys()
    assert links <= _local_links(
        "pit-wall/index.html", site_pit_wall.body(site, "pit-wall/index.html")
    )
    assert links == {d["page"] for d in site_garage.model(site)["drivers"] if d["page"]}


@pytest.mark.parametrize(
    ("best_cost", "other_cost", "phrase"),
    [
        (None, None, "there is no fuel bill to compare."),
        (0.01, 0.02, "That is also its best entry."),
        (None, 0.02, "Its best entry has no pricing data to compare."),
        (0.03, 0.02, "Its best entry costs $0.030 a pass."),
    ],
)
def test_driver_fuel_note_handles_missing_prices(best_cost, other_cost, phrase):
    best = {"car": "codex", "map": "high", "costPerPass": best_cost}
    other = {"car": "pi", "map": "low", "costPerPass": other_cost}
    priced = [e for e in (best, other) if e["costPerPass"] is not None]
    driver = {
        "name": "Example",
        "best": best,
        "cheapest": min(priced, key=lambda e: e["costPerPass"]) if priced else None,
    }
    assert site_drivers.fuel_note(driver).endswith(phrase)


def test_pit_wall_sort_toggles_direction_and_keeps_missing_values_last():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise the pit wall script")
    harness = r"""
const assert = require('node:assert/strict');
function element(attrs = {}) {
  return {
    attrs, listeners: {},
    getAttribute(key) { return this.attrs[key]; },
    hasAttribute(key) { return key in this.attrs; },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(event, fn) { this.listeners[event] = fn; }
  };
}
const heads = ['desc', 'asc'].map(dir => {
  const th = element({'aria-sort': 'none'});
  th.button = element({'data-dir': dir});
  th.querySelector = () => th.button;
  return th;
});
const rows = [[1, 0.02], [3, 0.01], [null, null], [3, 0.01]].map((values, i) => {
  const row = element({'data-i': String(i)});
  const cells = [
    element(values[0] === null ? {} : {'data-k': values[0], 'data-n': 4}),
    element({'data-usd': values[1] === null ? '' : String(values[1])})
  ];
  row.querySelectorAll = () => cells;
  return row;
});
const body = {rows: rows.slice(), appendChild(row) {
  this.rows.splice(this.rows.indexOf(row), 1); this.rows.push(row);
}};
const tools = {querySelectorAll: () => []};
const mode = element();
const wall = {
  querySelector: selector => ({
    '[data-pitwall-tools]': tools, 'tbody': body, '[data-pitwall-mode]': mode
  })[selector],
  querySelectorAll: () => heads
};
global.document = {querySelector: () => wall};
eval(process.argv[1]);
function click(column, order, direction) {
  heads[column].button.listeners.click();
  assert.deepEqual(body.rows.map(row => Number(row.getAttribute('data-i'))), order);
  assert.equal(heads[column].getAttribute('aria-sort'), direction);
  assert.equal(heads[1 - column].getAttribute('aria-sort'), 'none');
}
click(0, [1, 3, 0, 2], 'descending');
click(0, [0, 1, 3, 2], 'ascending');
click(0, [1, 3, 0, 2], 'descending');
click(1, [1, 3, 0, 2], 'ascending');
click(1, [0, 1, 3, 2], 'descending');
click(0, [1, 3, 0, 2], 'descending');
"""
    subprocess.run(
        [node, "-e", harness, site_pit_wall.SCRIPT],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("k", "n", "tone"),
    [
        (0, 1, 1),
        (1, 3, 1),
        (1, 2, 2),
        (69, 100, 2),
        (7, 10, 3),
        (4, 5, 4),
        (9, 10, 5),
        (19, 20, 6),
        (53, 54, 6),
        (1, 1, 7),
    ],
)
def test_pit_wall_shades_by_the_token_bins(k, n, tone):
    assert site_pit_wall.bin_of(k, n) == tone


def _pit_wall_rows(page: str) -> list[dict]:
    body = _between(page, "<tbody>", "</tbody>")
    rows = []
    for row in re.findall(r"<tr (.*?)</tr>", body):
        cells = re.findall(r'data-k="(\d+)" data-n="(\d+)"', row)
        rows.append(
            {
                "car": re.search(r'data-car="([^"]+)"', row)[1],
                "cells": [(int(k), int(n)) for k, n in cells[:-1]],
                "total": tuple(map(int, cells[-1])),
            }
        )
    return rows


def _check_pit_wall(files, site) -> None:
    view = site_pit_wall.model(site)
    columns = view["columns"]
    assert [c["races"] for c in columns] and sum(c["races"] for c in columns) == len(
        site.calendar
    )
    assert [row["id"] for row in view["rows"]] == [e["id"] for e in site.placed]
    for row, entry in zip(view["rows"], site.placed):
        assert sum(cell["k"] for cell in row["cells"]) == entry["k"] == row["k"]
        assert (
            sum(cell["n"] for cell in row["cells"]) == entry["n"] == len(site.calendar)
        )
        for cell, column in zip(row["cells"], columns):
            assert cell["n"] == column["races"], (row["id"], column["challenge"])
    # The page shows exactly those numbers, in standings order.
    page = files["pit-wall/index.html"]
    shown = _pit_wall_rows(page)
    assert len(shown) == len(site.placed)
    for got, entry in zip(shown, site.placed):
        assert got["car"] == entry["car"]
        assert got["total"] == (entry["k"], entry["n"])
        assert sum(k for k, _ in got["cells"]) == entry["k"]
        assert got["cells"] == [
            (entry["challenges"][c["challenge"]]["k"], c["races"]) for c in columns
        ]
    # Every column sorts; the table and the car chips carry the sideways cue.
    assert page.count('class="pw-sort"') == len(columns) + 2
    assert page.count("data-hscroll ") + page.count("data-hscroll>") >= 2
    assert "Scroll sideways for every challenge" in page


def test_pit_wall_heatmap_totals_equal_standings(tmp_path, committed_pages):
    _check_pit_wall(*_data_pages(_site_tree(tmp_path)))
    _check_pit_wall(*committed_pages)


def _check_driver_pages(files, site) -> None:
    featured = list(dict.fromkeys(entry["driver"] for entry in site.placed))
    slugs = site_drivers.slugs(featured)
    expected = {site_drivers.page_file(slugs[d]) for d in featured}
    built = {n for n in files if n.startswith("drivers/") and n != site_drivers.INDEX}
    assert built == expected
    index = files[site_drivers.INDEX]
    assert {f"drivers/{slugs[d]}/index.html" for d in featured} <= _local_links(
        site_drivers.INDEX, index
    )
    names = site.driver_names
    for driver in featured:
        name = site_drivers.page_file(slugs[driver])
        page = files[name]
        entries = [e for e in site.placed if e["driver"] == driver]
        best = entries[0]
        assert f"<h1>{html.escape(names[driver])}</h1>" in page
        assert f'<span class="big-num">{site_views.pct(best["rate"])}</span>' in page
        assert page.count('class="dv-row"') == len(entries)
        assert page.count("data-slips=") == len(entries)
        # The page is marked as the drivers route and links back to every driver.
        mark = f'href="{layout.href(site_drivers.INDEX, name)}" aria-current="page"'
        assert mark in page
        assert {f"drivers/{slugs[d]}/index.html" for d in featured} <= _local_links(
            name, page
        )
        assert "Cheapest way to run" in page or "no fuel bill to compare" in page


def test_every_driver_page_builds(tmp_path, committed_pages):
    _check_driver_pages(*_data_pages(_site_tree(tmp_path)))
    _check_driver_pages(*committed_pages)
    files, site = committed_pages
    assert len([n for n in files if n.startswith("drivers/")]) > 2


def test_data_pages_link_resolve_and_use_only_published_glyphs(committed_pages):
    files, _ = committed_pages
    names = _data_page_names(files)
    assert {"garage/index.html", "pit-wall/index.html", "drivers/index.html"} <= set(
        names
    )
    for name in names:
        page = files[name]
        for link in _local_links(name, page):
            assert link in files, (name, link)
        ids = set(re.findall(r'<symbol id="([^"]+)"', page))
        assert set(re.findall(r'<use href="#([^"]+)"', page)) <= ids, name
        assert page.count('<svg class="symbols"') == 1, name
        assert not _stray_product_names(page), (name, _stray_product_names(page))
        assert "faster" not in page.lower(), name
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        assert re.findall(r"<title>(.*?)</title>", head)[0].endswith(" · Agent Paddock")


def test_data_pages_wait_for_trust_sign_off_without_numbers(tmp_path):
    demoted = {BENCH: {"level": "self-reported", "evidence": [], "sign_off": None}}
    files, _ = _data_pages(_site_tree(tmp_path, demoted))
    assert {n for n in files if n.startswith("drivers/")} == {site_drivers.INDEX}
    for name in ("garage/index.html", "pit-wall/index.html", site_drivers.INDEX):
        main = _between(files[name], '<main class="wrap">', "</main>")
        text = " ".join(" ".join(Text(main).text).split())
        assert "Waiting on checks." in text, name
        assert not re.search(r"\d", text), (name, text)
        assert "data-garage" not in main and "data-pitwall" not in main


@pytest.mark.parametrize(
    ("provider_id", "name"),
    [
        ("openai/gpt-6.1-sol", "GPT-6.1 Sol"),
        ("anthropic/claude-opus-5-5", "Claude Opus 5.5"),
        ("google/gemini-3.8-flash", "Gemini 3.8 Flash"),
        ("together/deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
        ("openrouter/qwen3.8-27b", "Qwen 3.8 27B"),
        ("zai/glm-5.2", "GLM 5.2"),
        ("moonshot/kimi-k3", "Kimi K3"),
    ],
)
def test_driver_names_read_like_the_design(provider_id, name):
    assert site_views.model_name(provider_id) == name


def test_driver_names_that_collide_name_their_host():
    names = site_views.driver_names(
        ["moonshot/kimi-k3", "fireworks/kimi-k3", "zai/glm-5.2"]
    )
    assert names == {
        "fireworks/kimi-k3": "Kimi K3 · Fireworks",
        "moonshot/kimi-k3": "Kimi K3 · Moonshot",
        "zai/glm-5.2": "GLM 5.2",
    }


def test_embedded_page_data_cannot_close_its_script():
    block = site_views.json_script("x", {"name": "</script><b>&"})
    assert block.count("</script>") == 1 and "<b>" not in block
    assert json.loads(
        _between(block, '<script type="application/json" id="x">', "</script>")
    ) == {"name": "</script><b>&"}
