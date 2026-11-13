"""The races grid and one page per calendar race (PDW-06).

``/races/`` rebuilds ``design/web/Races.dc.html``: one card per calendar race,
challenge chips with counts and roundels, hardest first (by avg pass rate) or
A to Z, the first 12 cards and then "Show all". ``/races/<race-id>/`` rebuilds
``design/web/Race.dc.html`` for every calendar race: the header roundel and
stats, the lap funnel (All runs, By car, By driver; drivers with
:data:`DRIVER_MIN_RUNS` or more runs) with playback at
:data:`PLAYBACK_MS` per checkpoint, the classification of each entry's first
run (lap or fuel order, top 10 then all) and its retirements, the briefing as
the track card's ``[briefing] show`` allows, and the track's staged files and
sealed circuit from ``leaderboard.py track-facts``.

Every number comes from the site data files (``data.py``): the pages read the
same values ``data/races.json`` and ``data/races/<race-id>.json`` hold.
Checkpoints are named only as the data names them, so a checkpoint the track
card counts or hides never reaches a page.

The pages work without JavaScript: every card, station and finisher is in the
HTML. The script (:data:`SCRIPT`, shipped inside the one ``paddock.js``) adds
the filters, orders, "Show all" toggles, funnel views and playback.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import leaderboard as lb

from . import data as site_data
from . import glyphs, layout, views
from .routes import SITE_URL, Build, Route

#: The grid shows this many cards before "Show all".
GRID_FIRST = 12
#: The classification shows this many finishers before "Show all".
CLASSIFICATION_FIRST = 10
#: The By driver view lists drivers with at least this many runs recording checks.
DRIVER_MIN_RUNS = 5
#: Playback advances one checkpoint every this many milliseconds.
PLAYBACK_MS = 1600
#: The funnel ribbon's full width, in pixels, for every run still racing.
RIBBON_PX = 46
#: Red marks an avg pass rate under this share on a card.
RED_UNDER = 0.5

GRID_FILE = "races/index.html"

#: Car (harness) names as the vocabulary writes them, and their livery token.
CAR_NAMES = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "pi": "Pi",
    "antigravity": "Antigravity",
}
LIVERY = {
    "claude-code": "car-cc",
    "codex": "car-codex",
    "opencode": "car-opencode",
    "pi": "car-pi",
    "antigravity": "car-agy",
}

#: What each difficulty band means, from ``leaderboard.DIFFICULTY_EDGES``.
_EDGES = lb.DIFFICULTY_EDGES


def _band_text(band: int) -> str:
    if band == 1:
        return f"{_EDGES[0]:.0%} or more of runs finish"
    if band == len(_EDGES) + 1:
        return f"under {_EDGES[-1]:.0%} of runs finish"
    return f"{_EDGES[band - 1]:.0%} to {_EDGES[band - 2]:.0%} of runs finish"


# ---------------------------------------------------------------- formatting

esc = layout.esc


def race_file(race_id: str) -> str:
    return f"races/{race_id}/index.html"


def plural(count: int, one: str, many: str = "") -> str:
    return f"{count:,} {one if count == 1 else many or one + 's'}"


def rate_text(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    percent = rate * 100
    return f"{percent:.1f}%" if percent < 10 else f"{percent:.0f}%"


def lap_text(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def usd_text(usd: float | None, digits: int = 3) -> str | None:
    return None if usd is None else f"${usd:.{digits}f}"


def card_usd(usd: float | None) -> str:
    if usd is None:
        return "no price"
    return f"{usd_text(usd, 3 if usd < 0.1 else 2)} a run"


def challenge_label(challenge: str) -> str:
    if challenge in glyphs.RACE_TYPES:
        return glyphs.RACE_TYPES[challenge][0]
    return site_data.race_name(challenge, "")


def roundel(challenge: str, *, size: str = "", labelled: bool = False) -> str:
    """The race type's roundel; a challenge without a mark draws the bare frame."""
    if challenge in glyphs.RACE_TYPES:
        return glyphs.roundel(challenge, labelled=labelled, size=size)
    classes = "roundel" + (f" roundel-{size}" if size else "")
    return glyphs.icon(
        "roundel", classes, challenge_label(challenge) if labelled else ""
    )


def car_name(car: str) -> str:
    return CAR_NAMES.get(car, car)


def livery(car: str) -> str:
    return f"var(--{LIVERY.get(car, 'tower-mute')})"


def _attr(value: Any) -> str:
    """A JSON value for a ``data-*`` attribute."""
    return esc(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _kicker(glyph: str, text: str) -> str:
    return (
        '<div class="rc-kicker">'
        + (glyphs.icon(glyph) if glyph else "")
        + f"<span>{esc(text)}</span></div>"
    )


# ---------------------------------------------------------------- the grid


def _calendar(files: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [race for race in files["data/races.json"]["races"] if race["calendar"]]


def _hardest(races: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        races, key=lambda race: (race["rate"], race["shortName"].lower(), race["id"])
    )


def _challenges(races: Sequence[Mapping[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for race in races:
        counts[race["challenge"]] = counts.get(race["challenge"], 0) + 1
    known = [key for key in glyphs.RACE_TYPES if key in counts]
    return [(key, counts[key]) for key in known + sorted(set(counts) - set(known))]


def _stages(race: Mapping[str, Any]) -> str:
    stages = race["stages"]
    rates = [
        checkpoint["clearRate"]
        for checkpoint in (stages["checkpoints"] if stages else [])
        if checkpoint["clearRate"] is not None
    ]
    if not stages or not stages["checkpoints"]:
        more = stages["moreChecks"] if stages else 0
        text = (
            f"Stages: {plural(more, 'check')}, none named on the track card."
            if more
            else "Stages not recorded yet."
        )
        return f'<p class="rc-nostages">{esc(text)}</p>'
    weakest = min(rates) if rates else None
    rows = []
    for checkpoint in stages["checkpoints"]:
        rate = checkpoint["clearRate"]
        weak = rate is not None and rate == weakest and rate < 1
        width = 0 if rate is None else rate * 100
        shown = "not recorded" if rate is None else f"{rate * 100:.0f}%"
        rows.append(
            f'<li class="rc-stage{" is-weak" if weak else ""}">'
            f'<span class="rc-stage-name">{esc(checkpoint["label"])}</span>'
            f'<span class="rc-stage-bar" aria-hidden="true"><span style="width:{width:.1f}%"></span></span>'
            f'<span class="rc-stage-pct">{esc(shown)}</span></li>'
        )
    more = (
        f'<p class="rc-more-checks">and {plural(stages["moreChecks"], "more check")}</p>'
        if stages["moreChecks"]
        else ""
    )
    return (
        '<ul class="rc-stages" aria-label="Share of runs that cleared each stage">'
        + "".join(rows)
        + "</ul>"
        + more
    )


def race_card(race: Mapping[str, Any], href: str) -> str:
    rate = race["rate"]
    rate_attr = f"{rate:.6f}" if rate is not None else ""
    red = rate is not None and rate < RED_UNDER
    lap = lap_text(race["medianLapSeconds"])
    about = race.get("about")
    description = (
        about.get("what") if isinstance(about, Mapping) else None
    ) or race.get("title")
    return (
        f'<a class="rc-card" href="{esc(href)}" data-challenge="{esc(race["challenge"])}" '
        f'data-rate="{rate_attr}" data-name="{esc(race["shortName"].lower())}">'
        '<div class="rc-card-head">'
        + roundel(race["challenge"], size="lg")
        + '<div class="rc-card-name">'
        f'<span class="rc-cat">{esc(challenge_label(race["challenge"]))}</span>'
        f'<span class="rc-short">{esc(race["shortName"])}</span></div>'
        '<div class="rc-card-rate">'
        f'<span class="rc-rate{" is-red" if red else ""}">{esc(rate_text(rate))}</span>'
        f'<span class="rc-of">{race["k"]:,} of {plural(race["n"], "run")}</span></div></div>'
        + (f'<p class="rc-title">{esc(description)}</p>' if description else "")
        + _stages(race)
        + '<p class="rc-stats">'
        f"<span>{glyphs.icon('entry')}{esc(plural(race['entries'], 'entry', 'entries'))}</span>"
        f"<span>{glyphs.icon('lap-time')}{esc(f'{lap} lap' if lap else 'lap n/a')}</span>"
        f"<span>{glyphs.icon('fuel')}{esc(card_usd(race['medianCostUsd']))}</span>"
        "</p></a>"
    )


def grid_page(build: Build) -> str:
    files = build.data
    head = files["data/races.json"]
    calendar = _hardest(_calendar(files))
    count = len(calendar)
    waiting = head["state"] == site_data.AWAITING
    if waiting:
        title, lede = (
            "Races are on their way.",
            "No results document is trusted yet, so no race is on the calendar. "
            "The races appear here once a maintainer signs off a results document.",
        )
    elif not count:
        title, lede = (
            "No race is on the calendar yet.",
            f"A race joins the calendar at {lb.DIFFICULTY_MIN_RUNS} counted runs.",
        )
    else:
        challenges = _challenges(calendar)
        title = f"{plural(count, 'race')}. Pick one."
        lede = (
            f"The calendar holds {plural(count, 'race')} in "
            f"{plural(len(challenges), 'challenge')}. Each card shows how the whole "
            "field did, and the stages where runs drop out."
        )
    body = [
        '<div class="page-hero rc-hero">'
        + glyphs.icon("race-calendar", "hero-glyph")
        + f'<span class="eyebrow">{layout.PRODUCT}</span><h1>{esc(title)}</h1>'
        f'<p class="lede">{esc(lede)}</p></div>'
    ]
    if count:
        chips = [
            '<button type="button" class="rc-chip" role="radio" aria-checked="true" '
            f'data-challenge="all">All <span class="rc-chip-n">{count}</span></button>'
        ]
        for challenge, number in _challenges(calendar):
            chips.append(
                '<button type="button" class="rc-chip" role="radio" aria-checked="false" '
                f'data-challenge="{esc(challenge)}">{roundel(challenge)}'
                f'{esc(challenge_label(challenge))} <span class="rc-chip-n">{number}</span></button>'
            )
        body.append(
            '<div class="rc-controls" data-races-controls hidden>'
            '<div class="rc-scroll"><div class="rc-hscroll" role="radiogroup" aria-label="Challenge">'
            + "".join(chips)
            + "</div></div>"
            '<div class="rc-seg" role="radiogroup" aria-label="Order">'
            '<button type="button" role="radio" aria-checked="true" data-sort="hard">Hardest first</button>'
            '<button type="button" role="radio" aria-checked="false" data-sort="name">A to Z</button>'
            "</div></div>"
        )
        body.append(
            f'<div class="rc-grid" data-races-grid data-first="{GRID_FIRST}">'
            + "".join(race_card(race, f"{race['id']}/index.html") for race in calendar)
            + "</div>"
            '<p class="rc-more-wrap"><button type="button" class="btn ghost rc-more" '
            "data-races-more hidden>Show all races</button></p>"
        )
        off = len(head["races"]) - count
        body.append(
            '<p class="rc-note">Every counted run of each race at its current version; '
            "infrastructure faults are left out. Hardest first orders races by avg pass "
            "rate, lowest first. A stage's percentage is the share of runs that cleared "
            "it. Lap and fuel are the median run."
            + (
                f" {plural(off, 'more race has', 'more races have')} fewer than "
                f"{lb.DIFFICULTY_MIN_RUNS} counted runs and joins the calendar at "
                f"{lb.DIFFICULTY_MIN_RUNS}."
                if off
                else ""
            )
            + "</p>"
        )
    return layout.page(
        routes=build.routes,
        current=GRID_FILE,
        title="Races",
        body="".join(body),
        url=SITE_URL + "races/",
    )


# ---------------------------------------------------------------- the funnel


def _view(
    mode: str,
    key: str,
    counts: Sequence[int],
    labels: Sequence[str],
    more_checks: int,
) -> dict[str, Any]:
    """One funnel view: station widths, stats and panel copy, all from ``counts``.

    ``counts`` is the data layer's funnel: runs at the start, after each named
    checkpoint, and at the finish.
    """
    start = counts[0]
    who = {"all": "", "car": f" in {car_name(key)}", "driver": f" by {key}"}[mode]
    drops = [counts[k - 1] - counts[k] for k in range(1, len(labels) + 1)]
    worst = 0
    if drops and max(drops) > 0:
        worst = drops.index(max(drops)) + 1
    stats, titles, bodies = [], [], []
    stats.append(f"{plural(start, 'run')} start")
    titles.append("Lights out")
    bodies.append(
        f"Every run starts with the same briefing and the same files. "
        f"The ribbon is all {plural(start, 'run')}{who}."
    )
    for k, label in enumerate(labels, 1):
        before, after = counts[k - 1], counts[k]
        if before:
            stats.append(f"{after / before:.0%} clear · {before - after:,} retire")
        else:
            stats.append("no one left")
        titles.append(f"Checkpoint {k} · {label}")
        text = (
            f"{after:,} of the {plural(before, 'run')} still racing{who} clear it."
            if before
            else f"No run{who} is still racing here."
        )
        if k == worst:
            text += f" Most runs{who} that retire go out here."
        bodies.append(text)
    finish = counts[-1]
    last = counts[-2]
    stats.append(f"{finish:,} of {start:,} finish")
    titles.append("The finish")
    text = (
        f"{finish:,} of {plural(start, 'run')}{who} clear every checkpoint and finish."
        if finish
        else f"No run{who} finishes."
    )
    if last > finish:
        text += (
            f" {plural(last - finish, 'run')} cleared every named checkpoint and "
            "still did not finish"
            + (
                f": the track card names {plural(more_checks, 'more check')} by count only."
                if more_checks
                else "."
            )
        )
    text += " The stewards never told any run what they check."
    bodies.append(text)
    return {
        "mode": mode,
        "key": key,
        "n": start,
        "widths": [
            round(RIBBON_PX * count / start, 1) if start else 0 for count in counts
        ],
        "stats": stats,
        "titles": titles,
        "bodies": bodies,
        "worst": worst,
        "caption": f"Checkpoints from the {plural(start, 'run')}{who} that record their checks"
        + (", in any car." if mode == "driver" else "."),
    }


def funnel_views(race: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every view the lap section can show: all runs, each car, and each driver
    with at least :data:`DRIVER_MIN_RUNS` runs recording checks."""
    funnel = race["funnel"]
    labels = race["checkpoints"] or []
    more = race["moreChecks"] or 0
    views = [_view("all", "", funnel["all"], labels, more)]
    for car, counts in funnel["byCar"].items():
        views.append(_view("car", car, counts, labels, more))
    for driver, counts in funnel["byDriver"].items():
        if counts[0] >= DRIVER_MIN_RUNS:
            views.append(_view("driver", driver, counts, labels, more))
    return views


def _stations(view: Mapping[str, Any], labels: Sequence[str]) -> str:
    names = ["Lights out", *labels, "Checkered flag"]
    last = len(names) - 1
    selected = view["worst"] or last
    rows = []
    for k, name in enumerate(names):
        top = view["widths"][k - 1] if k else 0
        bottom = view["widths"][k] if k < last else 0
        mark = "GO" if k == 0 else ("" if k == last else str(k))
        dot = (
            glyphs.icon("flag-checkered", "glyph rc-dot-flag", "Checkered flag")
            if k == last
            else esc(mark)
        )
        classes = "rc-station is-lit" + (
            " is-worst" if k and k == view["worst"] else ""
        )
        rows.append(
            f'<li class="{classes}" data-station="{k}">'
            '<div class="rc-rail" aria-hidden="true">'
            f'<span class="rc-ribbon rc-ribbon-top{" is-blank" if k == 0 else ""}" style="width:{top}px"></span>'
            f'<span class="rc-dot">{dot}</span>'
            f'<span class="rc-ribbon rc-ribbon-bottom{" is-blank" if k == last else ""}" style="width:{bottom}px"></span>'
            "</div>"
            f'<button type="button" class="rc-station-card" aria-pressed="{"true" if k == selected else "false"}">'
            f'<span class="rc-station-name">{esc(name)}</span>'
            f'<span class="rc-station-stat">{esc(view["stats"][k])}</span></button></li>'
        )
    return '<ol class="rc-stations">' + "".join(rows) + "</ol>"


def _lap(race: Mapping[str, Any]) -> str:
    head = (
        _kicker("track-map", "The lap")
        + '<h2 class="rc-h2">Watch the field thin out.</h2>'
    )
    if not race["funnel"]:
        return (
            '<section class="rc-section" id="lap">'
            + head
            + '<p class="rc-copy">The lap shows where runs retire, checkpoint by checkpoint. '
            "The runs of this race at this version do not record their checks yet, so its "
            "funnel appears once they do.</p></section>"
        )
    labels = race["checkpoints"] or []
    views = funnel_views(race)
    view = views[0]
    selected = view["worst"] or len(labels) + 1
    modes = [("all", "All runs", view["n"])]
    if any(v["mode"] == "car" for v in views):
        modes.append(("car", "By car", None))
    if any(v["mode"] == "driver" for v in views):
        modes.append(("driver", "By driver", None))
    mode_buttons = "".join(
        f'<button type="button" role="radio" aria-checked="{"true" if mode == "all" else "false"}" '
        f'data-mode="{mode}">{esc(name)}'
        + (f' <span class="rc-chip-n">{number}</span>' if number is not None else "")
        + "</button>"
        for mode, name, number in modes
    )
    options = "".join(
        f'<button type="button" class="rc-chip rc-option" aria-pressed="false" '
        f'data-mode="{v["mode"]}" data-key="{esc(v["key"])}" hidden>'
        + (
            f'<span class="rc-swatch" style="background:{livery(v["key"])}" aria-hidden="true"></span>'
            if v["mode"] == "car"
            else ""
        )
        + f"{esc(car_name(v['key']) if v['mode'] == 'car' else v['key'])} "
        f'<span class="rc-chip-n">{v["n"]}</span></button>'
        for v in views[1:]
    )
    worst = (
        f'<p class="rc-worst" data-lap-worst>The worst checkpoint is '
        f"<b>{esc(labels[view['worst'] - 1])}</b>: "
        f"{esc(view['stats'][view['worst']])}.</p>"
        if view["worst"]
        else '<p class="rc-worst" data-lap-worst>No checkpoint retires a run.</p>'
    )
    return (
        f'<section class="rc-section rc-lap" id="lap" data-funnel="{_attr(views)}" '
        f'data-playback-ms="{PLAYBACK_MS}">'
        '<div class="rc-lap-copy">'
        + head
        + '<p class="rc-copy">The blue ribbon is every run still racing. A run retires at '
        "the first checkpoint it misses. Tap a checkpoint to see how many cleared it.</p>"
        '<div class="rc-lap-controls" data-lap-controls hidden>'
        f'<div class="rc-seg" role="radiogroup" aria-label="Show the lap for">{mode_buttons}</div>'
        f'<div class="rc-scroll"><div class="rc-hscroll" data-lap-options>{options}</div></div>'
        '<button type="button" class="btn solid rc-play" data-lap-play>Play the race</button>'
        "</div>" + worst + '<div class="rc-panel" data-lap-panel aria-live="polite">'
        f"<b>{esc(view['titles'][selected])}</b><span>{esc(view['bodies'][selected])}</span></div>"
        f'<p class="rc-caption" data-lap-caption>{esc(view["caption"])}</p>'
        "</div>" + _stations(view, labels) + "</section>"
    )


# ---------------------------------------------------------------- the result


def _finisher(position: int, row: Mapping[str, Any]) -> str:
    lap = lap_text(row["lapSeconds"])
    usd = usd_text(row["costUsd"])
    lap_shown = lap or "n/a"
    usd_shown = usd or "no price"
    lap_second = "lap n/a" if row["lapSeconds"] is None else f"{lap} lap"
    usd_second = usd_shown
    return (
        f'<li class="rc-finisher" data-lap="{"" if row["lapSeconds"] is None else row["lapSeconds"]}" '
        f'data-usd="{"" if row["costUsd"] is None else row["costUsd"]}" '
        f'data-lap-text="{esc(lap_shown)}" data-usd-text="{esc(usd_shown)}" '
        f'data-lap-second="{esc(lap_second)}" '
        f'data-usd-second="{esc(usd_second)}">'
        f'<span class="rc-pos{" is-podium" if position <= 3 else ""}">P{position}</span>'
        f'<span class="rc-livery" style="background:{livery(row["car"])}" aria-hidden="true"></span>'
        '<span class="rc-who">'
        f'<span class="rc-driver">{esc(row["driver"])} <span>in {views.car_tag(row["car"])}</span></span>'
        f'<span class="rc-map">engine map {views.map_tag(row["map"], row["car"])}</span></span>'
        '<span class="rc-values">'
        f'<span class="rc-main">{esc(lap_shown)}</span>'
        f'<span class="rc-second">{esc(usd_second)}</span></span></li>'
    )


def _retirements(classification: Mapping[str, Any]) -> str:
    retired = classification["retired"]
    head = (
        '<div class="rc-dnf-head">'
        + glyphs.icon("dnf")
        + f"<h3>Where the {'other ' if classification['finishers'] else ''}"
        f"{retired:,} retired</h3></div>"
    )
    blurb = '<p class="rc-copy">A DNF (did not finish) retires at the first checkpoint it misses.</p>'
    if not retired:
        return ""
    data = classification["retirements"]
    if data is None:
        return (
            '<div class="rc-dnf">'
            + head
            + blurb
            + '<p class="rc-copy">These runs do not record their checks, so where '
            "each one retired is not known yet.</p></div>"
        )
    rows = [(item["label"], item["runs"]) for item in data["checkpoints"]]
    for key, label in (
        ("undisclosed", "At checks the track card does not name"),
        ("unclassified", "Cleared every check and still did not pass"),
        ("unrecorded", "Checks not recorded"),
    ):
        if data[key]:
            rows.append((label, data[key]))
    top = max((runs for _, runs in rows), default=0)
    items = "".join(
        f'<li class="rc-dnf-row{" is-worst" if runs and runs == top else ""}">'
        f'<span class="rc-dnf-name">{esc(label)}</span>'
        f'<span class="rc-dnf-bar" aria-hidden="true"><span style="width:{(runs / top * 100) if top else 0:.1f}%"></span></span>'
        f'<span class="rc-dnf-n">{runs:,}</span></li>'
        for label, runs in rows
    )
    return (
        '<div class="rc-dnf">'
        + head
        + blurb
        + f'<ul class="rc-dnf-rows">{items}</ul></div>'
    )


def _result(race: Mapping[str, Any]) -> str:
    classification = race["classification"]
    finishers = classification["finishers"]
    starters = classification["starters"]
    heading = (
        f"{len(finishers):,} finished. {classification['retired']:,} did not."
        if finishers
        else "Nobody finished on a first run."
    )
    body = [
        '<section class="rc-section rc-result" id="result">',
        _kicker("flag-checkered", "The result · every entry's first run"),
        f'<h2 class="rc-h2">{esc(heading)}</h2>',
        '<p class="rc-copy">One result per entry: its first run of this race, as the '
        f"standings count it ({plural(starters, 'entry', 'entries')} started). Finishers "
        "are classified by lap time, from lights out to hand-in, or by what the run cost.</p>",
    ]
    if finishers:
        body.append(
            '<div class="rc-seg" role="radiogroup" aria-label="Classify finishers by" '
            "data-class-order hidden>"
            '<button type="button" role="radio" aria-checked="true" data-order="lap">'
            + glyphs.icon("lap-time")
            + "Lap time</button>"
            '<button type="button" role="radio" aria-checked="false" data-order="usd">'
            + glyphs.icon("fuel-bill")
            + "Fuel</button></div>"
            '<ol class="rc-classification" data-classification>'
            + "".join(_finisher(k, row) for k, row in enumerate(finishers, 1))
            + "</ol>"
        )
        if len(finishers) > CLASSIFICATION_FIRST:
            body.append(
                '<p><button type="button" class="btn ghost" data-class-more hidden '
                f'data-first="{CLASSIFICATION_FIRST}" data-total="{len(finishers)}">'
                f"Show all {len(finishers):,} finishers</button></p>"
            )
    body.append(_retirements(classification))
    body.append("</section>")
    return "".join(body)


# ---------------------------------------------------------------- briefing and track


def _brief_text(text: str) -> str:
    return f'<div class="rc-brief-text">{esc(text)}</div>'


def _briefing(race: Mapping[str, Any], brief: Mapping[str, str] | None) -> str:
    show = race["briefing"]["show"]
    words = race["briefing"]["words"]
    if show == "none":
        return (
            '<div class="rc-brief">'
            + _kicker("briefing", "The briefing")
            + '<p class="rc-copy">The track card keeps this race\'s briefing off the site.</p></div>'
        )
    if brief is None:
        return (
            '<div class="rc-brief">'
            + _kicker("briefing", "The briefing")
            + '<p class="rc-copy">This race has no public briefing file.</p></div>'
        )
    count = f" · {plural(words, 'word')}" if words else ""
    if brief["show"] == "excerpt":
        return (
            '<div class="rc-brief">'
            + _kicker("briefing", f"The briefing · the opening{count}")
            + _brief_text(brief["lead"])
            + '<p class="rc-caption">The track card shows only the opening of this briefing.</p></div>'
        )
    rest = (
        '<details class="rc-brief-more"><summary>Read it all</summary>'
        + _brief_text(brief["rest"])
        + "</details>"
        if brief["rest"]
        else ""
    )
    return (
        '<div class="rc-brief">'
        + _kicker("briefing", f"The briefing · word for word{count}")
        + _brief_text(brief["lead"])
        + rest
        + "</div>"
    )


def _number(value: float) -> str:
    return f"{value:g}"


def sealed_circuit(conditions: Mapping[str, Any]) -> list[str]:
    """The sealed-circuit chips, from the conditions ``track-facts`` reports."""
    chips = [f"{_number(conditions['cpus'])} processor cores"]
    memory = conditions["memory_mb"]
    chips.append(
        f"{_number(memory / 1024)} GiB memory"
        if memory % 1024 == 0
        else f"{memory:,} MB memory"
    )
    chips.append(f"{conditions['pids']:,} processes at most")
    limit = conditions["time_limit_seconds"]
    chips.append(
        f"{_number(limit / 60)}-minute time limit"
        if limit % 60 == 0
        else f"{_number(limit)} s time limit"
    )
    if conditions["read_only_root"]:
        chips.append("read-only root")
    if conditions["dropped_capabilities"] == ["ALL"]:
        chips.append("every capability dropped")
    chips.append(f"network: {conditions['network']}")
    chips.append("checks hidden from the driver")
    return chips


def _track(race: Mapping[str, Any]) -> str:
    track = race["track"]
    if track:
        files = (
            _kicker(
                "track-map",
                f"On the track · {plural(track['files'], 'file')}, {plural(track['lines'], 'line')}",
            )
            + '<ul class="rc-chips-static">'
            f'<li class="rc-file">{esc(plural(track["files"], "staged file"))}</li>'
            f'<li class="rc-file">{esc(plural(track["lines"], "line"))} to read</li>'
            + (
                f'<li class="rc-file">{esc(plural(race["briefing"]["words"], "word"))} of briefing</li>'
                if race["briefing"]["words"]
                else ""
            )
            + "</ul>"
        )
    else:
        files = (
            _kicker("track-map", "On the track")
            + '<p class="rc-copy">The staged files are counted once this race has a track card '
            "that <code>track-facts</code> accepts.</p>"
        )
    chips = "".join(
        f"<li>{esc(chip)}</li>" for chip in sealed_circuit(race["conditions"])
    )
    return (
        '<div class="rc-track">'
        + files
        + _kicker("fence", "The sealed circuit")
        + f'<ul class="rc-chips-static rc-circuit">{chips}</ul>'
        '<p class="rc-caption">The standalone runner\'s defaults, as '
        "<code>leaderboard.py track-facts</code> reports them.</p></div>"
    )


# ---------------------------------------------------------------- one race


def _header(race: Mapping[str, Any], calendar_size: int) -> str:
    difficulty = race["difficulty"]
    bars = "".join(
        f'<span class="{"is-on" if difficulty and k <= difficulty else ""}"></span>'
        for k in range(1, len(_EDGES) + 2)
    )
    if difficulty:
        rating = (
            f'<div class="rc-bars" role="img" aria-label="Difficulty {difficulty} of 5">{bars}</div>'
            f"<span><b>Difficulty {difficulty} of 5</b><br>{esc(_band_text(difficulty))}</span>"
        )
    else:
        rating = (
            f'<div class="rc-bars" role="img" aria-label="Difficulty not rated">{bars}</div>'
            f"<span><b>Difficulty not rated</b><br>fewer than {lb.DIFFICULTY_MIN_RUNS} counted runs</span>"
        )
    label = challenge_label(race["challenge"])
    return (
        f'<p class="rc-back"><a href="../index.html">All {plural(calendar_size, "race")}</a></p>'
        '<section class="rc-head">'
        '<div class="rc-head-id">'
        '<div class="rc-type">'
        + roundel(race["challenge"], size="xl", labelled=True)
        + f'<span class="rc-type-text">{esc(label)}<br>version {race["version"]}</span></div>'
        f"<h1>{esc(race['shortName'])}</h1>"
        + (f'<p class="lede">{esc(race["title"])}</p>' if race["title"] else "")
        + (f'<p class="rc-pitch">{esc(race["pitch"])}</p>' if race["pitch"] else "")
        + "</div>"
        '<div class="rc-head-stats">'
        '<div class="rc-tile rc-tile-dark">'
        f'<span class="rc-big">{esc(rate_text(race["rate"]))}</span>'
        f"<span>{race['k']:,} of {plural(race['n'], 'run')} finished</span>"
        f'<span class="rc-range">95% range {race["lo"] * 100:.0f}% to {race["hi"] * 100:.0f}%</span></div>'
        f'<div class="rc-tile rc-tile-soft">{rating}</div>'
        "</div></section>"
    )


def _about(race: Mapping[str, Any]) -> str:
    about = race.get("about")
    if not about:
        return ""
    return (
        '<section class="rc-section" aria-label="About this race">'
        + "".join(
            f'<p class="rc-copy"><strong>{label}:</strong> {esc(about[key])}</p>'
            for key, label in (
                ("what", "What it is"),
                ("hard", "What makes it hard"),
            )
        )
        + "</section>"
    )


def race_page(
    race: Mapping[str, Any],
    brief: Mapping[str, str] | None,
    calendar_size: int,
    routes: Sequence[Route],
) -> str:
    body = (
        _header(race, calendar_size)
        + _about(race)
        + _lap(race)
        + _result(race)
        + '<section class="rc-section rc-split" id="briefing">'
        + _briefing(race, brief)
        + _track(race)
        + "</section>"
        '<section class="rc-cta">'
        f'<span class="rc-cta-kicker">{esc(plural(race["classification"]["starters"], "entry", "entries"))} raced it</span>'
        '<p class="rc-cta-line">Think yours can finish it?</p>'
        '<a class="btn rc-cta-btn" href="../../race-it-yourself/index.html">Race it yourself</a>'
        "</section>"
    )
    current = race_file(race["id"])
    return layout.page(
        routes=routes,
        current=current,
        nav_current=GRID_FILE,
        title=f"{race['shortName']} · Races",
        body=body,
        url=SITE_URL + f"races/{race['id']}/",
    )


def race_pages(built: site_data.Site, routes: Sequence[Route]) -> dict[str, str]:
    """``{output file: page}`` for every calendar race."""
    calendar = _calendar(built.files)
    by_id = {race.id: race for race in built.data.calendar}
    pages = {}
    for summary in calendar:
        race = built.files[f"data/races/{summary['id']}.json"]
        pages[race_file(summary["id"])] = race_page(
            race,
            site_data.briefing(by_id[summary["id"]]),
            len(calendar),
            routes,
        )
    return pages


# ---------------------------------------------------------------- styles and script

CSS = """
/* ---------- races grid and race pages (PDW-06) ---------- */
.roundel.roundel-xl { width: 96px; height: 96px; }
.rc-hero .hero-glyph { width: 40px; height: 40px; }
.rc-controls { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; margin-bottom: 20px; min-width: 0; }
.rc-controls[hidden], .rc-lap-controls[hidden], [data-class-order][hidden] { display: none; }
.rc-scroll { position: relative; min-width: 0; }
.rc-scroll::after {
  content: ""; position: absolute; top: 0; right: 0; bottom: 2px; width: 40px; pointer-events: none;
  background: linear-gradient(to right, transparent, var(--paper)); opacity: 0; transition: opacity .2s var(--ease);
}
.rc-scroll[data-more="true"]::after { opacity: 1; }
.rc-hscroll { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; }
.rc-hscroll::-webkit-scrollbar { display: none; }
.rc-chip {
  flex: none; min-height: 44px; display: inline-flex; align-items: center; gap: 8px; padding: 0 14px 0 10px;
  border-radius: 999px; border: 1.5px solid var(--rule); background: var(--paper); color: var(--ink);
  font: 600 15px/1 var(--body); cursor: pointer;
}
.rc-chip[aria-checked="true"], .rc-chip[aria-pressed="true"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.rc-chip[aria-checked="true"] .roundel { color: var(--paper); }
.rc-chip-n { font-weight: 400; opacity: .7; }
.rc-swatch { width: 5px; height: 18px; border-radius: 2px; }
.rc-seg { align-self: flex-start; justify-self: start; display: inline-flex; flex-wrap: wrap; padding: 4px; border-radius: 999px; background: var(--soft); }
.rc-seg button {
  min-height: 44px; display: inline-flex; align-items: center; gap: 8px; padding: 0 16px; border-radius: 999px;
  border: 0; background: transparent; color: var(--ink); font: 600 15px/1 var(--body); cursor: pointer;
}
.rc-seg button[aria-checked="true"] { background: var(--ink); color: var(--paper); }
.rc-seg .glyph { width: 18px; height: 18px; }

.rc-grid { --tracks: 1; display: grid; grid-template-columns: repeat(var(--tracks), minmax(0, 1fr)); gap: 14px; }
.rc-grid > .rc-card { grid-column: span var(--span, 1); }
@media (min-width: 720px) { .rc-grid { --tracks: 2; } }
@media (min-width: 1100px) { .rc-grid { --tracks: 6; } .rc-grid > .rc-card { --span: 2; } }
.rc-card {
  display: flex; flex-direction: column; gap: 12px; padding: 20px; border-radius: 22px; border: 1.5px solid var(--rule);
  background: var(--paper); color: var(--ink); text-decoration: none; min-width: 0;
  transition: border-color .2s var(--ease), transform .12s var(--ease);
}
.rc-card:hover { border-color: var(--blue); color: var(--ink); }
.rc-card[hidden] { display: none; }
.rc-card-head { display: flex; align-items: center; gap: 12px; }
.rc-card-name { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.rc-cat { font: 500 11px/1.3 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--ink-2); }
.rc-short { font: italic 800 26px/1 var(--display); overflow-wrap: anywhere; }
.rc-card-rate { display: flex; flex-direction: column; align-items: flex-end; text-align: right; }
.rc-rate { font: 700 34px/1 var(--display); font-variant-numeric: tabular-nums; }
.rc-rate.is-red { color: var(--red); }
.rc-of { font-size: 12px; color: var(--muted); white-space: nowrap; }
.rc-title { margin: 0; font-size: 15px; line-height: 1.4; color: var(--ink-2); }
.rc-stages { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.rc-stage { display: grid; grid-template-columns: minmax(0, 1fr) 72px 44px; gap: 8px; align-items: center; font-size: 13px; }
.rc-stage-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rc-stage-bar { height: 6px; border-radius: 3px; background: var(--viz-empty); }
.rc-stage-bar span { display: block; height: 6px; border-radius: 3px; background: var(--blue); }
.rc-stage-pct { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-2); }
.rc-stage.is-weak .rc-stage-bar span { background: var(--red); }
.rc-stage.is-weak .rc-stage-pct { color: var(--red-ink); font-weight: 700; }
.rc-nostages, .rc-more-checks { margin: 0; font-size: 13px; color: var(--muted); }
.rc-stats {
  margin: auto 0 0; display: flex; flex-wrap: wrap; gap: 8px 14px; padding-top: 10px; border-top: 1px solid var(--viz-empty);
  font-size: 13px; color: var(--ink-2);
}
.rc-stats span { display: inline-flex; align-items: center; gap: 6px; }
.rc-stats .glyph { width: 18px; height: 18px; }
.rc-more-wrap { display: flex; justify-content: center; margin: 24px 0 0; }
.btn.rc-more { min-height: 48px; padding: 0 24px; font-size: 16px; }
.rc-note { max-width: 760px; margin-top: 28px; font-size: 14px; line-height: 1.5; color: var(--muted); }

.rc-back { margin: 20px 0 0; }
.rc-back a { display: inline-flex; align-items: center; min-height: 44px; font-weight: 600; text-decoration: none; }
.rc-back a:hover { text-decoration: underline; }
.rc-head { display: grid; gap: 28px; align-items: center; padding-block: 16px 56px; }
.rc-head-id { display: grid; gap: 14px; min-width: 0; }
.rc-type { display: flex; align-items: center; gap: 14px; }
.rc-type-text { font: 500 12px/1.6 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--ink-2); }
.rc-head h1 { font: italic 800 clamp(52px, 13vw, 92px)/.9 var(--display); overflow-wrap: anywhere; }
.rc-head .lede { margin: 0; font-size: 19px; }
.rc-pitch { margin: 0; color: var(--muted); }
.rc-head-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.rc-tile { padding: 20px; border-radius: 20px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.rc-tile-dark { background: var(--tower); color: var(--tower-fg); }
.rc-tile-dark span { font-size: 14px; color: var(--tower-mute); }
.rc-tile-dark .rc-big { font: 700 52px/1 var(--display); color: #3D9BFF; font-variant-numeric: tabular-nums; }
.rc-range { font: 400 12px/1.4 var(--mono); }
.rc-tile-soft { background: var(--soft); justify-content: space-between; gap: 12px; font-size: 14px; color: var(--ink-2); }
.rc-tile-soft b { color: var(--ink); }
.rc-bars { display: flex; gap: 3px; }
.rc-bars span { flex: 1; height: 12px; border-radius: 2px; background: var(--rule); }
.rc-bars span.is-on { background: var(--ink); }
@media (min-width: 900px) { .rc-head { grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); } }

.rc-section { padding-block: 40px; border-top: 1px solid var(--rule); }
.rc-kicker { display: flex; align-items: center; gap: 10px; font: 500 12px/1.3 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--ink-2); }
.rc-h2 { margin: 12px 0 14px; font: italic 800 clamp(34px, 8vw, 52px)/.95 var(--display); }
.rc-copy { margin: 0 0 14px; font-size: 17px; line-height: 1.5; color: var(--ink-2); max-width: 64ch; }
.rc-caption { margin: 8px 0 0; font-size: 13px; color: var(--muted); }
.rc-lap { display: grid; gap: 28px; align-items: start; }
@media (min-width: 900px) { .rc-lap { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 40px; } }
.rc-lap-copy { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.rc-lap-controls { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; min-width: 0; }
.rc-option[hidden] { display: none; }
.btn.rc-play { justify-self: start; min-height: 52px; padding: 0 26px; font-size: 17px; background: var(--blue); border-color: var(--blue); color: #FFFFFF; }
.btn.rc-play:hover { background: var(--blue-ink); border-color: var(--blue-ink); color: #FFFFFF; }
.rc-worst { margin: 0; font-size: 15px; color: var(--ink-2); }
.rc-worst b { color: var(--red-ink); }
.rc-panel { padding: 20px; border-radius: 18px; background: var(--soft); display: grid; gap: 6px; }
.rc-panel b { font-size: 18px; }
.rc-panel span { font-size: 16px; line-height: 1.5; color: var(--ink-2); }
.rc-stations { list-style: none; margin: 0; padding: 0; }
.rc-station { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 14px; align-items: stretch; }
.rc-rail { display: flex; flex-direction: column; align-items: center; }
.rc-ribbon-top, .rc-ribbon-bottom { flex: 1; min-height: 18px; background: var(--rule); transition: width 1s var(--ease), background .6s var(--ease); }
.rc-station.is-lit .rc-ribbon-top, .rc-station.is-lit .rc-ribbon-bottom { background: var(--blue); }
.rc-ribbon.is-blank { background: transparent !important; }
.rc-dot {
  width: 40px; height: 40px; flex: none; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  border: 3px solid var(--ink); background: var(--paper); color: var(--ink); font: 700 16px/1 var(--display);
  transition: background .3s var(--ease), color .3s var(--ease);
}
.rc-dot .glyph { width: 22px; height: 22px; }
.rc-station.is-worst .rc-dot { border-color: var(--red); }
.rc-station.is-worst .rc-station-stat { color: var(--red-ink); font-weight: 600; }
.rc-station-card {
  margin: 8px 0; min-height: 64px; padding: 10px 14px; border-radius: 14px; border: 1.5px solid var(--rule);
  background: var(--paper); display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 2px;
  cursor: pointer; text-align: left; font-family: var(--body); color: var(--ink);
}
.rc-station-card[aria-pressed="true"] { border-color: var(--blue); }
.rc-station:has(.rc-station-card[aria-pressed="true"]) .rc-dot { background: var(--ink); color: var(--paper); }
.rc-station-name { font-size: 16px; font-weight: 600; }
.rc-station-stat { font-size: 14px; color: var(--ink-2); }

.rc-result { display: flex; flex-direction: column; gap: 14px; max-width: 760px; }
.rc-result .rc-h2, .rc-result .rc-copy { margin: 0; }
.rc-classification { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.rc-finisher { display: grid; grid-template-columns: 40px 6px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 12px 2px; border-bottom: 1px solid var(--rule); }
.rc-finisher[hidden] { display: none; }
.rc-pos { font: italic 800 18px/1 "Saira", var(--display); }
.rc-pos.is-podium { color: var(--blue-ink); }
.rc-livery { width: 6px; height: 36px; border-radius: 2px; }
.rc-who { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.rc-driver { font-size: 16px; font-weight: 600; overflow-wrap: anywhere; }
.rc-driver span { font-weight: 400; color: var(--ink-2); }
.rc-map { font-size: 13px; color: var(--muted); }
.rc-values { display: flex; flex-direction: column; align-items: flex-end; }
.rc-main { font: 700 24px/1.1 var(--display); font-variant-numeric: tabular-nums; }
.rc-second { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.rc-dnf { margin-top: 10px; padding: 20px; border-radius: 20px; background: var(--soft); display: grid; gap: 12px; }
.rc-dnf-head { display: flex; align-items: center; gap: 10px; }
.rc-dnf-head h3 { margin: 0; font: 600 18px/1.3 var(--body); }
.rc-dnf .rc-copy { margin: 0; font-size: 15px; }
.rc-dnf-rows { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.rc-dnf-row { display: grid; grid-template-columns: minmax(0, 1fr) 44px; gap: 4px 10px; align-items: center; font-size: 14px; }
.rc-dnf-name { grid-column: 1 / 3; }
.rc-dnf-bar { height: 12px; border-radius: 3px; background: var(--paper); }
.rc-dnf-bar span { display: block; height: 12px; border-radius: 3px; background: var(--ink); }
.rc-dnf-row.is-worst .rc-dnf-bar span { background: var(--red); }
.rc-dnf-n { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }

.rc-split { display: grid; gap: 32px; align-items: start; }
@media (min-width: 900px) { .rc-split { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } }
.rc-brief, .rc-track { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.rc-brief-text {
  padding: 18px 20px; border-radius: 18px; border: 1px solid var(--rule); font: 400 13.5px/1.65 var(--mono);
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.rc-brief-more { margin: 0; padding: 0; border: 0; display: grid; gap: 12px; }
.rc-brief-more > summary {
  display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px; border-radius: 999px;
  border: 1.5px solid var(--ink); font-weight: 600; width: fit-content; list-style: none;
}
.rc-brief-more > summary::-webkit-details-marker { display: none; }
.rc-brief-more[open] > summary { margin-bottom: 12px; }
.rc-track .rc-kicker + .rc-copy { margin: 0; }
.rc-chips-static { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.rc-chips-static li { padding: 8px 12px; border-radius: 999px; border: 1px solid var(--rule); font-size: 14px; }
.rc-chips-static li.rc-file { border-radius: 10px; border-color: transparent; background: var(--soft); font: 500 14px/1.3 var(--mono); }
.rc-track .rc-kicker:nth-of-type(2) { margin-top: 10px; }

.rc-cta {
  margin-top: 40px; display: flex; flex-direction: column; align-items: center; text-align: center; gap: 14px;
  padding: 36px 20px; border-radius: 28px; background: var(--tower); color: var(--tower-fg);
}
.rc-cta-kicker { font: 500 12px/1.3 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--tower-mute); }
.rc-cta-line { margin: 0; font: italic 800 clamp(30px, 7vw, 46px)/1 var(--display); max-width: 640px; }
.btn.rc-cta-btn { min-height: 52px; padding: 0 26px; background: #3D9BFF; border-color: #3D9BFF; color: #0E1116; font-weight: 700; font-size: 17px; }
.btn.rc-cta-btn:hover { background: #7DBBFF; border-color: #7DBBFF; color: #0E1116; }
@media (prefers-reduced-motion: reduce) {
  .rc-ribbon-top, .rc-ribbon-bottom, .rc-dot, .rc-card { transition: none; }
}
"""

#: The grid's filters, order and "Show all"; the race page's funnel views,
#: playback, classification order and "Show all". Each part runs only when its
#: markup is on the page.
SCRIPT = """(function () {
  function all(root, selector) { return Array.prototype.slice.call(root.querySelectorAll(selector)); }
  function check(buttons, on) {
    buttons.forEach(function (b) { b.setAttribute('aria-checked', b === on ? 'true' : 'false'); });
  }
  // Arrow keys move the choice inside a radio group.
  function arrows(group, pick) {
    group.addEventListener('keydown', function (event) {
      var step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (!step) return;
      var buttons = all(group, '[role="radio"]').filter(function (b) { return !b.hidden; });
      var at = buttons.indexOf(document.activeElement);
      if (at < 0) return;
      event.preventDefault();
      var next = buttons[(at + step + buttons.length) % buttons.length];
      next.focus();
      pick(next);
    });
  }
  // A sideways scroller shows a fade at its right edge while more is hidden there.
  function cue(scroller) {
    var wrap = scroller.parentNode;
    function update() {
      var more = scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1;
      wrap.setAttribute('data-more', more ? 'true' : 'false');
    }
    scroller.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    update();
    return update;
  }

  var grid = document.querySelector('[data-races-grid]');
  if (grid) {
    var controls = document.querySelector('[data-races-controls]');
    var more = document.querySelector('[data-races-more]');
    var cards = all(grid, '.rc-card');
    var first = parseInt(grid.getAttribute('data-first'), 10) || 12;
    var state = { challenge: 'all', sort: 'hard', all: false };
    var chips = all(controls, '[data-challenge]');
    var sorts = all(controls, '[data-sort]');
    var updateCue = cue(controls.querySelector('.rc-hscroll'));
    function fill(shown) {
      var tracks = parseInt(getComputedStyle(grid).getPropertyValue('--tracks'), 10) || 1;
      var span = tracks === 6 ? 2 : 1;
      var columns = tracks / span;
      shown.forEach(function (card) { card.style.removeProperty('--span'); });
      var left = shown.length % columns;
      if (!left) return;
      var wide = tracks / left;
      shown.slice(shown.length - left).forEach(function (card) { card.style.setProperty('--span', String(wide)); });
    }
    function render() {
      var list = cards.filter(function (card) {
        return state.challenge === 'all' || card.getAttribute('data-challenge') === state.challenge;
      });
      list.sort(function (a, b) {
        if (state.sort === 'name') {
          return a.getAttribute('data-name').localeCompare(b.getAttribute('data-name'));
        }
        return parseFloat(a.getAttribute('data-rate')) - parseFloat(b.getAttribute('data-rate')) ||
          a.getAttribute('data-name').localeCompare(b.getAttribute('data-name'));
      });
      cards.forEach(function (card) { card.hidden = true; });
      var limit = state.all ? list.length : Math.min(first, list.length);
      var shown = list.slice(0, limit);
      list.forEach(function (card, i) { grid.appendChild(card); card.hidden = i >= limit; });
      fill(shown);
      more.hidden = list.length <= first;
      more.textContent = state.all ? 'Show fewer' : 'Show all ' + list.length + ' races';
    }
    function pickChip(chip) {
      check(chips, chip);
      state.challenge = chip.getAttribute('data-challenge');
      state.all = false;
      render();
    }
    function pickSort(button) {
      check(sorts, button);
      state.sort = button.getAttribute('data-sort');
      render();
    }
    chips.forEach(function (chip) { chip.addEventListener('click', function () { pickChip(chip); }); });
    sorts.forEach(function (button) { button.addEventListener('click', function () { pickSort(button); }); });
    arrows(chips[0].parentNode, pickChip);
    arrows(sorts[0].parentNode, pickSort);
    more.addEventListener('click', function () { state.all = !state.all; render(); });
    window.addEventListener('resize', function () {
      fill(cards.filter(function (card) { return !card.hidden; }));
    });
    controls.hidden = false;
    updateCue();
    render();
  }

  var lap = document.querySelector('[data-funnel]');
  if (lap) {
    var views = JSON.parse(lap.getAttribute('data-funnel'));
    var ms = parseInt(lap.getAttribute('data-playback-ms'), 10) || 1600;
    var stations = all(lap, '.rc-station');
    var last = stations.length - 1;
    var modes = all(lap, '[data-mode]').filter(function (b) { return b.getAttribute('role') === 'radio'; });
    var options = all(lap, '.rc-option');
    var panel = lap.querySelector('[data-lap-panel]');
    var caption = lap.querySelector('[data-lap-caption]');
    var worstLine = lap.querySelector('[data-lap-worst]');
    var play = lap.querySelector('[data-lap-play]');
    var view = views[0];
    var step = last;
    var selected = view.worst || last;
    var timer = null;
    var motion = typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
    function motionChanged() {
      if (motion.matches) { stop(); step = last; selected = last; show(); }
    }
    if (motion.addEventListener) motion.addEventListener('change', motionChanged);
    else if (motion.addListener) motion.addListener(motionChanged);
    function show() {
      stations.forEach(function (station, k) {
        var top = station.querySelector('.rc-ribbon-top');
        var bottom = station.querySelector('.rc-ribbon-bottom');
        top.style.width = (k ? view.widths[k - 1] : 0) + 'px';
        bottom.style.width = (k < last ? view.widths[k] : 0) + 'px';
        station.classList.toggle('is-lit', k <= step);
        station.classList.toggle('is-worst', k > 0 && k === view.worst);
        station.querySelector('.rc-station-stat').textContent = view.stats[k];
        station.querySelector('.rc-station-card').setAttribute('aria-pressed', k === selected ? 'true' : 'false');
      });
      panel.querySelector('b').textContent = view.titles[selected];
      panel.querySelector('span').textContent = view.bodies[selected];
      caption.textContent = view.caption;
      var name = view.worst ? stations[view.worst].querySelector('.rc-station-name').textContent : '';
      worstLine.innerHTML = '';
      if (view.worst) {
        var b = document.createElement('b');
        b.textContent = name;
        worstLine.appendChild(document.createTextNode('The worst checkpoint is '));
        worstLine.appendChild(b);
        worstLine.appendChild(document.createTextNode(': ' + view.stats[view.worst] + '.'));
      } else {
        worstLine.textContent = 'No checkpoint retires a run.';
      }
      play.textContent = timer ? 'Racing\\u2026' : 'Play the race';
    }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    function use(next) { stop(); view = next; step = last; selected = view.worst || last; show(); }
    function pickMode(button) {
      check(modes, button);
      var mode = button.getAttribute('data-mode');
      var first = null;
      options.forEach(function (option) {
        var mine = option.getAttribute('data-mode') === mode;
        option.hidden = !mine;
        if (mine && !first) first = option;
      });
      if (mode === 'all') { use(views[0]); }
      else if (first) { pickOption(first); }
      optionCue();
    }
    function pickOption(option) {
      options.forEach(function (o) { o.setAttribute('aria-pressed', o === option ? 'true' : 'false'); });
      var mode = option.getAttribute('data-mode');
      var key = option.getAttribute('data-key');
      use(views.filter(function (v) { return v.mode === mode && v.key === key; })[0] || views[0]);
    }
    modes.forEach(function (button) { button.addEventListener('click', function () { pickMode(button); }); });
    options.forEach(function (option) { option.addEventListener('click', function () { pickOption(option); }); });
    if (modes.length) arrows(modes[0].parentNode, pickMode);
    stations.forEach(function (station, k) {
      station.querySelector('.rc-station-card').addEventListener('click', function () {
        selected = k; show();
      });
    });
    play.addEventListener('click', function () {
      stop();
      if (motion.matches) { step = last; selected = last; show(); return; }
      step = 0; selected = 0;
      timer = setInterval(function () {
        step += 1; selected = step;
        if (step >= last) { stop(); }
        show();
      }, ms);
      show();
    });
    var optionCue = cue(lap.querySelector('[data-lap-options]'));
    lap.querySelector('[data-lap-controls]').hidden = false;
    optionCue();
  }

  var table = document.querySelector('[data-classification]');
  if (table) {
    var rows = all(table, '.rc-finisher');
    var order = document.querySelector('[data-class-order]');
    var toggle = document.querySelector('[data-class-more]');
    var by = 'lap';
    var open = false;
    var firstRows = toggle ? parseInt(toggle.getAttribute('data-first'), 10) : rows.length;
    function value(row, key) {
      var raw = row.getAttribute('data-' + key);
      return raw === '' ? Infinity : parseFloat(raw);
    }
    function render() {
      var list = rows.slice().sort(function (a, b) {
        return value(a, by) - value(b, by) || rows.indexOf(a) - rows.indexOf(b);
      });
      list.forEach(function (row, i) {
        table.appendChild(row);
        row.hidden = !open && i >= firstRows;
        row.querySelector('.rc-pos').textContent = 'P' + (i + 1);
        row.querySelector('.rc-pos').classList.toggle('is-podium', i < 3);
        var other = by === 'lap' ? 'usd' : 'lap';
        row.querySelector('.rc-main').textContent = row.getAttribute('data-' + by + '-text');
        row.querySelector('.rc-second').textContent = row.getAttribute('data-' + other + '-second');
      });
      if (toggle) {
        toggle.textContent = open ? 'Show the top ' + firstRows : 'Show all ' + rows.length + ' finishers';
      }
    }
    var buttons = all(order, '[data-order]');
    function pickOrder(button) { check(buttons, button); by = button.getAttribute('data-order'); render(); }
    buttons.forEach(function (button) { button.addEventListener('click', function () { pickOrder(button); }); });
    arrows(order, pickOrder);
    if (toggle) {
      toggle.addEventListener('click', function () { open = !open; render(); });
      toggle.hidden = false;
    }
    order.hidden = false;
    render();
  }
})();
"""
