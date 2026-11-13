"""The standings page and the entry pages (PDW-07).

``/standings/`` rebuilds ``design/web/Standings.dc.html``: the hero, the "How
to read these results" note, the scope panel (the whole calendar, races per
challenge, first-run basis), the ranking by average pass rate or by fuel bill,
the honest-ranges switch with its explainer, a 75-100% scale lined up with
the row bars, and rows that open to per-challenge bars and a link to the
entry's page.

``/entries/<entry-id>/`` rebuilds ``design/web/Entry.dc.html``: the hero with
the honest range on a 50-100% scale, the place, fuel bill, median lap and
weakest challenge tiles, and the season grid, one flag per calendar race
grouped by challenge, which a tap opens for its lap and fuel. ``/entries/``
lands on the list of every entry.

Every number comes from the site data files (``data.py``, PDW-03), which take
theirs from ``scripts/leaderboard.py``; nothing here ranks, rounds a range or
counts a pass on its own. The pages are static HTML. The small script
(:data:`SCRIPT`) only switches the view: without it the board shows the pass
ranking with ranges on, every row, and rows still open (they are
``<details>``).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import glyphs, layout, views
from .routes import SITE_URL, Build

STANDINGS = "standings/index.html"
ENTRIES = "entries/index.html"
SCRIPT_NAME = "standings.js"
AWAITING = "awaiting-trust-sign-off"

#: The standings scale: the row bars and the scale above them, in percent.
BOARD_SCALE = (75, 100)
#: The entry hero's range scale, in percent.
ENTRY_SCALE = (50, 100)
#: Rows the board shows before "Show all"; a tie at the cut stays whole.
TOP = 16
#: The standings scale's ticks, in percent.
TICKS = (75, 80, 85, 90, 95, 100)

#: Car (harness) names as the vocabulary writes them.
CARS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "pi": "Pi",
    "antigravity": "Antigravity",
}
#: A car's livery token (design/tokens.css); identity only, never a value.
LIVERY = {
    "claude-code": "car-cc",
    "codex": "car-codex",
    "opencode": "car-opencode",
    "pi": "car-pi",
    "antigravity": "car-agy",
}
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}

esc = layout.esc


# ---------------------------------------------------------------- formatting


def car_name(car: str) -> str:
    return CARS.get(car, car)


def livery(car: str) -> str:
    return f"var(--{LIVERY.get(car, 'viz-other')})"


def challenge_label(challenge: str) -> str:
    known = glyphs.RACE_TYPES.get(challenge)
    return known[0] if known else challenge


def challenge_order(challenge: str) -> tuple[int, str]:
    order = list(glyphs.RACE_TYPES)
    return (
        order.index(challenge) if challenge in order else len(order),
        challenge,
    )


def pct(rate: float) -> str:
    """A rate in ``[0, 1]`` as a percentage with one decimal: ``96.3%``."""
    return f"{rate * 100:.1f}%"


def scale_pos(rate: float, scale: tuple[int, int] = BOARD_SCALE) -> float:
    """Where ``rate`` (in ``[0, 1]``) sits on ``scale`` (in percent), as a
    percentage of the bar's width, held to the bar."""
    low, high = scale
    return min(100.0, max(0.0, (rate * 100 - low) / (high - low) * 100))


def css_pct(value: float) -> str:
    return f"{value:.1f}%"


def usd(value: float | None) -> str:
    if value is None:
        return "no price"
    return f"${value:.3f}" if value < 1 else f"${value:.2f}"


def lap(seconds: float | None) -> str:
    """A lap time as ``m:ss`` (``h:mm:ss`` past an hour)."""
    if seconds is None:
        return "n/a"
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def place_label(place: int | None, tie: bool) -> str:
    if place is None:
        return "–"
    return f"={place}" if tie else str(place)


def map_html(engine_map: str, car: str, route: str | None = None) -> str:
    """``engine map <label>``, the label bold behind its engine-map glyph."""
    return f"engine map {views.map_tag(engine_map, car, route)}"


def plural(count: int, one: str, many: str = "") -> str:
    return f"{count} {one if count == 1 else many or one + 's'}"


def short_hash(config_hash: str) -> str:
    return config_hash.removeprefix("sha256:")[:8]


def names(standings: Mapping[str, Any]) -> dict[str, str]:
    """Each standings driver's display name, two routes to one model told apart."""
    return views.driver_names(
        row["driver"] for row in list(standings["entries"]) + list(standings["partial"])
    )


def who(row: Mapping[str, Any], names: Mapping[str, str] | None = None) -> str:
    """``driver in car``, each bold behind its glyph, as the board writes an entry."""
    return (
        f"{views.driver_tag(row['driver'], (names or {}).get(row['driver']))} "
        f'<span class="st-car">in {views.car_tag(row["car"])}</span>'
    )


def ambiguous(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str, str]]:
    """Driver, car and map triples that name more than one entry: those rows
    also show their setup sheet's hash."""
    seen: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (row["driver"], row["car"], row["map"])
        seen[key] = seen.get(key, 0) + 1
    return {key for key, count in seen.items() if count > 1}


def setup_note(row: Mapping[str, Any], clash: set[tuple[str, str, str]]) -> str:
    if (row["driver"], row["car"], row["map"]) not in clash:
        return ""
    return f" · setup <code>{esc(short_hash(row['configHash']))}</code>"


# ---------------------------------------------------------------- ordering


def fuel_ranks(entries: Sequence[Mapping[str, Any]]) -> list[tuple[int, str]]:
    """``(order, label)`` per entry for the fuel-bill ranking.

    Priced entries come first, cheapest per race passed first; an equal bill is
    a tie and keeps the pass ranking's order. Entries without a price follow in
    the pass ranking's order and have no place (``–``).
    """
    priced = sorted(
        (i for i, row in enumerate(entries) if row["costPerPass"] is not None),
        key=lambda i: (entries[i]["costPerPass"], i),
    )
    unpriced = [i for i, row in enumerate(entries) if row["costPerPass"] is None]
    out: list[tuple[int, str]] = [(0, "")] * len(entries)
    for order, i in enumerate(priced):
        cost = entries[i]["costPerPass"]
        first = next(
            k for k, j in enumerate(priced) if entries[j]["costPerPass"] == cost
        )
        tie = sum(entries[j]["costPerPass"] == cost for j in priced) > 1
        out[i] = (order, f"={first + 1}" if tie else str(first + 1))
    for order, i in enumerate(unpriced, len(priced)):
        out[i] = (order, "–")
    return out


def top_by_pass(entries: Sequence[Mapping[str, Any]], top: int = TOP) -> int:
    """How many rows the pass ranking shows before "Show all": the first
    ``top``, and the rest of a tie that straddles the cut."""
    if len(entries) <= top:
        return len(entries)
    cut = entries[top - 1]["place"]
    return sum(1 for row in entries if row["place"] is not None and row["place"] <= cut)


def top_by_fuel(entries: Sequence[Mapping[str, Any]], top: int = TOP) -> int:
    """Keep equal fuel bills at the cut whole; unpriced rows have no tie."""
    priced = sorted(
        row["costPerPass"] for row in entries if row["costPerPass"] is not None
    )
    if len(priced) < top:
        return min(len(entries), top)
    cut = priced[top - 1]
    return sum(cost <= cut for cost in priced)


# ---------------------------------------------------------------- shared parts


def results_note(root: str, races: int) -> str:
    """The "How to read these results" note (the optics note, operator 2026-11-03)."""
    calendar = (
        f"a fixed calendar of {races} tasks" if races else "a fixed calendar of tasks"
    )
    return (
        '<section class="st-note" data-pdk="results-note">'
        + glyphs.icon("rulebook", "st-note-glyph")
        + '<div class="st-note-body"><h2 class="st-note-title">How to read these results</h2>'
        f"<p>These are AI-run benchmarks: AI agents race {calendar}, and the same "
        "automated checks grade every run. The board shows how each entry did on these "
        "races. It is not a comprehensive ranking, and different tasks or settings can "
        "give a different order.</p>"
        '<p class="st-note-links">'
        f'<a href="{root}contact/index.html">Independent and open source: about the paddock</a>'
        f'<a href="{root}rulebook/index.html">Read the method</a></p></div></section>'
    )


def awaiting(root: str) -> str:
    return (
        '<section class="st-awaiting" data-state="awaiting-trust-sign-off">'
        + glyphs.icon("flag-yellow", "st-panel-glyph")
        + "<div><h2>Awaiting trust sign-off</h2>"
        "<p>No results document has been reproduced or run on the bench yet, so "
        "there is nothing to rank. Results that are only self-reported wait on the "
        f'<a href="{root}community/index.html">community page</a> with their badge.</p>'
        "</div></section>"
    )


def _eyebrow(glyph: str, text: str) -> str:
    return f'<p class="st-eyebrow">{glyphs.icon(glyph, "st-eyebrow-glyph")}<span>{esc(text)}</span></p>'


def _script(root: str) -> str:
    return f'<script src="{root}{SCRIPT_NAME}" defer></script>'


def _range_bar(row: Mapping[str, Any], scale: tuple[int, int], css: str) -> str:
    lo, hi = scale_pos(row["lo"], scale), scale_pos(row["hi"], scale)
    return (
        f'<span class="{css}" aria-hidden="true" data-lo="{row["lo"]!r}" '
        f'data-hi="{row["hi"]!r}" data-rate="{row["rate"]!r}">'
        f'<span class="st-band" style="left:{css_pct(lo)};width:{css_pct(hi - lo)}"></span>'
        f'<span class="st-dot" style="left:{css_pct(scale_pos(row["rate"], scale))}"></span>'
        "</span>"
    )


def _range_text(row: Mapping[str, Any]) -> str:
    return f"{pct(row['lo'])} to {pct(row['hi'])}"


# ---------------------------------------------------------------- standings


def _scope(root: str, standings: Mapping[str, Any], races: Mapping[str, Any]) -> str:
    calendar = [race for race in races["races"] if race["calendar"]]
    counts: dict[str, int] = {}
    for race in calendar:
        counts[race["challenge"]] = counts.get(race["challenge"], 0) + 1
    chips = "".join(
        f'<li data-challenge="{esc(challenge)}">{esc(challenge_label(challenge))} '
        f'<span class="num">{count}</span></li>'
        for challenge, count in sorted(
            counts.items(), key=lambda item: (-item[1], challenge_order(item[0]))
        )
    )
    total = len(calendar)
    return (
        '<section class="st-scope">'
        f'<p class="st-scope-head">{glyphs.icon("race-calendar", "st-panel-glyph")}'
        f"<span><b>These standings cover all {total} races</b>, every challenge on the "
        "calendar, not one race.</span></p>"
        f'<ul class="st-chips" aria-label="Races per challenge">{chips}</ul>'
        f"<p>An entry's score is how many of the {total} it passed, counting its first "
        f'run of each. For one race on its own, <a href="{root}races/index.html">pick it '
        "from the calendar</a>: each race has its own result.</p></section>"
    )


def _controls() -> str:
    return (
        '<div class="st-controls" data-st-controls hidden>'
        '<div class="st-rank" role="radiogroup" aria-label="Rank by">'
        '<button type="button" role="radio" aria-checked="true" data-rank="pass">'
        "Avg pass rate</button>"
        '<button type="button" role="radio" aria-checked="false" data-rank="fuel">'
        "Fuel bill</button></div>"
        '<button type="button" class="st-switch" role="switch" aria-checked="true" '
        'data-ranges><span class="st-switch-track" aria-hidden="true">'
        '<span class="st-switch-knob"></span></span>Honest ranges</button></div>'
    )


def _explainer(root: str, entries: Sequence[Mapping[str, Any]], total: int) -> str:
    lead = entries[0]
    leaders = [row for row in entries if row["place"] == 1]
    lo, hi = scale_pos(lead["lo"]), scale_pos(lead["hi"])
    dot = scale_pos(lead["rate"])
    example = (
        f"Example: {lead['k']} of {lead['n']} is {pct(lead['rate'])}, "
        f"honest range {pct(lead['lo'])} to {pct(lead['hi'])}"
    )
    why = (
        f" That is why the top {NUMBER_WORDS.get(len(leaders), len(leaders))} share first place."
        if len(leaders) > 1
        else ""
    )
    return (
        '<section class="st-explainer st-pass-only st-ranges-only">'
        f'<h2 class="st-panel-head">{glyphs.icon("whisker", "st-panel-glyph")}'
        "What an honest range is</h2>"
        f"<p>{total} races is a small sample. Run the calendar again and an entry that "
        f"passed {lead['k']} might pass a few more or a few fewer. The grey bar is where "
        "its true pass rate most likely sits: we are 95% sure it is inside. The dot is "
        "the score it actually got.</p>"
        f'<div class="st-example" role="img" aria-label="{esc(example)}">'
        f'<span class="st-example-labels st-example-top"><span style="left:{css_pct(dot)}">'
        f"<b>{pct(lead['rate'])}</b></span></span>"
        '<span class="st-example-bar">'
        f'<span class="st-band" style="left:{css_pct(lo)};width:{css_pct(hi - lo)}"></span>'
        f'<span class="st-dot" style="left:{css_pct(dot)}"></span></span>'
        '<span class="st-example-labels">'
        f'<span style="left:{css_pct(lo)}">{pct(lead["lo"])}</span>'
        f'<span class="st-end" style="left:{css_pct(hi)}">{pct(lead["hi"])}</span>'
        "</span></div>"
        "<p><b>When two bars overlap, the board cannot tell those entries apart.</b>"
        f"{why}</p>"
        f'<a class="st-more" href="{root}learn/index.html">How the range is worked out, '
        "step by step</a></section>"
    )


def _callouts(
    entries: Sequence[Mapping[str, Any]], names: Mapping[str, str] | None = None
) -> str:
    lead = entries[0]
    leaders = [row for row in entries if row["place"] == 1]
    if len(leaders) > 1:
        count = NUMBER_WORDS.get(len(leaders), str(len(leaders)))
        verdict = (
            f"<b>Photo finish.</b> The top {count} share {lead['k']} of {lead['n']}, "
            "and their honest ranges overlap. The board calls it a tie."
        )
    else:
        verdict = (
            f"{views.driver_tag(lead['driver'], (names or {}).get(lead['driver']))} "
            f"<b>in</b> {views.car_tag(lead['car'])} <b>leads</b> "
            f"with {lead['k']} of {lead['n']}."
        )
        if len(entries) > 1 and entries[1]["hi"] >= lead["lo"]:
            verdict += (
                " Its honest range overlaps the next entry's, so the board cannot "
                "call it clearly better."
            )
    return (
        '<section class="st-callout st-pass-only">'
        + glyphs.icon("photo-finish", "st-callout-glyph")
        + f"<p>{verdict}</p></section>"
        '<section class="st-callout st-fuel-only soft">'
        + glyphs.icon("fuel-bill", "st-callout-glyph")
        + "<p><b>Fuel bill</b> is dollars per race passed: a cheap entry that fails a lot "
        "is not cheap. Entries without a per-token price for every run sit at the end.</p>"
        "</section>"
    )


def _scale() -> str:
    ticks = "".join(
        f'<span class="st-tick" style="left:{css_pct(scale_pos(tick / 100))}" '
        f'data-value="{tick}">{tick}{"%" if tick in (TICKS[0], TICKS[-1]) else ""}</span>'
        for tick in TICKS
    )
    return (
        '<div class="st-scale st-grid st-pass-only st-ranges-only" aria-hidden="true">'
        f'<span></span><span></span><span class="st-ticks">{ticks}</span>'
        '<span class="st-value-head">pass rate</span></div>'
    )


def _challenge_bars(challenges: Mapping[str, Mapping[str, int]]) -> str:
    rows = []
    for challenge in sorted(challenges, key=challenge_order):
        k, n = challenges[challenge]["k"], challenges[challenge]["n"]
        full = " full" if n and k == n else ""
        width = css_pct(k / n * 100 if n else 0)
        rows.append(
            f'<li class="st-cat{full}"><span>{esc(challenge_label(challenge))}</span>'
            f'<span class="st-cat-bar" aria-hidden="true"><span style="width:{width}">'
            f'</span></span><span class="num">{k}/{n}</span></li>'
        )
    return f'<ul class="st-cats" aria-label="Passed per challenge">{"".join(rows)}</ul>'


def _row(
    index: int,
    row: Mapping[str, Any],
    fuel: tuple[int, str],
    tops: tuple[bool, bool],
    clash: set[tuple[str, str, str]],
    root: str,
    names: Mapping[str, str] | None = None,
) -> str:
    place = place_label(row["place"], row["tie"])
    lead = " lead" if row["place"] == 1 else ""
    bill = usd(row["costPerPass"])
    foot = f"{row['k']} of {row['n']} races passed" + (
        f" · {bill} per race passed."
        if row["costPerPass"] is not None
        else ". Not every run has a per-token price, so there is no fuel bill."
    )
    return (
        f'<li class="st-item" data-entry="{esc(row["id"])}" data-pass-rank="{index}" '
        f'data-fuel-rank="{fuel[0]}" data-top-pass="{int(tops[0])}" '
        f'data-top-fuel="{int(tops[1])}"><details>'
        '<summary class="st-row st-grid">'
        f'<span class="st-pos{lead}"><span class="sr-only">Place </span>'
        f'<span class="st-by-pass">{place}</span>'
        f'<span class="st-by-fuel">{fuel[1]}</span></span>'
        f'<span class="st-livery" style="--c:{livery(row["car"])}" aria-hidden="true"></span>'
        f'<span class="st-who"><span class="st-name">{who(row, names)}</span>'
        f'<span class="st-map"><span>{map_html(row["map"], row["car"], row["driver"])}'
        f"{setup_note(row, clash)}</span></span>"
        + _range_bar(row, BOARD_SCALE, "st-range st-pass-only st-ranges-only")
        + f'<span class="sr-only st-pass-only">, honest range {_range_text(row)}</span>'
        "</span>"
        f'<span class="st-value num"><span class="st-by-pass">{pct(row["rate"])}</span>'
        f'<span class="st-by-fuel">{bill}</span></span>'
        "</summary>"
        '<div class="st-detail">'
        + _challenge_bars(row["challenges"])
        + f'<p class="st-foot">{foot}</p>'
        f'<a class="st-more" href="{root}entries/{esc(row["id"])}/index.html">'
        "See its whole season, race by race</a></div></details></li>"
    )


def board(root: str, standings: Mapping[str, Any], races: Mapping[str, Any]) -> str:
    entries = standings["entries"]
    total = standings["basis"]["calendarRaces"]
    if not entries:
        return (
            '<p class="st-empty">No entry has raced the whole calendar yet, so no one '
            f'is placed. Every entry\'s season is on <a href="{root}entries/index.html">'
            "its own page</a>.</p>"
        )
    fuel = fuel_ranks(entries)
    pass_top = top_by_pass(entries)
    fuel_top = top_by_fuel(entries)
    clash = ambiguous(entries + standings["partial"])
    shown = names(standings)
    rows = "".join(
        _row(i, row, fuel[i], (i < pass_top, fuel[i][0] < fuel_top), clash, root, shown)
        for i, row in enumerate(entries)
    )
    more = len(entries) > min(pass_top, TOP)
    partial = standings["partial"]
    return (
        '<div class="st-board" data-st-board data-rank="pass" data-ranges="on" '
        f'data-top="{TOP}">'
        + _controls()
        + _explainer(root, entries, total)
        + _callouts(entries, shown)
        + '<section class="st-standings" aria-label="Standings">'
        + _scale()
        + f'<ol class="st-list">{rows}</ol>'
        + (
            f'<button type="button" class="st-all" data-st-all hidden>'
            f"Show all {len(entries)} entries</button>"
            if more
            else ""
        )
        + "</section>"
        '<p class="st-small">'
        f"{plural(len(entries), 'entry', 'entries')} raced the whole calendar"
        + (
            f"; {plural(len(partial), 'more', 'more')} raced part of it and "
            f"{'is' if len(partial) == 1 else 'are'} not placed "
            f'(<a href="{root}entries/index.html">every entry\'s season</a>)'
            if partial
            else ""
        )
        + ". First result per race, so a lucky rerun cannot lift a score. "
        f'<a href="{root}pit-wall/index.html">Compare them all on the pit wall</a>, or '
        f'<a href="{root}drivers/index.html">follow one driver across every car</a>.</p>'
        "</div>"
    )


def standings_body(
    standings: Mapping[str, Any], races: Mapping[str, Any], root: str
) -> str:
    live = standings["state"] != AWAITING
    total = standings["basis"]["calendarRaces"]
    hero = (
        '<section class="st-hero">'
        + _eyebrow("timing-tower", "Standings · the full calendar")
        + "<h1>The timing tower.</h1>"
        '<p class="st-lede">Each entry is one driver in one car at one engine map. Tap a '
        "row to see where it wins and where it slips.</p></section>"
    )
    return (
        '<div class="st-page st-narrow">'
        + hero
        + results_note(root, total)
        + (
            _scope(root, standings, races) + board(root, standings, races)
            if live
            else awaiting(root)
        )
        + "</div>"
        + _script(root)
    )


# ---------------------------------------------------------------- entries


def _chips(entry_id: str, standings: Mapping[str, Any], root: str) -> str:
    chips = []
    clash = ambiguous(standings["entries"])
    shown = names(standings)
    for row in standings["entries"]:
        current = ' aria-current="page"' if row["id"] == entry_id else ""
        chips.append(
            f'<a href="{root}entries/{esc(row["id"])}/index.html"{current}>'
            f'<span class="st-livery" style="--c:{livery(row["car"])}" aria-hidden="true">'
            f"</span>{views.driver_tag(row['driver'], shown.get(row['driver']))} in "
            f"{views.car_tag(row['car'])} · "
            f"{views.map_tag(row['map'], row['car'], row['driver'])}{setup_note(row, clash)}</a>"
        )
    if not chips:
        return ""
    return (
        '<div class="en-chips-wrap"><nav class="en-chips" aria-label="Placed entries">'
        + "".join(chips)
        + "</nav></div>"
    )


def _tile(glyph: str, label: str, value: str, note: str, small: bool = False) -> str:
    size = " small" if small else ""
    return (
        f'<div class="en-tile"><span class="en-tile-label">{glyphs.icon(glyph, "en-tile-glyph")}'
        f'{esc(label)}</span><span class="en-tile-value{size}">{esc(value)}</span>'
        f'<span class="en-tile-note">{esc(note)}</span></div>'
    )


def weakest(challenges: Mapping[str, Mapping[str, int]]) -> tuple[str, int, int] | None:
    """The challenge with the lowest pass rate, the calendar's order breaking a tie."""
    rated = [
        (challenge, value["k"], value["n"])
        for challenge, value in sorted(
            challenges.items(), key=lambda kv: challenge_order(kv[0])
        )
        if value["n"]
    ]
    if not rated:
        return None
    return min(rated, key=lambda item: item[1] / item[2])


def _hero(entry: Mapping[str, Any], name: str | None = None) -> str:
    """The entry's card: its driver as the title behind the driver glyph, then
    its car and engine map, tagged."""
    who_ = entry["identity"]
    totals, rng = entry["totals"], entry["range"]
    row = {"rate": totals["rate"], "lo": rng["lo"], "hi": rng["hi"]}
    place = (
        _tile(
            "podium",
            "On the board",
            place_label(entry["place"], entry["tie"]),
            "by races passed",
        )
        if entry["place"] is not None
        else _tile(
            "podium", "On the board", "Not placed", "raced part of the calendar", True
        )
    )
    fuel = entry["fuel"]["costPerPass"]
    weak = weakest(entry["challenges"])
    if weak is None or weak[1] == weak[2]:
        note = (
            "no calendar races run"
            if weak is None
            else "all races run passed"
            if entry["place"] is None
            else "a clean sweep"
        )
        weak_tile = _tile("whisker", "Weakest challenge", "None", note, True)
    else:
        weak_tile = _tile(
            "whisker",
            "Weakest challenge",
            challenge_label(weak[0]),
            f"{weak[1]} of {weak[2]} passed",
            True,
        )
    return (
        '<section class="en-hero">'
        f'<div class="en-card" style="--c:{livery(who_["car"])}">'
        f'<div class="en-card-who">{glyphs.icon("driver", "en-card-glyph")}'
        f"<div><h1>{esc(name or views.model_name(who_['driver']))}</h1>"
        f"<p>in {views.car_tag(who_['car'])} · "
        f"{map_html(who_['map'], who_['car'], who_['driver'])}"
        f" · setup <code>{esc(short_hash(who_['configHash']))}</code></p></div></div>"
        f'<p class="en-pct"><b class="num">{pct(totals["rate"])}</b>'
        f"<span>{totals['k']} of {totals['n']} races passed</span></p>"
        '<div class="en-range-wrap">'
        + _range_bar(row, ENTRY_SCALE, "en-range")
        + f"<p>Honest range {_range_text(row)} · scale {ENTRY_SCALE[0]}% to {ENTRY_SCALE[1]}%</p>"
        "</div></div>"
        '<div class="en-tiles">'
        + place
        + _tile("fuel-bill", "Fuel bill", usd(fuel), "per race passed")
        + _tile(
            "lap-time",
            "Median lap",
            lap(entry["medianLapSeconds"]),
            "lights out to hand-in",
        )
        + weak_tile
        + "</div></section>"
    )


def _flag(race: Mapping[str, Any], result: Mapping[str, Any] | None) -> str:
    name = race["name"]
    label = challenge_label(race["challenge"])
    if result is None:
        return (
            f'<span class="en-flag none" role="img" aria-label="{esc(name)}, not raced">'
            "</span>"
        )
    passed = result["passed"]
    line = (
        "Checkered flag" if passed else "DNF: did not finish"
    ) + f" · lap {lap(result['lapSeconds'])} · fuel {usd(result['costUsd'])}"
    return (
        f'<button type="button" class="en-flag {"pass" if passed else "dnf"}" '
        f'aria-pressed="false" aria-label="{esc(name)}, '
        f'{"passed" if passed else "did not finish"}" '
        f'data-race="{esc(race["id"])}" data-title="{esc(name)} · {esc(label)}" '
        f'data-line="{esc(line)}">'
        + glyphs.icon("flag-checkered" if passed else "dnf", "en-flag-glyph")
        + "</button>"
    )


def season(entry: Mapping[str, Any], races: Mapping[str, Any]) -> str:
    calendar = [race for race in races["races"] if race["calendar"]]
    mine = {
        (race["race"], race["version"]): race
        for race in entry["races"]
        if race["calendar"]
    }
    groups: dict[str, list] = {}
    for race in calendar:
        groups.setdefault(race["challenge"], []).append(race)
    rows = []
    for challenge in sorted(groups, key=challenge_order):
        members = groups[challenge]
        results = [mine.get((race["id"], race["version"])) for race in members]
        passed = sum(1 for result in results if result and result["passed"])
        raced = sum(1 for result in results if result)
        mark = (
            glyphs.roundel(challenge, labelled=False)
            if challenge in glyphs.RACE_TYPES
            else ""
        )
        rows.append(
            f'<div class="en-group" data-challenge="{esc(challenge)}">'
            f'<p class="en-group-head">{mark}<b>{esc(challenge_label(challenge))}</b>'
            f'<span class="num">{passed}/{raced}</span></p>'
            '<div class="en-flags">'
            + "".join(_flag(race, result) for race, result in zip(members, results))
            + "</div></div>"
        )
    fails = [
        race["name"]
        for race in calendar
        if (race["id"], race["version"]) in mine
        and not mine[(race["id"], race["version"])]["passed"]
    ]
    missing = len(calendar) - len(mine)
    if fails:
        title = f"It dropped {plural(len(fails), 'race')}"
        line = ", ".join(fails) + ". Tap any flag for its lap and fuel."
    elif missing:
        title = (
            f"Passed all {plural(len(mine), 'race')} run"
            if mine
            else "No calendar races run"
        )
        line = "Tap any flag for its lap and fuel."
    else:
        title, line = "A clean sweep", "Tap any flag for its lap and fuel."
    off = sum(1 for race in entry["races"] if not race["calendar"])
    notes = []
    if missing:
        notes.append(
            f"It has not raced {plural(missing, 'calendar race')} yet, so it is not placed."
        )
    if off:
        notes.append(
            f"It also ran {plural(off, 'race')} not on the calendar yet; "
            f"{'it does' if off == 1 else 'they do'} not count."
        )
    return (
        '<section class="en-season">'
        f'<p class="st-eyebrow"><span>The season · all {len(calendar)} races</span></p>'
        "<h2>Every race, one flag each.</h2>"
        "<p>Its first run of every race, as the standings count it. Tap a flag to see "
        "the race.</p>"
        + "".join(f'<p class="st-small">{esc(note)}</p>' for note in notes)
        + f'<div class="en-grid" data-en-season>{"".join(rows)}</div>'
        f'<div class="en-pick" data-en-pick aria-live="polite" data-default-title="{esc(title)}" '
        f'data-default-line="{esc(line)}"><b class="en-pick-title">{esc(title)}</b>'
        f'<span class="en-pick-line">{esc(line)}</span></div></section>'
    )


def entry_body(
    entry: Mapping[str, Any],
    standings: Mapping[str, Any],
    races: Mapping[str, Any],
    root: str,
) -> str:
    return (
        '<div class="st-page en-page">'
        '<section class="en-top">'
        + _eyebrow("podium", "An entry · its season")
        + _chips(entry["id"], standings, root)
        + f'<a class="en-back" href="{root}standings/index.html">Back to the timing tower</a>'
        "</section>"
        + _hero(entry, names(standings).get(entry["identity"]["driver"]))
        + season(entry, races)
        + '<section class="en-next">'
        f'<a class="en-next-dark" href="{root}pit-wall/index.html"><span>Next</span>'
        "<b>Compare it on the pit wall</b></a>"
        f'<a href="{root}garage/index.html"><span>Or</span>'
        "<b>Change one thing in the garage</b></a>"
        "</section></div>" + _script(root)
    )


def _entry_link(
    row: Mapping[str, Any],
    root: str,
    clash: set,
    names: Mapping[str, str] | None = None,
) -> str:
    place = place_label(row["place"], row["tie"])
    return (
        f'<li><a class="en-list-row" href="{root}entries/{esc(row["id"])}/index.html">'
        f'<span class="st-pos">{place}</span>'
        f'<span class="st-livery" style="--c:{livery(row["car"])}" aria-hidden="true"></span>'
        f'<span class="st-who"><span class="st-name">{who(row, names)}</span>'
        f'<span class="st-map"><span>{map_html(row["map"], row["car"], row["driver"])}'
        f"{setup_note(row, clash)}</span></span></span>"
        f'<span class="st-value num">{pct(row["rate"])}<small>{row["k"]}/{row["n"]}</small>'
        "</span></a></li>"
    )


def entries_body(standings: Mapping[str, Any], root: str) -> str:
    hero = (
        '<section class="st-hero">'
        + _eyebrow("podium", "Entries · every season")
        + "<h1>An entry's season.</h1>"
        '<p class="st-lede">One driver in one car at one engine map. Pick an entry to see '
        "every race it ran, one flag each.</p></section>"
    )
    if standings["state"] == AWAITING:
        return f'<div class="st-page st-narrow">{hero}{awaiting(root)}</div>'
    clash = ambiguous(standings["entries"] + standings["partial"])
    shown = names(standings)
    placed = "".join(
        _entry_link(row, root, clash, shown) for row in standings["entries"]
    )
    partial = "".join(
        _entry_link(row, root, clash, shown) for row in standings["partial"]
    )
    return (
        '<div class="st-page st-narrow">'
        + hero
        + (
            '<section><h2 class="st-h2">Placed</h2>'
            f'<p class="st-small">Raced every race on the calendar, in standings order.</p>'
            f'<ol class="en-list">{placed}</ol></section>'
            if placed
            else ""
        )
        + (
            '<details class="en-partial"><summary>'
            f"{plural(len(standings['partial']), 'more entry', 'more entries')} raced "
            "part of the calendar</summary>"
            '<p class="st-small">Not placed: their pass rate covers only the calendar '
            "races they ran.</p>"
            f'<ul class="en-list">{partial}</ul></details>'
            if partial
            else ""
        )
        + "</div>"
    )


# ---------------------------------------------------------------- pages


def _load(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return data[f"data/{name}"]


def standings_page(build: Build) -> str:
    root = layout.up(STANDINGS)
    return layout.page(
        routes=build.routes,
        current=STANDINGS,
        title="Standings",
        body=standings_body(
            _load(build.data, "standings.json"), _load(build.data, "races.json"), root
        ),
        url=SITE_URL + "standings/",
    )


def entries_page(build: Build) -> str:
    root = layout.up(ENTRIES)
    return layout.page(
        routes=build.routes,
        current=ENTRIES,
        title="An entry's season",
        body=entries_body(_load(build.data, "standings.json"), root),
        url=SITE_URL + "entries/",
    )


def entry_file(entry_id: str) -> str:
    return f"entries/{entry_id}/index.html"


def entry_pages(build: Build) -> dict[str, str]:
    """One page per standings entry, placed and partial."""
    standings = _load(build.data, "standings.json")
    if standings["state"] == AWAITING:
        return {}
    races = _load(build.data, "races.json")
    shown = names(standings)
    pages = {}
    for row in standings["entries"] + standings["partial"]:
        entry = _load(build.data, f"entries/{row['id']}.json")
        current = entry_file(row["id"])
        who_ = entry["identity"]
        pages[current] = layout.page(
            routes=build.routes,
            current=current,
            nav_current=ENTRIES,
            title=(
                f"{shown.get(who_['driver'], views.model_name(who_['driver']))} in "
                f"{car_name(who_['car'])} · "
                f"{views.map_label(who_['map'], who_['car'], who_['driver'])}"
            ),
            body=entry_body(entry, standings, races, layout.up(current)),
            url=f"{SITE_URL}entries/{row['id']}/",
        )
    return pages


# ---------------------------------------------------------------- script and styles

#: The view switches. The board: rank by pass rate or fuel bill (rows move in
#: the DOM, so reading order follows the ranking), honest ranges on or off,
#: top rows or all. An entry: a tapped flag shows its lap and fuel; the chip row
#: scrolls its current entry into view.
SCRIPT = """(function () {
  var board = document.querySelector('[data-st-board]');
  if (board) {
    var list = board.querySelector('.st-list');
    var items = Array.prototype.slice.call(list.children);
    var controls = board.querySelector('[data-st-controls]');
    var radios = board.querySelectorAll('[data-rank]');
    var toggle = board.querySelector('[data-ranges]');
    var all = board.querySelector('[data-st-all]');
    var state = { rank: 'pass', ranges: true, all: false };
    var render = function () {
      var fuel = state.rank === 'fuel';
      board.setAttribute('data-rank', state.rank);
      board.setAttribute('data-ranges', state.ranges ? 'on' : 'off');
      items.sort(function (a, b) {
        return fuel ? a.dataset.fuelRank - b.dataset.fuelRank : a.dataset.passRank - b.dataset.passRank;
      });
      var shown = 0;
      items.forEach(function (item) {
        list.appendChild(item);
        item.hidden = !state.all && (fuel ? item.dataset.topFuel : item.dataset.topPass) !== '1';
        if (!item.hidden) shown += 1;
      });
      Array.prototype.forEach.call(radios, function (radio) {
        if (radio.getAttribute('role') === 'radio') {
          radio.setAttribute('aria-checked', radio.dataset.rank === state.rank ? 'true' : 'false');
        }
      });
      toggle.setAttribute('aria-checked', state.ranges ? 'true' : 'false');
      if (all) {
        all.hidden = !state.all && shown === items.length;
        all.textContent = state.all ? 'Show the top ' + board.dataset.top : 'Show all ' + items.length + ' entries';
      }
    };
    controls.hidden = false;
    controls.addEventListener('click', function (event) {
      var radio = event.target.closest('[role="radio"]');
      if (radio) { state.rank = radio.dataset.rank; render(); }
      if (event.target.closest('[data-ranges]')) { state.ranges = !state.ranges; render(); }
    });
    if (all) all.addEventListener('click', function () { state.all = !state.all; render(); });
    render();
  }
  var grid = document.querySelector('[data-en-season]');
  var pick = document.querySelector('[data-en-pick]');
  if (grid && pick) {
    var title = pick.querySelector('.en-pick-title');
    var line = pick.querySelector('.en-pick-line');
    grid.addEventListener('click', function (event) {
      var flag = event.target.closest('button.en-flag');
      if (!flag) return;
      var on = flag.getAttribute('aria-pressed') === 'true';
      Array.prototype.forEach.call(grid.querySelectorAll('button.en-flag'), function (other) {
        other.setAttribute('aria-pressed', 'false');
      });
      flag.setAttribute('aria-pressed', on ? 'false' : 'true');
      title.textContent = on ? pick.dataset.defaultTitle : flag.dataset.title;
      line.textContent = on ? pick.dataset.defaultLine : flag.dataset.line;
      pick.setAttribute('data-result', on ? '' : (flag.classList.contains('dnf') ? 'dnf' : 'pass'));
    });
  }
  var chips = document.querySelector('.en-chips');
  var current = chips && chips.querySelector('[aria-current="page"]');
  if (current) chips.scrollLeft = current.offsetLeft - (chips.clientWidth - current.offsetWidth) / 2;
})();
"""

CSS = """
/* ---------- standings and entry pages (PDW-07) ---------- */
.st-page { display: flex; flex-direction: column; gap: 28px; padding-block: 32px 24px; }
.st-narrow { max-width: 880px; margin-inline: auto; }
.st-page h1, .st-page h2 { margin: 0; }
.st-page p { margin: 0; }
.st-eyebrow {
  display: flex; align-items: center; gap: 10px; font: 500 12px/1.2 var(--mono); letter-spacing: .14em;
  text-transform: uppercase; color: var(--ink-2);
}
.st-eyebrow-glyph { width: 30px; height: 30px; color: var(--ink); }
.st-hero { display: flex; flex-direction: column; gap: 12px; }
.st-hero h1 { font: italic 800 clamp(48px, 12vw, 84px)/.92 var(--display); }
.st-lede { font-size: 18px; line-height: 1.5; color: var(--ink-2); max-width: 60ch; }
.st-small { font-size: 14px; line-height: 1.5; color: var(--muted); }
.st-h2 { font: italic 800 32px/1 var(--display); margin-bottom: 8px; }
.st-panel-glyph, .st-note-glyph { width: 30px; height: 30px; flex: none; color: var(--ink); }
.st-panel-head { display: flex; align-items: center; gap: 12px; font: 600 17px/1.3 var(--body); }

.st-note {
  display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: 14px; align-items: start;
  padding: 16px 18px; border-radius: var(--radius-xl); background: var(--soft);
}
.st-note-glyph { margin-top: 2px; }
.st-note-body { display: flex; flex-direction: column; gap: 6px; }
.st-note-title { font: 700 16px/1.3 var(--body); }
.st-note-body p { font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.st-note-links { display: flex; flex-wrap: wrap; gap: 0 18px; font-size: 14px; }
.st-note-links a, .st-small a { font-weight: 600; }
.st-note-links a { color: var(--ink-2); text-decoration: none; min-height: 44px; display: inline-flex; align-items: center; }
.st-note-links a:hover { color: var(--ink); text-decoration: underline; }

.st-scope, .st-awaiting {
  display: flex; flex-direction: column; gap: 12px; padding: 18px 20px; border-radius: var(--radius-xl);
  border: 1.5px solid var(--rule);
}
.st-scope p { font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.st-scope .st-scope-head { display: flex; align-items: center; gap: 12px; font-size: 17px; color: var(--ink); }
.st-chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.st-chips li { padding: 6px 10px; border-radius: 999px; background: var(--soft); font-size: 14px; }
.st-awaiting { flex-direction: row; align-items: flex-start; border-color: var(--amber); }
.st-awaiting h2 { font: 700 17px/1.3 var(--body); margin-bottom: 4px; }
.st-awaiting p { color: var(--ink-2); }

.st-board { display: flex; flex-direction: column; gap: 20px; }
.st-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.st-controls[hidden] { display: none; }
.st-rank { display: inline-flex; padding: 4px; border-radius: 999px; background: var(--soft); }
.st-rank button {
  min-height: 44px; padding: 0 18px; border-radius: 999px; border: 0; background: transparent; color: var(--ink);
  font: 600 15px var(--body); cursor: pointer;
}
.st-rank button[aria-checked="true"] { background: var(--ink); color: var(--paper); }
.st-switch {
  min-height: 44px; display: inline-flex; align-items: center; gap: 10px; padding: 0 14px 0 6px; border-radius: 999px;
  border: 1.5px solid var(--rule); background: var(--paper); color: var(--ink); font: 500 15px var(--body); cursor: pointer;
}
.st-switch-track { width: 40px; height: 24px; border-radius: 12px; background: var(--viz-other); position: relative; transition: background .2s; }
.st-switch-knob { position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%; background: #FFFFFF; transition: left .2s; }
.st-switch[aria-checked="true"] .st-switch-track { background: var(--blue); }
.st-switch[aria-checked="true"] .st-switch-knob { left: 19px; }

.st-board[data-rank="pass"] .st-by-fuel, .st-board[data-rank="fuel"] .st-by-pass,
.st-board[data-rank="fuel"] .st-pass-only, .st-board[data-rank="pass"] .st-fuel-only,
.st-board[data-ranges="off"] .st-ranges-only { display: none; }

.st-explainer {
  display: flex; flex-direction: column; gap: 12px; padding: 18px 20px; border-radius: var(--radius-xl); background: var(--soft);
  animation: st-in .35s ease-out;
}
.st-explainer p { font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.st-explainer p b { color: var(--ink); }
.st-example { display: flex; flex-direction: column; gap: 2px; padding: 4px 0 2px; }
.st-example-bar { position: relative; height: 14px; }
.st-example-bar::before { content: ""; position: absolute; left: 0; right: 0; top: 6px; height: 2px; background: var(--rule); }
.st-example-bar .st-band { top: 5px; height: 4px; }
.st-example-bar .st-dot { top: 2px; }
.st-example-labels { position: relative; height: 18px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.st-example-labels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.st-example-labels .st-end { transform: translateX(-100%); }
.st-example-labels b { color: var(--ink); }
.st-more { align-self: flex-start; font-size: 15px; font-weight: 600; min-height: 44px; display: inline-flex; align-items: center; }

.st-callout {
  display: flex; align-items: center; gap: 14px; padding: 16px 18px; border-radius: var(--radius-xl);
  background: var(--blue-soft); animation: st-in .35s ease-out;
}
.st-callout.soft { background: var(--soft); }
.st-callout p { font-size: 16px; line-height: 1.45; }
.st-callout-glyph { width: 36px; height: 36px; flex: none; color: var(--ink); }

/* the row grid: the scale above the rows uses the same columns, so its ticks
   sit over the row bars; the value column has a fixed width */
.st-grid { display: grid; grid-template-columns: 34px 6px minmax(0, 1fr) 76px; gap: 12px; }
.st-scale { align-items: end; padding: 0 2px 6px; }
.st-ticks { position: relative; height: 26px; }
.st-tick {
  position: absolute; top: 0; transform: translateX(-50%); font-size: 11px; color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.st-tick::after { content: ""; position: absolute; left: 50%; top: 18px; width: 1px; height: 6px; background: var(--viz-interval); }
.st-tick:first-child { transform: none; }
.st-tick:first-child::after { left: 0; }
.st-tick:last-child { transform: translateX(-100%); }
.st-tick:last-child::after { left: auto; right: 0; }
.st-value-head { text-align: right; font-size: 11px; line-height: 1.2; color: var(--muted); }

.st-list, .en-list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.st-item { border-bottom: 1px solid var(--rule); }
.st-item details { border: 0; border-radius: 0; padding: 0; margin: 0; }
.st-item[hidden] { display: none; }
.st-item summary { list-style: none; cursor: pointer; }
.st-item summary::-webkit-details-marker { display: none; }
.st-row { align-items: center; padding: 14px 2px; min-height: 44px; }
.st-row:hover { background: color-mix(in srgb, var(--soft) 60%, transparent); }
.st-pos { font: italic 800 20px/1 "Saira", var(--display); font-variant-numeric: tabular-nums; }
.st-board[data-rank="pass"] .st-pos.lead { color: var(--go-ink); }
.st-livery { width: 6px; height: 40px; border-radius: 2px; background: var(--c); }
.st-who { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.st-name { font-size: 16px; line-height: 1.3; overflow-wrap: anywhere; }
.st-name b { font-weight: 600; }
.st-car { color: var(--ink-2); }
.st-map { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.st-range, .en-range { position: relative; display: block; height: 10px; margin-top: 2px; }
.st-range::before, .en-range::before { content: ""; position: absolute; left: 0; right: 0; top: 4px; height: 2px; background: var(--rule); }
.st-band { position: absolute; top: 3px; height: 4px; background: var(--viz-interval); }
.st-dot { position: absolute; top: 0; width: 10px; height: 10px; margin-left: -5px; border-radius: 50%; background: var(--ink); }
.st-value { text-align: right; font: 700 28px/1 var(--display); font-variant-numeric: tabular-nums; white-space: nowrap; }
.st-board[data-rank="fuel"] .st-value { font-size: 22px; }
.st-value small { display: block; font: 500 12px/1.4 var(--mono); color: var(--muted); }
.st-detail { padding: 4px 2px 18px 52px; display: flex; flex-direction: column; gap: 8px; animation: st-in .35s ease-out; }
.st-cats { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.st-cat { display: grid; grid-template-columns: 96px minmax(0, 1fr) 44px; gap: 10px; align-items: center; font-size: 14px; }
.st-cat .num { text-align: right; color: var(--ink-2); }
.st-cat-bar { height: 10px; border-radius: 3px; background: var(--soft); }
.st-cat-bar span { display: block; height: 10px; border-radius: 3px; background: var(--viz-interval); }
.st-cat.full .st-cat-bar span { background: var(--blue); }
.st-foot { font-size: 14px; color: var(--ink-2); }
.st-all {
  margin-top: 12px; align-self: flex-start; min-height: 44px; padding: 0 20px; border-radius: 999px;
  border: 1.5px solid var(--ink); background: transparent; color: var(--ink); font: 600 15px var(--body); cursor: pointer;
}
.st-all[hidden] { display: none; }
.st-standings { display: flex; flex-direction: column; }
.st-empty { color: var(--ink-2); }
@keyframes st-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .st-explainer, .st-callout, .st-detail, .en-pick { animation: none; }
}

/* entry pages */
.en-page { gap: 40px; }
.en-back { align-self: flex-start; min-height: 44px; display: inline-flex; align-items: center; font-size: 15px; font-weight: 600; }
.en-top { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.en-chips-wrap { position: relative; min-width: 0; }
.en-chips-wrap::after {
  content: ""; position: absolute; top: 0; right: 0; bottom: 2px; width: 40px; pointer-events: none;
  background: linear-gradient(to right, transparent, var(--paper));
}
.en-chips {
  position: relative; display: flex; gap: 8px; overflow-x: auto; padding: 0 40px 2px 0;
  scrollbar-width: none; scroll-snap-type: x proximity;
}
.en-chips::-webkit-scrollbar { display: none; }
.en-chips a {
  flex: none; min-height: 44px; display: inline-flex; align-items: center; gap: 8px; padding: 0 14px 0 10px;
  border-radius: 999px; border: 1.5px solid var(--rule); color: var(--ink); text-decoration: none;
  font: 600 14px/1.3 var(--body); scroll-snap-align: start; max-width: min(320px, 82vw); padding-block: 6px;
}
.en-chips a .st-livery { flex: none; }
.en-chips a .st-livery { width: 5px; height: 20px; }
.en-chips a[aria-current="page"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.en-hero { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 20px; }
.en-card {
  display: flex; flex-direction: column; gap: 14px; padding: 24px; border-radius: 24px; background: var(--tower);
  color: var(--tower-fg); border-top: 6px solid var(--c); min-width: 0;
}
.en-card-who { display: flex; align-items: center; gap: 14px; min-width: 0; }
.en-card-glyph { width: 64px; height: 64px; flex: none; color: var(--tower-fg); }
.en-card h1 { font: italic 800 34px/1 var(--display); overflow-wrap: anywhere; }
.en-card-who p { font-size: 15px; color: var(--tower-mute); margin-top: 4px; }
.en-card code { color: var(--tower-fg); }
.en-pct { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.en-pct b { font: 700 72px/.9 var(--display); color: #3D9BFF; }
.en-pct span { font-size: 16px; color: var(--tower-mute); }
.en-range-wrap { display: flex; flex-direction: column; gap: 4px; }
.en-range { height: 12px; }
.en-range::before { top: 5px; background: var(--tower-rule); }
.en-range .st-band { top: 3px; height: 6px; border-radius: 3px; background: #6B7584; }
.en-range .st-dot { top: 1px; background: #3D9BFF; }
.en-range-wrap p { font-size: 13px; color: var(--tower-mute); }
.en-tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.en-tile { padding: 16px; border-radius: var(--radius-xl); background: var(--soft); display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.en-tile-label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.en-tile-glyph { width: 20px; height: 20px; color: var(--ink); }
.en-tile-value { font: 700 30px/1.1 var(--display); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.en-tile-value.small { font-size: 24px; }
.en-tile-note { font-size: 12px; color: var(--muted); }
.en-season { display: flex; flex-direction: column; gap: 14px; }
.en-season h2 { font: italic 800 clamp(32px, 7vw, 48px)/.95 var(--display); }
.en-season > p { font-size: 16px; line-height: 1.5; color: var(--ink-2); }
.en-season > p.st-eyebrow { font-size: 12px; }
.en-grid { display: flex; flex-direction: column; gap: 12px; }
.en-group { display: flex; flex-direction: column; gap: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--rule); }
.en-group-head { display: flex; align-items: center; gap: 10px; font-size: 15px; }
.en-group-head .roundel { width: 30px; height: 30px; }
.en-group-head .num { margin-left: auto; font-size: 14px; color: var(--ink-2); }
.en-flags { display: flex; flex-wrap: wrap; gap: 6px; }
.en-flag {
  width: 44px; height: 44px; border-radius: 10px; border: 1.5px solid var(--rule); background: var(--paper);
  display: inline-flex; align-items: center; justify-content: center; padding: 0; color: var(--ink); cursor: pointer;
}
.en-flag.dnf { border-color: color-mix(in srgb, var(--red) 35%, var(--paper)); background: color-mix(in srgb, var(--red) 5%, var(--paper)); color: var(--red); }
.en-flag.none { border-style: dashed; background: transparent; cursor: default; }
.en-flag[aria-pressed="true"] { border-color: var(--blue); background: var(--blue-soft); }
.en-flag-glyph { width: 18px; height: 18px; }
.en-pick { padding: 18px 20px; border-radius: var(--radius-xl); background: var(--soft); display: flex; flex-direction: column; gap: 4px; animation: st-in .35s ease-out; }
.en-pick[data-result="dnf"] { background: color-mix(in srgb, var(--red) 5%, var(--paper)); }
.en-pick-title { font-size: 17px; font-weight: 600; }
.en-pick-line { font-size: 15px; color: var(--ink-2); }
.en-next { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(280px, 100%), 1fr)); gap: 14px; }
.en-next a {
  display: flex; flex-direction: column; gap: 6px; padding: 24px; border-radius: 22px; background: var(--soft);
  color: var(--ink); text-decoration: none;
}
.en-next a.en-next-dark { background: var(--tower); color: var(--tower-fg); }
.en-next span { font: 500 12px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.en-next b { font: italic 800 32px/1 var(--display); }
.en-list-row {
  display: grid; grid-template-columns: 34px 6px minmax(0, 1fr) 76px; gap: 12px; align-items: center;
  padding: 12px 2px; border-bottom: 1px solid var(--rule); color: var(--ink); text-decoration: none; min-height: 44px;
}
.en-list-row:hover { background: color-mix(in srgb, var(--soft) 60%, transparent); color: var(--ink); }
.en-list-row .st-value { font-size: 22px; }
.en-partial { margin: 8px 0 0; }
.en-partial summary { min-height: 44px; display: flex; align-items: center; }
.en-partial .en-list { margin-top: 8px; }
@media (min-width: 1100px) {
  .en-hero { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); }
}
"""
