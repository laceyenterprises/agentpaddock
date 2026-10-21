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
import sys
from pathlib import Path
from typing import Any, Mapping

import leaderboard as lb

REPO_URL = "https://github.com/laceyenterprises/agentpaddock"
SITE_URL = "https://agentpaddock.ai/"
DEFAULT_OUT = lb.MODULE_ROOT / "site"

PAGES = (
    ("index.html", "Leaderboard"),
    ("matrix.html", "Harness x model"),
    ("tasks.html", "Tasks"),
    ("changelog.html", "Grading changelog"),
    ("methodology.html", "Methodology"),
)

STYLE = """
:root { color-scheme: light; --surface: #fcfcfb; --raised: #f3f2ee; --ink: #0b0b0b;
  --ink-2: #52514e; --rule: #dcdbd5; --series-1: #2a78d6; --band: #b9d2f2; --warn: #8a5a00; }
@media (prefers-color-scheme: dark) {
  :root { color-scheme: dark; --surface: #1a1a19; --raised: #242422; --ink: #f0efec;
    --ink-2: #c3c2b7; --rule: #3a3a37; --series-1: #3987e5; --band: #29476b; --warn: #e0b453; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main, footer { max-width: 1120px; margin: 0 auto; padding: 0 20px; }
header { padding-top: 24px; }
header h1 { margin: 0; font-size: 26px; }
header p { margin: 4px 0 12px; color: var(--ink-2); }
nav { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 10px 0; border-bottom: 1px solid var(--rule); }
nav a { color: var(--ink-2); text-decoration: none; }
nav a[aria-current] { color: var(--ink); font-weight: 600; }
a { color: var(--series-1); }
h2 { margin-top: 36px; font-size: 20px; }
h3 { margin-top: 24px; font-size: 16px; }
.note { color: var(--ink-2); font-size: 14px; }
.banner { margin: 16px 0; padding: 10px 14px; border-left: 4px solid var(--warn);
  background: var(--raised); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 4px; font-size: 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; white-space: nowrap; }
.nowrap { white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-size: 13px; }
.ci { display: inline-block; vertical-align: middle; }
.ci .track { stroke: var(--rule); }
.ci .band { fill: var(--band); }
.ci .point { fill: var(--series-1); }
.chart .bar { fill: var(--series-1); }
.chart text { fill: var(--ink); font-size: 12px; }
.chart .muted { fill: var(--ink-2); }
footer { margin-top: 48px; padding: 16px 20px 32px; color: var(--ink-2); font-size: 13px;
  border-top: 1px solid var(--rule); }
"""


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
    return f'{esc(shown)} <span class="note">{esc(cost["source"])}</span>'


def ci_svg(low: float, high: float, point: float | None) -> str:
    width, height = 120, 14
    x = lambda value: 2 + value * (width - 4)  # noqa: E731
    parts = [
        f'<svg class="ci" width="{width}" height="{height}" role="img" '
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


def document(title: str, active: str, body: str, *, canonical: bool = False) -> str:
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{name}"{current if name == active else ""}>{esc(label)}</a>'
        for name, label in PAGES
    )
    head_link = f'<link rel="canonical" href="{SITE_URL}">' if canonical else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)} · Paddock</title>\n{head_link}\n"
        f"<style>{STYLE}</style>\n</head>\n<body>\n"
        "<header><h1>Paddock</h1>"
        "<p>An open leaderboard for coding agents: model × harness on real repository tasks.</p>"
        f"<nav>{nav}</nav></header>\n<main>\n{body}\n</main>\n"
        "<footer>Built only from the committed <code>results/</code> of "
        f'<a href="{REPO_URL}">laceyenterprises/agentpaddock</a>. '
        f'Submit a run or a correction: <a href="{REPO_URL}/blob/main/CONTRIBUTING.md">'
        "CONTRIBUTING</a>.</footer>\n</body>\n</html>\n"
    )


def fairness_banner(results: lb.Results) -> str:
    harnesses = sorted({record["harness"]["name"] for record in results.records})
    status = {audit["harness"]: audit["status"] for audit in results.fairness.get("audits", [])}
    pending = [name for name in harnesses if status.get(name) != "passed"]
    if not pending:
        return ""
    return (
        '<p class="banner"><strong>Provisional.</strong> The harness fairness audit '
        f"(tools and permissions against vendor defaults) is not yet signed off for: "
        f'{esc(", ".join(pending))}. See <a href="methodology.html#fairness">fairness</a>.</p>'
    )


def board_title(board: Mapping[str, Any]) -> str:
    title = board["suite"]
    return f"{title} · task version {board['task_version']}" if board["split"] else title


def board_html(board: Mapping[str, Any]) -> str:
    rows = []
    for rank, row in enumerate(board["rows"], start=1):
        key = row["key"]
        low, high = row["ci"]
        rate = f"{row['passed']}/{row['n']}" if row["n"] else "—"
        mean = "" if row["mean_score"] is None else f"{row['mean_score']:.2f}"
        rows.append(
            "<tr>"
            f'<td class="num">{rank}</td>'
            f'<td class="nowrap">{esc(harness_label(key))}</td>'
            f"<td>{config_cell(key[2])}</td>"
            f"<td>{esc(model_label(key))}</td>"
            f'<td class="num">{rate}</td>'
            f'<td class="nowrap">{ci_svg(low, high, row["rate"])} {pct(low)}–{pct(high)}</td>'
            f'<td class="num">{mean}</td>'
            f'<td class="num">{row["tasks"]}</td>'
            f'<td class="num">{tokens_cell(row)}</td>'
            f"<td>{cost_cell(row)}</td>"
            f'<td class="note">{excluded_text(row["excluded"])}</td>'
            "</tr>"
        )
    anchor = f"{board['suite']}-v{board['task_version']}"
    return (
        f'<section id="{esc(anchor)}"><h2>{esc(board_title(board))}</h2>'
        f'<p class="note">{len(board["tasks"])} tasks: {esc(", ".join(board["tasks"]))}.</p>'
        '<div class="scroll"><table><thead><tr><th class="num">#</th><th>harness</th>'
        '<th>config</th><th>model</th><th class="num">passed / n</th><th>95% Wilson</th>'
        '<th class="num">mean</th><th class="num">tasks</th><th class="num">tokens / pass</th>'
        "<th>cost / pass</th><th>excluded</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div></section>"
    )


def index_page(results: lb.Results) -> str:
    boards = lb.boards(results.records)
    graded = sum(lb.included(record) for record in results.records)
    toc = ", ".join(
        f'<a href="#{esc(board["suite"])}-v{board["task_version"]}">{esc(board_title(board))}</a>'
        for board in boards
    )
    body = (
        fairness_banner(results)
        + f"<p>{len(results.records)} result rows from {len(results.documents)} sources, "
        f"{graded} of them graded and scored. Each board ranks harness × model "
        "combinations on one suite by pass rate with a 95% Wilson interval; rows are not "
        "comparable across suites or task versions. Read the "
        '<a href="methodology.html">methodology</a> before drawing conclusions: most cells '
        "are small.</p>"
        f"<p>Suites: {toc}.</p>" + "".join(board_html(board) for board in boards)
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
                cells.append('<td class="note">—</td>')
                continue
            lines = [
                f"{esc(suite)}{'' if version == 1 else f' v{version}'}: "
                f"{summary['passed']}/{summary['n']}"
                for (suite, version), summary in by_suite.items()
            ]
            cells.append(f"<td>{'<br>'.join(lines)}</td>")
        label = harness_label(harness)
        rows.append(f"<tr><th>{esc(label)}<br>{config_cell(harness[2])}</th>{''.join(cells)}</tr>")
    body = (
        "<h2>Harness × model</h2>"
        '<p class="note">Each cell lists passed / n per suite for one harness configuration '
        "and model. A dash means the combination has no rows. Coverage differs between "
        "cells, so compare within a suite on its board.</p>"
        f'<div class="scroll"><table><thead><tr><th>harness · config</th>{header}</tr></thead>'
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
            label = (
                f"{board_title(board)} · {harness_label(row['key'])} · {model_label(row['key'])}"
            )
            bars.append((label, row["tokens_per_pass"], row["passed"], row["n"]))
    intro = (
        "<h2>Tokens per pass</h2>"
        '<p class="note">All tokens of every scored attempt, failed attempts included, '
        "divided by passes. A row appears only when every one of its attempts has measured "
        f"tokens; {unmeasured} board rows do not and are left out rather than estimated.</p>"
    )
    if not bars:
        return intro + "<p>No board row in results/ has complete token measurements yet.</p>"
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
        intro + f'<div class="scroll"><svg class="chart" width="{label_w + bar_w + 80}" '
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
    for entry in sorted(results.changelog, key=lambda e: (e["date"], e["id"]), reverse=True):
        tag = entry["excludes"]
        excluded = f"{counts.get(tag, 0)} rows tagged <code>{esc(tag)}</code>" if tag else "—"
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
            + (f'<br><span class="note">{esc(entry["note"])}</span>' if entry["note"] else "")
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
    match = lb.fairness_repository_reference(source)
    if match:
        path, line = match
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
    sources = "".join(
        f"<tr><td><code>{esc(name)}</code></td><td>{esc(doc['source']['kind'])}</td>"
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
        for audit in sorted(results.fairness.get("audits", []), key=lambda a: a["harness"])
    )
    audit_evidence = "".join(
        fairness_evidence(audit)
        for audit in sorted(results.fairness.get("audits", []), key=lambda a: a["harness"])
    )
    prices = "".join(price_table_html(table) for _, table in sorted(results.price_tables.items()))
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
    """``{page name: html}`` for the whole site; raises ``ResultsError`` on invalid input."""
    results = lb.load_results(results_dir)
    return {
        "index.html": index_page(results),
        "matrix.html": matrix_page(results),
        "tasks.html": tasks_page(results),
        "changelog.html": changelog_page(results),
        "methodology.html": methodology_page(results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=lb.DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if --out differs from a build")
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
