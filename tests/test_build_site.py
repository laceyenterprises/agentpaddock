"""PDK-04: the static leaderboard site builds deterministically from results/ only."""

from __future__ import annotations

import html
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
        | {"paddock.css", "favicon.svg", "social-card.png"}
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
    pages = sorted(out.glob("*.html"))
    assert {page.name for page in pages} == {
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
        assert 'aria-label="Agent Paddock home"' in page and "PADDOCK" in page
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


#: Where a generated page may say "paddock" without the product name "Agent Paddock".
PRODUCT_NAME_ALLOWLIST = re.compile(
    r"Agent\s+Paddock"  # the product name itself
    r'|>AGENT</span><span class="wordmark-name">PADDOCK<'  # the stacked lockup
    r"|\bpaddock (?:run|show|baseline|lock)\b"  # the command
    r"|\bpaddock\.css\b"  # the stylesheet path
    r"|agentpaddock"  # the domain, repository and distribution
    r"|\bPADDOCK_[A-Z0-9_]+"  # environment variables
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


def test_every_page_title_and_eyebrow_names_agent_paddock():
    files = build_site.build(FIXTURE)
    for name, page in files.items():
        if not name.endswith(".html"):
            continue
        head = page.split("<head>", 1)[1].split("</head>", 1)[0]
        titles = re.findall(r"<title>(.*?)</title>", head)
        assert len(titles) == 1 and titles[0].endswith(" · Agent Paddock"), name
    for name, _ in build_site.PAGES:
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
        header = page.split('<header class="nav">', 1)[1].split("</header>", 1)[0]
        footer = page.split('<footer class="footer">', 1)[1].split("</footer>", 1)[0]
        assert header.count(lockup) == 1 and footer.count(lockup) == 1, name
        assert page.count("wordmark-agent") == 2, name
    # Both renderings come from brand(): replacing it replaces both.
    monkeypatch.setattr(build_site, "brand", lambda: "<i>synthetic lockup</i>")
    page = build_site.build(FIXTURE)["index.html"]
    assert (
        page.count("<i>synthetic lockup</i>") == 2
        and "wordmark" not in page.split("</head>")[1]
    )


def test_lockup_styles_follow_the_brand_spec():
    style = build_site.STYLE
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
    # Both lines inherit the brand text colour (ink on light, white on the dark footer).
    for selector, body in rules.items():
        if "wordmark" in selector:
            assert "color" not in body, selector
            assert not re.search(r"--(muted|tower-mute|ink-2)", body), selector
    declares(rules, ".brand", "color: var(--ink);")
    declares(rules, ".footer .brand", "color: var(--tower-fg);")
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
    urls = set()
    for name in pages:
        page = files[name]
        meta = _meta(page)
        tags = dict(meta)
        assert len(tags) == len(meta), name
        title = html.unescape(re.search(r"<title>(.*?)</title>", page)[1])
        url = "https://agentpaddock.ai/" + ("" if name == "index.html" else name)
        alt = build_site.CARD_ALT
        assert tags == {
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
    first = sorted(path.name for path in outs[0].iterdir())
    assert first == sorted(path.name for path in outs[1].iterdir())
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
