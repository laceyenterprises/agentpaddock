"""The garage, ``/garage/`` (PDW-08): build an entry and see how it did.

Rebuilds ``design/web/Garage.dc.html``. Pick a driver (model), a car (harness)
and one of six engine maps (the car's own default, low, medium, high, xhigh,
max). The default is named by the setting the driver ran at in that car, and
who chose it (``views.map_label``), or "not recorded".

- **Availability comes from the data.** A level is open for a driver when some
  results document has a counted run of that driver at that level, in any car
  and at any trust level (``data/standings.json`` and ``data/community.json``).
  Any other level is greyed out with the reason.
- **A raced entry shows its result**: its standings pass rate, races passed,
  fuel bill per pass and place, from the trusted standings. When one driver,
  car and engine map raced in more than one setup (configuration), the best is
  shown and the others counted.
- **Raced, but only in unverified documents**: the page says so and points at
  the community board, never at an unchecked number.
- **Open but unraced**: "Nobody has raced this yet", linking to
  ``/race-it-yourself/``.

The page renders the best entry at a supported map; its script swaps the
selection from a JSON block of the same numbers, so there is one template for
every state.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import glyphs, layout, views
from .drivers import slugs  # one slug rule for every driver page
from .routes import Route
from .views import LEVELS, esc

RACE_IT_YOURSELF = "race-it-yourself/index.html"
COMMUNITY = "community/index.html"

#: The car in its livery, as drawn in the approved prototype; ``--c`` paints it.
CAR_ART = """<svg class="gr-art" viewBox="0 0 900 300" role="img" aria-label="{label}">
<rect x="70" y="226" width="780" height="8" fill="var(--c)"/>
<path d="M76 226 L84 156 L160 150 L190 170 L330 166 C360 152 388 140 410 133 C418 108 442 94 470 95 C484 96 492 106 494 120 L520 122 C542 122 556 136 568 154 L604 176 C684 190 780 206 856 222 L856 226 Z" fill="var(--c)"/>
<rect x="36" y="64" width="14" height="120" rx="2" fill="var(--c)"/><rect x="36" y="64" width="128" height="16" rx="3" fill="var(--c)"/><rect x="44" y="88" width="112" height="10" rx="3" fill="var(--c)"/>
<path d="M118 98 L132 152" stroke="var(--c)" stroke-width="8" fill="none"/>
<circle cx="528" cy="112" r="25" fill="#111111"/><rect x="532" y="101" width="24" height="12" rx="6" fill="#FFFFFF"/>
<path d="M486 100 C504 80 560 82 580 150" stroke="#111111" stroke-width="9" stroke-linecap="round" fill="none"/>
<rect x="790" y="228" width="100" height="14" rx="3" fill="var(--c)"/><rect x="876" y="204" width="12" height="38" rx="2" fill="var(--c)"/>
<circle cx="196" cy="228" r="64" fill="#111111"/><circle cx="196" cy="228" r="40" fill="#3A3F47"/><circle cx="196" cy="228" r="12" fill="#F4F6F9"/>
<circle cx="722" cy="230" r="60" fill="#111111"/><circle cx="722" cy="230" r="37" fill="#3A3F47"/><circle cx="722" cy="230" r="11" fill="#F4F6F9"/>
</svg>"""


def key(driver: str, car: str, engine_map: str) -> str:
    return f"{driver}|{car}|{engine_map}"


def level_reason(
    name: str,
    engine_map: str,
    raced: Sequence[str],
    defaults: Sequence[str] = (),
) -> str:
    """Why ``engine_map`` is closed for a driver that has raced at ``raced``.
    ``defaults`` names the settings its default-map runs ran at, one per car."""
    default_words = list(dict.fromkeys(defaults)) or ["the car's default"]
    words = [
        word
        for level in raced
        for word in (
            [
                f"the default ({label})" if label != "the car's default" else label
                for label in default_words
            ]
            if level == "default"
            else [views.map_label(level)]
        )
    ]
    if not words:
        return f"No counted run has {name} at any of these engine maps."
    if len(words) == 1:
        return f"{name} has raced at one engine map only: {words[0]}."
    return (
        f"No counted run has {name} at {views.map_label(engine_map)}; "
        f"it has raced at {views.join_words(words)}."
    )


def map_note(name: str, open_levels: Sequence[str], others: Sequence[str]) -> str:
    if len(open_levels) == len(LEVELS):
        note = (
            f"Every level is open for {name}. Levels nobody has raced in this car "
            "show as a gap on the grid."
        )
    else:
        note = f"Greyed out: levels no counted run has put {name} at."
        if len(open_levels) == 1:
            note += " It runs at one setting only."
    if others:
        note += f" Also raced at {views.join_words(list(others))}: see the pit wall."
    return note


def model(site: views.SiteData) -> dict[str, Any]:
    """Drivers, cars, levels and every raced combination, from the data files."""
    trusted_levels = set(site.standings["basis"]["trust"])
    entries: dict[str, dict[str, Any]] = {}
    placed_count = len(site.placed)
    calendar = len(site.calendar)
    for entry in site.placed + site.partial:  # standings order: the first is the best
        combo = key(entry["driver"], entry["car"], entry["map"])
        if combo in entries:
            entries[combo]["setups"] += 1
            continue
        placed = entry["place"] is not None
        entries[combo] = {
            "id": entry["id"],
            "k": entry["k"],
            "n": entry["n"],
            "rate": entry["rate"],
            "pct": views.pct(entry["rate"]),
            "passOf": f"{entry['k']} of {entry['n']}",
            "usd": views.usd(entry["costPerPass"]),
            "place": views.place(entry),
            "placeNote": (
                f"of {views.plural(placed_count, 'placed entry', 'placed entries')}"
                if placed
                else f"raced {entry['n']} of {views.plural(calendar, 'calendar race')}"
            ),
            "placed": placed,
            "setups": 1,
        }
    unverified: dict[str, str] = {}
    for entry in site.community.get("entries", []):
        combo = key(entry["driver"], entry["car"], entry["map"])
        if entry["trust"] not in trusted_levels and combo not in entries:
            unverified.setdefault(combo, entry["trust"])
    raced = set(entries) | set(unverified)
    ids = [combo.split("|") for combo in sorted(raced)]
    names = site.driver_names
    featured = list(dict.fromkeys(entry["driver"] for entry in site.placed))
    others = sorted(
        {driver for driver, _, _ in ids} - set(featured), key=lambda d: names[d].lower()
    )
    page_slugs = slugs(featured)
    car_order = list(
        dict.fromkeys(
            [entry["car"] for entry in site.placed + site.partial]
            + sorted(car for _, car, _ in ids)
        )
    )
    drivers = []
    for driver in featured + others:
        maps = {engine_map for d, _, engine_map in ids if d == driver}
        open_levels = [level for level in LEVELS if level in maps]
        extra = sorted(maps - set(LEVELS), key=views.slug)
        name = names[driver]
        defaults = {car: views.map_label("default", car, driver) for car in car_order}
        raced_default = [
            defaults[c] for d, c, m in ids if d == driver and m == "default"
        ]
        best = next(
            (e for e in site.placed + site.partial if e["driver"] == driver), None
        )
        drivers.append(
            {
                "id": driver,
                "name": name,
                "featured": driver in page_slugs,
                "page": f"drivers/{page_slugs[driver]}/index.html"
                if driver in page_slugs
                else None,
                "levels": {
                    level: ""
                    if level in maps
                    else level_reason(name, level, open_levels, raced_default)
                    for level in LEVELS
                },
                # The default engine map's label in each car, for this driver.
                "defaults": defaults,
                "defaultTips": {car: views.map_tip(car, driver) for car in car_order},
                "note": map_note(name, open_levels, extra),
                "tip": (
                    f"{name} does best in {views.car_name(best['car'])} at engine map "
                    f"{views.map_label(best['map'], best['car'], driver)} ({best['k']} of {best['n']}). "
                    "The car changes the result more than people expect."
                    if best
                    else f"{name} has no checked result yet; its runs wait on the community board."
                ),
            }
        )
    return {
        "live": site.live,
        "calendar": calendar,
        "levels": [
            [level, views.map_label(level), views.map_glyph(level)] for level in LEVELS
        ],
        "cars": [[car, views.car_name(car), views.livery(car)] for car in car_order],
        "drivers": drivers,
        "entries": entries,
        "unverified": unverified,
        "start": start(site, drivers, car_order),
    }


def start(
    site: views.SiteData, drivers: Sequence[Mapping[str, Any]], cars: Sequence[str]
) -> list[str] | None:
    """The best entry at a supported map, then an open community level."""
    for entry in site.placed + site.partial:
        if entry["map"] in LEVELS:
            return [entry["driver"], entry["car"], entry["map"]]
    for driver in drivers:
        open_levels = [level for level, why in driver["levels"].items() if not why]
        if cars and open_levels:
            return [driver["id"], cars[0], open_levels[0]]
    return None


#: The fields of a raced entry the page script fills in.
EMBEDDED = ("pct", "passOf", "usd", "place", "placeNote", "setups")

#: Tips that rotate under the result; the first is the driver's own.
TIPS = (
    "A cheaper fuel bill only counts if the entry passes. That is why the bill is "
    "per race passed, not per run.",
    "The engine map is how hard the model thinks. The pit wall shows where thinking "
    "harder paid off and where it did not.",
)


def _chip(
    attr: str,
    value: str,
    label: str,
    on: bool,
    extra: str = "",
    inner: str = "",
    slot: bool = False,
) -> str:
    text = f"<span data-label>{esc(label)}</span>" if slot else esc(label)
    return (
        f'<button type="button" class="chip" {attr}="{esc(value)}" '
        f'aria-pressed="{"true" if on else "false"}"{extra}>{inner}{text}</button>'
    )


def _pickers(view: Mapping[str, Any], pick: Sequence[str]) -> str:
    driver, car, engine_map = pick
    chosen = next(d for d in view["drivers"] if d["id"] == driver)
    featured = [d for d in view["drivers"] if d["featured"]]
    others = [d for d in view["drivers"] if not d["featured"]]
    driver_chips = "".join(
        _chip("data-driver", d["id"], d["name"], d["id"] == driver) for d in featured
    )
    more = ""
    if others:
        more = (
            f'<details class="gr-more"{" open" if chosen in others else ""}>'
            f"<summary>{views.plural(len(others), 'more driver')}, part of the calendar or unchecked</summary>"
            '<div class="chips">'
            + "".join(
                _chip("data-driver", d["id"], d["name"], d["id"] == driver)
                for d in others
            )
            + "</div></details>"
        )
    car_chips = "".join(
        _chip(
            "data-car",
            car_id,
            name,
            car_id == car,
            inner=f'<span class="stripe" style="--c:{colour}" aria-hidden="true"></span>',
        )
        for car_id, name, colour in view["cars"]
    )
    default_name = chosen["defaults"][car]
    map_chips = ""
    for level, name, glyph in view["levels"]:
        why = chosen["levels"][level]
        extra = (
            f' aria-disabled="true" title="{esc(why)}" aria-describedby="gr-map-note"'
            if why
            else ""
        )
        map_chips += _chip(
            "data-map",
            level,
            default_name if level == "default" else name,
            level == engine_map,
            extra,
            inner=glyphs.icon(glyph),
            slot=level == "default",
        )
    return (
        '<section class="gr-pick">'
        '<div class="gr-step"><span class="label">1 · Driver, the model</span>'
        f'<div class="chips" role="group" aria-label="Driver">{driver_chips}</div>{more}</div>'
        '<div class="gr-step"><span class="label">2 · Car, the harness</span>'
        f'<div class="chips" role="group" aria-label="Car">{car_chips}</div></div>'
        '<div class="gr-step"><span class="label">3 · Engine map, the effort</span>'
        f'<div class="chips" role="group" aria-label="Engine map">{map_chips}</div>'
        f'<p class="gr-note" id="gr-map-note" aria-live="polite" data-slot="note">{esc(chosen["note"])}</p></div>'
        "</section>"
    )


def _result(view: Mapping[str, Any], pick: Sequence[str], root: str) -> str:
    driver, car, engine_map = pick
    chosen = next(d for d in view["drivers"] if d["id"] == driver)
    _, car_label, colour = next(c for c in view["cars"] if c[0] == car)
    map_glyph = next(g for level, _, g in view["levels"] if level == engine_map)
    combo = key(driver, car, engine_map)
    entry = view["entries"].get(combo)
    trust = view["unverified"].get(combo)

    def hidden(on: bool) -> str:
        return "" if on else " hidden"

    setups = entry["setups"] if entry else 1
    art = CAR_ART.format(label=esc(f"{chosen['name']} in {car_label}, in its livery"))
    return (
        f'<section class="gr-result" style="--c:{colour}" aria-live="polite" data-result>'
        f'<div class="gr-car" data-slot="art">{art}</div>'
        f'<p class="gr-title"><b class="ent">{glyphs.icon("driver")}'
        f'<span data-slot="driver">{esc(chosen["name"])}</span></b> '
        f'<span>in <b class="ent">{glyphs.icon("car")}<span data-slot="car">{esc(car_label)}</span></b>'
        f' · engine map <b class="ent"><span data-slot="mapGlyph">{glyphs.icon(map_glyph)}</span>'
        f'<span data-slot="map">{esc(views.map_label(engine_map, car, driver))}</span></b>'
        f"{views.map_info(views.map_tip(car, driver), context=car_label + ' on ' + chosen['name'], hidden=engine_map != 'default')}</span></p>"
        f'<div class="gr-raced" data-state="raced"{hidden(bool(entry))}>'
        f'<div class="gr-score"><span class="gr-pct" data-slot="pct">{esc(entry["pct"] if entry else "")}</span>'
        f'<span><span data-slot="passOf">{esc(entry["passOf"] if entry else "")}</span> races passed</span></div>'
        '<div class="gr-tiles">'
        '<div class="gr-tile"><span>Fuel bill</span>'
        f'<b data-slot="usd">{esc(entry["usd"] if entry else "")}</b><small>per race passed</small></div>'
        '<div class="gr-tile"><span>On the board</span>'
        f'<b data-slot="place">{esc(entry["place"] if entry else "")}</b>'
        f'<small data-slot="placeNote">{esc(entry["placeNote"] if entry else "")}</small></div></div>'
        f'<p class="gr-setups" data-slot="setups"{hidden(setups > 1)}>'
        f"Raced in {setups} setups of this car; this is the best of them.</p>"
        f'<a class="btn ghost" data-slot="driverPage" href="{root}{esc(chosen["page"] or "")}"'
        f"{hidden(bool(chosen['page']))}>See this driver in every car</a></div>"
        f'<div class="gr-unverified" data-state="unverified"{hidden(bool(trust))}>'
        + glyphs.icon("flag-yellow")
        + '<p><b>Raced, not yet checked.</b> This entry has a <span data-slot="trust">'
        f"{esc(trust or '')}</span> result. It shows on the community board until it is "
        f'checked. <a href="{root}{COMMUNITY}">See the community board</a></p></div>'
        f'<div class="gr-unraced" data-state="unraced"{hidden(not entry and not trust)}>'
        '<span class="gr-unraced-title">Nobody has raced this yet.</span>'
        "<p>That is a gap on the grid. Race it from your own fork and it shows up here, "
        "with a badge saying how far it has been checked.</p>"
        f'<a class="btn solid" href="{root}{RACE_IT_YOURSELF}">Race it yourself</a></div>'
        f'<p class="gr-tip"><b>Did you know?</b> <span data-slot="tip">{esc(chosen["tip"])}</span></p>'
        "</section>"
    )


def body(site: views.SiteData, current: str) -> str:
    view = model(site)
    root = layout.up(current)
    calendar = view["calendar"]
    lede = "Put a driver in a car and set the engine map." + (
        f" See how that entry really did across all {views.plural(calendar, 'race')}."
        if calendar
        else ""
    )
    hero = views.hero("entry", "Build an entry.", lede)
    if not view["live"]:
        return hero + views.caveat(
            "flag-yellow",
            "Waiting on checks.",
            "No results document has been verified yet, so the garage has no results "
            "to show rather than unchecked ones.",
        )
    pick = view["start"]
    if pick is None:
        return hero + views.caveat(
            "flag-yellow", "An empty garage.", "No entry has a counted run yet."
        )
    data = {
        "calendar": calendar,
        "levels": view["levels"],
        "cars": view["cars"],
        "drivers": view["drivers"],
        "entries": {
            combo: {field: entry[field] for field in EMBEDDED}
            for combo, entry in view["entries"].items()
        },
        "unverified": view["unverified"],
        "tips": list(TIPS),
        "root": root,
    }
    return (
        hero
        + '<div class="gr" data-garage>'
        + _pickers(view, pick)
        + _result(view, pick, root)
        + "</div>"
        + views.json_script("garage-data", data)
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
/* ---------- garage (PDW-08) ---------- */
.gr { display: grid; grid-template-columns: minmax(0, 1fr); gap: 32px; align-items: start; }
.gr-pick { display: grid; gap: 26px; }
/* the default chip names the setting that ran, which can be long: it wraps, never overflows */
.chip[data-map="default"] { white-space: normal; max-width: 100%; text-align: left; line-height: 1.25; padding-block: 8px; }
.chip[data-map="default"] .glyph { flex: none; }
.gr-step { display: grid; gap: 10px; }
.gr-note { font-size: 14px; line-height: 1.45; color: var(--muted); }
.gr-more summary { min-height: 44px; display: flex; align-items: center; }
.gr-more { margin: 0; padding: 4px 16px; }
.gr-more[open] { padding-bottom: 14px; }
.gr-result { display: grid; gap: 16px; padding: 24px; border-radius: 28px; background: var(--soft); }
.gr-art { width: 100%; height: auto; display: block; }
.gr-car.rolled { animation: gr-roll .5s var(--ease); }
@keyframes gr-roll { from { transform: translateX(-24px); opacity: .2; } to { transform: none; opacity: 1; } }
.gr-title { font-size: 18px; }
.gr-title > span { color: var(--ink-2); }
.gr-title .ent { color: var(--ink); }
.gr-raced, .gr-unraced { display: grid; gap: 14px; }
.gr-raced[hidden], .gr-unraced[hidden], .gr-unverified[hidden], .gr-setups[hidden], .gr-raced .btn[hidden] { display: none; }
.gr-score { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.gr-pct { font: 700 80px/.9 var(--display); font-variant-numeric: tabular-nums; }
.gr-score > span:last-child { color: var(--ink-2); }
.gr-tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.gr-tile { padding: 14px; border-radius: 16px; background: var(--paper); display: grid; gap: 2px; }
.gr-tile span, .gr-tile small { font-size: 13px; color: var(--muted); }
.gr-tile small { font-size: 12px; }
.gr-tile b { font: 700 28px/1.1 var(--display); }
.gr-setups { font-size: 14px; color: var(--ink-2); }
.gr-raced .btn { justify-self: start; }
.gr-unverified { display: flex; gap: 12px; align-items: flex-start; padding: 16px; border-radius: 16px; background: var(--paper); }
.gr-unraced { padding: 18px; border-radius: 16px; border: 2px dashed var(--viz-interval); background: var(--paper); }
.gr-unraced-title { font: italic 800 30px/1 var(--display); }
.gr-unraced p { color: var(--ink-2); }
.gr-unraced .btn { justify-self: start; min-height: 48px; }
.gr-tip { padding-top: 12px; border-top: 1px solid var(--rule); }
@media (min-width: 900px) {
  .gr { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 36px; }
  .gr-result { position: sticky; top: 84px; }
}
@media (prefers-reduced-motion: reduce) { .gr-car.rolled { animation: none; } }
"""

#: The garage picker: swaps the selection from the page's JSON block and keeps
#: it in the address (``#driver|car|map``) so a build can be shared.
SCRIPT = """(function () {
  var garage = document.querySelector('[data-garage]');
  var source = document.getElementById('garage-data');
  if (!garage || !source) return;
  var data = JSON.parse(source.textContent);
  var drivers = {};
  data.drivers.forEach(function (d) { drivers[d.id] = d; });
  var cars = {};
  data.cars.forEach(function (c) { cars[c[0]] = c; });
  var levelGlyphs = {};
  data.levels.forEach(function (l) { levelGlyphs[l[0]] = l[2]; });
  var result = garage.querySelector('[data-result]');
  var turns = 0;
  function slot(name) { return garage.querySelector('[data-slot="' + name + '"]'); }
  function chips(attr) { return garage.querySelectorAll('[' + attr + ']'); }
  function pressed(attr) {
    var on = garage.querySelector('[' + attr + '][aria-pressed="true"]');
    return on ? on.getAttribute(attr) : null;
  }
  var state = { driver: pressed('data-driver'), car: pressed('data-car'), map: pressed('data-map') };
  function open(driver, map) { return drivers[driver].levels[map] === ''; }
  function bestMap(driver, car) {
    var maps = data.levels.map(function (l) { return l[0]; }).filter(function (m) { return open(driver, m); });
    for (var i = maps.length - 1; i >= 0; i--) {
      if (data.entries[driver + '|' + car + '|' + maps[i]]) return maps[i];
    }
    return maps.length ? maps[maps.length - 1] : data.levels[0][0];
  }
  function render(moved) {
    var d = drivers[state.driver], car = cars[state.car];
    var combo = state.driver + '|' + state.car + '|' + state.map;
    var entry = data.entries[combo], trust = data.unverified[combo];
    Array.prototype.forEach.call(chips('data-driver'), function (c) {
      c.setAttribute('aria-pressed', c.getAttribute('data-driver') === state.driver ? 'true' : 'false');
    });
    Array.prototype.forEach.call(chips('data-car'), function (c) {
      c.setAttribute('aria-pressed', c.getAttribute('data-car') === state.car ? 'true' : 'false');
    });
    Array.prototype.forEach.call(chips('data-map'), function (c) {
      var level = c.getAttribute('data-map'), why = d.levels[level];
      var label = c.querySelector('[data-label]');
      if (label) label.textContent = d.defaults[state.car];
      c.setAttribute('aria-pressed', level === state.map ? 'true' : 'false');
      if (why) {
        c.setAttribute('aria-disabled', 'true');
        c.setAttribute('title', why);
        c.setAttribute('aria-describedby', 'gr-map-note');
      } else {
        c.removeAttribute('aria-disabled');
        c.removeAttribute('title');
        c.removeAttribute('aria-describedby');
      }
    });
    result.style.setProperty('--c', car[2]);
    var art = slot('art').querySelector('svg');
    art.setAttribute('aria-label', d.name + ' in ' + car[1] + ', in its livery');
    slot('driver').textContent = d.name;
    slot('car').textContent = car[1];
    slot('map').textContent = state.map === 'default' ? d.defaults[state.car] : state.map;
    var info = garage.querySelector('.map-info');
    info.querySelector('.map-tip').hidePopover();
    info.querySelector('button').setAttribute('aria-expanded', 'false');
    info.querySelector('button').setAttribute('aria-label', 'About this engine map for ' + car[1] + ' on ' + d.name);
    info.querySelector('button').dataset.pinned = 'false';
    info.hidden = state.map !== 'default';
    info.querySelector('.map-tip').textContent = d.defaultTips[state.car];
    slot('mapGlyph').querySelector('use').setAttribute('href', '#' + levelGlyphs[state.map]);
    slot('note').textContent = d.note;
    garage.querySelector('[data-state="raced"]').hidden = !entry;
    garage.querySelector('[data-state="unverified"]').hidden = !trust;
    garage.querySelector('[data-state="unraced"]').hidden = !!(entry || trust);
    if (entry) {
      ['pct', 'passOf', 'usd', 'place', 'placeNote'].forEach(function (k) { slot(k).textContent = entry[k]; });
      var setups = slot('setups');
      setups.hidden = entry.setups < 2;
      setups.textContent = 'Raced in ' + entry.setups + ' setups of this car; this is the best of them.';
      var link = slot('driverPage');
      link.hidden = !d.page;
      if (d.page) link.setAttribute('href', data.root + d.page);
    }
    if (trust) slot('trust').textContent = trust;
    var tips = [d.tip].concat(data.tips);
    slot('tip').textContent = tips[turns % tips.length];
    if (moved) {
      var box = slot('art');
      box.classList.remove('rolled'); void box.offsetWidth; box.classList.add('rolled');
      if (history.replaceState) history.replaceState(null, '', '#' + encodeURIComponent(combo));
    }
  }
  function pick(change) {
    turns += 1;
    if (change.driver) state.driver = change.driver;
    if (change.car) state.car = change.car;
    if (change.map) state.map = change.map;
    else if (!open(state.driver, state.map) || !data.entries[state.driver + '|' + state.car + '|' + state.map]) {
      state.map = bestMap(state.driver, state.car);
    }
    render(true);
  }
  garage.addEventListener('click', function (event) {
    var chip = event.target.closest('button.chip');
    if (!chip || !garage.contains(chip)) return;
    if (chip.hasAttribute('data-driver')) pick({ driver: chip.getAttribute('data-driver') });
    else if (chip.hasAttribute('data-car')) pick({ car: chip.getAttribute('data-car') });
    else if (chip.hasAttribute('data-map')) {
      if (chip.getAttribute('aria-disabled') === 'true') {
        slot('note').textContent = chip.getAttribute('title');
        return;
      }
      pick({ map: chip.getAttribute('data-map') });
    }
  });
  var hash = decodeURIComponent(location.hash.slice(1)).split('|');
  if (hash.length === 3 && drivers[hash[0]] && cars[hash[1]] && open(hash[0], hash[2])) {
    state = { driver: hash[0], car: hash[1], map: hash[2] };
    var chosen = garage.querySelector('[data-driver="' + CSS.escape(hash[0]) + '"]');
    var more = chosen && chosen.closest('details');
    if (more) more.open = true;
    render(false);
  }
})();
"""
