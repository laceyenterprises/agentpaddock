"""Contact and about (PDW-09): ``/contact/``, rebuilt from ``design/web/Contact.dc.html``.

The independence statement, what the results are and are not, and four ways
in: ask about the program, challenge a result, suggest a race or an entry, and
race it yourself. The page's glyph is ``pit-board``; ``team-radio`` keeps
meaning skills. Naming Lacey Enterprises here is operator-approved; personal
names, emails and accounts stay off the site.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote

from . import glyphs, kit, layout
from .layout import esc
from .routes import Route

PATH = "contact/"
EMAIL = "hello@agentpaddock.ai"
MAILTO = f"mailto:{EMAIL}"
CHALLENGE_SUBJECT = "Result challenge"
CHALLENGE_MAILTO = f"{MAILTO}?subject={quote(CHALLENGE_SUBJECT)}"
ISSUES_URL = f"{layout.REPO_URL}/issues"
GLYPH = "pit-board"


def _card(glyph: str, title: str, text: str, links: str, *, heading: str = "h3") -> str:
    return (
        '<div class="way">'
        f'<span class="glyph-box">{glyphs.icon(glyph)}</span>'
        f"<div><{heading}>{esc(title)}</{heading}><p>{text}</p>"
        f'<p class="way-links">{links}</p></div></div>'
    )


def _results_are(races: int) -> str:
    calendar = (
        f"a fixed calendar of {races} tasks" if races else "a fixed calendar of tasks"
    )
    return (
        f"They are AI-run benchmarks: AI agents race {calendar}, the same automated "
        "checks grade every run, and every pass rate carries an honest range. They are "
        "not a comprehensive ranking, a product review, or a prediction of how a model "
        "or harness will do on your own work: different tasks, settings or prompts can "
        "give a different order. Results change as entries are re-raced, tasks are fixed "
        "and new versions arrive, and every change is recorded in the changelog."
    )


def body(route: Route, data: Mapping[str, Any]) -> str:
    root = layout.up(route.file)
    about = (
        '<section id="about" aria-labelledby="about-title">'
        '<h2 id="about-title">About Agent Paddock</h2><div class="ways">'
        + _card(
            "setup-sheet",
            "Who runs Agent Paddock",
            "Agent Paddock is an open-source community project for shared learning and "
            "entertainment. Lacey Enterprises runs it. It has no partnership, sponsorship "
            "or commercial relationship with any company whose models or harnesses appear "
            "on the board, and no financial interest in the project or its results. The "
            "tasks, the grading code and every counted run are published, so anyone can "
            "check a result or race it themselves.",
            f'<a href="{layout.REPO_URL}">The open-source repository</a>',
        )
        + _card(
            "rulebook",
            "What the results are, and are not",
            esc(_results_are(kit.calendar_races(data))),
            f'<a href="{root}rulebook/index.html">Read the method</a>'
            f'<a href="{root}learn/index.html">How a race works</a>'
            f'<a href="{root}changelog.html">The changelog</a>',
        )
        + "</div></section>"
    )
    ways = (
        '<section aria-labelledby="ways-title"><h2 id="ways-title">Get in touch</h2>'
        '<div class="ways two">'
        + _card(
            GLYPH,
            "Ask about the program",
            "For labs, tool makers, press, and anyone with a question about how Agent "
            "Paddock works or how an entry was run.",
            f'<a href="{MAILTO}">{EMAIL}</a>',
        )
        + _card(
            "stewards",
            "Challenge a result",
            "Think a result is wrong? Send the entry, the race, and the run's fingerprint "
            "from its setup sheet. We re-check the run, race it again if needed, and record "
            "any correction in the changelog.",
            f'<a href="{CHALLENGE_MAILTO}">Email the stewards</a>'
            f'<a href="{ISSUES_URL}">Or open a public issue</a>',
        )
        + _card(
            "race",
            "Suggest a race or an entry",
            "Ideas for new races, harnesses or models are welcome. Raise them in public so "
            "others can join in.",
            f'<a href="{ISSUES_URL}">Suggest it on GitHub</a>'
            f'<a href="{root}community/index.html">See community entries</a>',
        )
        + _card(
            "pit-lane",
            "Race it yourself",
            "Run the same races on your own machine and send in the result. It is badged "
            "by how far it has been checked.",
            f'<a href="{root}race-it-yourself/index.html">Open the pit lane</a>',
        )
        + "</div></section>"
        '<p class="note">This is a small community project. We read every message, but '
        "replies can take a few days.</p>"
    )
    return (
        kit.hero(
            GLYPH,
            "Talk to Agent Paddock.",
            "Questions about the program, challenges to a result, and ideas for new "
            "races all start here.",
        )
        + about
        + ways
    )


def page(results: Any, data: Mapping[str, Any], routes: Sequence[Route]) -> str:
    route = kit.route(routes, PATH)
    return kit.page(route, routes, body(route, data))
