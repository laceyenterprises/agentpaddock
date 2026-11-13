"""The community board (PDW-09): ``/community/``, rebuilt from ``design/web/Community.dc.html``.

Three tabs from CONTRIBUTING.md (Entries, Tracks, Fork results), the trust
ladder with its badges, and the board itself. The board lists every community
result from ``data/community.json`` (PDW-03): each entry whose results are not
Agent Paddock's own bench runs, with its trust badge. Until one exists the
board says so honestly, and every card on the page is marked EXAMPLE: it shows
the shape of a submission, never a real one.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import glyphs, kit, layout, views
from .layout import esc
from .routes import Route

PATH = "community/"
#: The trust level of Agent Paddock's own bench runs: not a community result.
HOUSE = "paddock-run"


def community_entries(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Community entries from the site data, in its order (highest trust first)."""
    return [
        entry
        for entry in data["data/community.json"]["entries"]
        if entry["trust"] != HOUSE
    ]


def community_documents(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        document
        for document in data["data/community.json"]["documents"]
        if document["trust"] != HOUSE
    ]


def _example_tag() -> str:
    return '<span class="tag-example">EXAMPLE</span>'


def _facts(rows: Sequence[tuple[str, str]]) -> str:
    return (
        '<dl class="facts-list">'
        + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows)
        + "</dl>"
    )


def _fuel(entry: Mapping[str, Any]) -> str:
    cost = entry.get("costPerPass")
    if cost is None:
        return "fuel bill unknown"
    return f"${cost:,.2f} fuel bill per race passed"


def real_card(entry: Mapping[str, Any]) -> str:
    """One community entry, as the site data gives it."""
    engine_map = entry["map"]
    short_hash = entry["configHash"].removeprefix("sha256:")[:8]
    return (
        '<article class="tile result">'
        '<div class="card-head"><div>'
        f"<b>{views.car_tag(entry['car'])} · {esc(entry['driver'])}</b>"
        f'<span class="mono">engine map {views.map_tag(engine_map, entry["car"])} · car {esc(short_hash)}</span>'
        f"</div>{kit.badge(entry['trust'], size='sm')}</div>"
        f'<div class="score"><b>{int(entry["k"])} of {int(entry["n"])}</b>'
        f"<span>races passed · {esc(_fuel(entry))}</span></div></article>"
    )


def _example_fork_card() -> str:
    return (
        '<article class="tile example">'
        + _example_tag()
        + '<div class="card-head"><div><b>night-shift <span class="muted">· review suite</span></b>'
        '<span class="mono">github.com/your-team/agentpaddock @ 3f2a9c1…</span></div>'
        + kit.badge("self-reported", size="sm")
        + "</div>"
        '<div class="score"><b>5 of 6</b><span>races passed · fuel unknown until the '
        "runner counts tokens</span></div></article>"
    )


def _entries_panel() -> str:
    return (
        '<div class="tabpanel" role="tabpanel" id="tab-entries" aria-labelledby="tabbtn-entries">'
        '<h2 class="tab-title">Entries</h2>'
        "<p>An entry is one driver, one car and one engine map. The driver is the model, the "
        "car is your harness setup, written down so exactly that it has a fingerprint, and "
        "the engine map is the effort. <b>Change any one and it is a different entry.</b></p>"
        '<article class="tile example">'
        + _example_tag()
        + '<div class="who-line"><span class="car-number">27</span>'
        "<div><b>your-team / night-shift</b><span>car number 27 · livery car-opencode</span></div></div>"
        + _facts(
            (
                ("Driver", "provider/model-id"),
                ("Car", "opencode · sha256:9c41…e07a"),
                ("Engine map", "high"),
                ("Tires", "locks/oss-opencode.lock.json"),
                ("Briefing", "custom · one public line"),
                ("Team radio", "2 skills, pinned by version"),
            )
        )
        + "</article>"
        + kit.tick_list(
            (
                "The briefing can stay private: hand in its fingerprint and one public line, "
                "never the text.",
                "Skills ride on the team radio. They count toward the car's fingerprint, but "
                "the board does not score them yet.",
                "Pick a car number from 1 to 99 and a livery colour. The colour names your car; "
                "it never means a result.",
            )
        )
        + "</div>"
    )


def _tracks_panel() -> str:
    return (
        '<div class="tabpanel" role="tabpanel" id="tab-tracks" aria-labelledby="tabbtn-tracks">'
        '<h2 class="tab-title">Tracks</h2>'
        "<p>A track is a new race: your task, its stewards and a public card that says what "
        "the race is without giving the answer away. <b>Every community track starts as an "
        "exhibition.</b> It is raced and shown, but never counts in the standings until a "
        "maintainer puts it on the calendar.</p>"
        '<article class="tile example">'
        + _example_tag()
        + '<div class="who-line">'
        + glyphs.roundel("hard-review", labelled=False, size="lg")
        + '<div><span class="kicker">Hard review · exhibition</span>'
        '<span class="track-name">Your circuit</span></div></div>'
        '<p class="note">One sentence that says what the race is, with no hint at the answer.</p>'
        '<div class="checks">'
        "<div><span>1 · Reads every file</span><span>shown with its clear rate</span></div>"
        "<div><span>2 · Finds the real defect</span><span>shown with its clear rate</span></div>"
        "<div><span>+ 2 more checks</span><span>counted, not named</span></div></div>"
        '<p class="note">Difficulty: not enough runs yet. It needs 10 counted runs before it '
        "gets a rating from 1 to 5.</p></article>"
        '<h3 class="sr-title">Before a maintainer can promote it, a track must</h3>'
        + kit.tick_list(
            (
                "pass the reference answer and fail an empty one;",
                "never copy the answer into the driver's sandbox;",
                "give the same grade twice for the same hand-in;",
                "run offline and inside its track limits;",
                "be MIT licensed, like the rest of the races.",
            )
        )
        + "</div>"
    )


def _forks_panel(entries: Sequence[Mapping[str, Any]]) -> str:
    card = real_card(entries[0]) if entries else _example_fork_card()
    return (
        '<div class="tabpanel" role="tabpanel" id="tab-forks" aria-labelledby="tabbtn-forks">'
        '<h2 class="tab-title">Fork results</h2>'
        "<p>Raced it on your own fork? Hand in the results file with the fork's address and "
        "exact commit, so anyone can check your work. <b>Every result shows a badge for how "
        "far it has been checked.</b></p>"
        + card
        + '<h3 class="sr-title">The trust ladder</h3>'
        + kit.ladder()
        + '<p class="note">The community board shows every badge. The main standings count '
        "only "
        + kit.badge("reproduced", size="sm")
        + " and "
        + kit.badge(HOUSE, size="sm")
        + ".</p></div>"
    )


TABS = (("entries", "Entries"), ("tracks", "Tracks"), ("forks", "Fork results"))


def _board(entries: Sequence[Mapping[str, Any]], documents: int, root: str) -> str:
    if not entries:
        return (
            '<section class="board-dark" aria-labelledby="board-title">'
            '<span class="kicker">The board is empty for now</span>'
            '<h2 id="board-title" class="big-line">No community results yet. The first one '
            "gets the pole.</h2>"
            f'<a class="btn" href="{root}race-it-yourself/index.html">Race it yourself</a>'
            "</section>"
        )
    noun = "document" if documents == 1 else "documents"
    return (
        '<section aria-labelledby="board-title">'
        '<span class="kicker">The community board</span>'
        '<h2 id="board-title">Every community result, with its badge.</h2>'
        f'<p class="note">{len(entries)} {"entry" if len(entries) == 1 else "entries"} from '
        f"{documents} handed-in results {noun}, highest badge first.</p>"
        '<div class="board-cards">'
        + "".join(real_card(entry) for entry in entries)
        + "</div></section>"
    )


def body(route: Route, data: Mapping[str, Any]) -> str:
    root = layout.up(route.file)
    entries = community_entries(data)
    documents = community_documents(data)
    tabs = "".join(
        f'<button type="button" role="tab" class="chip" id="tabbtn-{key}" '
        f'aria-controls="tab-{key}" aria-selected="false">{esc(label)}</button>'
        for key, label in TABS
    )
    howto = "".join(
        f'<li><span class="glyph-box">{glyphs.icon(glyph)}</span>{esc(label)}</li>'
        for glyph, label in (
            ("pit-lane", "1 · Fork it"),
            ("start-lights", "2 · Race it"),
            ("flag-checkered", "3 · Hand it in"),
            ("scrutineering", "4 · Get a badge"),
        )
    )
    examples = (
        "Cards marked EXAMPLE show the shape of a submission, not a real one. "
        if not entries
        else "Cards marked EXAMPLE show the shape of a submission; the board above is real. "
    )
    return (
        kit.hero(
            "entry",
            "Bring your own car. Or your own track.",
            "Anyone can enter a car, build a new race or hand in results from their own fork. "
            "The same stewards check all of it, and a badge says how far.",
        )
        + f'<ol class="steps4" aria-label="How it works">{howto}</ol>'
        + '<section data-tabs aria-label="What to bring">'
        f'<div class="tabbar" role="tablist" aria-label="What to bring" hidden>{tabs}</div>'
        + _entries_panel()
        + _tracks_panel()
        + _forks_panel(entries)
        + "</section>"
        + _board(entries, len(documents), root)
        + f'<p class="note">{examples}The rules here are the '
        f'<a href="{layout.REPO_URL}/blob/main/CONTRIBUTING.md">contributing guide</a>.</p>'
    )


def page(results: Any, data: Mapping[str, Any], routes: Sequence[Route]) -> str:
    route = kit.route(routes, PATH)
    return kit.page(route, routes, body(route, data))
