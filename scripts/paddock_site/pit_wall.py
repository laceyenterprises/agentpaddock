"""The pit wall, ``/pit-wall/`` (PDW-08): every placed entry by challenge.

Rebuilds ``design/web/PitWall.dc.html``: a heatmap of races passed per
challenge for each placed standings entry, with the whole calendar and the
fuel bill beside it. The shade runs from fewest races passed to all. The page renders in standings order
with counts; its script adds the car filter, the percent-or-count switch and
sorting by any column. The operator allows this page to be dense.

Every cell is a standings entry's per-challenge ``k`` and ``n`` from
``data/standings.json``; the column headers count the calendar races per
challenge in ``data/races.json``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import glyphs, layout, views
from .drivers import slugs
from .routes import Route
from .views import esc

#: Pass-rate bins of the ``--viz-seq`` ramp (design/tokens.css): under 50%,
#: 50-69, 70-79, 80-89, 90-94, 95-99 and 100%, as lower bounds in percent.
BINS = (50, 70, 80, 90, 95, 100)


def bin_of(k: int, n: int) -> int:
    """The ramp step, 1 to 7, for ``k`` of ``n``; exact on integers."""
    if not n:
        return 0
    return 1 + sum(k * 100 >= edge * n for edge in BINS)


def model(site: views.SiteData) -> dict[str, Any]:
    """Columns and rows of the heatmap, numbers straight from the data files."""
    columns = [
        {
            "challenge": challenge,
            "label": views.challenge_label(challenge),
            "races": races,
        }
        for challenge, races in site.challenges()
    ]
    names = site.driver_names
    page_slugs = slugs(list(dict.fromkeys(entry["driver"] for entry in site.placed)))
    rows = []
    for entry in site.placed:
        cells = []
        for column in columns:
            got = entry["challenges"].get(column["challenge"])
            cells.append({"k": got["k"], "n": got["n"]} if got else None)
        rows.append(
            {
                "id": entry["id"],
                "driver": entry["driver"],
                "driverName": names[entry["driver"]],
                "driverPage": f"drivers/{page_slugs[entry['driver']]}/index.html",
                "car": entry["car"],
                "carName": views.car_name(entry["car"]),
                "map": entry["map"],
                "k": entry["k"],
                "n": entry["n"],
                "place": entry["place"],
                "tie": entry["tie"],
                "costPerPass": entry["costPerPass"],
                "unpricedRuns": entry.get("unpricedRuns", 0),
                "unmeteredRuns": entry.get("unmeteredRuns", 0),
                "cells": cells,
            }
        )
    return {
        "live": site.live,
        "calendar": len(site.calendar),
        "columns": columns,
        "rows": rows,
        "partial": len(site.partial),
        "cars": sorted({row["car"] for row in rows}),
    }


def _cell(k: int | None, n: int | None, *, total: bool = False) -> str:
    if k is None or n is None:
        return '<td class="pw-cell"><span class="pw-none">–</span></td>'
    tone = bin_of(k, n)
    return (
        f'<td class="pw-cell" data-k="{k}" data-n="{n}">'
        f'<span class="pw-heat b{tone}{" pw-total" if total else ""}">{k}/{n}</span></td>'
    )


def _row(row: Mapping[str, Any], index: int, root: str) -> str:
    cells = "".join(
        _cell(cell["k"], cell["n"]) if cell else _cell(None, None)
        for cell in row["cells"]
    )
    cost = row["costPerPass"]
    return (
        f'<tr data-car="{esc(row["car"])}" data-i="{index}">'
        '<th scope="row" class="pw-entry"><div class="pw-entry-in">'
        f'<span class="pw-livery" style="--c:{views.livery(row["car"])}" aria-hidden="true"></span>'
        f'<span class="pw-who"><a href="{root}{esc(row["driverPage"])}">{views.driver_tag(row["driver"], row["driverName"])}</a>'
        # Each dot leads the item after it, so a line never ends on a dot and the
        # rank never sits alone.
        f"<small>{views.car_tag(row['car'])} {views.map_tag(row['map'], row['car'], row['driver'], views.DOT)}"
        f' <span class="pw-place">·&nbsp;{esc(views.place(row))}</span></small></span></div></th>'
        + cells
        + _cell(row["k"], row["n"], total=True)
        + f'<td class="pw-cell pw-fuel" data-usd="{"" if cost is None else cost}">'
        + (
            f"<span>{esc(views.usd(cost))}</span>"
            if cost is not None
            else _no_bill(row)
        )
        + "</td></tr>"
    )


def _no_bill(row: Mapping[str, Any]) -> str:
    """No fuel bill, and why: how many counted runs carry no price."""
    gap = (row["unpricedRuns"], row["unmeteredRuns"], row["k"])
    why = views.fuel_gap(*gap)
    return (
        f'<span class="pw-noprice" title="{esc(views.fuel_gap_sentence(*gap, row["n"]))}">'
        f"no price{f'<small>{esc(why)}</small>' if why else ''}</span>"
    )


def _header(columns: Sequence[Mapping[str, Any]], calendar: int) -> str:
    def sortable(index: int, inner: str, label: str, sort: str = "desc") -> str:
        return (
            f'<th scope="col" aria-sort="none"><button type="button" class="pw-sort" '
            f'data-sort="{index}" data-dir="{sort}" aria-label="Sort by {esc(label)}">'
            f"{inner}</button></th>"
        )

    heads = [
        sortable(
            index,
            (
                glyphs.roundel(column["challenge"], labelled=False, size="sm")
                if column["challenge"] in glyphs.RACE_TYPES
                else ""
            )
            + f"<span>{esc(column['label'])}</span>"
            f"<small>{views.plural(column['races'], 'race')}</small>",
            column["label"],
        )
        for index, column in enumerate(columns)
    ]
    heads.append(
        sortable(
            len(columns),
            f"<span>All races</span><small>{views.plural(calendar, 'race')}</small>",
            "all races",
        )
    )
    heads.append(
        sortable(
            len(columns) + 1,
            "<span>Fuel bill</span><small>$ per pass</small>",
            "fuel bill, cheapest first",
            "asc",
        )
    )
    return (
        '<thead><tr><th scope="col" class="pw-entry">Entry</th>'
        + "".join(heads)
        + "</tr></thead>"
    )


def _tools(cars: Sequence[str]) -> str:
    chips = (
        '<button type="button" class="chip" data-car="" aria-pressed="true">All cars</button>'
        + "".join(
            f'<button type="button" class="chip" data-car="{esc(car)}" aria-pressed="false">'
            f'<span class="stripe" style="--c:{views.livery(car)}" aria-hidden="true"></span>'
            f"{esc(views.car_name(car))}</button>"
            for car in cars
        )
    )
    return (
        '<div class="pw-tools" data-pitwall-tools hidden>'
        '<div class="hscroll pw-cars" data-hscroll>'
        f'<div class="hscroll-track" data-hscroll-track role="group" aria-label="Filter by car">{chips}</div></div>'
        '<button type="button" class="chip pw-mode" role="switch" aria-checked="false" '
        "data-pitwall-mode>Show %</button></div>"
    )


def _legend() -> str:
    labels = ("under 50%", "50%", "70%", "80%", "90%", "95%", "all")
    swatches = "".join(
        f'<span class="pw-swatch b{tone}" title="{esc(label)}"></span>'
        for tone, label in enumerate(labels, 1)
    )
    return (
        '<div class="pw-legend"><span>Share of races passed</span>'
        f'<span class="pw-ramp" aria-hidden="true">{swatches}</span>'
        "<span>under 50% → all</span></div>"
    )


def body(site: views.SiteData, current: str) -> str:
    view = model(site)
    root = layout.up(current)
    hero = views.hero(
        "pit-wall",
        "Where each entry wins and slips.",
        "Races passed per challenge, shaded from fewest to all. Tap a column to sort by it.",
    )
    if not view["live"]:
        return hero + views.caveat(
            "flag-yellow",
            "Waiting on checks.",
            "No results document has been verified yet, so the pit wall stays empty "
            "rather than show unchecked numbers.",
        )
    if not view["rows"]:
        return hero + views.caveat(
            "flag-yellow",
            "No full season yet.",
            "No entry has raced every race on the calendar, so there is nothing "
            "to compare side by side yet.",
        )
    rows = "".join(_row(row, index, root) for index, row in enumerate(view["rows"]))
    partial = (
        f"; {views.plural(view['partial'], 'more entry', 'more entries')} raced part "
        f'of the calendar (<a href="{root}standings/index.html">standings</a>)'
        if view["partial"]
        else ""
    )
    return (
        hero
        + '<div class="pw" data-pitwall>'
        + _tools(view["cars"])
        + '<div class="hscroll pw-scroll" data-hscroll>'
        '<div class="hscroll-track pw-track" data-hscroll-track tabindex="0" role="region" '
        'aria-label="Entries by challenge, scrolls sideways">'
        '<table class="pw-table">'
        '<caption class="sr-only">Races passed by each placed entry, per challenge</caption>'
        + _header(view["columns"], view["calendar"])
        + f"<tbody>{rows}</tbody></table></div></div>"
        '<p class="scroll-cue pw-cue">Scroll sideways for every challenge</p>'
        + _legend()
        + f'<p class="footnote">Each entry\'s first counted result on each race{partial}. '
        "Fuel bill is dollars per race passed, failed runs included.</p></div>"
    )


def page(site: views.SiteData, route: Route, routes: Sequence[Route]) -> dict[str, str]:
    return {
        route.file: layout.page(
            routes=routes,
            current=route.file,
            title=route.title,
            body=body(site, route.file),
            url=route.url,
        )
    }


CSS = """
/* ---------- pit wall (PDW-08) ---------- */
.pw { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
.pw > * { min-width: 0; }
.pw-tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pw-tools[hidden] { display: none; }
.pw-cars { flex: 1 1 260px; min-width: 0; }
.pw-cars .hscroll-track { flex-wrap: nowrap; }
.pw-cars .chip { flex: none; }
.pw-mode { margin-left: auto; border-color: var(--ink); }
.pw-mode[aria-checked="true"] { background: var(--paper); color: var(--ink); }
.pw-scroll { border: 1px solid var(--rule); border-radius: 16px; overflow: hidden; }
.pw-scroll::after { bottom: 0; }
.pw-track { padding-bottom: 0; }
.pw-track:focus-visible { outline-offset: -2px; }
.pw-table { border-collapse: separate; border-spacing: 0; width: 100%; min-width: 860px; font-size: 14px; font-variant-numeric: tabular-nums; }
.pw-table thead th { background: var(--paper); border-bottom: 1px solid var(--rule); padding: 0; }
.pw-table th.pw-entry { position: sticky; left: 0; z-index: 1; background: var(--paper); text-align: left; padding: 8px 14px; min-width: 200px; max-width: 240px; border-bottom: 1px solid var(--rule); }
.pw-table thead th.pw-entry { z-index: 2; font: 600 12px/1.2 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-2); }
.pw-sort {
  width: 100%; min-height: 56px; padding: 8px 6px; border: 0; background: transparent; color: var(--ink);
  font: 600 12px/1.2 var(--body); cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 2px;
}
.pw-sort small { font: 500 11px/1.2 var(--mono); color: var(--muted); }
.pw-sort .roundel { margin-bottom: 2px; }
th[aria-sort="ascending"] .pw-sort, th[aria-sort="descending"] .pw-sort { background: var(--blue-soft); }
.pw-entry { font-weight: 500; }
.pw-table tbody th.pw-entry { border-bottom: 1px solid var(--soft); }
.pw-entry-in { display: flex; align-items: center; gap: 10px; }
.pw-livery { width: 5px; height: 30px; border-radius: 2px; background: var(--c); flex: none; }
.pw-who { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
.pw-who a { color: var(--ink); font-weight: 600; text-decoration: none; min-height: 24px; overflow-wrap: anywhere; }
.pw-who a:hover { text-decoration: underline; }
.pw-who small { font-size: 12px; color: var(--muted); }
.pw-cell { padding: 3px; }
.pw-heat, .pw-fuel span, .pw-none {
  min-height: 38px; display: flex; align-items: center; justify-content: center; border-radius: 8px; font-weight: 600; white-space: nowrap; padding: 0 6px;
}
.pw-fuel span, .pw-none { background: var(--soft); color: var(--ink); }
.pw-fuel .pw-noprice { flex-direction: column; gap: 1px; line-height: 1.15; color: var(--ink-2); }
.pw-noprice small { font-size: 11px; font-weight: 500; color: var(--muted); }
.pw-place { white-space: nowrap; }
.pw-total { box-shadow: inset 0 0 0 2px var(--paper); outline: 1.5px solid var(--ink); outline-offset: -1.5px; }
.b1 { background: var(--viz-seq-1); color: var(--viz-seq-ink-light); }
.b2 { background: var(--viz-seq-2); color: var(--viz-seq-ink-light); }
.b3 { background: var(--viz-seq-3); color: var(--viz-seq-ink-light); }
.b4 { background: var(--viz-seq-4); color: var(--viz-seq-ink-light); }
.b5 { background: var(--viz-seq-5); color: var(--viz-seq-ink-dark); }
.b6 { background: var(--viz-seq-6); color: var(--viz-seq-ink-dark); }
.b7 { background: var(--viz-seq-7); color: var(--viz-seq-ink-dark); }
.pw-table tr[hidden] { display: none; }
.pw-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-2); }
.pw-ramp { display: inline-flex; gap: 2px; }
.pw-swatch { width: 22px; height: 12px; }
@media (min-width: 900px) { .pw-cue { display: none; } }
@media (max-width: 599px) {
  .pw-table th.pw-entry { min-width: 148px; max-width: 160px; padding: 8px 10px; }
  .pw-who small { font-size: 11px; }
}
"""

#: Car filter, percent-or-count switch and column sort. Without JavaScript the
#: table stays in standings order with counts and the tools stay hidden.
SCRIPT = """(function () {
  var wall = document.querySelector('[data-pitwall]');
  if (!wall) return;
  var tools = wall.querySelector('[data-pitwall-tools]');
  var body = wall.querySelector('tbody');
  var rows = Array.prototype.slice.call(body.rows);
  var heads = Array.prototype.slice.call(wall.querySelectorAll('thead th[aria-sort]'));
  var mode = wall.querySelector('[data-pitwall-mode]');
  var sortedColumn = null, sortDirection = null;
  tools.hidden = false;
  function cell(row, column) { return row.querySelectorAll('td')[column]; }
  function value(row, column, dir) {
    var td = cell(row, column);
    if (td.hasAttribute('data-usd')) {
      var usd = td.getAttribute('data-usd');
      return usd === '' ? (dir === 'asc' ? Infinity : -Infinity) : Number(usd);
    }
    if (!td.hasAttribute('data-k')) return dir === 'asc' ? Infinity : -Infinity;
    return Number(td.getAttribute('data-k')) / Number(td.getAttribute('data-n'));
  }
  function sort(column, dir) {
    sortedColumn = column;
    sortDirection = dir;
    rows.sort(function (a, b) {
      var x = value(a, column, dir), y = value(b, column, dir);
      if (x !== y) return dir === 'asc' ? x - y : y - x;
      return Number(a.getAttribute('data-i')) - Number(b.getAttribute('data-i'));
    });
    rows.forEach(function (row) { body.appendChild(row); });
    heads.forEach(function (th, i) {
      th.setAttribute('aria-sort', i === column ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');
    });
  }
  heads.forEach(function (th, i) {
    var button = th.querySelector('button');
    button.addEventListener('click', function () {
      var dir = i === sortedColumn ? (sortDirection === 'asc' ? 'desc' : 'asc') : button.getAttribute('data-dir');
      sort(i, dir);
    });
  });
  Array.prototype.forEach.call(tools.querySelectorAll('[data-car]'), function (chip) {
    chip.addEventListener('click', function () {
      var car = chip.getAttribute('data-car');
      Array.prototype.forEach.call(tools.querySelectorAll('[data-car]'), function (other) {
        other.setAttribute('aria-pressed', other === chip ? 'true' : 'false');
      });
      rows.forEach(function (row) { row.hidden = !!car && row.getAttribute('data-car') !== car; });
    });
  });
  mode.addEventListener('click', function () {
    var percent = mode.getAttribute('aria-checked') !== 'true';
    mode.setAttribute('aria-checked', percent ? 'true' : 'false');
    mode.textContent = percent ? 'Show counts' : 'Show %';
    Array.prototype.forEach.call(wall.querySelectorAll('td[data-k] .pw-heat'), function (span) {
      var td = span.parentNode, k = Number(td.getAttribute('data-k')), n = Number(td.getAttribute('data-n'));
      var share = k === n ? 100 : Math.min(99, Math.round(k / n * 100));
      span.textContent = percent ? share + '%' : k + '/' + n;
    });
  });
})();
"""
