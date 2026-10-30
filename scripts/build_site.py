"""Build the Paddock leaderboard site from ``results/`` only (PDK-04).

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
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import leaderboard as lb

REPO_URL = "https://github.com/laceyenterprises/agentpaddock"
SITE_URL = "https://agentpaddock.ai/"
DEFAULT_OUT = lb.MODULE_ROOT / "site"

PAGES = (
    ("index.html", "Leaderboard"),
    ("matrix.html", "Harness × model"),
    ("tasks.html", "Tasks"),
    ("changelog.html", "Changelog"),
    ("methodology.html", "Methodology"),
)

STYLE = """
:root {
  /* paper and ink (Searchlight family) */
  --paper: #FFFFFF;
  --soft: #F4F6F9;
  --rule: #E3E7EC;
  --ink: #111111;
  --ink-2: #4F5761;
  --muted: #626C78;
  /* Lacey Enterprises brand: blue = interactive, green = the finish */
  --blue: #007BFF;
  --blue-ink: #0057B3;
  --blue-soft: #EAF3FF;
  --go: #00C27A;
  --go-ink: #007A4D;
  --go-soft: #E2F7EE;
  /* flags */
  --red: #E5202E;
  --red-ink: #B3121C;
  --amber: #F5B700;
  --amber-ink: #8A5A00;
  /* the timing tower never changes with the theme */
  --tower: #0E1116;
  --tower-2: #171B22;
  --tower-rule: #262C35;
  --tower-fg: #F2F4F7;
  --tower-mute: #8B94A3;
  --tower-lit: #2BD99A;
  /* team liveries: one colour per car (harness), used in every chart */
  --car-cc: #E0703A;
  --car-codex: #10A37A;
  --car-opencode: #C2185B;
  --car-pi: #007BFF;
  --car-agy: #D99A00;

  --display: "Saira Condensed", "Arial Narrow", "Roboto Condensed", system-ui, sans-serif;
  --body: "Inter", -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;

  --radius-card: 18px;
  --radius-inner: 12px;
  --ease: cubic-bezier(.2, .8, .2, 1);
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0C0F13; --soft: #141920; --rule: #262C35; --ink: #F2F4F7; --ink-2: #B9C1CC; --muted: #8B94A3;
    --blue: #3D9BFF; --blue-ink: #7DBBFF; --blue-soft: #0F2238;
    --go: #2BD99A; --go-ink: #5BE3AE; --go-soft: #0D2A20;
    --red: #FF4D4F; --red-ink: #FF8587; --amber: #FFC83D; --amber-ink: #FFD36B;
    --tower: #161B22; --tower-2: #1D232C; --tower-rule: #2B323C;
    --car-cc: #F08A55; --car-codex: #2BC495; --car-opencode: #E8558E; --car-pi: #3D9BFF; --car-agy: #FFC83D;
    color-scheme: dark;
  }
}

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

/* ---------- nav ---------- */
.nav {
  position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20;
  background: color-mix(in srgb, var(--paper) 92%, transparent);
  backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--rule);
}
.nav .wrap { display: flex; align-items: center; gap: 24px; height: 64px; }
.brand { display: inline-flex; align-items: center; gap: 10px; color: var(--ink); text-decoration: none; flex: none; }
.brand svg { width: 30px; height: 30px; display: block; }
.wordmark { font: italic 800 22px/1 var(--display); letter-spacing: .06em; text-transform: uppercase; }
.nav-links { display: flex; gap: 4px; margin-left: auto; overflow-x: auto; scrollbar-width: none; min-width: 0; }
.nav-links::-webkit-scrollbar { display: none; }
.nav-links a {
  color: var(--ink-2); text-decoration: none; font-size: 14.5px; font-weight: 500; padding: 8px 12px; border-radius: 999px;
  white-space: nowrap; transition: background .2s var(--ease), color .2s var(--ease);
}
.nav-links a:hover, .nav-links a[aria-current="page"] { background: var(--soft); color: var(--ink); }
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


/* ---------- footer ---------- */
.footer { background: var(--tower); color: var(--tower-fg); padding-block: 0 40px; }
.footer .checker { margin-bottom: 40px; }
.footer-grid { display: grid; grid-template-columns: 1.4fr 1fr 1fr 1fr; gap: 32px; }
.footer h4 { margin: 0 0 10px; font: 600 11.5px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--tower-mute); }
.footer a { color: var(--tower-fg); text-decoration: none; display: block; padding: 3px 0; font-size: 14.5px; }
.footer a:hover { color: var(--tower-lit); }
.footer p { color: var(--tower-mute); font-size: 14px; }
.footer .brand { color: var(--tower-fg); margin-bottom: 12px; }
.footer-base { margin-top: 36px; padding-top: 18px; border-top: 1px solid var(--tower-rule); font: 400 12px var(--mono); color: var(--tower-mute); display: flex; flex-wrap: wrap; gap: 8px 24px; justify-content: space-between; }

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
.symbols { position: absolute; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
a:hover { color: var(--blue-ink); }
.brand:hover { color: var(--ink); }
html { scroll-padding-top: 90px; }
.wrap { max-width: 1488px; min-width: 0; }
main { padding-bottom: 64px; }
h2 { font: 700 36px/1.1 var(--display); margin: 40px 0 20px; }
h3 { font: 600 24px/1.3 var(--display); margin: 28px 0 12px; }
h4 { margin: 24px 0 12px; }
p, ul, ol, pre { margin-block: 12px; }
.footer p a { display: inline; }
.wordmark { font: italic 800 22px/1 "Saira", var(--display); letter-spacing: .01em; }
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
.earlier { list-style: none; padding: 0; }
.earlier li { padding-block: 10px; border-bottom: 1px solid var(--rule); }
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
.footer .brand { display: inline-flex; }
.footer .brand { --ink: var(--tower-fg); --paper: var(--tower); }
.footer-grid { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); }
@media (max-width: 1279px) {
  .tower-table { min-width: 1200px; }
}
@media (max-width: 960px) {
  .nav .wrap { gap: 12px; }
  .nav-links a { padding-inline: 10px; }
  .cards { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 640px) {
  .wrap { padding-inline: 16px; }
  .nav .wrap { height: 56px; gap: 12px; }
  .nav .gh { display: none; }
  .wordmark { font-size: 19px; }
  .nav-links { margin-left: 0; }
  .nav-links a { font-size: 13px; }
  .page-hero { padding-top: 36px; }
  .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .card { padding: 18px; }
  .footer-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; }
}
"""

MARK = """<g transform="translate(9 0) skewX(-10)">
    <rect x="18" y="16" width="64" height="68" rx="7" fill="var(--ink)"/>
    <rect x="26" y="26" width="8" height="12" fill="#007BFF"/><rect x="38" y="26" width="36" height="12" fill="var(--paper)"/>
    <rect x="26" y="44" width="8" height="12" fill="#00C27A"/><rect x="38" y="44" width="26" height="12" fill="var(--paper)"/>
    <rect x="26" y="62" width="8" height="12" fill="#E0703A"/><rect x="38" y="62" width="16" height="12" fill="var(--paper)"/>
  </g>"""

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
    return (
        '<a class="brand" href="index.html" aria-label="Paddock home">'
        '<svg viewBox="0 0 100 100" aria-hidden="true"><use href="#broadcast"/></svg>'
        '<span class="wordmark">PADDOCK</span></a>'
    )


def hero(eyebrow: str, title: str, lede: str = "") -> str:
    return (
        f'<div class="page-hero"><span class="eyebrow">{esc(eyebrow)}</span>'
        f"<h1>{esc(title)}</h1>"
        + (f'<p class="lede">{lede}</p>' if lede else "")
        + "</div>"
    )


def document(title: str, active: str, body: str, *, canonical: bool = False) -> str:
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{name}"{current if name == active else ""}>{esc(label)}</a>'
        for name, label in PAGES
    )
    head_link = f'<link rel="canonical" href="{SITE_URL}">' if canonical else ""
    # Other pages keep their content but share the same table and page treatments.
    body = body.replace(
        '<div class="scroll"><table>', '<div class="grid-wrap"><table class="grid">'
    )
    body = re.sub(r"<th\b(?![^>]*\bscope=)([^>]*)>", r'<th scope="col"\1>', body)
    if active not in ("index.html", "matrix.html") and "<h1>" not in body:
        body = re.sub(r"<h2>(.*?)</h2>", lambda m: hero("Paddock", m[1]), body, count=1)
    prose = ' class="prose"' if active == "methodology.html" else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} · Paddock</title>\n{head_link}\n"
        '<link rel="icon" href="favicon.svg" type="image/svg+xml">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
        "&amp;family=JetBrains+Mono:wght@400;500;600&amp;family=Saira:ital,wght@1,800"
        '&amp;family=Saira+Condensed:ital,wght@0,600;0,700;1,800&amp;display=swap" rel="stylesheet">\n'
        '<link rel="stylesheet" href="paddock.css">\n</head>\n<body>\n'
        '<svg width="0" height="0" aria-hidden="true" class="symbols">'
        f'<defs><symbol id="broadcast" viewBox="0 0 100 100">{MARK}</symbol></defs></svg>\n'
        f'<header class="nav"><nav class="wrap" aria-label="Main">{brand()}'
        f'<div class="nav-links">{nav}</div>'
        f'<a class="btn ghost sm gh" href="{REPO_URL}">GitHub</a></nav></header>\n'
        f'<main class="wrap"><div{prose}>\n{body}\n</div></main>\n'
        '<footer class="footer"><div class="checker"></div><div class="wrap">'
        f'<div class="footer-grid"><div>{brand()}'
        "<p>Built only from the committed <code>results/</code> of "
        f'<a href="{REPO_URL}">laceyenterprises/agentpaddock</a>. '
        f'Submit a run or a correction: <a href="{REPO_URL}/blob/main/CONTRIBUTING.md">'
        "CONTRIBUTING</a>.</p></div><div>"
        f'<a href="{REPO_URL}">GitHub</a>'
        f'<a href="{REPO_URL}/blob/main/CONTRIBUTING.md">CONTRIBUTING</a>'
        f'<a href="{REPO_URL}/blob/main/LICENSE">MIT LICENSE</a></div></div>'
        '<div class="footer-base">A Lacey Enterprises project · MIT licensed · '
        "© 2026 Lacey Enterprises LLC</div></div></footer>\n</body>\n</html>\n"
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


DESCRIPTION = (
    "An open leaderboard for coding agents: model × harness on real repository tasks."
)
COMPARABILITY = (
    "Each board ranks harness × model combinations on one suite by pass rate with a 95% "
    "Wilson interval; rows are not comparable across suites or task versions."
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
        f'<details><summary>{len(board["tasks"])} tasks</summary><ul class="task-list">{tasks}</ul></details>'
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
    return document(board_title(board), "index.html", body + board_html(board))


def board_card(board: Mapping[str, Any]) -> str:
    rows = "".join(
        f'<li class="tower-row"><span class="pos{" p1" if rank == 1 else ""}">{rank}</span>'
        f'{livery(row["key"])}<div class="who">{who(row["key"])}</div>'
        f'<span class="fin">{finish(row)}</span></li>'
        for rank, row in enumerate(board["rows"][:5], start=1)
    )
    return (
        f'<article class="card" id="{esc(board_anchor(board))}"><h3>{esc(board["suite"])}</h3>'
        f'<p class="kicker">task version {board["task_version"]} · {len(board["tasks"])} tasks · '
        f'{len(board["rows"])} entries</p><div class="tower"><ol>{rows}</ol></div>'
        f'<a class="card-link" href="{esc(board_filename(board))}">Full board →</a></article>'
    )


def index_page(results: lb.Results) -> str:
    boards = lb.boards(results.records)
    graded = sum(lb.included(record) for record in results.records)
    latest: dict[str, int] = {}
    for board in boards:
        latest[board["suite"]] = max(
            latest.get(board["suite"], 0), board["task_version"]
        )
    current = [b for b in boards if b["task_version"] == latest[b["suite"]]]
    earlier = [b for b in boards if b["task_version"] != latest[b["suite"]]]
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
    earlier_html = "".join(
        f'<li><a id="{esc(board_anchor(b))}" href="{esc(board_filename(b))}">'
        f"{esc(b['suite'])} · task version {b['task_version']}</a></li>"
        for b in earlier
    )
    body = (
        hero("Paddock", "Leaderboard", DESCRIPTION)
        + f'<div class="facts">{facts_html}</div>'
        + fairness_banner(results)
        + '<section aria-labelledby="current-boards"><h2 id="current-boards">Current boards</h2>'
        + '<div class="cards">'
        + "".join(board_card(board) for board in current)
        + "</div></section>"
        + '<section aria-labelledby="earlier-boards"><h2 id="earlier-boards">Earlier task versions</h2>'
        + f'<ul class="earlier">{earlier_html}</ul></section>'
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
        hero("Paddock", "Harness × model")
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
Paddock commit it ran at; and its cost.</p>
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
<p>The first results were imported from the eval bench that preceded Paddock, keeping each
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


def build(results_dir: Path = lb.DEFAULT_RESULTS) -> dict[str, str]:
    """``{filename: text}`` for the whole site; raises ``ResultsError`` on invalid input."""
    results = lb.load_results(results_dir)
    return {
        "index.html": index_page(results),
        "matrix.html": matrix_page(results),
        "tasks.html": tasks_page(results),
        "changelog.html": changelog_page(results),
        "methodology.html": methodology_page(results),
        "paddock.css": STYLE,
        "favicon.svg": FAVICON,
        **{
            board_filename(board): board_page(board)
            for board in lb.boards(results.records)
        },
    }


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
    if args.check:
        stale = [
            name
            for name, text in files.items()
            if not (args.out / name).is_file()
            or (args.out / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"{args.out} is stale: {', '.join(stale)}", file=sys.stderr)
            return 1
        print(f"{args.out} is current")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.out / name).write_text(text, encoding="utf-8")
    print(f"Site written to {args.out}: " + ", ".join(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
