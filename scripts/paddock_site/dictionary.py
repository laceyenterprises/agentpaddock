"""The dictionary (PDW-09): ``/dictionary/``, rebuilt from ``design/web/Dictionary.dc.html``.

The terms are generated from ``design/glyphs/index.json``, the published glyph
set's index: one card per glyph, with that glyph, in the index's order and
group. :data:`WORDING` gives a term the prototype's visitor wording and the
page where you meet it; a glyph the wording does not cover yet falls back to
its index line (``"Term: meaning"``), so a new glyph shows up here without a
code change. The five effort variants (group ``engine``) are drawings of the
one engine-map term, not words of their own.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

from . import glyphs, kit, layout
from .layout import esc
from .routes import Route

PATH = "dictionary/"
INDEX_PATH = glyphs.DESIGN / "glyphs" / "index.json"
#: Index groups that hold drawings of another term rather than terms.
SKIPPED_GROUPS = frozenset({"engine"})

#: Group id -> chip label, in chip order (from the prototype).
GROUPS: dict[str, str] = {
    "cast": "The cast",
    "grip": "Grip and coaching",
    "flags": "Flags",
    "race": "The race",
    "rules": "Rules",
    "fuel": "Fuel",
    "running": "Running it",
    "new": "On the board",
}


class Wording(NamedTuple):
    term: str
    meaning: str
    #: The route path where a visitor meets the term, or ``None``.
    where: str | None = None
    #: The link's label when it differs from the route's.
    where_label: str = ""


def _calendar(races: int) -> str:
    if races:
        return f"All {races} races, grouped by challenge"
    return "Every race, grouped by challenge"


#: Glyph id -> visitor wording (from the prototype). The race calendar's
#: ``{calendar}`` is filled from the site data; other meanings are literal.
WORDING: dict[str, Wording] = {
    "driver": Wording("Driver", "The model behind the wheel", "drivers/"),
    "car": Wording("Car", "The harness that runs the model", "garage/"),
    "entry": Wording(
        "Entry", "The driver in the car, one model in one harness", "entries/"
    ),
    "tires": Wording("Tires", "The tools, the car's grip on the environment"),
    "team-radio": Wording(
        "Team radio", "Skills, the crew's coaching during the lap", "", "Home"
    ),
    "briefing": Wording(
        "Briefing", "The instructions the driver is given before the lap"
    ),
    "engine-map": Wording(
        "Engine map",
        "The effort level, how hard the model thinks before it acts",
        "learn/",
    ),
    "flag-checkered": Wording("Checkered flag", "The task passed every required check"),
    "flag-green": Wording("Green flag", "Lights out, the run is on track"),
    "flag-yellow": Wording("Yellow flag", "The result waits for an audit"),
    "flag-red": Wording(
        "Red flag", "An infrastructure fault, not the model's; the run is raced again"
    ),
    "flag-black": Wording("Black flag", "A run held and checked before it counts"),
    "dnf": Wording("DNF, did not finish", "The task failed", "races/"),
    "race": Wording("Race", "One task, exactly the same for every entry", "races/"),
    "track-map": Wording("Track map", "How a run works, in four sectors", "learn/"),
    "race-calendar": Wording("Race calendar", "{calendar}", "races/", "The calendar"),
    "lap-time": Wording("Lap time", "Wall-clock time for a task"),
    "whisker": Wording(
        "Whiskers", "The honest range, the 95% interval around a pass rate", "learn/"
    ),
    "photo-finish": Wording("Photo finish", "A statistical tie", "standings/"),
    "timing-tower": Wording("Timing tower", "The standings", "standings/"),
    "podium": Wording("Podium", "The top three"),
    "exhibition": Wording(
        "Exhibition",
        "A community race that is raced and shown, but not yet counted in the standings",
        "community/",
    ),
    "rulebook": Wording("Rulebook", "The methodology", "rulebook/"),
    "setup-sheet": Wording(
        "Setup sheet", "The car's exact configuration; its fingerprint names the entry"
    ),
    "fence": Wording("Fence", "The containment around the whole circuit", "races/"),
    "scrutineering": Wording(
        "Scrutineering", "How far a result has been checked", "community/"
    ),
    "stewards": Wording("Stewards", "The grader that rules on every finish", "learn/"),
    "track-limits": Wording("Track limits", "The CPU, memory and time caps"),
    "track-card": Wording(
        "Track card", "A race's public description, and which of its checks it shows", "races/"
    ),
    "checkpoint": Wording(
        "Checkpoint", "One check the stewards run, shown by name, by count or kept hidden", "races/"
    ),
    "fuel-gauge": Wording("Fuel gauge", "A spend cap"),
    "fuel-bill": Wording("Fuel bill", "Dollars per pass"),
    "fuel": Wording("Fuel", "Tokens"),
    "slipstream": Wording(
        "Slipstream", "Cached tokens, read at a fraction of the price"
    ),
    "fuel-triad": Wording(
        "The fuel triad", "Input (cached %) : output, the tokens behind each race passed"
    ),
    "mileage": Wording(
        "Mileage", "Races passed per $10, or per million tokens", "", "Home"
    ),
    "lean-rich": Wording(
        "Lean, rich", "Less fuel per pass, more fuel per pass: the two ends of the fuel axis"
    ),
    "racing-line": Wording(
        "Racing line",
        "The best-value entries: nothing burning less fuel beats them on pass rate",
    ),
    "guzzler": Wording("The guzzler", "The entry that burns the most fuel per race passed"),
    "pit-lane": Wording("Pit lane", "Run the bench yourself", "race-it-yourself/"),
    "pit-wall": Wording(
        "Pit wall", "Every entry against every challenge, side by side", "pit-wall/"
    ),
    "shakedown": Wording(
        "Shakedown", "A first test lap that checks a new car works before it races"
    ),
    "harness-drill": Wording(
        "Harness drill", "A standard test of the car in the garage, the same for every entry"
    ),
    "start-lights": Wording("Lights out", "A run starts"),
    "trophy": Wording("Trophy", "A challenge winner"),
    "roundel": Wording("Roundel", "The badge that shows a race's challenge"),
    "pit-board": Wording(
        "Pit board",
        "Contact Agent Paddock: questions, result challenges and feedback",
        "contact/",
        "Contact and about",
    ),
}


class Term(NamedTuple):
    glyph: str
    group: str
    term: str
    meaning: str
    where: str | None
    where_label: str


@lru_cache(maxsize=1)
def _index(path: Path) -> tuple[tuple[str, str, str], ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple((item["id"], item["group"], item["meaning"]) for item in raw)


def terms(races: int = 0, path: Path = INDEX_PATH) -> list[Term]:
    """Every dictionary term, in the glyph index's order."""
    out = []
    for glyph, group, line in _index(path):
        if group in SKIPPED_GROUPS:
            continue
        if group not in GROUPS:
            raise ValueError(f"glyph {glyph!r}: group {group!r} has no chip label")
        wording = WORDING.get(glyph)
        if wording is None:
            term, _, meaning = line.partition(": ")
            wording = Wording(
                term, (meaning or line)[:1].upper() + (meaning or line)[1:]
            )
        out.append(
            Term(
                glyph,
                group,
                wording.term,
                (
                    wording.meaning.format(calendar=_calendar(races))
                    if glyph == "race-calendar"
                    else wording.meaning
                ),
                wording.where,
                wording.where_label,
            )
        )
    return out


def _card(term: Term, routes: Sequence[Route], current: str) -> str:
    link = ""
    if term.where is not None:
        target = kit.route(routes, term.where)
        label = term.where_label or target.label
        link = (
            f'<a href="{layout.href(target.file, current)}">'
            f"Where you meet it: {esc(label)}</a>"
        )
    text = f"{term.term} {term.meaning}"
    return (
        f'<li data-group="{esc(term.group)}" data-text="{esc(text)}" id="term-{esc(term.glyph)}">'
        '<article class="term">'
        f'<span class="glyph-box">{glyphs.icon(term.glyph)}</span>'
        f"<div><h3>{esc(term.term)}</h3><p>{esc(term.meaning)}</p>{link}</div>"
        "</article></li>"
    )


def body(route: Route, routes: Sequence[Route], data: Mapping[str, Any]) -> str:
    listed = terms(kit.calendar_races(data))
    present = [group for group in GROUPS if any(t.group == group for t in listed)]
    cards = "".join(_card(term, routes, route.file) for term in listed)
    return (
        kit.hero(
            "briefing",
            "Racing words, agent meanings.",
            "Every word Agent Paddock uses, with the glyph you will see next to it.",
        )
        + '<section data-filter aria-label="The words">'
        '<div class="find-row">'
        '<label class="find" hidden><span>Find a word</span>'
        '<input type="search" placeholder="engine map, DNF, fuel…" autocomplete="off"></label>'
        + kit.chips(
            "group",
            "Group",
            [("all", "All")] + [(group, GROUPS[group]) for group in present],
        )
        + "</div>"
        f'<ul class="terms">{cards}</ul>'
        '<p class="none note" hidden>No word matches that yet. Try “fuel” or “flag”.</p>'
        "</section>"
    )


def page(results: Any, data: Mapping[str, Any], routes: Sequence[Route]) -> str:
    route = kit.route(routes, PATH)
    return kit.page(route, routes, body(route, routes, data), main_class="wide")
