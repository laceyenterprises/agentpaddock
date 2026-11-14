"""The Learn page (PDW-05): how a race works, in labs a visitor can play with.

It rebuilds ``design/web/Learn.dc.html`` (``LearnDesktop`` is the same page at
1440 px) as ``/learn/``:

- the sector stepper: the four sectors of a lap; tap the track to drive the
  next one, tap a sector number to jump, and after sector 4 a tap starts
  another lap;
- the engine-map lab: one driver's ladder on the hard-review races, five levels
  always on the dial, a level it has not raced greyed with the reason, the best
  level (the highest low end of its honest range) and notes written from the
  numbers;
- the honest-ranges lab: 9, 54 or 162 runs, plus and minus a pass, the range bar
  and the Wilson score interval in four steps with live numbers;
- same driver, different car: one driver in every car it raced, the bar floor
  dropping below 80% when a value needs it;
- the flags, DNF (did not finish) spelled out.

Every number comes from ``data/drivers.json`` (``paddock_site/data.py``, so
``scripts/leaderboard.py``) or from :func:`leaderboard.wilson`. This module
turns them into a *view*: every state a lab can show, its text already
written. The page renders the first state of each lab, embeds the view as JSON
and ``learn.js`` only switches between states; it does no statistics. The rules
for what stays on screen when the driver changes (the held level, the held car)
are tables in the view (``show``), so they are tested here and the script
only looks them up.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence

import leaderboard as lb

from . import glyphs, layout
from .routes import Route

#: The engine-map dial: always these five, in order.
LEVELS = ("low", "medium", "high", "xhigh", "max")
#: The honest-ranges lab's sample sizes.
SIZES = (9, 54, 162)
#: The size the honest-ranges lab starts on when no result has one of SIZES runs.
START_SIZE = 54
#: The pass rate the honest-ranges lab starts at with no trusted result yet: an
#: example for the toy, never shown as anyone's result.
EXAMPLE_RATE = 0.9
#: Two levels this many points apart get a "more" or "less" note.
NOTABLE_POINTS = 8
#: A cost ratio is worth a note from this many times as much.
COST_RATIO = 2.0
#: Where the car bars start unless a value sits below it.
BAR_FLOOR = 80

SCRIPT_NAME = "learn.js"
#: The script's output file, beside the page.
SCRIPT_FILE = "learn/" + SCRIPT_NAME
DATA_ID = "learn-data"

#: Car -> livery token (design/tokens.css); a car without one draws muted.
LIVERY = {
    "claude-code": "car-cc",
    "codex": "car-codex",
    "opencode": "car-opencode",
    "pi": "car-pi",
    "antigravity": "car-agy",
}

#: The track: one curve and the four sector marks on it (from the prototype).
TRACK_PATH = (
    "M24 118 C74 118 74 42 134 42 C194 42 194 108 254 108 C300 108 300 40 322 34"
)
SECTOR_POINTS = ((24, 118), (134, 42), (254, 108), (322, 34))


def track_stops(path: str = TRACK_PATH, samples: int = 400) -> list[float]:
    """Each sector mark's distance along the track, as a share of its length.

    The track is a move and three cubic curves, one per stretch between marks,
    so the marks are the curves' ends. ``learn.js`` drives the car along the
    real curve between them when scrolling drives the lap.
    """
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", path)]
    start, rest = numbers[:2], numbers[2:]
    lengths, x0, y0 = [], start[0], start[1]
    for k in range(0, len(rest), 6):
        x1, y1, x2, y2, x3, y3 = rest[k : k + 6]
        length, px, py = 0.0, x0, y0
        for i in range(1, samples + 1):
            t = i / samples
            u = 1 - t
            x = u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3
            y = u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3
            length += math.hypot(x - px, y - py)
            px, py = x, y
        lengths.append(length)
        x0, y0 = x3, y3
    total, run, stops = sum(lengths), 0.0, [0.0]
    for length in lengths:
        run += length
        stops.append(round(run / total, 4))
    return stops


#: (title, glyph, what happens, what to notice). No numbers: a number on this
#: page comes from the data.
SECTORS = (
    (
        "The briefing",
        "briefing",
        "The driver gets its instructions: the task and the code to work on.",
        "Every entry reads the same words, and the answer is never in them.",
    ),
    (
        "On the track",
        "tires",
        "The model drives its car, reading files, running commands and editing code with the tools its harness mounts: its tires. Nobody helps it mid-race.",
        "Fuel burns the whole way: every token the model reads or writes goes on the bill.",
    ),
    (
        "The hand-in",
        "flag-checkered",
        "It writes its answer to one file and stops; that file is all the stewards see.",
        "A run that hands in nothing is a DNF, short for did not finish, however "
        "close it got.",
    ),
    (
        "The stewards",
        "stewards",
        "Stewards check the answer against checks the driver never saw. Pass every "
        "required check and it takes the checkered flag.",
        "Miss one required check and the run is a DNF: it counts as a fail, not thrown out.",
    ),
)

#: (name, glyph, counted?, what it means): design/vocabulary.md "Flags".
FLAGS = (
    (
        "Checkered",
        "flag-checkered",
        "counted",
        "Finished: the run passed every required check.",
    ),
    (
        "DNF",
        "dnf",
        "counted",
        "Did not finish: it missed a required check or handed in nothing. It counts as a fail.",
    ),
    (
        "Red",
        "flag-red",
        "not counted",
        "Infrastructure, not the model: a proxy restart, a crashed host. "
        "The run is left out and raced again.",
    ),
    (
        "Black",
        "flag-black",
        "not counted",
        "An integrity hold: something looks off, so a person reviews it before it counts.",
    ),
    (
        "Yellow",
        "flag-yellow",
        "not yet",
        "Audit pending: the result waits until it is checked.",
    ),
    (
        "Green",
        "flag-green",
        "not a result",
        "Lights out: the run is on the track right now.",
    ),
)

esc = layout.esc


# ---------------------------------------------------------------- words


def money(usd: float | None) -> str:
    """A dollar figure as the prototype shows it; no price is said, not guessed."""
    if usd is None:
        return "no price"
    return f"${usd:.3f}" if usd < 0.1 else f"${usd:.2f}"


def percent(rate: float, places: int = 1) -> str:
    return f"{rate * 100:.{places}f}%"


def headline(rate: float) -> str:
    """``96.3%``, and ``100%`` rather than ``100.0%``."""
    text = percent(rate)
    return text.replace(".0%", "%")


def join(words: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    words = list(words)
    if len(words) < 2:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def driver_names(drivers: Sequence[str]) -> dict[str, str]:
    """Each driver's chip label: the model part of its id, the whole id when two
    providers serve a model of the same name."""
    short = {driver: driver.rsplit("/", 1)[-1] for driver in drivers}
    counts: dict[str, int] = {}
    for name in short.values():
        counts[name] = counts.get(name, 0) + 1
    return {
        driver: name if counts[name] == 1 else driver for driver, name in short.items()
    }


def challenge_label(challenge: str) -> str:
    label = glyphs.RACE_TYPES.get(challenge, (challenge.replace("-", " "),))[0]
    return label.lower()


def engine_map_phrase(engine_map: str) -> str:
    if engine_map == "default":
        return "the car's own default engine map"
    return f"a {engine_map} engine map"


# ---------------------------------------------------------------- the engine-map lab


def best_level(dial: Mapping[str, Mapping[str, Any]]) -> str:
    """The level whose honest range starts highest.

    Ties go to the higher pass rate, then the cheaper run (an unpriced run is
    never the cheaper), then the lower level.
    """

    def key(level: str) -> tuple:
        rung = dial[level]
        cost = rung["costPerRun"]
        return (
            rung["lo"],
            rung["rate"],
            -cost if cost is not None else -math.inf,
            -LEVELS.index(level),
        )

    return max(dial, key=key)


def level_note(level: str, dial: Mapping[str, Mapping[str, Any]], best: str) -> str:
    """What the numbers say about ``level``, in a sentence or three."""
    rung = dial[level]
    rate, cost = rung["rate"] * 100, rung["costPerRun"]
    below = [other for other in LEVELS[: LEVELS.index(level)] if other in dial]
    if not below:
        text = f"At {level}, {rate:.0f}% of runs pass" + (
            f", for {money(cost)} a run."
            if cost is not None
            else "; no price on record."
        )
    else:
        prev = dial[below[-1]]
        points = round(rate - prev["rate"] * 100)
        more = "."
        if cost is not None and prev["costPerRun"] is not None:
            delta = cost - prev["costPerRun"]
            if delta > 0.0005:
                more = f", for {money(delta)} more a run."
            elif delta < -0.0005:
                more = ", and costs less."
        if points >= NOTABLE_POINTS:
            text = f"{cap(level)} adds {points} points over {below[-1]}{more}"
        elif points <= -NOTABLE_POINTS:
            text = f"{cap(level)} passes {-points} points less often than {below[-1]}{more}"
        else:
            text = f"{cap(level)} lands within a few points of {below[-1]}{more}"
    top = dial[best]
    if level == best:
        text += " This is its best level here: its honest range starts highest."
    elif rung["hi"] >= top["lo"]:
        # The best range starts highest, so overlapping is reaching its low end.
        text += (
            f" Its honest range overlaps {best}'s, so the two are a statistical tie."
        )
    else:
        text += f" Clearly behind {best}."
    if level != best and cost and top["costPerRun"]:
        if cost >= COST_RATIO * top["costPerRun"]:
            ratio = cost / top["costPerRun"]
            text += f" It costs {ratio:.1f} times as much per run as {best}."
        elif top["costPerRun"] >= COST_RATIO * cost:
            ratio = top["costPerRun"] / cost
            text += f" {cap(best)} costs {ratio:.1f} times as much per run."
    return text


def level_view(level: str, dial: Mapping[str, Mapping[str, Any]], best: str) -> dict:
    rung = dial[level]
    return {
        "pct": percent(rung["rate"], 0),
        "runs": f"{rung['k']} of {rung['n']} runs passed",
        "usd": money(rung["costPerRun"]),
        "w": percent(rung["rate"]),
        "lo": percent(rung["lo"]),
        "span": percent(rung["hi"] - rung["lo"]),
        "range": f"{percent(rung['lo'])} to {percent(rung['hi'])}",
        "note": level_note(level, dial, best),
    }


def dial_of(ladder: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rungs = {rung["map"]: rung for rung in ladder["levels"]}
    return {level: rungs[level] for level in LEVELS if level in rungs}


def ladder_view(ladder: Mapping[str, Any], name: str, challenge: str) -> dict:
    """One driver's ladder: every level's view and the held-level table.

    ``show[held]`` is the level on screen when ``held`` is the level the
    visitor last had on screen: the same level when this ladder has it,
    otherwise its best. ``show[""]`` is the level before any pick.
    """
    dial = dial_of(ladder)
    best = best_level(dial)
    car = ladder["car"]
    race = challenge_label(challenge)
    missing = [level for level in LEVELS if level not in dial]
    off_dial = [rung["map"] for rung in ladder["levels"] if rung["map"] not in LEVELS]
    caption = f"Every counted {race} run of {name} in {car} on the calendar."
    if missing:
        caption += f" Greyed out: {join(missing)}, which it has not raced on {race} in this car."
    if off_dial:
        named = [
            "the car's own default" if level == "default" else level
            for level in off_dial
        ]
        caption += f" It also raced at {join(named)}, off this dial."
    return {
        "driver": ladder["driver"],
        "name": name,
        "car": car,
        "best": best,
        "levels": {
            level: level_view(level, dial, best) if level in dial else None
            for level in LEVELS
        },
        "show": {
            "": best,
            **{level: level if level in dial else best for level in LEVELS},
        },
        "fell": {
            level: f"It has not raced at {level} in this car, so this shows its best level, {best}."
            for level in missing
        },
        "why": {
            level: f"{name} has not raced at {level} in {car}" for level in missing
        },
        "sub": (
            f"{name} in {car}, on the {race} races. "
            "Turn the engine map up and watch the pass rate and the bill."
        ),
        "caption": caption,
    }


def dial_runs(ladder: Mapping[str, Any]) -> int:
    return sum(rung["n"] for rung in dial_of(ladder).values())


def pick_ladders(ladders: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """One ladder per driver: the car with the most levels on the dial, then the
    most counted runs, then the first car by name. A ladder needs two levels on
    the dial to compare. The most-measured ladder comes first, so the chip the
    lab starts on is the first in its row."""
    chosen: dict[str, tuple[tuple, Mapping[str, Any]]] = {}
    for ladder in ladders:
        dial = dial_of(ladder)
        if len(dial) < 2:
            continue
        key = (-len(dial), -dial_runs(ladder), ladder["car"])
        driver = ladder["driver"]
        if driver not in chosen or key < chosen[driver][0]:
            chosen[driver] = (key, ladder)
    return sorted(
        (ladder for _key, ladder in chosen.values()),
        key=lambda ladder: (-dial_runs(ladder), ladder["driver"]),
    )


def engine_map_lab(drivers: Mapping[str, Any]) -> dict:
    ladders = pick_ladders(drivers.get("ladders", []))
    names = driver_names([ladder["driver"] for ladder in ladders])
    challenge = drivers.get("ladderChallenge", "hard-review")
    views = [
        ladder_view(ladder, names[ladder["driver"]], challenge) for ladder in ladders
    ]
    return {"ladders": views, "start": 0}


def shown_level(view: Mapping[str, Any], held: str | None) -> str:
    """The level on screen for ``held`` (the level kept from the last driver)."""
    return view["show"][held or ""]


# ---------------------------------------------------------------- same driver, different car


def bar_floor(rates: Sequence[float]) -> int:
    """Where the bars start: 80% unless a value needs room below it, then the
    multiple of 5 at least two points under the lowest value."""
    lowest = min(rate * 100 for rate in rates)
    return max(0, min(BAR_FLOOR, math.floor((lowest - 2) / 5) * 5))


def floor_caption(floor: int, lowest: str) -> str:
    if floor == BAR_FLOOR:
        return f"Bars start at {floor}%, where the field is close."
    if floor == 0:
        return "Bars start at 0%."
    return (
        f"Bars start at {floor}%, lower than usual, so {lowest} still shows a bar. "
        "The field is close, so the differences sit at the top."
    )


def car_set_view(entry: Mapping[str, Any], name: str, all_cars: Sequence[str]) -> dict:
    """One driver in every car: each car's view and the held-car table.

    ``show[car]`` is the index on screen when ``car`` is the car the visitor
    last had on screen: that car when this driver raced in it, otherwise its
    best (the best-placed). ``fell[car]`` is the note for the second case.
    """
    cars = list(entry["cars"])
    best = min(
        range(len(cars)),
        key=lambda i: (
            cars[i]["place"] if cars[i]["place"] is not None else math.inf,
            i,
        ),
    )
    rates = [car["rate"] for car in cars]
    floor = bar_floor(rates)
    lowest = min(range(len(cars)), key=lambda i: (rates[i], i))
    index = {car["car"]: i for i, car in enumerate(cars)}
    n = cars[best]["n"]
    return {
        "driver": entry["driver"],
        "name": name,
        "best": best,
        "floor": floor,
        "caption": floor_caption(floor, cars[lowest]["car"]),
        "label": f"All {len(cars)} cars",
        "sub": (
            f"{name} at {engine_map_phrase(entry['map'])}, across all {n} races. "
            "Only the harness changes."
        ),
        "cars": [
            {
                "car": car["car"],
                "livery": f"var(--{LIVERY.get(car['car'], 'tower-mute')})",
                "eyebrow": f"{name} in {car['car']}",
                "pct": percent(car["rate"]),
                "of": f"{car['k']} of {car['n']}",
                "usd": money(car["costPerPass"]),
                "w": f"{(car['rate'] * 100 - floor) / (100 - floor) * 100:.0f}%",
            }
            for car in cars
        ],
        "show": {"": best, **{car: index.get(car, best) for car in all_cars}},
        "fell": {
            car: f"It has not raced in {car}, so this shows its best car."
            for car in all_cars
            if car not in index
        },
    }


def car_sets(drivers: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The drivers in more than one car, the one with the best car first."""
    sets = [
        entry for entry in drivers.get("sameDriverDifferentCar", []) if entry["cars"]
    ]
    return sorted(
        sets,
        key=lambda entry: (
            -max((car["rate"], car["n"]) for car in entry["cars"])[0],
            -max((car["rate"], car["n"]) for car in entry["cars"])[1],
            entry["driver"],
        ),
    )


def car_lab(drivers: Mapping[str, Any]) -> dict:
    sets = car_sets(drivers)
    names = driver_names([entry["driver"] for entry in sets])
    all_cars = sorted({car["car"] for entry in sets for car in entry["cars"]})
    views = [car_set_view(entry, names[entry["driver"]], all_cars) for entry in sets]
    return {"sets": views, "start": 0}


def shown_car(view: Mapping[str, Any], held: str | None) -> int:
    """The car index on screen for ``held`` (the car kept from the last driver)."""
    return view["show"].get(held or "", view["best"])


def top_result(
    sets: Sequence[Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any]] | None:
    """The best avg pass rate of any one car in the lab: (driver, car)."""
    found = None
    for entry in sets:
        for car in entry["cars"]:
            if found is None or (car["rate"], car["n"]) > (
                found[1]["rate"],
                found[1]["n"],
            ):
                found = (entry["driver"], car)
    return found


# ---------------------------------------------------------------- the honest-ranges lab


def wilson_table(sizes: Sequence[int] = SIZES) -> list[list[list[float]]]:
    """``table[i][k]`` is :func:`leaderboard.wilson` for ``k`` of ``sizes[i]``."""
    return [[list(lb.wilson(k, n)) for k in range(n + 1)] for n in sizes]


def range_view(k: int, n: int) -> dict[str, str]:
    """The lab's words for ``k`` of ``n``; ``learn.js`` writes the same."""
    lo, hi = lb.wilson(k, n)
    perfect = lb.wilson(n, n)[0]
    return {
        "k": str(k),
        "n": str(n),
        "pct": percent(k / n),
        "p": f"{k / n:.3f}",
        "lo": percent(lo),
        "span": percent(hi - lo),
        "dot": percent(k / n),
        "range": f"{percent(lo)} to {percent(hi)}",
        "width": f"{(hi - lo) * 100:.0f}",
        "centre": f"{(lo + hi) / 2:.3f}",
        "half": f"{(hi - lo) / 2:.3f}",
        "perfect": percent(perfect),
    }


def ranges_lab(drivers: Mapping[str, Any]) -> dict:
    sets = car_sets(drivers)
    lead = top_result(sets)
    if lead and lead[1]["n"] in SIZES:
        start, k = SIZES.index(lead[1]["n"]), lead[1]["k"]
    else:
        rate = lead[1]["rate"] if lead else EXAMPLE_RATE
        start = SIZES.index(START_SIZE)
        k = round(rate * START_SIZE)
    names = driver_names([entry["driver"] for entry in sets])
    return {
        "sizes": list(SIZES),
        "start": start,
        "k": k,
        "z": f"{lb.Z_95:.2f}",
        "table": wilson_table(),
        "title": (
            f"How sure is {headline(lead[1]['rate'])}?"
            if lead
            else "How sure is a pass rate?"
        ),
        "source": (
            f"{headline(lead[1]['rate'])} is {names[lead[0]]} in {lead[1]['car']}: "
            f"{lead[1]['k']} of {lead[1]['n']} races passed."
            if lead
            else ""
        ),
    }


# ---------------------------------------------------------------- the view


def view(drivers: Mapping[str, Any]) -> dict:
    """Every state the page's labs can show, from one ``drivers.json``."""
    return {
        "state": drivers.get("state"),
        "stepper": {
            "points": [list(point) for point in SECTOR_POINTS],
            "hint": "Tap the track, or a sector number",
            "lastHint": "Tap the track to start another lap",
            "scrollHint": "Scroll to drive the lap",
            "stops": track_stops(),
            "next": "Next sector",
            "last": "Another lap",
        },
        "engineMap": engine_map_lab(drivers),
        "ranges": ranges_lab(drivers),
        "cars": car_lab(drivers),
    }


def embed(value: Any) -> str:
    """JSON safe inside ``<script type="application/json">``."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------------------------------------------------------------- HTML


def _bind(name: str, value: str, tag: str = "span", attrs: str = "") -> str:
    return f'<{tag} data-bind="{name}"{attrs}>{esc(value)}</{tag}>'


def _eyebrow(text: str, glyph: str = "") -> str:
    icon = glyphs.icon(glyph) if glyph else ""
    return f'<span class="lab-eyebrow">{icon}{esc(text)}</span>'


def _scroll_row(label: str, hint: str, buttons: str, row_id: str) -> str:
    return (
        f'<div class="chip-scroll" data-more="true">'
        f'<div class="chip-row" id="{row_id}" role="group" aria-label="{esc(label)}">{buttons}</div>'
        f'<p class="scroll-hint" aria-hidden="true">{esc(hint)}</p></div>'
    )


def _chip(name: str, title: str, on: bool, attr: str) -> str:
    return (
        f'<button type="button" class="chip" {attr} aria-pressed="{str(on).lower()}" '
        f'title="{esc(title)}">{esc(name)}</button>'
    )


def stepper_html(stepper: Mapping[str, Any]) -> str:
    marks = "".join(
        f'<g class="sector-mark{" on" if i == 0 else ""}" data-sector="{i}" role="button" tabindex="0" '
        f'aria-label="Sector {i + 1}: {esc(SECTORS[i][0])}">'
        f'<circle class="hit" cx="{x}" cy="{y}" r="26"/>'
        f'<circle class="dot" cx="{x}" cy="{y}" r="13"/>'
        f'<text x="{x}" y="{y + 5}">{i + 1}</text></g>'
        for i, (x, y) in enumerate(SECTOR_POINTS)
    )
    x, y = SECTOR_POINTS[0]
    track = (
        '<div class="track" id="track">'
        '<svg viewBox="0 0 346 150" role="group" aria-labelledby="track-label">'
        '<title id="track-label">A lap in four sectors</title>'
        f'<path class="track-bed" d="{TRACK_PATH}"/>'
        f'<path class="track-done" id="track-done" d="{TRACK_PATH}" pathLength="100" '
        'stroke-dasharray="25 100"/>'
        f"{marks}"
        f'<g class="track-car" id="track-car" style="transform: translate({x}px, {y}px)">'
        '<circle r="9"/></g></svg>'
        f'<p class="track-hint" id="track-hint">{esc(stepper["hint"])}</p></div>'
    )
    panels = "".join(
        f'<div class="sector" data-sector-panel="{i}">'
        f'<div class="sector-head">{glyphs.icon(glyph, "sector-glyph")}'
        f'<div><span class="lab-eyebrow">Sector {i + 1} of 4</span>'
        f"<h3>{esc(title)}</h3></div></div>"
        f"<p>{esc(body)}</p>"
        f'<p class="notice"><b>Notice:</b> {esc(notice)}</p></div>'
        for i, (title, glyph, body, notice) in enumerate(SECTORS)
    )
    return (
        '<section class="lab stepper" aria-label="The four sectors of a lap">'
        f'{track}<div class="sectors" aria-live="polite">{panels}'
        '<div class="sector-actions">'
        '<button type="button" class="pill ghost" id="sector-back">Back</button>'
        f'<button type="button" class="pill solid" id="sector-next">{esc(stepper["next"])}</button>'
        "</div></div></section>"
    )


def _level_tile(level: str, ladder: Mapping[str, Any] | None, shown: str) -> str:
    available = bool(ladder and ladder["levels"][level])
    on = available and level == shown
    why = "" if available or not ladder else ladder["why"][level]
    return (
        f'<button type="button" class="level{"" if available else " off"}" data-level="{level}" '
        f'aria-pressed="{str(on).lower()}" aria-disabled="{str(not available).lower()}"'
        + (f' title="{esc(why)}"' if why else "")
        + f">{glyphs.icon('engine-map-' + level, 'level-glyph')}<span>{level}</span></button>"
    )


def _awaiting(text: str) -> str:
    return f'<div class="caveat">{glyphs.icon("flag-yellow")}<p>{esc(text)}</p></div>'


AWAITING_TEXT = (
    "No trusted results yet. This lab fills in once a results document is "
    "reproduced, or run by the bench itself."
)


def engine_map_html(lab: Mapping[str, Any]) -> str:
    ladders = lab["ladders"]
    head = (
        _eyebrow("Try it · the engine map", "engine-map")
        + "<h2>Does thinking harder help?</h2>"
    )
    if not ladders:
        return (
            f'<section class="lab" id="engine-map">{head}'
            f"{_awaiting(AWAITING_TEXT)}</section>"
        )
    current = ladders[lab["start"]]
    shown = shown_level(current, None)
    level = current["levels"][shown]
    chips = "".join(
        _chip(ladder["name"], ladder["driver"], i == lab["start"], f'data-ladder="{i}"')
        for i, ladder in enumerate(ladders)
    )
    tiles = "".join(_level_tile(name, current, shown) for name in LEVELS)
    return (
        f'<section class="lab" id="engine-map">{head}'
        + _bind("em-sub", current["sub"], "p", ' class="lab-sub"')
        + _scroll_row("Driver", "Swipe for more drivers", chips, "em-drivers")
        + f'<div class="levels" role="group" aria-label="Engine map">{tiles}</div>'
        '<div class="readout" aria-live="polite">'
        '<div class="readout-big">'
        + _bind("em-pct", level["pct"], "span", ' class="big"')
        + '<span class="readout-sub">'
        + _bind("em-runs", level["runs"])
        + " · "
        + _bind("em-usd", level["usd"])
        + " per run</span></div>"
        '<div class="readout-bar">'
        '<div class="rate-bar" role="img" aria-label="Pass rate and honest range">'
        f'<span class="rate-fill" id="em-fill" style="width: {level["w"]}"></span>'
        f'<span class="rate-range" id="em-range-bar" style="left: {level["lo"]}; width: {level["span"]}"></span>'
        "</div>"
        '<span class="small">Honest range: '
        + _bind("em-range", level["range"])
        + "</span></div></div>"
        + _bind("em-note", level["note"], "p", ' class="lab-note" aria-live="polite"')
        + _bind("em-caption", current["caption"], "p", ' class="lab-caption"')
        + "</section>"
    )


def ranges_html(lab: Mapping[str, Any]) -> str:
    n = lab["sizes"][lab["start"]]
    words = range_view(lab["k"], n)
    sizes = "".join(
        _chip(f"{size} runs", f"{size} runs", i == lab["start"], f'data-size="{i}"')
        for i, size in enumerate(lab["sizes"])
    )
    ticks = "".join(
        f'<span style="left: {tick}%">{tick}%</span>' for tick in (0, 25, 50, 75, 100)
    )
    return (
        '<section class="lab narrow" id="honest-ranges">'
        + _eyebrow("Try it · honest ranges", "whisker")
        + f"<h2>{esc(lab['title'])}</h2>"
        '<p class="lab-sub">A pass rate from a handful of races comes with a range: where the true rate most likely sits. Change the runs and passes to watch it move.</p>'
        + (f'<p class="lab-caption">{esc(lab["source"])}</p>' if lab["source"] else "")
        + f'<div class="chip-row wraps" id="hr-sizes" role="group" aria-label="Number of runs">{sizes}</div>'
        '<div class="panel">'
        '<div class="hr-top">'
        '<div class="stepper-ctl">'
        '<button type="button" class="square" id="hr-less" aria-label="One fewer pass">−</button>'
        '<span class="hr-count">'
        + _bind("hr-k", words["k"])
        + " of "
        + _bind("hr-n", words["n"])
        + "</span>"
        '<button type="button" class="square" id="hr-more" aria-label="One more pass">+</button>'
        "</div>" + _bind("hr-pct", words["pct"], "span", ' class="big"') + "</div>"
        '<div class="hr-scale" role="img" aria-label="The honest range on a scale from 0% to 100%">'
        '<div class="hr-line"><span class="hr-axis"></span>'
        f'<span class="hr-range" id="hr-range-bar" style="left: {words["lo"]}; width: {words["span"]}"></span>'
        f'<span class="hr-dot" id="hr-dot" style="left: {words["dot"]}"></span></div>'
        f'<div class="hr-ticks" aria-hidden="true">{ticks}</div></div>'
        '<p class="hr-says" aria-live="polite">Honest range: <b>'
        + _bind("hr-range", words["range"])
        + "</b>. That is "
        + _bind("hr-width", words["width"])
        + " points wide"
        + _bind("hr-nine", ": nine runs can only say so much." if n == 9 else ".")
        + "</p></div>"
        '<div class="steps">'
        "<h3>How the range is worked out</h3>"
        '<ol class="step-list">'
        "<li>Start with the score: <b>p</b> = passes ÷ runs. Here, "
        + _bind("hr-k", words["k"])
        + " ÷ "
        + _bind("hr-n", words["n"])
        + " = "
        + _bind("hr-p", words["p"])
        + ".</li>"
        f"<li>Decide how sure to be. We use 95%, which sets <b>z</b> = {esc(lab['z'])}.</li>"
        "<li>Wilson's score interval pulls the centre a little toward 50% and sets the "
        "width from <b>p</b> and the number of runs <b>n</b>:"
        '<pre class="formula">centre     = (p + z²/2n) ÷ (1 + z²/n)\n'
        "half-width = z × √(p(1 − p)/n + z²/4n²)\n"
        "             ÷ (1 + z²/n)\n"
        "range      = centre ± half-width</pre>"
        "Here: centre = "
        + _bind("hr-centre", words["centre"])
        + ", half-width = "
        + _bind("hr-half", words["half"])
        + ", so the range is "
        + _bind("hr-range", words["range"])
        + ".</li>"
        "<li>Why Wilson and not a flat plus-or-minus? It never runs past 0% or 100%, "
        "and it stays honest at the edges: a perfect "
        + _bind("hr-n", words["n"])
        + " of "
        + _bind("hr-n", words["n"])
        + " gives "
        + _bind("hr-perfect", words["perfect"])
        + " to 100%, not a flat 100%, because a few more runs could still miss one.</li></ol><p class=\"lab-sub\">Rule of thumb: to halve the range, run about four times as many races. The board's one rule: <b>when two entries' ranges overlap, it calls them a tie.</b></p></div></section>"
    )


def _car_button(car: Mapping[str, Any], i: int, on: bool) -> str:
    return (
        f'<button type="button" class="car" data-car="{i}" aria-pressed="{str(on).lower()}">'
        f'<span class="livery" style="background: {car["livery"]}"></span>{esc(car["car"])}</button>'
    )


def _car_bar(car: Mapping[str, Any], on: bool) -> str:
    color = car["livery"] if on else "var(--viz-other)"
    return (
        f'<div class="car-bar{" on" if on else ""}"><span class="car-bar-name">{esc(car["car"])}</span>'
        f'<span class="car-bar-track"><span style="width: {car["w"]}; background: {color}"></span></span>'
        f'<span class="car-bar-pct">{esc(car["pct"])}</span></div>'
    )


def car_html(lab: Mapping[str, Any]) -> str:
    sets = lab["sets"]
    head = (
        _eyebrow("Try it · change the car", "car")
        + "<h2>Same driver. Different car.</h2>"
    )
    if not sets:
        return (
            f'<section class="lab" id="change-the-car">{head}'
            f"{_awaiting(AWAITING_TEXT)}</section>"
        )
    current = sets[lab["start"]]
    shown = shown_car(current, None)
    car = current["cars"][shown]
    chips = "".join(
        _chip(entry["name"], entry["driver"], i == lab["start"], f'data-set="{i}"')
        for i, entry in enumerate(sets)
    )
    buttons = "".join(
        _car_button(item, i, i == shown) for i, item in enumerate(current["cars"])
    )
    bars = "".join(_car_bar(item, i == shown) for i, item in enumerate(current["cars"]))
    return (
        f'<section class="lab" id="change-the-car">{head}'
        + _bind("car-sub", current["sub"], "p", ' class="lab-sub"')
        + _scroll_row("Driver", "Swipe for more drivers", chips, "car-drivers")
        + _scroll_row("Car", "Swipe for more cars", buttons, "car-cars")
        + '<div class="panel car-panel" aria-live="polite"><div class="car-now">'
        + _bind("car-eyebrow", car["eyebrow"], "span", ' class="lab-eyebrow"')
        + _bind("car-pct", car["pct"], "span", ' class="big"')
        + '<div class="car-stats"><div class="stat">'
        f'<span class="stat-label">{glyphs.icon("flag-checkered")}Races passed</span>'
        + _bind("car-of", car["of"], "span", ' class="stat-value"')
        + '</div><div class="stat">'
        f'<span class="stat-label">{glyphs.icon("fuel-bill")}Fuel bill</span>'
        + _bind("car-usd", car["usd"], "span", ' class="stat-value"')
        + '<span class="stat-unit">per race passed</span></div></div></div>'
        '<div class="car-bars">'
        + _bind("car-label", current["label"], "span", ' class="car-bars-label"')
        + f'<div id="car-bars">{bars}</div>'
        + _bind("car-caption", current["caption"], "p", ' class="lab-caption"')
        + "</div></div></section>"
    )


def flags_html() -> str:
    tiles = "".join(
        f'<button type="button" class="flag" data-flag="{i}" aria-pressed="{str(i == 0).lower()}">'
        f"{glyphs.icon(glyph, 'flag-glyph')}"
        + (
            '<abbr title="did not finish">DNF</abbr>'
            if name == "DNF"
            else f"<span>{esc(name)}</span>"
        )
        + "</button>"
        for i, (name, glyph, _counted, _body) in enumerate(FLAGS)
    )
    panels = "".join(
        f'<div class="flag-says" data-flag-panel="{i}">'
        f"<b>{esc(name)} · {esc(counted)}</b><p>{esc(body)}</p></div>"
        for i, (name, _glyph, counted, body) in enumerate(FLAGS)
    )
    return (
        '<section class="lab" id="flags">'
        + _eyebrow("Read the flags", "flag-checkered")
        + "<h2>Every run ends under a flag.</h2>"
        f'<div class="flags">{tiles}</div>'
        f'<div class="flag-panels" aria-live="polite">{panels}</div></section>'
    )


def next_html(current: str) -> str:
    races = layout.href("races/index.html", current)
    garage = layout.href("garage/index.html", current)
    return (
        '<section class="next">'
        f'<a class="next-card dark" href="{races}"><span class="lab-eyebrow">Next</span>'
        "<span>See every race</span></a>"
        f'<a class="next-card" href="{garage}"><span class="lab-eyebrow">Or</span>'
        "<span>Build your own entry</span></a></section>"
    )


def body(drivers: Mapping[str, Any], current: str) -> str:
    """The Learn page's main content for one ``drivers.json``."""
    state = view(drivers)
    return (
        '<div class="learn" id="learn">'
        '<div class="page-hero">'
        f'<span class="eyebrow">{layout.PRODUCT}</span><h1>How an agent runs a race.</h1>'
        '<p class="lede">Four sectors, the same for every entry.</p></div>'
        + stepper_html(state["stepper"])
        + engine_map_html(state["engineMap"])
        + ranges_html(state["ranges"])
        + car_html(state["cars"])
        + flags_html()
        + next_html(current)
        + f'<script type="application/json" id="{DATA_ID}">{embed(state)}</script>'
        f'<script src="{SCRIPT_NAME}" defer></script>'
        "</div>"
    )


def page(route: Route, routes: Sequence[Route], drivers: Mapping[str, Any]) -> str:
    return layout.page(
        routes=routes,
        current=route.file,
        title=route.title,
        body=body(drivers, route.file),
        url=route.url,
    )


# ---------------------------------------------------------------- script

#: ``learn/learn.js``: switches between the states the view holds. It reads
#: every number and sentence from the embedded view, except the honest-ranges
#: words, which it formats from the view's Wilson table exactly as
#: :func:`range_view` does.
SCRIPT = r"""(function () {
  var root = document.getElementById('learn');
  var source = document.getElementById('learn-data');
  if (!root || !source) return;
  var view = JSON.parse(source.textContent);
  function byId(id) { return document.getElementById(id); }
  function all(selector, scope) {
    return Array.prototype.slice.call((scope || root).querySelectorAll(selector));
  }
  function bind(name, value) {
    all('[data-bind="' + name + '"]').forEach(function (el) { el.textContent = value; });
  }
  function press(selector, on) {
    all(selector).forEach(function (el, i) { el.setAttribute('aria-pressed', on(el, i) ? 'true' : 'false'); });
  }
  function pct(value) { return (value * 100).toFixed(1) + '%'; }

  // A row that scrolls sideways shows a cue while more of it is off screen.
  function cue(wrap) {
    var row = wrap.querySelector('.chip-row');
    var more = row.scrollWidth - row.clientWidth - row.scrollLeft > 4;
    wrap.setAttribute('data-more', more ? 'true' : 'false');
  }
  var rows = all('.chip-scroll');
  rows.forEach(function (wrap) {
    wrap.querySelector('.chip-row').addEventListener('scroll', function () { cue(wrap); }, { passive: true });
    cue(wrap);
  });
  window.addEventListener('resize', function () { rows.forEach(cue); });
  // Bring a picked chip fully into its row, sideways only.
  function reveal(el) {
    var row = el.parentNode;
    var box = el.getBoundingClientRect();
    var frame = row.getBoundingClientRect();
    if (box.right > frame.right) row.scrollLeft += box.right - frame.right + 8;
    else if (box.left < frame.left) row.scrollLeft -= frame.left - box.left + 8;
  }

  // ---- the sector stepper: the track advances, a number jumps, after 4 another lap
  var stepper = view.stepper;
  var sector = 0;
  // While scrolling drives the lap (below), the scroll position is the one control.
  var lap = null;
  function showSector(i) {
    sector = i;
    all('[data-sector-panel]').forEach(function (el) {
      el.hidden = Number(el.getAttribute('data-sector-panel')) !== sector;
    });
    all('[data-sector]').forEach(function (el) {
      var on = Number(el.getAttribute('data-sector')) === sector;
      el.classList.toggle('on', on);
      if (on) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current');
    });
    var point = stepper.points[sector];
    byId('track-car').style.transform = 'translate(' + point[0] + 'px, ' + point[1] + 'px)';
    byId('track-done').setAttribute('stroke-dasharray', (sector + 1) * 25 + ' 100');
    var last = sector === stepper.points.length - 1;
    byId('track-hint').textContent = lap ? stepper.scrollHint : last ? stepper.lastHint : stepper.hint;
    byId('sector-next').textContent = last ? stepper.last : stepper.next;
  }
  function nextSector() { showSector((sector + 1) % stepper.points.length); }
  // A tap or key on the stepper hands it back to taps, wherever the lap is.
  function tapped(action) {
    return function (event) { endLap(); action(event); };
  }
  byId('track').addEventListener('click', tapped(function (event) {
    var mark = event.target.closest && event.target.closest('[data-sector]');
    if (mark) showSector(Number(mark.getAttribute('data-sector')));
    else nextSector();
  }));
  all('[data-sector]').forEach(function (mark) {
    mark.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        endLap();
        showSector(Number(mark.getAttribute('data-sector')));
      }
    });
  });
  byId('sector-next').addEventListener('click', tapped(nextSector));
  byId('sector-back').addEventListener('click', tapped(function () { showSector(Math.max(0, sector - 1)); }));
  showSector(0);

  // ---- on a phone, the first scroll down the page drives the lap. The stepper
  // pins under the nav, the car follows the track through the four sectors, then
  // the stepper lets go and the page scrolls on. The run of scroll it used is
  // removed without moving anything on screen, so it never pins again. It gives
  // way to a wide screen, reduced motion, a link into the page, a page opened
  // below the stepper, a screen too short to hold it and a tap.
  var bed = root.querySelector('.track-bed');
  function along(p) {
    var stops = stepper.stops;
    var x = p * (stops.length - 1);
    var k = Math.min(stops.length - 2, Math.floor(x));
    return stops[k] + (stops[k + 1] - stops[k]) * (x - k);
  }
  function drive(p) {
    var s = Math.round(p * (stepper.points.length - 1));
    if (s !== sector) showSector(s);
    var share = along(p);
    var point = bed.getPointAtLength(share * bed.getTotalLength());
    byId('track-car').style.transform = 'translate(' + point.x + 'px, ' + point.y + 'px)';
    byId('track-done').setAttribute('stroke-dasharray', share * 100 + ' 100');
  }
  function endLap() {
    if (!lap) return;
    var run = lap;
    lap = null;
    window.removeEventListener('scroll', run.scrolled);
    window.removeEventListener('resize', run.resized);
    var html = document.documentElement;
    var before = run.section.getBoundingClientRect().top;
    html.style.overflowAnchor = 'none';
    html.style.scrollBehavior = 'auto';
    run.section.classList.remove('scrolly');
    run.spacer.parentNode.removeChild(run.spacer);
    var shift = run.section.getBoundingClientRect().top - before;
    if (shift) window.scrollBy(0, shift);
    requestAnimationFrame(function () { html.style.overflowAnchor = ''; html.style.scrollBehavior = ''; });
    showSector(sector);
  }
  function media(query) { return window.matchMedia ? window.matchMedia(query) : null; }
  function changed(list, fn) {
    if (!list) return;
    if (list.addEventListener) list.addEventListener('change', fn);
    else if (list.addListener) list.addListener(fn);
  }
  (function () {
    var narrow = media('(max-width: 899px)');
    var still = media('(prefers-reduced-motion: reduce)');
    var section = root.querySelector('.stepper');
    var nav = document.querySelector('.nav');
    if (!narrow || !narrow.matches || (still && still.matches) || window.location.hash) return;
    if (!section || !bed || !bed.getPointAtLength) return;
    var top = nav ? nav.offsetHeight + (parseFloat(getComputedStyle(nav).top) || 0) : 0;
    var start = section.getBoundingClientRect().top;
    section.classList.add('scrolly');
    var room = window.innerHeight - top;
    var tall = section.getBoundingClientRect().height;
    // Centred in the room under the nav; it must fit, and the page must open above it.
    var pin = top + Math.max(8, Math.round((room - tall) / 2));
    if (tall > room - 16 || start < pin) { section.classList.remove('scrolly'); return; }
    // About half a screen of scroll per sector.
    var length = 4 * Math.max(280, Math.round(window.innerHeight * 0.55));
    var run = document.createElement('div');
    run.className = 'stepper-run';
    section.parentNode.insertBefore(run, section);
    run.appendChild(section);
    var spacer = document.createElement('div');
    spacer.setAttribute('aria-hidden', 'true');
    spacer.style.height = length + 'px';
    run.appendChild(spacer);
    section.style.setProperty('--pin', pin + 'px');
    var width = window.innerWidth;
    var ticking = false;
    function update() {
      ticking = false;
      if (!lap) return;
      var q = (pin - run.getBoundingClientRect().top) / length;
      if (q >= 1) { drive(1); endLap(); return; }
      // A tenth of the run at each end holds the first and last sector.
      drive(Math.min(1, Math.max(0, (q - 0.1) / 0.8)));
    }
    lap = {
      section: section,
      spacer: spacer,
      scrolled: function () {
        if (!ticking) { ticking = true; requestAnimationFrame(update); }
      },
      resized: function () { if (window.innerWidth !== width) endLap(); }
    };
    showSector(0);
    drive(0);
    window.addEventListener('scroll', lap.scrolled, { passive: true });
    window.addEventListener('resize', lap.resized);
    changed(narrow, function () { if (!narrow.matches) endLap(); });
    changed(still, function () { if (still.matches) endLap(); });
  })();

  // ---- the engine-map lab: the level on screen is held across drivers
  var lab = view.engineMap;
  if (lab.ladders.length) {
    var ladder = lab.start;
    var held = null;
    var shown = function () { return lab.ladders[ladder].show[held || '']; };
    var showLadder = function () {
      var current = lab.ladders[ladder];
      var level = shown();
      var rung = current.levels[level];
      press('[data-ladder]', function (el) { return Number(el.getAttribute('data-ladder')) === ladder; });
      all('[data-level]').forEach(function (el) {
        var name = el.getAttribute('data-level');
        var open = !!current.levels[name];
        el.classList.toggle('off', !open);
        el.setAttribute('aria-disabled', open ? 'false' : 'true');
        el.setAttribute('aria-pressed', open && name === level ? 'true' : 'false');
        if (open) el.removeAttribute('title'); else el.setAttribute('title', current.why[name]);
      });
      bind('em-sub', current.sub);
      bind('em-pct', rung.pct);
      bind('em-runs', rung.runs);
      bind('em-usd', rung.usd);
      bind('em-range', rung.range);
      bind('em-note', rung.note + (held && current.fell[held] ? ' ' + current.fell[held] : ''));
      bind('em-caption', current.caption);
      byId('em-fill').style.width = rung.w;
      byId('em-range-bar').style.left = rung.lo;
      byId('em-range-bar').style.width = rung.span;
    };
    all('[data-ladder]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        held = held || shown();
        ladder = Number(chip.getAttribute('data-ladder'));
        showLadder();
        reveal(chip);
      });
    });
    all('[data-level]').forEach(function (tile) {
      tile.addEventListener('click', function () {
        var name = tile.getAttribute('data-level');
        if (!lab.ladders[ladder].levels[name]) return;
        held = name;
        showLadder();
      });
    });
  }

  // ---- the honest-ranges lab: the Wilson table comes from leaderboard.wilson
  var ranges = view.ranges;
  var size = ranges.start;
  var passes = ranges.k;
  function showRange() {
    var n = ranges.sizes[size];
    var bounds = ranges.table[size][passes];
    var lo = bounds[0], hi = bounds[1];
    press('[data-size]', function (el) { return Number(el.getAttribute('data-size')) === size; });
    bind('hr-k', String(passes));
    bind('hr-n', String(n));
    bind('hr-pct', pct(passes / n));
    bind('hr-p', (passes / n).toFixed(3));
    bind('hr-range', pct(lo) + ' to ' + pct(hi));
    bind('hr-width', ((hi - lo) * 100).toFixed(0));
    bind('hr-nine', n === 9 ? ': nine runs can only say so much.' : '.');
    bind('hr-centre', ((lo + hi) / 2).toFixed(3));
    bind('hr-half', ((hi - lo) / 2).toFixed(3));
    bind('hr-perfect', pct(ranges.table[size][n][0]));
    byId('hr-range-bar').style.left = pct(lo);
    byId('hr-range-bar').style.width = pct(hi - lo);
    byId('hr-dot').style.left = pct(passes / n);
    byId('hr-less').disabled = passes === 0;
    byId('hr-more').disabled = passes === n;
  }
  all('[data-size]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var next = Number(chip.getAttribute('data-size'));
      // Keep the pass rate: the same score, measured on more or fewer runs.
      passes = Math.round(passes / ranges.sizes[size] * ranges.sizes[next]);
      size = next;
      showRange();
    });
  });
  byId('hr-less').addEventListener('click', function () { passes = Math.max(0, passes - 1); showRange(); });
  byId('hr-more').addEventListener('click', function () {
    passes = Math.min(ranges.sizes[size], passes + 1);
    showRange();
  });
  showRange();

  // ---- same driver, different car: the car on screen is held across drivers
  var cars = view.cars;
  if (cars.sets.length) {
    var set = cars.start;
    var renderedSet = set;
    var heldCar = null;
    var carIndex = function () {
      var current = cars.sets[set];
      var index = current.show[heldCar || ''];
      return index === undefined ? current.best : index;
    };
    var element = function (tag, className, text) {
      var el = document.createElement(tag);
      if (className) el.className = className;
      if (text !== undefined) el.textContent = text;
      return el;
    };
    var showCars = function () {
      var current = cars.sets[set];
      var index = carIndex();
      var car = current.cars[index];
      press('[data-set]', function (el) { return Number(el.getAttribute('data-set')) === set; });
      var row = byId('car-cars');
      var bars = byId('car-bars');
      // Keep the focused car button when only the selection changes.
      if (renderedSet !== set) {
        row.textContent = '';
        bars.textContent = '';
        current.cars.forEach(function (item, i) {
          var button = element('button', 'car');
          button.type = 'button';
          button.setAttribute('data-car', String(i));
          var livery = element('span', 'livery');
          livery.style.background = item.livery;
          button.appendChild(livery);
          button.appendChild(document.createTextNode(item.car));
          row.appendChild(button);
          var bar = element('div', 'car-bar');
          bar.appendChild(element('span', 'car-bar-name', item.car));
          var track = element('span', 'car-bar-track');
          var fill = element('span');
          fill.style.width = item.w;
          track.appendChild(fill);
          bar.appendChild(track);
          bar.appendChild(element('span', 'car-bar-pct', item.pct));
          bars.appendChild(bar);
        });
        renderedSet = set;
      }
      press('[data-car]', function (el) { return Number(el.getAttribute('data-car')) === index; });
      all('.car-bar', bars).forEach(function (bar, i) {
        bar.classList.toggle('on', i === index);
        bar.querySelector('.car-bar-track > span').style.background =
          i === index ? current.cars[i].livery : 'var(--viz-other)';
      });
      cue(row.parentNode);
      var fell = heldCar && current.fell[heldCar];
      bind('car-sub', current.sub + (fell ? ' ' + fell : ''));
      bind('car-eyebrow', car.eyebrow);
      bind('car-pct', car.pct);
      bind('car-of', car.of);
      bind('car-usd', car.usd);
      bind('car-label', current.label);
      bind('car-caption', current.caption);
    };
    all('[data-set]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        heldCar = heldCar || cars.sets[set].cars[carIndex()].car;
        set = Number(chip.getAttribute('data-set'));
        showCars();
        reveal(chip);
      });
    });
    byId('car-cars').addEventListener('click', function (event) {
      var button = event.target.closest && event.target.closest('[data-car]');
      if (!button) return;
      heldCar = cars.sets[set].cars[Number(button.getAttribute('data-car'))].car;
      showCars();
      reveal(button);
    });
  }

  // ---- the flags
  function showFlag(i) {
    press('[data-flag]', function (el) { return Number(el.getAttribute('data-flag')) === i; });
    all('[data-flag-panel]').forEach(function (el) {
      el.hidden = Number(el.getAttribute('data-flag-panel')) !== i;
    });
  }
  all('[data-flag]').forEach(function (tile) {
    tile.addEventListener('click', function () { showFlag(Number(tile.getAttribute('data-flag'))); });
  });
  showFlag(0);
})();
"""


# ---------------------------------------------------------------- styles

#: The Learn page's styles, mobile first (390 px), then 560 and 900 px.
CSS = """
/* ---------- learn page (PDW-05) ---------- */
.learn { display: grid; grid-template-columns: minmax(0, 1fr); gap: 72px; padding-bottom: 24px; }
.learn .page-hero { padding-block: 40px 0; max-width: 760px; }
.learn h2 { margin: 0; font: italic 800 clamp(34px, 8vw, 54px)/.95 var(--display); }
.learn h3 { margin: 0; }
.learn p { margin: 0; }
.lab { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; min-width: 0; }
.lab.narrow { max-width: 760px; }
.lab-eyebrow {
  display: inline-flex; align-items: center; gap: 10px;
  font: 600 12px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--ink-2);
}
.lab-eyebrow .glyph { width: 28px; height: 28px; color: var(--ink); }
.lab-sub { font-size: 17px; line-height: 1.5; color: var(--ink-2); max-width: 640px; }
.lab-note { font-size: 17px; line-height: 1.5; max-width: 720px; }
.lab-caption { font-size: 13px; line-height: 1.45; color: var(--muted); max-width: 720px; }
.learn .big { font: 700 64px/1 var(--display); font-variant-numeric: tabular-nums; }
.learn .small { font-size: 14px; color: var(--muted); }
.learn .caveat { margin: 0; }
.panel { padding: 22px; border-radius: 24px; background: var(--soft); display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0; }

/* buttons: every target at least 44 px */
.chip, .pill, .car, .level, .flag, .square { font-family: var(--body); color: var(--ink); cursor: pointer; }
.chip {
  flex: none; min-height: 44px; padding: 0 14px; border-radius: 999px; border: 1.5px solid var(--rule);
  background: var(--paper); font-size: 15px; font-weight: 600; white-space: nowrap;
}
.chip[aria-pressed="true"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.pill { min-height: 48px; padding: 0 22px; border-radius: 999px; font-size: 16px; font-weight: 600; border: 1.5px solid var(--ink); }
.pill.ghost { background: var(--paper); }
.pill.solid { background: var(--blue); border-color: var(--blue); color: #FFFFFF; }
.square {
  width: 48px; height: 48px; border-radius: 14px; border: 1.5px solid var(--ink); background: var(--paper);
  font-size: 24px; font-weight: 700; line-height: 1;
}
.square:disabled { opacity: .38; cursor: not-allowed; }

/* a row that scrolls sideways, and its cue */
.chip-scroll { position: relative; min-width: 0; }
.chip-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; overscroll-behavior-x: contain; }
.chip-row::-webkit-scrollbar { display: none; }
.chip-row.wraps { flex-wrap: wrap; overflow: visible; }
.chip-scroll::after {
  content: ""; position: absolute; top: 0; right: 0; width: 56px; height: 48px; pointer-events: none;
  background: linear-gradient(to right, transparent, var(--paper)); opacity: 0; transition: opacity .2s var(--ease);
}
.chip-scroll[data-more="true"]::after { opacity: 1; }
.scroll-hint { display: none; margin-top: 6px !important; font-size: 13px; color: var(--muted); }
.chip-scroll[data-more="true"] .scroll-hint { display: block; }

/* the sector stepper */
.stepper { grid-template-columns: minmax(0, 1fr); gap: 28px; align-items: center; }
.track { border-radius: 24px; background: var(--soft); padding: 18px; cursor: pointer; display: grid; gap: 8px; }
.track svg { width: 100%; height: auto; display: block; overflow: visible; }
.track-bed { fill: none; stroke: var(--rule); stroke-width: 22; stroke-linecap: round; }
.track-done { fill: none; stroke: var(--blue); stroke-width: 4; stroke-linecap: round; transition: stroke-dasharray .6s ease; }
.sector-mark { cursor: pointer; font: 700 14px var(--display); text-anchor: middle; }
.sector-mark .hit { fill: transparent; }
.sector-mark .dot { fill: var(--paper); stroke: var(--ink); stroke-width: 2.5; }
.sector-mark text { fill: var(--ink); }
.sector-mark.on .dot { fill: var(--ink); }
.sector-mark.on text { fill: var(--paper); }
.sector-mark:focus-visible { outline: none; }
.sector-mark:focus-visible .dot { stroke: var(--blue); stroke-width: 4; }
.track-car { transition: transform .6s cubic-bezier(.3, .7, .2, 1); }
.track-car circle { fill: var(--blue); stroke: var(--paper); stroke-width: 3; }
.track-hint { font-size: 14px; color: var(--muted); text-align: center; }
.sectors { display: grid; gap: 18px; }
.sector { display: grid; gap: 14px; animation: learn-in .4s ease-out; }
.sector[hidden] { display: none; }
.sector-head { display: flex; align-items: center; gap: 14px; }
.sector-glyph { width: 48px; height: 48px; flex: none; }
.sector-head div { display: grid; gap: 2px; }
.sector h3 { font: italic 800 34px/1 var(--display); }
.sector > p { font-size: 18px; line-height: 1.55; }
.sector .notice { padding: 14px 16px; border-radius: 14px; background: var(--blue-soft); font-size: 16px; line-height: 1.5; }
.sector-actions { display: flex; gap: 10px; }
/* Scrolling drives the lap (learn.js: phones, the first scroll down only). The stepper pins under the nav
   while its run scrolls by. The four panels share one cell, so the pinned block keeps one height. */
.stepper-run { min-width: 0; }
.stepper.scrolly { position: sticky; top: var(--pin, 72px); z-index: 1; background: var(--paper); }
.stepper.scrolly .track-car, .stepper.scrolly .track-done { transition: none; }
.stepper.scrolly .sector { grid-area: 1 / 1; align-content: start; animation: none; transition: opacity .3s ease; }
.stepper.scrolly .sector[hidden] { display: grid; visibility: hidden; opacity: 0; transition: none; }
.stepper.scrolly .sector-actions { display: none; }
@keyframes learn-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .sector, .track-car, .track-done, .rate-fill, .rate-range, .hr-range, .hr-dot, .car-bar-track span { animation: none; transition: none; }
}

/* the engine-map lab */
.levels { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; max-width: 560px; }
.level {
  min-height: 76px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
  border-radius: 16px; border: 1.5px solid var(--rule); background: var(--paper); font-size: 13px; font-weight: 600;
}
.level-glyph { width: 32px; height: 32px; }
.level[aria-pressed="true"] { border-color: var(--blue); background: var(--blue-soft); }
.level.off { opacity: .38; border-style: dashed; cursor: not-allowed; }
.readout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; align-items: end; max-width: 760px; }
.readout-big { display: grid; gap: 4px; }
.learn .readout .big { font-size: 72px; }
.readout-sub { font-size: 16px; color: var(--ink-2); }
.readout-bar { display: grid; gap: 6px; }
.rate-bar { position: relative; height: 22px; border-radius: 6px; background: var(--soft); }
.rate-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; background: var(--blue); transition: width .5s ease; }
.rate-range { position: absolute; top: 9px; height: 4px; background: var(--ink); transition: left .5s ease, width .5s ease; }

/* the honest-ranges lab */
.hr-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.stepper-ctl { display: flex; align-items: center; gap: 10px; }
.hr-count { font: 700 34px/1 var(--display); min-width: 120px; text-align: center; font-variant-numeric: tabular-nums; }
.learn .hr-top .big { font-size: 56px; }
.hr-scale { display: grid; gap: 4px; }
.hr-line { position: relative; height: 22px; }
.hr-axis { position: absolute; left: 0; right: 0; top: 10px; height: 2px; background: var(--rule); }
.hr-range { position: absolute; top: 7px; height: 8px; border-radius: 4px; background: var(--viz-interval, #9AA3AF); transition: left .5s ease, width .5s ease; }
.hr-dot {
  position: absolute; top: 3px; width: 16px; height: 16px; margin-left: -8px; border-radius: 50%;
  background: var(--blue); border: 3px solid var(--paper); transition: left .5s ease;
}
.hr-ticks { position: relative; height: 16px; font-size: 11px; color: var(--muted); }
.hr-ticks span { position: absolute; top: 0; transform: translateX(-50%); }
.hr-ticks span:first-child { transform: none; }
.hr-ticks span:last-child { transform: translateX(-100%); }
.hr-says { font-size: 17px; line-height: 1.5; }
.learn .steps { display: grid; gap: 12px; }
.learn .steps h3 { font: 600 18px/1.3 var(--body); }
.step-list { margin: 0; padding: 0; list-style: none; counter-reset: step; display: grid; gap: 10px; font-size: 16px; line-height: 1.5; }
.step-list li { counter-increment: step; position: relative; padding-left: 40px; min-width: 0; }
.step-list li::before { content: counter(step); position: absolute; left: 0; top: 0; font: 700 18px/1.5 var(--display); color: var(--blue); }
.formula {
  margin: 10px 0 !important; padding: 16px; border-radius: 14px; background: var(--tower); color: var(--tower-fg);
  font: 12px/1.7 var(--mono); white-space: pre-wrap;
}

/* same driver, different car */
.car {
  flex: none; min-height: 52px; display: flex; align-items: center; gap: 10px; padding: 0 16px 0 12px;
  border-radius: 16px; border: 1.5px solid var(--rule); background: var(--paper); font-size: 16px; font-weight: 600;
}
.car[aria-pressed="true"] { border-color: var(--blue); background: var(--blue-soft); }
.livery { width: 6px; height: 32px; border-radius: 2px; flex: none; }
.car-panel { grid-template-columns: minmax(0, 1fr); gap: 22px; max-width: 760px; }
.car-now { display: grid; gap: 12px; align-content: start; }
.learn .car-now .big { font-size: 76px; line-height: .9; }
.car-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.stat { padding: 12px 14px; border-radius: 14px; background: var(--paper); display: grid; gap: 4px; align-content: start; }
.stat-label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.stat-label .glyph { width: 20px; height: 20px; color: var(--ink); }
.stat-value { font: 700 26px/1 var(--display); }
.stat-unit { font-size: 12px; color: var(--muted); }
.car-bars { display: grid; gap: 10px; align-content: center; }
.car-bars-label { font-size: 13px; font-weight: 600; color: var(--ink-2); }
.car-bar { display: grid; grid-template-columns: 96px minmax(0, 1fr) 52px; gap: 10px; align-items: center; font-size: 14px; }
.car-bar-name { overflow-wrap: anywhere; }
.car-bar.on { font-weight: 700; }
.car-bar-track { height: 12px; border-radius: 4px; background: var(--paper); }
.car-bar-track span { display: block; height: 12px; border-radius: 4px; transition: background .3s, width .4s; }
.car-bar-pct { text-align: right; font-variant-numeric: tabular-nums; }

/* the flags: three across on a phone, six from 560 px, never an orphan */
.flags { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; max-width: 760px; }
.flag {
  min-height: 92px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
  border-radius: 16px; border: 1.5px solid var(--rule); background: var(--paper); font-size: 14px; font-weight: 600;
}
.flag abbr { text-decoration: none; }
.flag-glyph { width: 34px; height: 34px; }
.flag[aria-pressed="true"] { border-color: var(--blue); background: var(--blue-soft); }
.flag-panels { display: grid; gap: 10px; max-width: 760px; }
.flag-says { padding: 18px 20px; border-radius: 18px; background: var(--soft); display: grid; gap: 4px; }
.flag-says[hidden] { display: none; }
.flag-says b { font-size: 18px; }
.flag-says p { font-size: 16px; line-height: 1.5; color: var(--ink-2); }

/* where to go next */
.next { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
.next-card {
  display: grid; gap: 6px; padding: 24px; border-radius: 22px; background: var(--soft); color: var(--ink); text-decoration: none;
}
.next-card:hover { color: var(--ink); }
.next-card > span:last-child { font: italic 800 32px/1.05 var(--display); }
.next-card.dark { background: var(--tower); color: var(--tower-fg); }
.next-card.dark:hover { color: var(--tower-fg); }
.next-card.dark .lab-eyebrow { color: var(--tower-mute); }

@media (min-width: 560px) {
  .formula { font-size: 13px; }
  .flags { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .readout { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .car-panel { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .next { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 900px) {
  .learn { gap: 88px; }
  .stepper { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
"""
