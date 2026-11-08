"""The drivers, ``/drivers/`` and ``/drivers/<driver-id>/`` (PDW-08).

Rebuilds ``design/web/Driver.dc.html`` as one static page per featured driver:
a driver (model) with at least one placed standings entry, so it has raced the
complete calendar in some car. Each page shows the driver's best entry, every
placed entry car by car, where the picked entry wins and slips by challenge,
and its cheapest entry against its best on fuel. ``/drivers/`` lands on the
list of featured drivers.

Every number is a placed entry from ``data/standings.json``.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Mapping, Sequence

from . import glyphs, layout, views
from .routes import Route
from .views import esc

INDEX = "drivers/index.html"


def page_file(driver_slug: str) -> str:
    return f"drivers/{driver_slug}/index.html"


def slugs(provider_ids: Sequence[str]) -> dict[str, str]:
    """A page slug per driver; two that collide both gain a digest."""
    out = {provider_id: views.driver_slug(provider_id) for provider_id in provider_ids}
    counts: dict[str, int] = defaultdict(int)
    for value in out.values():
        counts[value] += 1
    return {
        provider_id: value
        if counts[value] == 1
        else f"{value}-{hashlib.sha256(provider_id.encode()).hexdigest()[:8]}"
        for provider_id, value in out.items()
    }


def model(site: views.SiteData) -> list[dict[str, Any]]:
    """Featured drivers, best first, each with its placed entries in standings order."""
    names = site.driver_names
    placed: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for entry in site.placed:  # standings order: a driver's first entry is its best
        placed[entry["driver"]].append(entry)
    partial: dict[str, int] = defaultdict(int)
    for entry in site.partial:
        partial[entry["driver"]] += 1
    page_slugs = slugs(list(placed))
    columns = site.challenges()
    out = []
    for driver, entries in placed.items():
        best = entries[0]
        priced = [entry for entry in entries if entry["costPerPass"] is not None]
        cheapest = min(priced, key=lambda e: e["costPerPass"]) if priced else None
        out.append(
            {
                "driver": driver,
                "slug": page_slugs[driver],
                "file": page_file(page_slugs[driver]),
                "name": names[driver],
                "via": views.host_name(driver) if views.routed(driver) else None,
                "entries": [
                    {
                        **entry,
                        "byChallenge": [
                            {
                                "challenge": challenge,
                                "label": views.challenge_label(challenge),
                                **entry["challenges"].get(challenge, {"k": 0, "n": 0}),
                            }
                            for challenge, _ in columns
                            if challenge in entry["challenges"]
                        ],
                    }
                    for entry in entries
                ],
                "best": best,
                "cheapest": cheapest,
                "cars": list(dict.fromkeys(entry["car"] for entry in entries)),
                "partial": partial[driver],
            }
        )
    return out


def _entry_words(entry: Mapping[str, Any]) -> str:
    return (
        f"{views.car_name(entry['car'])} at engine map {views.map_name(entry['map'])}"
    )


def _fuel(value: float | None) -> str:
    return "no price" if value is None else f"{views.usd(value)} a pass"


def career(driver: Mapping[str, Any]) -> str:
    parts = [
        views.plural(len(driver["entries"]), "placed entry", "placed entries"),
        views.plural(len(driver["cars"]), "car"),
    ]
    if driver["via"]:
        parts.append(f"via {driver['via']}")
    if driver["partial"]:
        parts.append(f"{driver['partial']} more raced part of the calendar")
    return " · ".join(parts)


def headline(entries: Sequence[Mapping[str, Any]]) -> str:
    if len(entries) == 1:
        return "One entry so far."
    spread = entries[0]["k"] - entries[-1]["k"]
    if spread == 0:
        return "Every setup gets the same finish."
    return f"Best to worst setup: {views.plural(spread, 'race')} apart."


def bar_floor(entries: Sequence[Mapping[str, Any]]) -> int:
    """Where the bars start, in percent: 80, or the lowest rate's ten below."""
    lowest = min(entry["k"] * 100 // entry["n"] for entry in entries)
    return max(0, min(80, (lowest - 1) // 10 * 10))


def fuel_note(driver: Mapping[str, Any]) -> str:
    name, best, cheapest = driver["name"], driver["best"], driver["cheapest"]
    if cheapest is None:
        return (
            f"No entry of {esc(name)} has a price for every run, so there is no "
            "fuel bill to compare."
        )
    text = (
        f"Cheapest way to run {esc(name)}: {esc(_entry_words(cheapest))}, "
        f"{_fuel(cheapest['costPerPass'])}."
    )
    if cheapest is best:
        return text + " That is also its best entry."
    if best["costPerPass"] is None:
        return text + " Its best entry has no pricing data to compare."
    return text + f" Its best entry costs {_fuel(best['costPerPass'])}."


def slips_note(entry: Mapping[str, Any]) -> str:
    slips = [row["label"] for row in entry["byChallenge"] if row["k"] < row["n"]]
    if not slips:
        return "A clean sweep: it finishes every race in every challenge."
    return f"It drops races in {esc(views.join_words(slips))}. Everything else it finishes."


def _chips(drivers: Sequence[Mapping[str, Any]], current: str) -> str:
    links = "".join(
        f'<a class="chip" href="{layout.href(driver["file"], current)}"'
        + (' aria-current="page"' if driver["file"] == current else "")
        + f">{esc(driver['name'])}</a>"
        for driver in drivers
    )
    return (
        '<nav class="dv-pick" aria-label="Drivers">'
        '<span class="label">The driver · pick one</span>'
        '<div class="hscroll" data-hscroll>'
        f'<div class="hscroll-track" data-hscroll-track>{links}</div></div></nav>'
    )


def _best(driver: Mapping[str, Any]) -> str:
    best = driver["best"]
    return (
        '<div class="panel dark dv-best">'
        f'<span class="label">{glyphs.icon("podium")}Its best entry</span>'
        f'<div class="dv-best-num"><span class="big-num">{views.pct(best["rate"])}</span>'
        f"<span>{best['k']} of {best['n']} races passed · {esc(views.place(best))} on the board</span></div>"
        f"<span>in <b>{esc(views.car_name(best['car']))}</b> at engine map "
        f"{esc(views.map_name(best['map']))} · {_fuel(best['costPerPass'])}</span></div>"
    )


def _rows(driver: Mapping[str, Any]) -> tuple[str, int]:
    entries = driver["entries"]
    floor = bar_floor(entries)
    out = []
    for index, entry in enumerate(entries):
        width = max(0.0, (entry["k"] / entry["n"] * 100 - floor) / (100 - floor) * 100)
        out.append(
            f'<button type="button" class="dv-row" data-entry="{index}" '
            f'aria-pressed="{"true" if index == 0 else "false"}" aria-controls="slips-{index}">'
            f'<span class="dv-livery" style="--c:{views.livery(entry["car"])}" aria-hidden="true"></span>'
            '<span class="dv-row-main">'
            f'<span class="dv-row-who"><b>{esc(views.car_name(entry["car"]))}</b>'
            + glyphs.icon(views.map_glyph(entry["map"]))
            + f"<span>{esc(views.map_name(entry['map']))} · {_fuel(entry['costPerPass'])}</span></span>"
            f'<span class="dv-bar"><span style="width:{width:.1f}%"></span></span></span>'
            f'<span class="dv-pct">{views.pct(entry["rate"])}</span></button>'
        )
    return "".join(out), floor


def _slips(driver: Mapping[str, Any]) -> str:
    panels = []
    for index, entry in enumerate(driver["entries"]):
        rows = "".join(
            f'<div class="dv-cat{" slip" if row["k"] < row["n"] else ""}">'
            f"<span>{esc(row['label'])}</span>"
            f'<span class="dv-cat-bar"><span style="width:{row["k"] / row["n"] * 100 if row["n"] else 0:.1f}%"></span></span>'
            f'<span class="dv-cat-num">{row["k"]}/{row["n"]}</span></div>'
            for row in entry["byChallenge"]
        )
        panels.append(
            f'<div class="dv-slips" id="slips-{index}" data-slips="{index}">'
            f'<span class="dv-slips-title">{esc(driver["name"])} in {esc(_entry_words(entry))}: '
            f"{entry['k']} of {entry['n']}</span>{rows}"
            f'<p class="dv-note">{slips_note(entry)}</p></div>'
        )
    return (
        '<section class="panel dv-wins" aria-live="polite">'
        '<span class="label">Where it wins and slips</span>'
        + "".join(panels)
        + "</section>"
    )


def driver_body(
    site: views.SiteData,
    driver: Mapping[str, Any],
    drivers: Sequence[Mapping[str, Any]],
) -> str:
    current = driver["file"]
    root = layout.up(current)
    calendar = len(site.calendar)
    rows, floor = _rows(driver)
    floor_note = (
        f" Bars start at {floor}% because the field is close at the top."
        if floor
        else ""
    )
    return (
        '<div class="dv" data-driver>'
        + _chips(drivers, current)
        + '<section class="dv-head">'
        '<div class="dv-name">'
        + glyphs.icon("driver", "dv-glyph")
        + f'<div><span class="eyebrow">Agent Paddock</span><h1>{esc(driver["name"])}</h1>'
        f'<p class="dv-career">{esc(career(driver))}</p></div></div>'
        + _best(driver)
        + "</section>"
        '<section class="dv-cars">'
        f'<span class="label">{glyphs.icon("car")}Car by car</span>'
        f"<h2>{esc(headline(driver['entries']))}</h2>"
        f'<p class="dv-note">Same driver, same {views.plural(calendar, "race")}. '
        f"Only the car and its engine map change.{floor_note}</p>"
        f'<div class="dv-rows" role="group" aria-label="Entries">{rows}</div>'
        '<p class="dv-hint">Tap an entry to see where it wins and where it slips.</p></section>'
        + _slips(driver)
        + '<section class="dv-fuel">'
        + glyphs.icon("fuel-bill")
        + f"<p>{fuel_note(driver)}</p></section>"
        '<section class="next-cards">'
        f'<a href="{root}garage/index.html"><span>Next</span><b>Put it in a car yourself</b></a>'
        f'<a href="{root}pit-wall/index.html"><span>Or</span><b>Compare every driver</b></a></section>'
        f'<p class="footnote">Each entry\'s first counted result on each of the '
        f"{views.plural(calendar, 'calendar race')}, as the standings count it. "
        "Fuel bill is dollars per race passed, failed runs included.</p></div>"
    )


def index_body(site: views.SiteData, drivers: Sequence[Mapping[str, Any]]) -> str:
    hero = views.hero(
        "driver",
        "The drivers.",
        "A driver is the model. Pick one to see how it does in every car it has "
        "raced the whole calendar in.",
    )
    if not site.live:
        return hero + views.caveat(
            "flag-yellow",
            "Waiting on checks.",
            "No results document has been verified yet, so no driver has a page yet.",
        )
    if not drivers:
        return hero + views.caveat(
            "flag-yellow",
            "No full season yet.",
            "A driver gets a page once one of its entries has raced every race on the calendar.",
        )
    cards = "".join(
        f'<a class="dv-card" href="{layout.href(driver["file"], INDEX)}">'
        f"<b>{esc(driver['name'])}</b>"
        f'<span class="dv-card-num">{views.pct(driver["best"]["rate"])}</span>'
        f"<span>best: {esc(_entry_words(driver['best']))}</span>"
        f'<span class="dv-card-meta">{esc(career(driver))}</span></a>'
        for driver in drivers
    )
    names = {driver["driver"] for driver in drivers}
    others = sorted({entry["driver"] for entry in site.partial} - names)
    more = (
        f'<p class="footnote">{views.plural(len(others), "more driver")} raced part of '
        f'the calendar; try them in the <a href="{layout.href("garage/index.html", INDEX)}">garage</a>.</p>'
        if others
        else ""
    )
    return hero + f'<div class="dv-cards">{cards}</div>' + more


def pages(
    site: views.SiteData, route: Route, routes: Sequence[Route]
) -> dict[str, str]:
    """``/drivers/`` and one page per featured driver."""
    drivers = model(site)
    out = {
        route.file: layout.page(
            routes=routes,
            current=route.file,
            title=route.title,
            body=index_body(site, drivers),
            url=route.url,
        )
    }
    for driver in drivers:
        out[driver["file"]] = layout.page(
            routes=routes,
            current=driver["file"],
            title=f"{driver['name']} · Drivers",
            body=driver_body(site, driver, drivers),
            url=route.url + driver["slug"] + "/",
            nav_current=route.file,
        )
    return out


CSS = """
/* ---------- drivers (PDW-08) ---------- */
.dv { display: grid; grid-template-columns: minmax(0, 1fr); gap: 48px; padding-top: 28px; }
.dv > * { min-width: 0; }
.dv-pick { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
.dv-pick .hscroll-track .chip { flex: none; }
.dv-head { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 24px; align-items: center; }
.dv-name { display: flex; align-items: center; gap: 18px; min-width: 0; }
.dv-glyph { width: 88px; height: 88px; flex: none; color: var(--ink); }
.dv-name h1 { margin: 6px 0 0; font: italic 800 clamp(40px, 10vw, 72px)/.92 var(--display); overflow-wrap: anywhere; }
.dv-career { margin-top: 8px; color: var(--ink-2); }
.dv-best { gap: 8px; }
.dv-best-num { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.dv-best .big-num { color: var(--blue); }
.dv-best-num span:last-child { color: var(--tower-mute); }
.dv-cars, .dv-wins, .dv-fuel { max-width: 760px; }
.dv-cars { display: grid; gap: 14px; }
.dv-cars h2 { margin: 0; font: italic 800 clamp(32px, 7vw, 48px)/.95 var(--display); }
.dv-note, .dv-hint { color: var(--ink-2); }
.dv-hint { font-size: 14px; color: var(--muted); }
.dv-rows { display: flex; flex-direction: column; border-top: 1px solid var(--rule); }
.dv-row {
  display: grid; grid-template-columns: 6px minmax(0, 1fr) 72px; gap: 12px; align-items: center; min-height: 64px;
  padding: 12px 4px; border: 0; border-bottom: 1px solid var(--rule); background: var(--paper); text-align: left;
  cursor: pointer; font: 400 16px/1.3 var(--body); color: var(--ink);
}
.dv-row[aria-pressed="true"] { background: var(--blue-soft); }
.dv-livery { width: 6px; height: 40px; border-radius: 2px; background: var(--c); }
.dv-row-main { display: grid; gap: 6px; min-width: 0; }
.dv-row-who { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dv-row-who .glyph { width: 20px; height: 20px; }
.dv-row-who span { color: var(--ink-2); font-size: 14px; }
.dv-bar, .dv-cat-bar { display: block; height: 8px; border-radius: 3px; background: var(--soft); }
.dv-bar span, .dv-cat-bar span { display: block; height: 100%; border-radius: 3px; background: var(--viz-other); transition: width .4s var(--ease); }
.dv-row[aria-pressed="true"] .dv-bar span { background: var(--blue); }
.dv-pct { text-align: right; font: 700 24px/1 var(--display); font-variant-numeric: tabular-nums; }
.dv-slips { display: grid; gap: 10px; }
.dv-slips[hidden] { display: none; }
.dv-slips-title { font-size: 18px; font-weight: 600; }
.dv-cat { display: grid; grid-template-columns: 104px minmax(0, 1fr) 52px; gap: 10px; align-items: center; font-size: 15px; }
.dv-cat-bar { height: 12px; border-radius: 4px; background: var(--paper); }
.dv-cat-bar span { background: var(--blue); border-radius: 4px; }
.dv-cat-num { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-2); }
.dv-cat.slip .dv-cat-bar span { background: var(--red); }
.dv-cat.slip .dv-cat-num { color: var(--red-ink); font-weight: 700; }
.dv-fuel { display: flex; align-items: center; gap: 14px; padding: 18px 20px; border-radius: 18px; border: 1.5px solid var(--rule); }
.dv-cards { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
@media (min-width: 720px) {
  .dv-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dv-card:last-child:nth-child(odd) { grid-column: 1 / -1; }
}
@media (min-width: 1100px) {
  .dv-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .dv-card:last-child:nth-child(odd) { grid-column: auto; }
  .dv-card:last-child:nth-child(3n + 1) { grid-column: 1 / -1; }
}
.dv-card { display: grid; gap: 4px; padding: 20px; border-radius: 20px; background: var(--soft); color: var(--ink); text-decoration: none; min-height: 44px; }
.dv-card:hover { color: var(--ink); background: var(--blue-soft); }
.dv-card b { font-size: 18px; }
.dv-card-num { font: 700 44px/1 var(--display); color: var(--blue-ink); font-variant-numeric: tabular-nums; }
.dv-card-meta { font-size: 13px; color: var(--muted); }
"""

#: Picking an entry row shows its wins and slips.
SCRIPT = """(function () {
  var page = document.querySelector('[data-driver]');
  if (!page) return;
  var rows = page.querySelectorAll('[data-entry]');
  var panels = page.querySelectorAll('[data-slips]');
  Array.prototype.forEach.call(panels, function (panel, i) { panel.hidden = i !== 0; });
  Array.prototype.forEach.call(rows, function (row) {
    row.addEventListener('click', function () {
      var pick = row.getAttribute('data-entry');
      Array.prototype.forEach.call(rows, function (other) {
        other.setAttribute('aria-pressed', other === row ? 'true' : 'false');
      });
      Array.prototype.forEach.call(panels, function (panel) {
        var on = panel.getAttribute('data-slips') === pick;
        panel.hidden = !on;
        if (on) { panel.classList.remove('data-in'); void panel.offsetWidth; panel.classList.add('data-in'); }
      });
    });
  });
})();
"""
