"""The home page, ``/`` (PDW-04): ``design/web/Main.dc.html`` built for real.

Top to bottom, as in the prototype: the lights-out hero, the live ticker, the
photo finish, the "How to read these results" note, the learn-in-60-seconds
tiles, the seven-challenge row, the hardest-race and fuel cards and the call to
build an entry. The shared layout adds the header, the menu and the footer.

Every number is read from the site data files (``data/standings.json``,
``data/races.json`` and the leaders' ``data/entries/<id>.json``, see
``data.py``); nothing is copied from the prototype's sample values. Every icon
is a published glyph or a race-type roundel (``glyphs.py``).

The ticker reads ``live_feed_url`` (``site_config.py``) once the page loads,
with a short timeout, and rotates its ticks. When the feed is unreachable or
says nothing valid, it shows built-in sample runs labelled "Sample runs":
recorded first results of the leading entries, plus one example of the red
flag. An infrastructure rerun always shows the red flag, never DNF.

The page's behaviour lives in :data:`SCRIPT`, which ships inside the site's one
script file and does nothing on any other page.
"""

from __future__ import annotations

import json
import math
from itertools import zip_longest
from typing import Any, Mapping, Sequence

from . import glyphs, layout
from .routes import Route

TITLE = "The open leaderboard for coding agents"
EYEBROW = "Agent Paddock: the open leaderboard for coding agents"
HEADLINE = "Which coding agent is better? Watch them race."
LEDE = (
    "Every model, in the harness it runs in, on the same real repository work. "
    "Graded by stewards the agent never sees."
)
IDLE_LINE = "Five red lights. When they go out, the race is on."
SAMPLE_STAMP = "Sample runs"

RESULTS_NOTE_TITLE = "How to read these results"

#: Ticker kinds -> (glyph id, flag name). An infrastructure rerun is the red
#: flag; only a run that failed its checks is a DNF.
TICK_FLAGS: dict[str, tuple[str, str]] = {
    "passed": ("flag-checkered", "CHECKERED"),
    "dnf": ("dnf", "DNF · DID NOT FINISH"),
    "racing": ("flag-green", "GREEN · RACING"),
    "rerun": ("flag-red", "RED · RERUN"),
}
#: Words a feed may use for each kind.
TICK_ALIASES: dict[str, str] = {
    "passed": "passed",
    "checkered": "passed",
    "finished": "passed",
    "dnf": "dnf",
    "failed": "dnf",
    "racing": "racing",
    "green": "racing",
    "running": "racing",
    "rerun": "rerun",
    "red": "rerun",
    "infra": "rerun",
    "infrastructure": "rerun",
}

#: "Learn the paddock in 60 seconds": (term, glyph id, what it means). The
#: driver and car lines name the grid from the standings (see :func:`cast`).
CAST: tuple[tuple[str, str, str], ...] = (
    ("Driver", "driver", "The model behind the wheel{drivers}."),
    (
        "Car",
        "car",
        "The harness the model runs in{cars}. The same driver can do better in one "
        "car than another.",
    ),
    (
        "Engine map",
        "engine-map",
        "How hard the model thinks before it acts, from low to max. More effort "
        "costs more fuel and is not always better.",
    ),
    (
        "Tires",
        "tires",
        "The tools the car mounts: reading files, searching, running commands. "
        "They are its grip on the code.",
    ),
    (
        "Team radio",
        "team-radio",
        "Skills: coaching from the crew. Packaged know-how the driver can call on "
        "mid-race, like how this repo runs its tests. The board does not measure "
        "skills yet.",
    ),
    (
        "Stewards",
        "stewards",
        "The grader at the finish line. The driver never sees its checks, so it "
        "cannot play to them.",
    ),
    (
        "Fuel",
        "fuel",
        "Tokens. The fuel bill is what one race passed costs in dollars, so cheap "
        "failures do not look good.",
    ),
)

#: What each challenge asks, in the races calendar's order (``glyphs.RACE_TYPES``).
CHALLENGE_COPY: dict[str, str] = {
    "coding": "Build a change from a spec, then prove it with a counterexample.",
    "fix": "One reviewed defect, touching only the files you are allowed to. "
    "Hidden tests decide.",
    "hard-fix": "The pit wall makes the wrong call. Fix the real fault and write a "
    "test that fails on the old code.",
    "review": "Scrutineer a change. Find the real defects and nothing that is not "
    "there.",
    "hard-review": "Longer changes, several real defects and decoys that look wrong "
    "but are fine.",
    "docs": "Bring the setup sheet up to date with the code. Exact defaults, "
    "nothing invented.",
    "rca": "Read the crash evidence and name what really happened.",
}

NUMBER_WORDS = {
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
}

#: The prototype's "driver in a car" illustration (``design/web/Main.dc.html``),
#: in the design tokens' ink and accent.
CAR_ILLUSTRATION = (
    '<svg class="home-car" viewBox="0 0 900 300" aria-hidden="true" focusable="false">'
    '<rect x="70" y="226" width="780" height="8" class="ink"/>'
    '<path d="M76 226 L84 156 L160 150 L190 170 L330 166 C360 152 388 140 410 133 '
    "C418 108 442 94 470 95 C484 96 492 106 494 120 L520 122 C542 122 556 136 568 154 "
    'L604 176 C684 190 780 206 856 222 L856 226 Z" class="ink"/>'
    '<rect x="36" y="64" width="14" height="120" rx="2" class="ink"/>'
    '<rect x="36" y="64" width="128" height="16" rx="3" class="ink"/>'
    '<rect x="44" y="88" width="112" height="10" rx="3" class="ink"/>'
    '<path d="M118 98 L132 152" class="ink-line" stroke-width="8" fill="none"/>'
    '<circle cx="528" cy="112" r="25" class="accent"/>'
    '<rect x="532" y="101" width="24" height="12" rx="6" class="ink"/>'
    '<path d="M486 100 C504 80 560 82 580 150" class="ink-line" stroke-width="9" '
    'stroke-linecap="round" fill="none"/>'
    '<rect x="790" y="228" width="100" height="14" rx="3" class="ink"/>'
    '<rect x="876" y="204" width="12" height="38" rx="2" class="ink"/>'
    '<circle cx="196" cy="228" r="64" class="ink"/><circle cx="196" cy="228" r="40" class="hub"/>'
    '<circle cx="196" cy="228" r="12" class="paper"/>'
    '<circle cx="722" cy="230" r="60" class="ink"/><circle cx="722" cy="230" r="37" class="hub"/>'
    '<circle cx="722" cy="230" r="11" class="paper"/></svg>'
)

#: The five lamp centres of the published ``start-lights`` glyph.
LAMPS = (5.2, 8.6, 12.0, 15.4, 18.8)

esc = layout.esc


# ---------------------------------------------------------------- words


def pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.1f}%"


def usd(value: float) -> str:
    """A dollar figure readable at a glance: cents to three places below $1."""
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.3f}"
    return f"${value:.4f}"


def count_word(count: int) -> str:
    return NUMBER_WORDS.get(count, str(count))


def plural(count: int, singular: str, plural_form: str = "") -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def entry_name(entry: Mapping[str, Any]) -> str:
    """``driver in car``, with the engine map unless it is the default."""
    identity = entry.get("identity", entry)
    name = f"{identity['driver']} in {identity['car']}"
    engine_map = identity.get("map")
    return f"{name} · {engine_map}" if engine_map and engine_map != "default" else name


def challenge_label(challenge: str) -> str:
    return (
        glyphs.RACE_TYPES[challenge][0] if challenge in glyphs.RACE_TYPES else challenge
    )


def calendar_races(races: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [race for race in races.get("races", []) if race.get("calendar")]


# ---------------------------------------------------------------- picks


def leaders(standings: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The entries in first place: one, or several tied."""
    return [entry for entry in standings.get("entries", []) if entry["place"] == 1]


def hardest_race(races: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The calendar race with the lowest pass rate (then more runs, then by id)."""
    scored = [race for race in calendar_races(races) if race.get("rate") is not None]
    if not scored:
        return None
    return min(scored, key=lambda race: (race["rate"], -race["n"], race["id"]))


def fuel_pair(
    standings: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Two entries with the same finish and the widest fuel bill gap.

    Same finish means the same passes on the same calendar: placed entries
    share a place exactly when their pass rates are equal. The best place with
    at least two priced entries wins; within it, the cheapest and the dearest.
    """
    groups: dict[int, list[Mapping[str, Any]]] = {}
    for entry in standings.get("entries", []):
        if entry.get("costPerPass") is not None and entry.get("place") is not None:
            groups.setdefault(entry["place"], []).append(entry)
    for place in sorted(groups):
        group = groups[place]
        if len(group) < 2:
            continue
        cheap = min(group, key=lambda e: (e["costPerPass"], e["id"]))
        dear = max(group, key=lambda e: (e["costPerPass"], e["id"]))
        if dear["costPerPass"] > cheap["costPerPass"] > 0:
            return cheap, dear
    return None


def ratio_text(ratio: float) -> str:
    return f"{round(ratio)}×" if ratio >= 2 else f"{ratio:.1f}×"


def sample_ticks(
    standings: Mapping[str, Any], entries: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, str]]:
    """Sample runs for the ticker: recorded first results of the leading
    entries (one finish and one DNF each, while they last), then one example
    of the red flag, so the fallback teaches every flag the feed can show."""
    finishes: list[dict[str, str]] = []
    dnfs: list[dict[str, str]] = []
    for standing in standings.get("entries", [])[:6]:
        entry = entries.get(standing["id"])
        if not entry:
            continue
        for passed, bucket in ((True, finishes), (False, dnfs)):
            race = next(
                (
                    r
                    for r in entry.get("races", [])
                    if r.get("calendar") and r.get("passed") is passed
                ),
                None,
            )
            if race:
                bucket.append(
                    {
                        "who": entry_name(entry),
                        "what": f"{challenge_label(race['challenge'])}: {race['name']}",
                        "when": "From the committed results",
                        "kind": "passed" if passed else "dnf",
                    }
                )
    ticks: list[dict[str, str]] = []
    for finish, dnf in zip_longest(finishes, dnfs):
        if finish:
            ticks.append(finish)
        if dnf:
            ticks.append(dnf)
    ticks = ticks[:4]
    ticks.append(
        {
            "who": "Any entry",
            "what": "An infrastructure fault stops the run",
            "when": "Example: the run goes again and never counts as a DNF",
            "kind": "rerun",
        }
    )
    return ticks


# ---------------------------------------------------------------- sections


def hero(go_line: str) -> str:
    lamps = "".join(
        f'<circle class="lamp" cx="{x:g}" cy="7.75" r="1.3"/>' for x in LAMPS
    )
    return (
        '<section class="home-hero">'
        '<div class="home-hero-copy">'
        f'<span class="eyebrow">{esc(EYEBROW)}</span><h1>{esc(HEADLINE)}</h1>'
        f'<p class="home-hero-lede">{esc(LEDE)}</p></div>'
        f'<div class="home-lights" data-lights data-go-line="{esc(go_line)}">'
        '<svg class="home-lights-art" viewBox="0 0 24 24" role="img" '
        'aria-label="Start lights: 0 of 5 lit" data-art>'
        f'<use href="#start-lights"/>{lamps}</svg>'
        f'<p class="home-go" data-go hidden>{glyphs.icon("flag-green", "home-go-flag")}'
        "LIGHTS OUT</p>"
        f'<p class="home-hero-line" data-line aria-live="polite">{esc(IDLE_LINE)}</p>'
        '<button type="button" class="home-start" data-start hidden>Lights out</button>'
        "</div></section>"
    )


def ticker(feed_url: str, samples: Sequence[Mapping[str, str]]) -> str:
    first = samples[0]
    glyph, flag_name = TICK_FLAGS[first["kind"]]
    payload = json.dumps(list(samples), ensure_ascii=False, separators=(",", ":"))
    return (
        '<section class="home-ticker" data-ticker aria-label="On track now" '
        f'data-feed="{esc(feed_url)}" data-samples="{esc(payload)}" data-source="sample">'
        '<div class="home-ticker-head"><span class="live-dot" aria-hidden="true"></span>'
        '<span class="home-kicker">On track now</span>'
        f'<span class="home-ticker-stamp" data-stamp>{SAMPLE_STAMP}</span></div>'
        '<div class="home-tick" data-tick>'
        f'<span class="home-tick-flag" data-flag>{glyphs.icon(glyph, "home-tick-glyph")}</span>'
        '<span class="home-tick-text">'
        f"<b data-who>{esc(first['who'])}</b>"
        f"<span data-what>{esc(first['what'])}</span>"
        f'<span class="home-tick-when" data-when>{esc(first["when"])}</span></span>'
        f'<span class="home-tick-name" data-flag-name>{esc(flag_name)}</span>'
        "</div></section>"
    )


def _range_scale(rows: Sequence[Mapping[str, Any]]) -> float:
    """The left edge of the leaders' range bars: the lowest bound, down to 10%."""
    return math.floor(min(row["lo"] for row in rows) * 10) / 10


def _livery(car: str) -> str:
    token = layout.CAR_LIVERY.get(car, "tower-mute")
    return f'<span class="livery" style="--c:var(--{token})" aria-hidden="true"></span>'


def photo_finish(standings: Mapping[str, Any]) -> str:
    front = leaders(standings)
    kicker = (
        '<div class="home-kicker-row">'
        f"{glyphs.icon('photo-finish')}"
        '<span class="home-kicker">At the top</span></div>'
    )
    if not front:
        return (
            '<section class="home-pf"><div class="home-pf-copy">'
            f'{kicker}<h2 class="home-h2">The grid is forming.</h2>'
            '<p class="home-p">No entry has a counted result on every calendar race yet, '
            "so nobody is placed. Standings appear once trusted results cover the "
            "calendar.</p></div></section>"
        )
    lead = front[0]
    finished = f"{lead['k']} of the {lead['n']} races"
    if len(front) > 1:
        heading = "A photo finish."
        line = f"{count_word(len(front))} entries finished {finished}."
        low = min(row["lo"] for row in front)
        high = max(row["hi"] for row in front)
        why = (
            '<details class="home-why"><summary>'
            '<span class="why-closed">Why is it a tie?</span>'
            '<span class="why-open">Got it</span></summary>'
            f"<p>With {plural(lead['n'], 'race')}, each result has an honest range: "
            f"here {pct(low)} to {pct(high)}. The {count_word(len(front)).lower()} ranges "
            "overlap, so the board calls it a tie instead of naming a winner.</p>"
            "</details>"
        )
    else:
        heading = "Out in front."
        line = f"One entry finished {finished}, more than any other."
        why = ""
    floor = _range_scale(front)
    span = 1 - floor or 1

    def at(value: float) -> str:
        return f"{max(0.0, min(1.0, (value - floor) / span)) * 100:.1f}%"

    rows = "".join(
        "<li>"
        '<a class="home-leader" href="standings/index.html">'
        f"{_livery(row['car'])}"
        '<span class="home-leader-who">'
        f'<span class="home-leader-name">{esc(entry_name(row))}</span>'
        f'<span class="home-range" style="--lo:{at(row["lo"])};--hi:{at(row["hi"])};'
        f'--dot:{at(row["rate"])}" aria-hidden="true"><i></i><b></b><em></em></span>'
        f'<span class="sr-only">Honest range {pct(row["lo"])} to {pct(row["hi"])}</span>'
        "</span>"
        f'<span class="home-leader-rate">{pct(row["rate"])}</span></a></li>'
        for row in front
    )
    return (
        '<section class="home-pf"><div class="home-pf-copy">'
        f'{kicker}<h2 class="home-h2">{heading}</h2>'
        f'<p class="home-p">{esc(line)}</p>{why}</div>'
        f'<ol class="home-leaders" aria-label="First place">{rows}</ol></section>'
    )


def results_note(calendar: int) -> str:
    races = (
        f"a fixed calendar of {calendar} tasks"
        if calendar
        else "a fixed calendar of tasks"
    )
    return (
        '<section class="home-note" data-pdk="results-note" aria-labelledby="results-note">'
        f"{glyphs.icon('rulebook', 'home-note-glyph')}"
        f'<div><h2 class="home-note-title" id="results-note">{RESULTS_NOTE_TITLE}</h2>'
        f"<p>These are AI-run benchmarks: AI agents race {races}, and the same "
        "automated checks grade every run. The board shows how each entry did on these "
        "races. It is not a comprehensive ranking, and different tasks or settings can "
        "give a different order.</p>"
        '<p class="home-note-links">'
        '<a href="contact/index.html">Independent and open source: about the paddock</a>'
        '<a href="rulebook/index.html">Read the method</a></p></div></section>'
    )


def _grid_names(values: Sequence[str], joiner: str) -> str:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    if not seen:
        return ""
    if len(seen) == 1:
        return f": {seen[0]}"
    shown = seen[:5]
    tail = " and the rest of the grid" if len(seen) > len(shown) else ""
    if tail:
        return ": " + ", ".join(shown) + tail
    return ": " + ", ".join(shown[:-1]) + f" {joiner} " + shown[-1]


def cast(standings: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    rows = list(standings.get("entries", [])) + list(standings.get("partial", []))
    drivers = _grid_names([row["driver"] for row in rows], "and")
    cars = _grid_names(sorted({row["car"] for row in rows}), "or")
    return [
        (term, glyph, body.format(drivers=drivers, cars=cars))
        for term, glyph, body in CAST
    ]


def learn(standings: Mapping[str, Any]) -> str:
    terms = cast(standings)
    tiles = "".join(
        f'<button type="button" class="cast-tile" data-pick="cast" '
        f'aria-pressed="{"true" if i == 0 else "false"}" aria-controls="cast-detail">'
        f"{glyphs.icon(glyph, 'cast-glyph')}<span>{esc(term)}</span></button>"
        for i, (term, glyph, _body) in enumerate(terms)
    )
    panels = "".join(
        f'<div class="home-detail-panel" data-panel="cast"{"" if i == 0 else " hidden"}>'
        f'<b class="home-detail-term">{esc(term)}</b><p>{esc(body)}</p></div>'
        for i, (term, _glyph, body) in enumerate(terms)
    )
    return (
        '<section class="home-learn">'
        '<h2 class="home-h2">Learn the paddock in 60 seconds.</h2>'
        f'<div class="cast-grid" role="group" aria-label="Racing terms">{tiles}</div>'
        f'<div class="home-detail" id="cast-detail" aria-live="polite">{panels}</div>'
        '<a class="home-more" href="learn/index.html">See how a race works, step by step</a>'
        "</section>"
    )


def challenges(races: Mapping[str, Any]) -> str:
    summary = {row["challenge"]: row for row in races.get("challenges", [])}
    types = list(glyphs.RACE_TYPES)
    rated = [t for t in types if summary.get(t, {}).get("rate") is not None]
    hardest = (
        min(rated, key=lambda t: (summary[t]["rate"], types.index(t)))
        if rated
        else None
    )
    selected = types.index(hardest) if hardest else 0
    cards, panels, dots = [], [], []
    for i, race_type in enumerate(types):
        label = glyphs.RACE_TYPES[race_type][0]
        row = summary.get(race_type)
        rate = row["rate"] if row else None
        on = i == selected
        cards.append(
            f'<button type="button" class="challenge-card" data-pick="challenge" '
            f'aria-pressed="{"true" if on else "false"}" aria-controls="challenge-detail">'
            f"{glyphs.roundel(race_type, labelled=False, size='xl')}"
            f'<span class="challenge-name">{esc(label)}</span>'
            f'<span class="challenge-pct">{pct(rate)}</span></button>'
        )
        if row:
            title = f"{label} · {pct(rate)} average pass rate"
            tail = f" {plural(row['races'], 'race')} on the calendar."
            if race_type == hardest:
                tail += " The hardest challenge on the calendar."
        else:
            title = label
            tail = " No race of this challenge is on the calendar yet."
        panels.append(
            f'<div class="home-detail-panel" data-panel="challenge"{"" if on else " hidden"}>'
            f'<b class="challenge-title">{esc(title)}</b>'
            f"<p>{esc(label)}: {esc(CHALLENGE_COPY[race_type])}{esc(tail)}</p></div>"
        )
        dots.append(
            f'<span class="swipe-dot{" on" if i == 0 else ""}" data-dot></span>'
        )
    return (
        '<section class="home-challenges">'
        '<h2 class="home-h2">Seven challenges. One is brutal.</h2>'
        '<div class="challenge-scroller">'
        '<div class="challenge-row" role="group" aria-label="Challenges" data-challenge-row>'
        + "".join(cards)
        + '</div><span class="challenge-fade swipe-only" aria-hidden="true"></span></div>'
        '<div class="swipe-cue swipe-only" aria-hidden="true">'
        f'<span class="swipe-dots">{"".join(dots)}</span>'
        '<span class="swipe-hint">Swipe for all seven <span class="swipe-arrow">→</span></span></div>'
        f'<div class="challenge-detail" id="challenge-detail" aria-live="polite">{"".join(panels)}</div>'
        "</section>"
    )


def hardest_card(races: Mapping[str, Any]) -> str:
    race = hardest_race(races)
    if race is None:
        return (
            '<a class="home-card home-card-dark" href="races/index.html">'
            '<span class="home-kicker">The hardest race</span>'
            '<span class="home-card-title">Not decided yet</span>'
            '<span class="home-card-line">A race joins the calendar once it has enough '
            "counted runs to rank its difficulty.</span></a>"
        )
    return (
        '<a class="home-card home-card-dark" href="races/index.html">'
        '<span class="home-kicker">The hardest race</span>'
        f'<span class="home-card-title">{esc(race["name"])}</span>'
        f'<span class="home-card-big">{pct(race["rate"])}</span>'
        f'<span class="home-card-line">of {plural(race["n"], "run")} finished. '
        "Watch where the rest retire.</span></a>"
    )


def fuel_card(standings: Mapping[str, Any]) -> str:
    glyph = glyphs.icon("fuel-bill", "home-card-glyph")
    pair = fuel_pair(standings)
    if pair:
        cheap, dear = pair
        ratio = dear["costPerPass"] / cheap["costPerPass"]
        title = f"Same finish. {ratio_text(ratio)} the mileage."
        line = (
            f"{entry_name(cheap)} passes a race for {usd(cheap['costPerPass'])}. "
            f"{entry_name(dear)}, tied on the finish, pays {usd(dear['costPerPass'])}."
        )
    else:
        priced = [e for e in standings.get("entries", []) if e.get("costPerPass") is not None]
        if priced:
            best = priced[0]
            title = "The fuel bill."
            line = (
                f"{entry_name(best)}, the best-placed priced entry, passes a race for "
                f"{usd(best['costPerPass'])}."
            )
        else:
            title = "The fuel bill."
            line = "What one race passed costs appears once runs are priced."
    return (
        '<a class="home-card" href="standings/index.html">'
        f'{glyph}<span class="home-card-title">{esc(title)}</span>'
        f'<span class="home-card-line">{esc(line)}</span></a>'
    )


def call_to_action() -> str:
    return (
        '<section class="home-cta">'
        f"{CAR_ILLUSTRATION}"
        '<h2 class="home-h2">Put a driver in a car. See how it does.</h2>'
        '<p class="home-cta-actions">'
        '<a class="home-btn solid" href="garage/index.html">Build an entry</a>'
        '<a class="home-btn ghost" href="learn/index.html">How a race works</a></p>'
        "</section>"
    )


def go_line(standings: Mapping[str, Any], calendar: int) -> str:
    entered = len(standings.get("entries", [])) + len(standings.get("partial", []))
    if entered and calendar:
        return (
            f"{plural(entered, 'entry', 'entries')} have raced the "
            f"{plural(calendar, 'race')} on the calendar. Every run is graded the same way."
        )
    return "Every run is graded the same way, by stewards the agent never sees."


def body(
    standings: Mapping[str, Any],
    races: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> str:
    calendar = len(calendar_races(races))
    return (
        '<div class="home" data-home>'
        + hero(go_line(standings, calendar))
        + ticker(config["live_feed_url"], sample_ticks(standings, entries))
        + photo_finish(standings)
        + results_note(calendar)
        + learn(standings)
        + challenges(races)
        + '<section class="home-cards">'
        + hardest_card(races)
        + fuel_card(standings)
        + "</section>"
        + call_to_action()
        + "</div>"
    )


def page(
    *,
    routes: Sequence[Route],
    standings: Mapping[str, Any],
    races: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> str:
    home = next(route for route in routes if route.path == "")
    return layout.page(
        routes=routes,
        current=home.file,
        title=TITLE,
        body=body(standings, races, entries, config),
        url=home.url,
        canonical=True,
    )


# ---------------------------------------------------------------- styles


CSS = """
/* ---------- home (PDW-04) ---------- */
.home { display: flex; flex-direction: column; gap: 72px; padding-block: 20px 72px; }
.home-h2 { font: italic 800 clamp(32px, 7vw, 46px)/.95 var(--display); margin: 0; }
.home-p { font-size: 17px; line-height: 1.5; color: var(--ink-2); }
.home-kicker { font: 500 12px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--ink-2); }
.home-kicker-row { display: flex; align-items: center; gap: 10px; }
.home-kicker-row .glyph { width: 28px; height: 28px; }
.home a:focus-visible, .home button:focus-visible, .home summary:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
@keyframes home-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes home-pop { 0% { transform: scale(.6); opacity: 0; } 60% { transform: scale(1.08); opacity: 1; } 100% { transform: scale(1); } }
@keyframes home-pulse { 0% { box-shadow: 0 0 0 0 rgba(43, 217, 154, .55); } 70% { box-shadow: 0 0 0 10px rgba(43, 217, 154, 0); } 100% { box-shadow: 0 0 0 0 rgba(43, 217, 154, 0); } }
.home .in { animation: home-in .45s var(--ease); }

/* the lights-out hero */
.home-hero {
  border-radius: 28px; background: var(--tower); color: var(--tower-fg); padding: clamp(28px, 6vw, 64px);
  display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 32px; align-items: center;
}
.home-hero-copy { display: flex; flex-direction: column; gap: 18px; }
.home-hero .eyebrow { color: var(--tower-mute); font-weight: 500; letter-spacing: .14em; }
.home-hero h1 { margin: 0; font: italic 800 clamp(44px, 11vw, 84px)/.92 var(--display); text-wrap: balance; }
.home-hero-lede { font-size: 18px; line-height: 1.5; color: #B9C1CC; max-width: 460px; }
.home-lights { display: flex; flex-direction: column; align-items: center; gap: 18px; text-align: center; }
.home-lights-art { width: min(260px, 70vw); height: auto; aspect-ratio: 1 / 1; margin-bottom: -40px; color: var(--tower-fg); --pdk-accent: var(--tower-rule); }
.home-lights-art .lamp { fill: transparent; transition: fill .15s; }
.home-lights-art .lamp.on { fill: var(--red); }
.home-go { display: flex; align-items: center; gap: 10px; font: italic 800 34px/1 var(--display); color: var(--tower-lit); animation: home-pop .5s var(--ease); }
.home-go[hidden] { display: none; }
.home-go-flag { width: 34px; height: 34px; color: var(--tower-fg); }
.home-hero-line { min-height: 48px; font-size: 16px; line-height: 1.45; color: #D5DBE3; max-width: 320px; }
.home-start {
  min-height: 52px; padding: 0 28px; border: 0; border-radius: 999px; background: var(--blue); color: #FFFFFF;
  font: 600 17px var(--body); cursor: pointer;
}
.home-start:hover { background: var(--blue-ink); }
.home-start[hidden] { display: none; }

/* the live ticker */
.home-ticker { display: flex; flex-direction: column; gap: 14px; }
.home-ticker-head { display: flex; align-items: center; gap: 10px; }
.live-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--tower-lit); animation: home-pulse 1.8s infinite; flex: none; }
.home-ticker[data-source="sample"] .live-dot { background: var(--muted); animation: none; }
.home-ticker-stamp { margin-left: auto; font-size: 13px; color: var(--muted); text-align: right; }
.home-tick { display: flex; align-items: center; gap: 16px; padding: 18px 20px; border-radius: 18px; background: var(--soft); min-height: 88px; }
.home-tick-glyph { width: 34px; height: 34px; flex: none; color: var(--ink); display: block; }
.home-tick-text { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.home-tick-text b { font-size: 17px; font-weight: 600; overflow-wrap: anywhere; }
.home-tick-text span { font-size: 15px; color: var(--ink-2); overflow-wrap: anywhere; }
.home-tick-text .home-tick-when { font-size: 13px; color: var(--muted); }
.home-tick-name { margin-left: auto; flex: none; max-width: 30%; text-align: right; font: 500 12px/1.3 var(--mono); letter-spacing: .08em; color: var(--muted); }

/* the photo finish */
.home-pf { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 32px; align-items: start; }
.home-pf-copy { display: flex; flex-direction: column; gap: 12px; }
.home-why { border: 0; border-radius: 0; padding: 0; margin: 0; }
.home-why summary {
  list-style: none; display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px; border-radius: 999px;
  border: 1.5px solid var(--ink); background: var(--paper); font: 600 15px var(--body); cursor: pointer;
}
.home-why summary::-webkit-details-marker { display: none; }
.home-why .why-open, .home-why[open] .why-closed { display: none; }
.home-why[open] .why-open { display: inline; }
.home-why p { margin-top: 12px; padding: 16px 18px; border-radius: 14px; background: var(--blue-soft); font-size: 16px; line-height: 1.5; color: var(--ink); animation: home-in .45s var(--ease); }
.home-leaders { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.home-leader {
  display: grid; grid-template-columns: 6px minmax(0, 1fr) auto; gap: 14px; align-items: center; min-height: 44px;
  padding: 16px 2px; border-bottom: 1px solid var(--rule); color: var(--ink); text-decoration: none;
}
.home-leader:hover { color: var(--ink); background: var(--soft); }
.home-leader .livery { width: 6px; height: 40px; border-radius: 2px; background: var(--c, var(--muted)); }
.home-leader-who { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.home-leader-name { font-size: 17px; font-weight: 600; overflow-wrap: anywhere; }
.home-range { position: relative; height: 10px; display: block; }
.home-range i { position: absolute; left: 0; right: 0; top: 4px; height: 2px; background: var(--rule); }
.home-range b { position: absolute; left: var(--lo); right: calc(100% - var(--hi)); top: 3px; height: 4px; background: var(--muted); }
.home-range em { position: absolute; left: var(--dot); top: 0; width: 10px; height: 10px; margin-left: -5px; border-radius: 50%; background: var(--ink); }
.home-leader-rate { font: 700 30px/1 var(--display); font-variant-numeric: tabular-nums; }

/* how to read these results */
.home-note { display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: 14px; align-items: start; padding: 16px 18px; border-radius: 18px; background: var(--soft); }
.home-note-glyph { width: 30px; height: 30px; margin-top: 2px; color: var(--ink); }
.home-note-title { margin: 0 0 6px; font: 700 16px/1.3 var(--body); }
.home-note p { font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.home-note-links { display: flex; flex-wrap: wrap; gap: 4px 18px; margin-top: 6px; }
.home-note-links a { display: inline-flex; align-items: center; min-height: 44px; color: var(--ink-2); font-weight: 600; font-size: 14px; text-decoration: none; }
.home-note-links a:hover { color: var(--ink); text-decoration: underline; }

/* the learn tiles: seven racing terms */
.home-learn, .home-challenges { display: flex; flex-direction: column; gap: 18px; }
.cast-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.cast-tile, .challenge-card {
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-width: 0;
  border-radius: 18px; border: 1.5px solid var(--rule); background: var(--paper); color: var(--ink);
  font: 600 14px/1.15 var(--body); text-align: center; cursor: pointer;
  transition: border-color .2s var(--ease), background .2s var(--ease);
}
.cast-tile { min-height: 96px; padding: 10px 4px; }
.cast-tile[aria-pressed="true"], .challenge-card[aria-pressed="true"] { border-color: var(--blue); background: var(--blue-soft); }
.cast-glyph { width: 34px; height: 34px; }
.home-detail { padding: 20px 22px; border-radius: 18px; background: var(--soft); }
.home-detail-panel { display: flex; flex-direction: column; gap: 6px; }
.home-detail-panel[hidden] { display: none; }
.home-detail-term { font: italic 800 28px/1.1 var(--display); }
.home-detail p { font-size: 17px; line-height: 1.5; color: var(--ink-2); }
.home-more { align-self: flex-start; display: inline-flex; align-items: center; min-height: 44px; font-size: 16px; font-weight: 600; }

/* the seven challenges: a row that scrolls sideways below 720 px */
.challenge-scroller { position: relative; }
.challenge-row {
  display: flex; gap: 10px; overflow-x: auto; padding: 0 28px 4px 0; scroll-snap-type: x proximity;
  scrollbar-width: none; -webkit-overflow-scrolling: touch; overscroll-behavior-x: contain;
}
.challenge-row::-webkit-scrollbar { display: none; }
.challenge-card { flex: 0 0 92px; min-height: 132px; padding: 12px 6px; justify-content: flex-start; scroll-snap-align: start; }
.challenge-card .roundel-xl { width: 52px; height: 52px; }
.challenge-name { font-size: 14px; line-height: 1.2; }
.challenge-pct { font: 700 20px/1 var(--display); font-variant-numeric: tabular-nums; }
.challenge-fade { position: absolute; top: 0; right: 0; bottom: 4px; width: 56px; pointer-events: none; background: linear-gradient(to right, transparent, var(--paper) 85%); }
.swipe-cue { display: flex; align-items: center; gap: 10px; font-size: 14px; color: var(--muted); }
.swipe-dots { display: flex; gap: 5px; align-items: center; }
.swipe-dot { width: 6px; height: 6px; border-radius: 3px; background: var(--rule); transition: width .3s, background .3s; }
.swipe-dot.on { width: 18px; background: var(--blue); }
.challenge-detail { padding: 4px 2px; }
.challenge-title { font-size: 18px; font-weight: 600; }
.challenge-detail p { font-size: 16px; line-height: 1.5; color: var(--ink-2); }

/* the hardest race and the fuel bill */
.home-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 16px; }
.home-card {
  display: flex; flex-direction: column; gap: 10px; padding: 26px; border-radius: 24px; background: var(--soft);
  color: var(--ink); text-decoration: none; transition: transform .12s var(--ease);
}
.home-card:hover { color: var(--ink); transform: translateY(-1px); }
.home-card-dark, .home-card-dark:hover { background: var(--tower); color: var(--tower-fg); }
.home-card-dark .home-kicker { color: var(--tower-mute); }
.home-card-glyph { width: 34px; height: 34px; }
.home-card-title { font: italic 800 40px/.95 var(--display); overflow-wrap: anywhere; }
.home-card-big { font: 700 64px/1 var(--display); color: #3D9BFF; font-variant-numeric: tabular-nums; }
.home-card-line { font-size: 16px; line-height: 1.5; color: var(--ink-2); }
.home-card-dark .home-card-line { color: #B9C1CC; }

/* put a driver in a car */
.home-cta { display: flex; flex-direction: column; align-items: center; gap: 18px; text-align: center; padding: 24px 0; }
.home-car { width: min(560px, 100%); height: auto; }
.home-car .ink { fill: var(--ink); }
.home-car .ink-line { stroke: var(--ink); }
.home-car .accent { fill: var(--blue); }
.home-car .hub { fill: var(--ink-2); }
.home-car .paper { fill: var(--paper); }
.home-cta-actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.home-btn {
  display: inline-flex; align-items: center; min-height: 52px; padding: 0 26px; border-radius: 999px;
  font-weight: 600; font-size: 17px; text-decoration: none; border: 1.5px solid var(--ink);
}
.home-btn.solid { background: var(--blue); border-color: var(--blue); color: #FFFFFF; }
.home-btn.solid:hover { background: var(--blue-ink); border-color: var(--blue-ink); color: #FFFFFF; }
.home-btn.ghost { color: var(--ink); }
.home-btn.ghost:hover { background: var(--soft); color: var(--ink); }

@media (min-width: 720px) {
  .cast-grid { grid-template-columns: repeat(7, minmax(0, 1fr)); }
  .challenge-row { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); overflow: visible; padding-right: 0; }
  .challenge-card { flex: none; }
  .swipe-only { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .home .in, .home-go, .home-why p { animation: none; }
  .live-dot { animation: none; }
  .home-card, .home-lights-art .lamp { transition: none; }
}
"""


# ---------------------------------------------------------------- behaviour


def _script() -> str:
    flags = json.dumps(
        {kind: list(value) for kind, value in TICK_FLAGS.items()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    aliases = json.dumps(TICK_ALIASES, separators=(",", ":"))
    return (
        """
/* home (PDW-04): lights out, the live ticker, the tiles and the challenge row */
(function () {
  var root = document.querySelector('[data-home]');
  if (!root) return;
  var each = function (list, fn) { Array.prototype.forEach.call(list, fn); };
  var reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function enter(node) {
    if (reduce) return;
    node.classList.remove('in');
    void node.offsetWidth;
    node.classList.add('in');
  }

  // Tiles and cards that show one detail panel at a time.
  function picker(group, onPick) {
    var buttons = root.querySelectorAll('[data-pick="' + group + '"]');
    var panels = root.querySelectorAll('[data-panel="' + group + '"]');
    each(buttons, function (button, i) {
      button.addEventListener('click', function () {
        each(buttons, function (b, j) { b.setAttribute('aria-pressed', i === j ? 'true' : 'false'); });
        each(panels, function (p, j) { p.hidden = i !== j; if (i === j) enter(p); });
        if (onPick) onPick(i);
      });
    });
    return buttons;
  }
  picker('cast');
  var cards = picker('challenge');

  // The challenge row: position dots follow the scroll below 720 px.
  var row = root.querySelector('[data-challenge-row]');
  var dots = root.querySelectorAll('[data-dot]');
  if (row && cards.length) {
    var mark = function () {
      var left = row.scrollLeft, at = 0;
      each(cards, function (card, j) { if (card.offsetLeft <= left + 8) at = j; });
      if (left + row.clientWidth >= row.scrollWidth - 2) at = cards.length - 1;
      each(dots, function (dot, j) { dot.classList.toggle('on', j === at); });
    };
    row.addEventListener('scroll', mark, { passive: true });
    var chosen = row.querySelector('[aria-pressed="true"]');
    if (chosen && row.scrollWidth > row.clientWidth && chosen.offsetLeft + chosen.offsetWidth > row.clientWidth) {
      row.scrollLeft = chosen.offsetLeft - (row.clientWidth - chosen.offsetWidth) / 2;
    }
    mark();
  }

  // Lights out: five red lamps, one every 650 ms, then all out.
  var lights = root.querySelector('[data-lights]');
  if (lights) {
    var art = lights.querySelector('[data-art]');
    var lamps = lights.querySelectorAll('.lamp');
    var start = lights.querySelector('[data-start]');
    var line = lights.querySelector('[data-line]');
    var go = lights.querySelector('[data-go]');
    var timer = null, counting = false;
    var light = function (n) {
      each(lamps, function (lamp, i) { lamp.classList.toggle('on', i < n); });
      art.setAttribute('aria-label', 'Start lights: ' + n + ' of 5 lit');
    };
    start.hidden = false;
    start.addEventListener('click', function () {
      if (counting) return;
      counting = true;
      go.hidden = true;
      line.textContent = 'Hold it\\u2026';
      start.textContent = 'Hold\\u2026';
      var lit = 0;
      light(0);
      clearInterval(timer);
      timer = setInterval(function () {
        lit += 1;
        if (lit <= 5) { light(lit); return; }
        clearInterval(timer);
        counting = false;
        light(0);
        art.setAttribute('aria-label', 'Start lights: all out');
        go.hidden = false;
        line.textContent = lights.getAttribute('data-go-line');
        start.textContent = 'Run it again';
      }, 650);
    });
  }

  // The live ticker: the feed when it answers, else the labelled sample runs.
  var ticker = root.querySelector('[data-ticker]');
  if (!ticker) return;
  var FLAGS = """
        + flags
        + """;
  var KINDS = """
        + aliases
        + """;
  var stamp = ticker.querySelector('[data-stamp]');
  var tick = ticker.querySelector('[data-tick]');
  var use = ticker.querySelector('[data-flag] use');
  var fields = {
    who: ticker.querySelector('[data-who]'),
    what: ticker.querySelector('[data-what]'),
    when: ticker.querySelector('[data-when]'),
    name: ticker.querySelector('[data-flag-name]')
  };
  var text = function (value, max) { return typeof value === 'string' ? value.slice(0, max) : ''; };
  var kindOf = function (t) {
    // An infrastructure rerun is a red flag, never a DNF.
    if (t.infra === true || t.infrastructure === true || t.rerun === true) return 'rerun';
    var raw = String(t.kind || '').toLowerCase();
    var kind = Object.prototype.hasOwnProperty.call(KINDS, raw) ? KINDS[raw] : null;
    if (!kind && /rerun|^\\s*red\\b/i.test(String(t.flagName || ''))) return 'rerun';
    return kind && Object.prototype.hasOwnProperty.call(FLAGS, kind) ? kind : null;
  };
  var clean = function (list) {
    var out = [];
    each(list.slice(0, 50), function (t) {
      if (!t || typeof t !== 'object') return;
      var kind = kindOf(t), who = text(t.who, 120), what = text(t.what, 160);
      if (kind && who && what) out.push({ who: who, what: what, when: text(t.when, 80), kind: kind });
    });
    return out;
  };
  var samples = [];
  try { samples = clean(JSON.parse(ticker.getAttribute('data-samples') || '[]')); } catch (e) { samples = []; }
  if (!samples.length) return;
  var ticks = samples, at = 0;
  var show = function (t) {
    var flag = FLAGS[t.kind];
    use.setAttribute('href', '#' + flag[0]);
    fields.name.textContent = flag[1];
    fields.who.textContent = t.who;
    fields.what.textContent = t.what;
    fields.when.textContent = t.when;
    enter(tick);
  };
  var useSamples = function () {
    if (ticker.getAttribute('data-source') === 'sample') return;
    ticker.setAttribute('data-source', 'sample');
    stamp.textContent = '"""
        + SAMPLE_STAMP
        + """';
    ticks = samples; at = 0; show(ticks[0]);
  };
  setInterval(function () {
    if (document.hidden || ticks.length < 2) return;
    at = (at + 1) % ticks.length;
    show(ticks[at]);
  }, 3200);
  var url = ticker.getAttribute('data-feed');
  var load = function () {
    if (!url || !window.fetch) return;
    var abort = window.AbortController ? new AbortController() : null;
    var timeout = setTimeout(function () { if (abort) abort.abort(); useSamples(); }, 4000);
    fetch(url, { cache: 'no-store', credentials: 'omit', signal: abort ? abort.signal : undefined })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        clearTimeout(timeout);
        var live = d && d.schema === 'paddock-live/v1' && Array.isArray(d.ticks) ? clean(d.ticks) : [];
        if (!live.length) { useSamples(); return; }
        var racing = typeof d.racing === 'number' && d.racing > 0 ? d.racing + ' racing \\u00b7 ' : '';
        var updated = text(d.updated, 40);
        ticker.setAttribute('data-source', 'live');
        stamp.textContent = 'Live from the bench \\u00b7 ' + racing + (updated ? 'updated ' + updated : 'just now');
        ticks = live; at = 0; show(ticks[0]);
      })
      .catch(function () { clearTimeout(timeout); useSamples(); });
  };
  load();
  setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
"""
    )


SCRIPT = _script()
