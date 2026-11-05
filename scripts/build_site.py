"""Build the Agent Paddock site from ``results/`` only (PDK-04, PDW-02).

The input is the committed ``results/`` tree (see ``scripts/leaderboard.py``):
results documents, dated price tables, the grading changelog and the fairness
checklist. Nothing is read from a private ledger, and nothing is estimated: a
token count nobody measured is shown as not measured, and a cost without a
price source is shown as "unknown".

Every link is relative, so the same output works at the site root and opened
straight from disk. The build is deterministic: the same ``results/`` gives
byte-identical pages.

    python3 scripts/build_site.py                 # write site/
    python3 scripts/build_site.py --check         # fail if site/ is stale
    python3 scripts/build_site.py --results DIR --out DIR

The share image is the one file not built here: ``scripts/render_social_card.py``
renders ``scripts/social_card.html`` to the committed ``scripts/social-card.png``,
which the build copies. The PNG carries the SHA-256 of the card source, so
``--check`` reports a stale card without a browser.

Every page shares one layout (``scripts/paddock_site/``): the header with the
lockup and the nav, the menu panel, the footer, the inlined glyph sprite and
the design tokens. The pages themselves are registered in
``paddock_site/routes.py`` and built by the functions :data:`ROUTES` names
below; a route without a builder shows a placeholder until its PDW page ticket
lands. ``design/web/README.md`` maps every route to its prototype and ticket.

The pages' numbers come from the data files under ``data/``
(``paddock_site/data.py``, PDW-03), built from the same ``results/`` and the
``tasks/`` beside it. The build makes them first and hands them to every page
builder with the results; the Learn page (``paddock_site/learn.py``, PDW-05)
reads ``data/drivers.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any, Mapping, Sequence

import leaderboard as lb

from paddock_site import data as site_data
from paddock_site import community, contact, dictionary, garage, kit, layout, learn, pit_lane, pit_wall, placeholder, views
from paddock_site import drivers as site_drivers
from paddock_site import races as race_pages
from paddock_site import rulebook
from paddock_site import routes as site_routes
from paddock_site import standings as standings_pages
from paddock_site.layout import (  # re-exported for callers and tests
    CARD_ALT as CARD_ALT,
    CARD_NAME,
    CARD_SIZE,
    DESCRIPTION,
    GTM_CONTAINER_ID as GTM_CONTAINER_ID,
    MARK,
    PRODUCT,
    REPO_URL,
    SCRIPT_NAME,
)
from paddock_site.routes import SITE_URL

DEFAULT_OUT = lb.MODULE_ROOT / "site"
CARD_TEMPLATE = lb.MODULE_ROOT / "scripts" / "social_card.html"
CARD_PNG = lb.MODULE_ROOT / "scripts" / "social-card.png"
#: The PNG tEXt keyword holding the SHA-256 of the card source it was rendered from.
CARD_KEY = "agentpaddock-card-source-sha256"
CARD_MARK_SLOT = "<!-- broadcast-mark -->"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Page styles for today's board pages; the stylesheet puts the design tokens
#: before them and the shared layout after them (``layout.stylesheet``).
STYLE = """
/* ---------- base ---------- */
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 400 16px/1.6 var(--body);
  font-feature-settings: "cv11", "ss01";
}
body::before {
  content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px);
  z-index: 10; background: var(--paper);
}
a { color: var(--blue-ink); text-underline-offset: 3px; }
a:hover { color: var(--blue-ink); }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; border-radius: 4px; }
h1, h2, h3 { text-wrap: balance; margin: 0; }
p { margin: 0; }
code, .mono { font-family: var(--mono); font-size: .86em; }
.num { font-variant-numeric: tabular-nums; }

.wrap {
  max-width: 1120px; margin: 0 auto;
  padding-left: max(24px, env(safe-area-inset-left, 0px));
  padding-right: max(24px, env(safe-area-inset-right, 0px));
}
.section { padding-block: 80px; }
.section.soft { background: var(--soft); }
.section-head { display: grid; gap: 12px; max-width: 720px; margin-bottom: 40px; }
.eyebrow {
  font: 600 12px/1.2 var(--mono); letter-spacing: .16em; text-transform: uppercase; color: var(--blue-ink);
  display: inline-flex; align-items: center; gap: 10px;
}
h2.h2 { font: 700 clamp(30px, 4vw, 44px)/1.05 var(--display); letter-spacing: -.005em; }
.lede { font-size: 18px; color: var(--ink-2); max-width: 640px; }
.small { font-size: 14px; color: var(--muted); }

/* engineer's line: the same fact in the field's own terms */
.eng {
  font: 400 12.5px/1.55 var(--mono); color: var(--muted);
  border-top: 1px dashed var(--rule); padding-top: 10px; margin-top: auto;
}
.eng b { color: var(--ink-2); font-weight: 500; }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 44px; padding: 0 20px;
  border-radius: 999px; font: 600 15px/1 var(--body); text-decoration: none; border: 1.5px solid var(--ink);
  transition: transform .12s var(--ease), background .2s var(--ease), color .2s var(--ease);
}
.btn:active { transform: translateY(1px); }
.btn.solid { background: var(--ink); color: var(--paper); }
.btn.solid:hover { background: var(--ink-2); border-color: var(--ink-2); color: var(--paper); }
.btn.ghost { background: transparent; color: var(--ink); }
.btn.ghost:hover { background: var(--soft); color: var(--ink); }
.btn.sm { min-height: 36px; padding: 0 14px; font-size: 14px; }
.btn svg { width: 16px; height: 16px; }


/* ---------- timing tower ---------- */
.tower {
  background: var(--tower); color: var(--tower-fg); border-radius: var(--radius-card); overflow: hidden;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .25), 0 18px 48px -18px rgba(10, 14, 20, .55);
  min-width: 0;
}
.tower-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px 12px;
  border-bottom: 1px solid var(--tower-rule);
}
.tower-title { font: italic 800 18px/1 var(--display); letter-spacing: .08em; text-transform: uppercase; }
.tower-sub { font: 500 11px/1.2 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--tower-mute); }
.tower-cols, .tower-row {
  display: grid; grid-template-columns: 34px 4px minmax(0, 1fr) 62px 62px; align-items: center; column-gap: 10px;
  padding: 0 16px;
}
.tower-cols { font: 500 10.5px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--tower-mute); padding-block: 10px 6px; }
.tower-cols span:nth-child(n+4) { text-align: right; }
.tower ol { list-style: none; margin: 0; padding: 0 0 6px; }
.tower-row { padding-block: 8px; border-top: 1px solid var(--tower-rule); }
.tower-row:first-child { border-top: 0; }
.pos {
  font: italic 800 20px/1 var(--display); text-align: center; color: var(--tower-fg); font-variant-numeric: tabular-nums;
}
.pos.p1 { color: var(--tower-lit); }
.livery { width: 4px; height: 30px; border-radius: 2px; background: var(--c, var(--tower-mute)); }
.who { min-width: 0; display: grid; gap: 1px; }
.who b { font: 600 14.5px/1.2 var(--body); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.who span { font: 400 12px/1.2 var(--mono); color: var(--tower-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fin { white-space: nowrap; text-align: right; font: italic 800 22px/1 var(--display); font-variant-numeric: tabular-nums; }
.fin small { font: 500 11px var(--mono); color: var(--tower-mute); font-style: normal; margin-left: 1px; }
.cost { text-align: right; font: 500 12.5px/1 var(--mono); color: var(--tower-fg); font-variant-numeric: tabular-nums; }
.cost.na { color: var(--tower-mute); }
.tower-foot {
  display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 16px 14px;
  border-top: 1px solid var(--tower-rule); font: 400 11.5px/1.4 var(--mono); color: var(--tower-mute);
}
.tower-foot a { color: var(--tower-fg); font-weight: 600; white-space: nowrap; }
.checker {
  height: 10px; background-color: var(--tower-fg);
  background-image: conic-gradient(var(--tower) 25%, transparent 0 50%, var(--tower) 0 75%, transparent 0);
  background-size: 10px 10px;
}

/* ---------- checkered finish-line rule ---------- */
.finish-line {
  height: 12px; background-color: var(--paper);
  background-image: conic-gradient(var(--ink) 25%, transparent 0 50%, var(--ink) 0 75%, transparent 0);
  background-size: 12px 12px; opacity: .9;
}

/* ---------- findings ---------- */
.cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.card {
  background: var(--paper); border: 1px solid var(--rule); border-radius: var(--radius-card); padding: 28px;
  display: flex; flex-direction: column; gap: 12px; min-width: 0;
}
.card .big { font: italic 800 clamp(44px, 5.4vw, 60px)/.95 var(--display); color: var(--blue); font-variant-numeric: tabular-nums; letter-spacing: -.01em; }
.card .kicker { font: 600 12px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.card h3 { font: 600 21px/1.25 var(--body); letter-spacing: -.01em; }
.card p { color: var(--ink-2); }
.caveat {
  margin-top: 24px; display: flex; gap: 12px; align-items: flex-start; font-size: 14px; color: var(--ink-2);
  padding: 14px 16px; border-radius: var(--radius-inner); background: var(--paper); border: 1px solid var(--rule);
}
.flag-chip {
  flex: none; display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 5px; margin-top: 1px;
}
.flag-chip.yellow { background: var(--amber); }
.flag-chip svg { width: 14px; height: 14px; }


/* ---------- standings page ---------- */
.page-hero { padding-block: 56px 32px; }
.page-hero h1 { font: italic 800 clamp(44px, 6.4vw, 76px)/.95 var(--display); text-transform: uppercase; letter-spacing: -.01em; margin-top: 14px; }
.page-hero .lede { margin-top: 16px; }

.grid-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: var(--radius-card); background: var(--paper); }
table.grid { border-collapse: collapse; width: 100%; min-width: 680px; font-variant-numeric: tabular-nums; }
.grid th, .grid td { padding: 10px 12px; border-top: 1px solid var(--rule); text-align: center; font-size: 14px; }
.grid thead th { border-top: 0; background: var(--soft); font: 600 11.5px/1.2 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.grid thead th .dot { margin-right: 6px; vertical-align: -1px; }
.grid th[scope="row"] { text-align: left; font-weight: 600; white-space: nowrap; }
.grid th[scope="row"] small { display: block; font: 400 11.5px var(--mono); color: var(--muted); }
.grid td .cell { display: inline-flex; align-items: center; justify-content: center; gap: 1px; white-space: nowrap; padding: 0 6px; min-width: 46px; height: 34px; border-radius: 8px; font: italic 800 19px/1 var(--display); }
.grid td .cell.best { outline: 2px solid var(--ink); outline-offset: 1px; }
.grid td.empty { color: var(--muted); }

* { box-sizing: border-box; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
a:hover { color: var(--blue-ink); }
html { scroll-padding-top: 90px; }
.wrap { max-width: 1488px; min-width: 0; }
main { padding-bottom: 64px; }
h2 { font: 700 36px/1.1 var(--display); margin: 40px 0 20px; }
h3 { font: 600 24px/1.3 var(--display); margin: 28px 0 12px; }
h4 { margin: 24px 0 12px; }
p, ul, ol, pre { margin-block: 12px; }
.note { color: var(--muted); font-size: 13px; }
.page-hero { max-width: 100%; }
.page-hero h1 { overflow-wrap: anywhere; }
.lede { max-width: 72ch; }
.facts { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border-block: 1px solid var(--rule); }
.fact { padding: 20px 12px 20px 0; display: grid; gap: 6px; }
.fact b { font: italic 800 36px/1 var(--display); }
.fact span { color: var(--muted); font-size: 13px; }
.caveat { margin-bottom: 28px; }
.caveat p { margin: 0; }
.flag-chip { color: #111111; }
.card h3 { margin: 0; overflow-wrap: anywhere; }
.card .kicker { line-height: 1.6; }
.card .tower { margin-top: auto; }
.card-link { align-self: flex-start; }
.also { list-style: none; padding: 0; margin: 0; font: 500 12.5px/1.4 var(--mono); }
.also li + li { margin-top: 6px; }
[id] { scroll-margin-top: 90px; }
details { border: 1px solid var(--rule); border-radius: var(--radius-inner); padding: 14px 20px; margin-bottom: 24px; }
summary { cursor: pointer; font-weight: 600; }
.task-list { columns: 3 220px; padding-left: 20px; overflow-wrap: anywhere; }
.table-wrap, .grid-wrap, .scroll { max-width: 100%; overflow-x: auto; }
.tower-table { border-collapse: collapse; width: 100%; white-space: nowrap; }
.tower-table th, .tower-table td { padding: 12px 8px; border-bottom: 1px solid var(--tower-rule); text-align: right; }
.tower-table th { color: var(--tower-mute); font: 500 10px/1.3 var(--mono); text-transform: uppercase; }
.tower-table td { font: 400 12.5px/1.4 var(--mono); }
.tower-table th:nth-child(3), .tower-table td:nth-child(3) { text-align: left; }
.tower-table td:nth-child(2) { padding-inline: 0; width: 4px; }
.tower-table .who { display: table-cell; }
.tower-table .who b { display: block; overflow: visible; font-size: 14.5px; }
.tower-table .who span { display: block; overflow: visible; margin-top: 5px; font-size: 11px; }
.tower .who a { color: inherit; text-decoration: none; }
.tower .who a:hover { text-decoration: underline; }
.tower-table .pos { font: italic 800 22px/1 var(--display); text-align: center; }
.tower-table .fin { font: italic 800 24px/1 var(--display); }
.tower .note { color: var(--tower-mute); font-size: 11px; }
.tower .cost { white-space: nowrap; }
.ci { display: inline-block; vertical-align: middle; width: 72px; height: 14px; }
.ci .track { stroke: var(--tower-rule); }
.ci .band { fill: var(--tower-mute); }
.ci .point { fill: var(--tower-lit); }
.tag { font: 500 10px/1 var(--mono); padding: 3px 4px; border: 1px solid var(--tower-rule); border-radius: 4px; color: var(--tower-mute); }
.tower-row { grid-template-columns: 26px 4px minmax(0, 1fr) 64px; }
.tower-row .who b { font-size: 12px; }
.tower-row .who span { font-size: 10px; }
.tower-foot { flex-wrap: wrap; }
.grid-wrap { margin-block: 16px 28px; }
.grid td { vertical-align: top; text-align: left; overflow-wrap: anywhere; }
.grid .num { text-align: right; }
.matrix td { text-align: center; }
.grid td .cell { background: color-mix(in srgb, var(--blue) var(--shade), var(--paper)); color: var(--ink); font: 500 12px/1.5 var(--mono); height: auto; padding: 6px 10px; margin: 2px; }
.grid code { overflow-wrap: anywhere; }
.chart { font-family: var(--mono); }
.chart .bar { fill: var(--blue); }
.chart text { fill: var(--ink); font-size: 12px; }
.chart .muted { fill: var(--muted); }
.prose > p, .prose > ul, .prose > ol { max-width: 72ch; }
pre { overflow-x: auto; padding: 20px; background: var(--soft); border-radius: var(--radius-inner); }
@media (max-width: 1279px) {
  .tower-table { min-width: 1200px; }
}
@media (max-width: 960px) {
  .cards { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 640px) {
  .wrap { padding-inline: 16px; }
  .page-hero { padding-top: 36px; }
  .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .card { padding: 18px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; }
}
"""

FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
    "<style>:root { --ink: #111111; --paper: #FFFFFF; } "
    "@media (prefers-color-scheme: dark) { :root { --ink: #F2F4F7; --paper: #0C0F13; } }</style>\n"
    + MARK
    + "\n</svg>\n"
)


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def harness_label(key: tuple) -> str:
    name, version = key[0], key[1]
    return f"{name} {version}" if version else name


def model_label(key: tuple) -> str:
    provider_id, reasoning = key[3], key[4]
    return f"{provider_id} @{reasoning}" if reasoning else provider_id


def short_hash(config_hash: str) -> str:
    return config_hash.removeprefix("sha256:")[:8]


def config_cell(config_hash: str) -> str:
    return (
        f'<a href="methodology.html#config-{esc(short_hash(config_hash))}">'
        f"<code>{esc(short_hash(config_hash))}</code></a>"
    )


def tokens_text(tokens: float | None) -> str:
    if tokens is None:
        return ""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    return f"{tokens / 1000:.0f}k"


def tokens_cell(summary: Mapping[str, Any]) -> str:
    if summary["tokens_per_pass"] is not None:
        return esc(tokens_text(summary["tokens_per_pass"]))
    if summary["n"] and not summary["passed"]:
        return "no passes"
    return f'<span class="note">not measured ({summary["tokens_measured"]}/{summary["n"]})</span>'


def cost_cell(summary: Mapping[str, Any]) -> str:
    cost = summary["cost_per_pass"]
    shown = lb.format_cost(cost)
    if shown == lb.UNKNOWN:
        return "unknown"
    tag = {"published-rate": "list", "provider-invoice-reconciled": "invoice"}[
        cost["source"]
    ]
    return f'{esc(shown)} <span class="tag">{tag}</span>'


def ci_svg(low: float, high: float, point: float | None) -> str:
    width, height = 120, 14
    x = lambda value: 2 + value * (width - 4)  # noqa: E731
    parts = [
        f'<svg class="ci" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="95% interval {pct(low)} to {pct(high)}">',
        f'<line class="track" x1="2" x2="{width - 2}" y1="7" y2="7" stroke-width="1"/>',
        f'<rect class="band" x="{x(low):.1f}" y="3" width="{max(x(high) - x(low), 1):.1f}" '
        'height="8" rx="2"/>',
    ]
    if point is not None:
        parts.append(f'<circle class="point" cx="{x(point):.1f}" cy="7" r="4"/>')
    parts.append("</svg>")
    return "".join(parts)


def excluded_text(excluded: Mapping[str, int]) -> str:
    if not excluded:
        return ""
    return ", ".join(f"{count} {esc(tag)}" for tag, count in excluded.items())


def brand() -> str:
    """The one brand lockup, as the header and footer of a root page render it."""
    return layout.brand()


def hero(eyebrow: str, title: str, lede: str = "") -> str:
    return (
        f'<div class="page-hero"><span class="eyebrow">{esc(eyebrow)}</span>'
        f"<h1>{esc(title)}</h1>"
        + (f'<p class="lede">{lede}</p>' if lede else "")
        + "</div>"
    )


def document(
    title: str,
    active: str,
    body: str,
    *,
    canonical: bool = False,
    filename: str | None = None,
) -> str:
    """One of today's board pages in the shared layout.

    ``active`` is the route the page belongs to (its nav and footer mark);
    ``filename`` is the output name when it differs, as for a board page.
    """
    # Other pages keep their content but share the same table and page treatments.
    body = body.replace(
        '<div class="scroll"><table>', '<div class="grid-wrap"><table class="grid">'
    )
    body = re.sub(r"<th\b(?![^>]*\bscope=)([^>]*)>", r'<th scope="col"\1>', body)
    if active not in ("index.html", "matrix.html") and "<h1>" not in body:
        body = re.sub(r"<h2>(.*?)</h2>", lambda m: hero(PRODUCT, m[1]), body, count=1)
    name = filename or active
    return layout.page(
        routes=ROUTES,
        current=name,
        nav_current=active,
        title=title,
        body=body,
        url=SITE_URL if name == "index.html" else SITE_URL + name,
        canonical=canonical,
        main_class="prose" if active == "methodology.html" else "",
    )


def fairness_banner(results: lb.Results) -> str:
    harnesses = sorted({record["harness"]["name"] for record in results.records})
    status = {
        audit["harness"]: audit["status"]
        for audit in results.fairness.get("audits", [])
    }
    pending = [name for name in harnesses if status.get(name) != "passed"]
    if not pending:
        return ""
    return (
        '<div class="caveat"><span class="flag-chip yellow" aria-hidden="true">⚑</span>'
        "<p><strong>Provisional.</strong> The harness fairness audit "
        f"(tools and permissions against vendor defaults) is not yet signed off for: "
        f'{esc(", ".join(pending))}. See <a href="methodology.html#fairness">fairness</a>.</p></div>'
    )


def board_title(board: Mapping[str, Any]) -> str:
    title = board["suite"]
    return (
        f"{title} · task version {board['task_version']}" if board["split"] else title
    )


COMPARABILITY = (
    "Each board ranks harness × model combinations on one suite by pass rate with a 95% "
    "Wilson interval; rows are not comparable across suites or task versions."
)
TASK_VERSIONS = (
    "Task versions are per task: a suite's newer versions can cover only the tasks that "
    "changed. Each board compares only rows at one task version."
)


def plural(count: int, singular: str, plural_form: str = "") -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def featured_board(boards: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The board a suite's overview card features, chosen from that suite's boards.

    Task versions are per task, so a suite's newest version may cover only the tasks
    that were bumped. Feature the board covering the most tasks; break ties by the
    higher task version. Suite boards have distinct versions, so the final
    entry-count tie-breaker is purely defensive for inputs with duplicate versions.
    """
    return max(
        boards,
        key=lambda board: (
            len(board["tasks"]),
            board["task_version"],
            len(board["rows"]),
        ),
    )


def board_anchor(board: Mapping[str, Any]) -> str:
    return f"{board['suite']}-v{board['task_version']}"


def board_filename(board: Mapping[str, Any]) -> str:
    # Suite labels outside the usual slug alphabet still need a single safe filename.
    suite = board["suite"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", suite):
        suite = "~" + suite.encode("utf-8").hex()
    return f"board-{suite}-v{board['task_version']}.html"


def livery(key: tuple) -> str:
    token = {
        "claude-code": "car-cc",
        "codex": "car-codex",
        "opencode": "car-opencode",
        "pi": "car-pi",
        "antigravity": "car-agy",
    }.get(key[0], "tower-mute")
    return f'<div class="livery" style="--c:var(--{token})" aria-hidden="true"></div>'


def who(key: tuple) -> str:
    return (
        f"<b>{esc(model_label(key))}</b><span>{esc(harness_label(key))} · "
        f"config {config_cell(key[2])}</span>"
    )


def finish(row: Mapping[str, Any]) -> str:
    return f"{row['passed']}<small> / {row['n']}</small>" if row["n"] else "—"


def board_html(board: Mapping[str, Any]) -> str:
    rows = []
    for rank, row in enumerate(board["rows"], start=1):
        key = row["key"]
        low, high = row["ci"]
        mean = "" if row["mean_score"] is None else f"{row['mean_score']:.2f}"
        rows.append(
            "<tr>"
            f'<td class="pos{" p1" if rank == 1 else ""}">{rank}</td>'
            f'<td>{livery(key)}</td><td class="who">{who(key)}</td>'
            f'<td class="fin">{finish(row)}</td>'
            f"<td>{ci_svg(low, high, row['rate'])} {pct(low)}–{pct(high)}</td>"
            f"<td>{mean}</td><td>{row['tasks']}</td><td>{tokens_cell(row)}</td>"
            f'<td class="cost">{cost_cell(row)}</td>'
            f'<td title="{excluded_text(row["excluded"])}">{sum(row["excluded"].values())}</td>'
            "</tr>"
        )
    tasks = "".join(f"<li>{esc(task)}</li>" for task in board["tasks"])
    return (
        f'<section id="{esc(board_anchor(board))}">'
        f'<details><summary>{plural(len(board["tasks"]), "task")}</summary><ul class="task-list">{tasks}</ul></details>'
        '<div class="tower"><div class="tower-head">'
        f'<span class="tower-title">{esc(board_title(board))}</span></div>'
        '<div class="table-wrap" role="region" aria-label="Board standings" tabindex="0">'
        '<table class="tower-table"><thead><tr><th scope="col">#</th>'
        '<th scope="col"><span class="sr-only">Livery</span></th><th scope="col">model · harness · config</th>'
        '<th scope="col">passed / n</th><th scope="col">95% Wilson</th>'
        '<th scope="col">mean</th><th scope="col">tasks</th><th scope="col">tokens / pass</th>'
        '<th scope="col">cost / pass</th><th scope="col">excluded</th></tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div><div class="tower-foot">'
        "<span>list = published-rate · invoice = provider-invoice-reconciled</span>"
        '</div><div class="checker"></div></div></section>'
    )


def board_page(board: Mapping[str, Any]) -> str:
    body = hero(
        f"Board · task version {board['task_version']}", board["suite"], COMPARABILITY
    )
    return document(
        board_title(board),
        "index.html",
        body + board_html(board),
        filename=board_filename(board),
    )


def board_counts(board: Mapping[str, Any]) -> str:
    return (
        f"task version {board['task_version']} · {plural(len(board['tasks']), 'task')} · "
        f"{plural(len(board['rows']), 'entry', 'entries')}"
    )


def board_card(board: Mapping[str, Any], others: Sequence[Mapping[str, Any]]) -> str:
    """A suite's card: the featured board's mini tower, then one line per other board."""
    rows = "".join(
        f'<li class="tower-row"><span class="pos{" p1" if rank == 1 else ""}">{rank}</span>'
        f'{livery(row["key"])}<div class="who">{who(row["key"])}</div>'
        f'<span class="fin">{finish(row)}</span></li>'
        for rank, row in enumerate(board["rows"][:5], start=1)
    )
    also = "".join(
        f'<li><a id="{esc(board_anchor(other))}" href="{esc(board_filename(other))}">'
        f"Also: {board_counts(other)} →</a></li>"
        for other in sorted(others, key=lambda b: b["task_version"], reverse=True)
    )
    return (
        f'<article class="card" id="{esc(board_anchor(board))}"><h3>{esc(board["suite"])}</h3>'
        f'<p class="kicker">{board_counts(board)}</p><div class="tower"><ol>{rows}</ol></div>'
        + (f'<ul class="also">{also}</ul>' if also else "")
        + f'<a class="card-link" href="{esc(board_filename(board))}">Full board →</a></article>'
    )


def index_page(results: lb.Results) -> str:
    boards = lb.boards(results.records)
    graded = sum(lb.included(record) for record in results.records)
    by_suite: dict[str, list[dict[str, Any]]] = {}
    for board in boards:
        by_suite.setdefault(board["suite"], []).append(board)
    cards = []
    for suite_boards in by_suite.values():
        featured = featured_board(suite_boards)
        others = [b for b in suite_boards if b is not featured]
        cards.append(board_card(featured, others))
    facts = [
        (len(results.records), "result rows"),
        (graded, "graded and scored"),
        (len(boards), "boards"),
        (len({r["harness"]["name"] for r in results.records}), "harnesses"),
        (len({lb.row_key(r)[3:] for r in results.records}), "models"),
    ]
    facts_html = "".join(
        f'<div class="fact"><b>{value}</b><span>{label}</span></div>'
        for value, label in facts
    )
    body = (
        hero(PRODUCT, "Leaderboard", f"{DESCRIPTION} {TASK_VERSIONS}")
        + f'<div class="facts">{facts_html}</div>'
        + fairness_banner(results)
        + '<section aria-labelledby="current-boards"><h2 id="current-boards">Current boards</h2>'
        + '<div class="cards">'
        + "".join(cards)
        + "</div></section>"
        + f"<p>{len(results.records)} result rows from {len(results.documents)} sources, "
        f"{graded} of them graded and scored. {COMPARABILITY} Read the "
        '<a href="methodology.html">methodology</a> before drawing conclusions: most cells '
        "are small.</p>"
    )
    return document("Leaderboard", "index.html", body, canonical=True)


def matrix_page(results: lb.Results) -> str:
    data = lb.matrix(results.records)
    header = "".join(
        f"<th>{esc(model[0] + (' @' + model[1] if model[1] else ''))}</th>"
        for model in data["models"]
    )
    rows = []
    for harness in data["harnesses"]:
        cells = []
        for model in data["models"]:
            by_suite = data["cells"].get((harness, model))
            if not by_suite:
                cells.append(
                    '<td class="empty"><span aria-label="No rows">·</span></td>'
                )
                continue
            lines = []
            for (suite, version), summary in by_suite.items():
                shade = round((summary["rate"] or 0) * 24)
                label = f"{suite}{'' if version == 1 else f' v{version}'}"
                lines.append(
                    f'<span class="cell" style="--shade:{shade}%">{esc(label)}: '
                    f"{summary['passed']}/{summary['n']}</span>"
                )
            cells.append(f"<td>{'<br>'.join(lines)}</td>")
        label = harness_label(harness)
        rows.append(
            f'<tr><th scope="row">{esc(label)}<br>{config_cell(harness[2])}</th>{"".join(cells)}</tr>'
        )
    body = (
        hero(PRODUCT, "Harness × model")
        + '<p class="note">Each cell lists passed / n per suite for one harness configuration '
        "and model. A dot means the combination has no rows. Coverage differs between "
        "cells, so compare within a suite on its board.</p>"
        f'<div class="grid-wrap"><table class="grid matrix"><thead><tr><th>harness · config</th>{header}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>" + tokens_chart(results)
    )
    return document("Harness x model", "matrix.html", body)


def tokens_chart(results: lb.Results) -> str:
    """Tokens per pass for every board row whose attempts were all measured."""
    bars = []
    unmeasured = 0
    for board in lb.boards(results.records):
        for row in board["rows"]:
            if row["tokens_per_pass"] is None:
                unmeasured += row["n"] > 0
                continue
            label = f"{board_title(board)} · {harness_label(row['key'])} · {model_label(row['key'])}"
            bars.append((label, row["tokens_per_pass"], row["passed"], row["n"]))
    intro = (
        "<h2>Tokens per pass</h2>"
        '<p class="note">All tokens of every scored attempt, failed attempts included, '
        "divided by passes. A row appears only when every one of its attempts has measured "
        f"tokens; {unmeasured} board rows do not and are left out rather than estimated.</p>"
    )
    if not bars:
        return (
            intro
            + "<p>No board row in results/ has complete token measurements yet.</p>"
        )
    label_w, bar_w, row_h = 420, 360, 26
    top = max(value for _, value, _, _ in bars)
    height = row_h * len(bars) + 8
    marks = []
    for index, (label, value, passed, n) in enumerate(bars):
        y = 4 + index * row_h
        width = max(value / top * bar_w, 4) if top else 4
        marks.append(
            f"<g><title>{esc(label)}: {esc(tokens_text(value))} tokens per pass "
            f"({passed}/{n} passed)</title>"
            f'<text class="muted" x="{label_w - 8}" y="{y + 15}" text-anchor="end">{esc(label)}</text>'
            f'<path class="bar" d="M{label_w},{y + 4}h{width - 4:.1f}a4,4 0 0 1 4,4v8a4,4 0 0 1 -4,4'
            f'h-{width - 4:.1f}z"/>'
            f'<text x="{label_w + width + 6:.1f}" y="{y + 15}">{esc(tokens_text(value))}</text></g>'
        )
    return (
        intro
        + f'<div class="scroll"><svg class="chart" width="{label_w + bar_w + 80}" '
        f'height="{height}" role="img" aria-label="Tokens per pass by board row">'
        + "".join(marks)
        + "</svg></div>"
    )


def tasks_page(results: lb.Results) -> str:
    rows_by_suite: dict[str, list[str]] = {}
    for task in lb.discrimination(results.records):
        runs = "; ".join(
            f"{esc(harness_label(key))} {esc(model_label(key))}: <code>{esc(letters)}</code>"
            for key, letters in task["runs"].items()
        )
        rows_by_suite.setdefault(task["suite"], []).append(
            "<tr>"
            f"<td>{esc(task['task'])}</td>"
            f'<td class="num">{task["task_version"]}</td>'
            f'<td class="num">{task["passed"]}/{task["n"]}</td>'
            f'<td class="num">{task["combos_passing"]}/{task["combos"]}</td>'
            f"<td>{'yes' if task['discriminates'] else 'no'}</td>"
            f'<td class="note">{runs}</td>'
            "</tr>"
        )
    sections = "".join(
        f"<h3>{esc(suite)}</h3>"
        '<div class="scroll"><table><thead><tr><th>task</th><th class="num">version</th>'
        '<th class="num">passed / n</th><th class="num">combos passing</th>'
        "<th>discriminates</th><th>runs (P passed, F not passed)</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        for suite, rows in sorted(rows_by_suite.items())
    )
    body = (
        "<h2>Per-task discrimination</h2>"
        '<p class="note">A task discriminates when at least one scored attempt passed it and '
        "at least one did not. A task every combination passes, or none does, cannot rank "
        "anything; it is a candidate for a harder version.</p>" + sections
    )
    return document("Tasks", "tasks.html", body)


def changelog_page(results: lb.Results) -> str:
    counts = lb.exclusion_counts(results.records)
    rows = []
    for entry in sorted(
        results.changelog, key=lambda e: (e["date"], e["id"]), reverse=True
    ):
        tag = entry["excludes"]
        excluded = (
            f"{counts.get(tag, 0)} rows tagged <code>{esc(tag)}</code>" if tag else "—"
        )
        rows.append(
            f'<tr id="{esc(entry["id"])}"><td>{esc(entry["date"])}</td>'
            f"<td><strong>{esc(entry['id'])}</strong><br>"
            f'<span class="note">{esc(entry["kind"])}</span></td>'
            f"<td>{esc(entry['summary'])}</td><td>{esc(entry['boundary'])}</td>"
            f"<td>{excluded}</td></tr>"
        )
    body = (
        "<h2>Grading changelog</h2>"
        '<p class="note">Every change to grading, tasks, harness capture or pricing that '
        "affects what a result means, with the comparability boundary it draws. Rows on the "
        "wrong side of a boundary keep their data and carry the tag shown; they are not "
        "scored.</p>"
        '<div class="scroll"><table><thead><tr><th>date</th><th>change</th><th>what changed</th>'
        "<th>comparability boundary</th><th>excluded</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    return document("Grading changelog", "changelog.html", body)


def price_table_html(table: lb.PriceTable) -> str:
    rows = []
    for model, entry in sorted(table.models.items()):
        rates = (
            "".join(f'<td class="num">{entry[key]:.4g}</td>' for key in lb.TOKEN_KEYS)
            if entry["source"] in lb.PRICED_SOURCES
            else '<td class="num" colspan="3">unknown</td>'
        )
        rows.append(
            f"<tr><td>{esc(model)}</td>{rates}<td>{esc(entry['source'])}</td>"
            f"<td>{esc(entry['as_of'])}</td><td>{esc(entry['reference'])}"
            + (
                f'<br><span class="note">{esc(entry["note"])}</span>'
                if entry["note"]
                else ""
            )
            + "</td></tr>"
        )
    return (
        f'<h3 id="prices-{esc(table.date)}">Price table {esc(table.date)}</h3>'
        f'<p class="note">{esc(table.note)}</p>'
        '<div class="scroll"><table><thead><tr><th>model</th><th class="num">input</th>'
        '<th class="num">cache read</th><th class="num">output</th><th>source</th>'
        "<th>as of</th><th>reference</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def fairness_source(source: str) -> str:
    """Link vendor documentation and repository lines; escape telemetry selectors."""
    if source.startswith("https://"):
        return f'<a href="{esc(source)}" rel="noopener noreferrer">{esc(source)}</a>'
    match = re.match(
        r"^((?:results|lib|batteries|locks|scripts)/[\w./-]+):(\d+)\b", source
    )
    if match and lb.fairness_repository_reference(source):
        path, line = match.groups()
        return f'<a href="{REPO_URL}/blob/main/{esc(path)}#L{line}">{esc(source)}</a>'
    return f"<code>{esc(source)}</code>"


def fairness_evidence(audit: Mapping[str, Any]) -> str:
    if not audit.get("evidence"):
        return ""
    rows = "".join(
        f"<tr><td>{esc(item['id'])}</td><td>{esc(item['status'])}</td>"
        f"<td>{esc(item['notes'])}<ul>"
        + "".join(f"<li>{fairness_source(source)}</li>" for source in item["sources"])
        + "</ul></td></tr>"
        for item in audit["evidence"]
    )
    return (
        f"<h4>{esc(audit['harness'])}: checklist evidence</h4>"
        '<div class="scroll"><table><thead><tr><th>check</th><th>finding</th>'
        f"<th>evidence</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def methodology_page(results: lb.Results) -> str:
    configs = "".join(
        f'<tr id="config-{esc(short_hash(key))}"><td><code>{esc(short_hash(key))}</code></td>'
        f"<td><code>{esc(lb.canonical_json(descriptor))}</code></td></tr>"
        for key, descriptor in results.configs().items()
    )
    # Display the logical document name, independent of its storage compression.
    sources = "".join(
        f"<tr><td><code>{esc(name.removesuffix('.gz'))}</code></td><td>{esc(doc['source']['kind'])}</td>"
        f'<td class="num">{len(doc["records"])}</td><td>{esc(doc["source"]["description"])}'
        f'<br><span class="note">Evidence: {esc(doc["source"]["evidence"])}</span></td></tr>'
        for name, doc in sorted(results.documents.items())
    )
    checklist = "".join(
        f"<li><strong>{esc(item['id'])}</strong>: {esc(item['check'])}</li>"
        for item in results.fairness.get("checklist", [])
    )
    audits = "".join(
        f"<tr><td>{esc(audit['harness'])}</td><td>{esc(audit['status'])}</td>"
        f"<td>{esc(audit['date'] or '—')}</td><td>{esc(audit['notes'])}</td>"
        f"<td>{esc(audit.get('sign_off') or '—')}</td></tr>"
        for audit in sorted(
            results.fairness.get("audits", []), key=lambda a: a["harness"]
        )
    )
    audit_evidence = "".join(
        fairness_evidence(audit)
        for audit in sorted(
            results.fairness.get("audits", []), key=lambda a: a["harness"]
        )
    )
    prices = "".join(
        price_table_html(table) for _, table in sorted(results.price_tables.items())
    )
    body = f"""
<h2>Methodology</h2>
<h3>What a row is</h3>
<p>Each row in <code>results/</code> is one cell: one graded attempt of one task by one harness
configuration and one model. It records the suite, task and <code>task_version</code>; the
harness name, version and configuration hash; the model's provider id; the verdict and
partial-credit score; tokens (fresh input, cache reads, output); wall time; the run id and the
Agent Paddock commit it ran at; and its cost.</p>
<h3>What is scored</h3>
<ul>
<li>A verifier outside the agent's sandbox grades every attempt from its submission and the
harness's own tool-call record. Only <code>passed</code> is a pass; <code>failed</code>,
<code>format_failed</code> and <code>no_submission</code> count in n.</li>
<li>Infrastructure failures and quarantines (<code>agent_error</code>, <code>error</code>,
<code>integrity_hold</code>, <code>skipped_no_taint_gate</code>) are never scored. They carry
<code>excludedBecause: infra:&lt;reason&gt;</code> and are counted in each board's excluded
column.</li>
<li>Rows graded before a fix that changed what a pass means carry the fix's tag, for example
<code>pre-GYMDOCS-01</code>, and are not scored. Each tag is a
<a href="changelog.html">grading changelog</a> entry.</li>
<li>Results compare only within one task version. A task whose content changes gets a new
<code>task_version</code>; rows at different versions are never pooled, so a suite with rows at
two versions has two boards.</li>
<li>Rows from different harness configurations are different rows, even with the same harness
name and model: the configuration hash is part of the row.</li>
</ul>
<h3>Statistics</h3>
<p>Pass rate is passed / n with a 95% Wilson score interval (z = 1.96). Mean is partial credit,
the fraction of a task's sub-checks passed, averaged over scored attempts that report one.
Boards rank by pass rate, then by the interval's lower bound. Most cells are small (n of 4 to
18): overlapping intervals are not a ranking.</p>
<h3>Tokens and cost</h3>
<ul>
<li>Tokens are what the harness reported: fresh input, cache-read input and output (reasoning
included). A count nobody measured is null, never zero.</li>
<li>Tokens per pass is the tokens of every scored attempt, failed attempts included, divided by
passes. It is shown only when every scored attempt in the row was measured.</li>
<li>A row's cost is its tokens priced at the dated price table its results document names. The
table gives each price a source: <code>provider-invoice-reconciled</code> (checked against a
provider invoice), <code>published-rate</code> (the provider's list price) or
<code>unknown</code>. Cost per pass is shown only when every scored attempt is priced; any
missing token count, missing price or unknown source makes it "unknown", never a number.
Gateway or proxy cost estimates are never a source.</li>
<li>Costs are API-equivalent: a run on a subscription plan is priced at the API rate.</li>
</ul>
{prices}
<h3 id="fairness">Harness fairness</h3>
<p>Before results are published, every harness's tool and permission configuration is audited
against its vendor defaults with this checklist, and the audit is recorded here.</p>
<p>Each finding is <code>pass</code>, <code>fail</code> or <code>not-verifiable</code>.
An <code>audited</code> harness has evidence for every check passing; it remains provisional
until a maintainer signs off. A <code>pending</code> harness has unresolved checks.
The audit date records the evidence review, not sign-off. Vendor documentation describes
the defaults on that date; without a recorded CLI version it cannot establish historical
defaults. Current runner code does not establish how an imported run executed.</p>
<ol>{checklist}</ol>
<div class="scroll"><table><thead><tr><th>harness</th><th>audit</th><th>date</th><th>notes</th>
<th>maintainer sign-off</th></tr></thead><tbody>{audits}</tbody></table></div>
{audit_evidence}
<h3>Harness configurations</h3>
<p class="note">A row's configuration hash is the SHA-256 of this canonical JSON descriptor.</p>
<div class="scroll"><table><thead><tr><th>hash</th><th>descriptor</th></tr></thead>
<tbody>{configs}</tbody></table></div>
<h3>Sources</h3>
<p>The first results were imported from the eval bench that preceded Agent Paddock, keeping each
source's exclusions. Those rows carry no token or wall-time measurements and no harness
version, so their tokens and cost read "not measured" and "unknown". Published aggregates with
no per-attempt rows are not imported: only rows can be recomputed.</p>
<div class="scroll"><table><thead><tr><th>file</th><th>kind</th><th class="num">rows</th>
<th>source</th></tr></thead><tbody>{sources}</tbody></table></div>
<h3>Reproduce this site</h3>
<pre><code>git clone {REPO_URL}
cd agentpaddock
python3 scripts/leaderboard.py validate
python3 scripts/build_site.py</code></pre>
<p>To submit results or a vendor correction, see
<a href="{REPO_URL}/blob/main/CONTRIBUTING.md">CONTRIBUTING</a>.</p>
"""
    return document("Methodology", "methodology.html", body)


def card_source() -> str:
    """The share card's HTML: the committed template with the site's one Broadcast mark."""
    template = CARD_TEMPLATE.read_text(encoding="utf-8")
    if template.count(CARD_MARK_SLOT) != 1:
        raise ValueError(f"{CARD_TEMPLATE.name} needs exactly one {CARD_MARK_SLOT}")
    return template.replace(CARD_MARK_SLOT, MARK)


def card_source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """``(type, body)`` for each chunk of a PNG, through IEND."""
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG")
    chunks, i = [], 8
    while i + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[i : i + 8])
        chunks.append((kind, data[i + 8 : i + 8 + length]))
        i += 12 + length
        if kind == b"IEND":
            break
    return chunks


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from a PNG's IHDR."""
    chunks = png_chunks(data)
    if not chunks or chunks[0][0] != b"IHDR":
        raise ValueError("PNG does not start with IHDR")
    _, body = chunks[0]
    return struct.unpack(">II", body[:8])


def png_text(data: bytes) -> dict[str, str]:
    """A PNG's tEXt chunks, keyword -> text."""
    return {
        key.decode("latin-1"): value.decode("latin-1")
        for kind, body in png_chunks(data)
        if kind == b"tEXt" and b"\0" in body
        for key, value in [body.split(b"\0", 1)]
    }


def stamp_png(data: bytes, key: str, value: str) -> bytes:
    """``data`` with one tEXt chunk ``key`` = ``value`` right after IHDR."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    keyword = key.encode("latin-1")
    kept = [
        chunk(kind, body)
        for kind, body in png_chunks(data)
        if not (kind == b"tEXt" and body.split(b"\0", 1)[0] == keyword)
    ]
    text = chunk(b"tEXt", keyword + b"\0" + value.encode("latin-1"))
    return PNG_SIGNATURE + kept[0] + text + b"".join(kept[1:])


def card_problem(png: bytes) -> str:
    """Why the committed card does not match its source, or ``""`` when it does."""
    try:
        size, stamp = png_size(png), png_text(png).get(CARD_KEY)
    except (ValueError, struct.error):
        return f"{CARD_NAME} is not a PNG"
    if size != CARD_SIZE:
        return f"{CARD_NAME} is {size[0]}x{size[1]}, not {CARD_SIZE[0]}x{CARD_SIZE[1]}"
    if stamp != card_source_hash(card_source()):
        return (
            f"{CARD_NAME} was rendered from a different card source; "
            "run python3 scripts/render_social_card.py"
        )
    return ""


def _site_page(module):
    """A PDW page: the results, the site data (``data/*.json``, parsed) and the routes."""
    return lambda build: module.page(build.results, build.data, build.routes)


def learn_page(build: site_routes.Build) -> str:
    """Build the Learn page from the parsed site data."""
    route = next(route for route in build.routes if route.path == "learn/")
    return learn.page(route, build.routes, build.data["data/drivers.json"])


#: Pages cut from the site data files (PDW-08): route path -> builder taking
#: the parsed data (``views.SiteData``), the route and the registry, and
#: returning ``{output file: page}``. A data page may write more than its own
#: route's file, as ``/drivers/`` writes one page per driver.
DATA_PAGES = {
    "garage/": garage.page,
    "pit-wall/": pit_wall.page,
    "drivers/": site_drivers.pages,
}
#: Page styles and scripts of the data pages, in the one stylesheet and script.
DATA_CSS = views.CSS + garage.CSS + pit_wall.CSS + site_drivers.CSS
DATA_SCRIPT = views.SCRIPT + garage.SCRIPT + pit_wall.SCRIPT + site_drivers.SCRIPT

#: Every page the site builds, in nav order. Today's board pages are built by
#: the functions above; a page ticket adds its builder here (which also keeps
#: its merge inside the pages workflow's ``scripts/build_site.py`` path filter).
def _from_results(page: Any) -> site_routes.Builder:
    """A builder for one of today's board pages, which read only the results."""
    return lambda build: page(build.results)


ROUTES = site_routes.registry(
    {
        "": _from_results(index_page),
        "learn/": learn_page,
        "races/": race_pages.grid_page,
        "standings/": standings_pages.standings_page,
        "entries/": standings_pages.entries_page,
        "matrix.html": _from_results(matrix_page),
        "tasks.html": _from_results(tasks_page),
        "changelog.html": _from_results(changelog_page),
        "methodology.html": _from_results(methodology_page),
        "community/": _site_page(community),
        "rulebook/": _site_page(rulebook),
        "dictionary/": _site_page(dictionary),
        "race-it-yourself/": _site_page(pit_lane),
        "contact/": _site_page(contact),
        **DATA_PAGES,
    }
)
#: ``(output file, title)`` for every registered page.
PAGES = tuple((route.file, route.title) for route in ROUTES)


def build(results_dir: Path = lb.DEFAULT_RESULTS) -> dict[str, str | bytes]:
    """Site files; raises ``ResultsError`` on invalid results or ``FileNotFoundError`` for a missing card."""
    results = lb.load_results(results_dir)
    try:
        card = CARD_PNG.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"{CARD_NAME} is missing; run python3 scripts/render_social_card.py"
        ) from exc
    # The site data layer (PDW-03), computed once: the data/*.json files and
    # the pages built from them read the same values.
    site = site_data.site(results, results_dir.parent / "tasks")
    data_files = site_data.render_files(site)
    parsed_data = views.SiteData.parse(data_files)
    inputs = site_routes.Build(
        results,
        {name: json.loads(text) for name, text in data_files.items()},
        ROUTES,
    )
    files: dict[str, str | bytes] = {}
    for route in ROUTES:
        if route.path in DATA_PAGES:
            files.update(route.builder(parsed_data, route, ROUTES))
        else:
            files[route.file] = (
                route.builder(inputs) if route.builder else placeholder.page(route, ROUTES)
            )
    # One page per calendar race (PDW-06) and per standings entry (PDW-07).
    files.update(race_pages.race_pages(site, ROUTES))
    files.update(standings_pages.entry_pages(inputs))
    files.update(
        {
            "paddock.css": layout.stylesheet(
                STYLE + race_pages.CSS + standings_pages.CSS + kit.CSS + learn.CSS + DATA_CSS
            ),
            SCRIPT_NAME: layout.SCRIPT + race_pages.SCRIPT + kit.SCRIPT + DATA_SCRIPT,
            standings_pages.SCRIPT_NAME: standings_pages.SCRIPT,
        learn.SCRIPT_FILE: learn.SCRIPT,
            "favicon.svg": FAVICON,
            CARD_NAME: card,
        }
    )
    for board in lb.boards(results.records):
        files[board_filename(board)] = board_page(board)
    files.update(data_files)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=lb.DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check", action="store_true", help="fail if --out differs from a build"
    )
    args = parser.parse_args(argv)
    try:
        files = build(args.results)
    except lb.ResultsError as exc:
        print(f"results/ does not validate:\n{exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    card = card_problem(files[CARD_NAME])
    if args.check:
        stale = [
            name
            for name, content in files.items()
            if not (args.out / name).is_file()
            or read(args.out / name, content) != content
        ]
        if card:
            stale.append(card)
        if stale:
            print(f"{args.out} is stale: {', '.join(stale)}", file=sys.stderr)
            return 1
        print(f"{args.out} is current")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (args.out / name).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            (args.out / name).write_bytes(content)
        else:
            (args.out / name).write_text(content, encoding="utf-8")
    print(f"Site written to {args.out}: " + ", ".join(files))
    if card:
        print(f"warning: {card}", file=sys.stderr)
    return 0


def read(path: Path, like: str | bytes) -> str | bytes:
    return (
        path.read_bytes()
        if isinstance(like, bytes)
        else path.read_text(encoding="utf-8")
    )


if __name__ == "__main__":
    sys.exit(main())
