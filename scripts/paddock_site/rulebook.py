"""The rulebook (PDW-09): ``/rulebook/``, rebuilt from ``design/web/Rulebook.dc.html``.

Sixteen rules, each with its glyph, the rule and why it is there. Internal
metadata retains the source section it restates: a heading in the repository's
documentation or in the site's own methodology page. ``tests/test_build_site.py`` checks that every
named section exists, so a renamed heading fails the build's tests rather than
leaving a dead citation.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, NamedTuple, Sequence

from . import glyphs, kit, layout
from .layout import esc
from .routes import Route

PATH = "rulebook/"

#: Group id -> chip label, in chip order (from the prototype).
GROUPS: dict[str, str] = {
    "before": "Before the lights",
    "track": "On the track",
    "flag": "At the flag",
    "board": "On the board",
    "community": "Community",
}

#: The site's own methodology page; its sections are ``<h3>`` headings.
METHODOLOGY = "methodology.html"


class Source(NamedTuple):
    #: A repository-relative Markdown file, or :data:`METHODOLOGY`.
    document: str
    #: The heading text, exactly as written in that document.
    section: str

    @property
    def label(self) -> str:
        name = "Methodology" if self.document == METHODOLOGY else self.document
        return f"{name} · {self.section}"

    def href(self, current: str) -> str:
        if self.document == METHODOLOGY:
            return layout.href(METHODOLOGY, current)
        return f"{layout.REPO_URL}/blob/main/{self.document}#{slug(self.section)}"


def slug(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


class Rule(NamedTuple):
    group: str
    glyph: str
    title: str
    rule: str
    why: str
    source: Source


GUIDE = "design/guide/README.md"
CONTRIBUTING = "CONTRIBUTING.md"


def rules(races: int = 0) -> list[Rule]:
    """The sixteen rules, in the prototype's order; ``races`` sizes the calendar."""
    sample = (
        f"{races} races is a small sample."
        if races
        else "A calendar of races is a small sample."
    )
    return [
        Rule(
            "before",
            "briefing",
            "Same briefing, word for word",
            "Every entry gets exactly the same instructions and the same files.",
            "If one car got a better briefing, you would be measuring the briefing, not the driver.",
            Source(GUIDE, "A race in four sectors"),
        ),
        Rule(
            "before",
            "fence",
            "A sealed circuit",
            "Each race runs in its own sandbox with the same limits for everyone: 2 CPUs, "
            "2 GiB of memory, a read-only root and a time limit.",
            "Equal limits mean a result reflects the entry, not a bigger machine.",
            Source("design/guide/race-it-yourself.md", "Runners (PDK-01)"),
        ),
        Rule(
            "before",
            "stewards",
            "The answer is never in the car",
            "Hidden tests, planted defects and decoys stay outside the sandbox. The stewards "
            "grade afterwards, with checks the driver never sees.",
            "A driver that could read the checks could play to them; it sees only the format its answer must take.",
            Source(CONTRIBUTING, "Submit a track"),
        ),
        Rule(
            "track",
            "lap-time",
            "The clock is part of the race",
            "A run stopped by its harness's time limit is a DNF (did not finish), and it counts.",
            "Taking forever is a real failure for a coding agent, so it counts against the entry.",
            Source(METHODOLOGY, "What is scored"),
        ),
        Rule(
            "track",
            "pit-lane",
            "No help mid-race",
            "If a run had to be nudged to keep going and then finished, it is left out: it "
            "answered a changed prompt.",
            "A nudged run answered a different question from every other entry, so its "
            "result would not compare.",
            Source(METHODOLOGY, "What is scored"),
        ),
        Rule(
            "flag",
            "flag-checkered",
            "Every required check, or no flag",
            "A race is passed only when every required check passes. Partial credit is "
            "shown, but it is not a pass.",
            "Most real tasks are all or nothing: half a fix still breaks production.",
            Source("design/guide/races.md", "Partial-credit checks"),
        ),
        Rule(
            "flag",
            "dnf",
            "What counts as a DNF",
            "Handing in nothing, handing in an answer in the wrong shape or missing a "
            "required check all count as did not finish.",
            "Counting these keeps the board honest about what an entry delivers.",
            Source(METHODOLOGY, "What is scored"),
        ),
        Rule(
            "flag",
            "flag-red",
            "Red flag: not the model's fault",
            "A crashed host or a proxy restart mid-race is infrastructure. The run is left "
            "out and raced again.",
            "Blaming the model for the track's failure would punish whoever happened to be "
            "racing at the wrong moment.",
            Source(GUIDE, "How a run ended"),
        ),
        Rule(
            "flag",
            "flag-black",
            "Black flag: an integrity hold",
            "If something about a run looks off, it is held and checked before it counts.",
            "Some results are too surprising to publish unchecked.",
            Source("design/vocabulary.md", "Flags: how a run ended"),
        ),
        Rule(
            "board",
            "timing-tower",
            "The first result counts",
            "An entry's score is its first counted run of each race.",
            "If reruns counted, a lucky second try could lift a score. The first run is the "
            "one you would have got.",
            Source("design/web/README.md", "Site data (PDW-03)"),
        ),
        Rule(
            "board",
            "whisker",
            "Honest ranges, and ties",
            "Every pass rate comes with a 95% range. When two entries' ranges overlap, the "
            "board calls it a tie.",
            f"{sample.removesuffix('.')}, and the range says how sure the board can be.",
            Source(GUIDE, "Reading the board"),
        ),
        Rule(
            "board",
            "fuel-bill",
            "The fuel bill counts every run",
            "Fuel bill is dollars per race passed, including what the failed runs spent. "
            "An unknown price is never shown as zero.",
            "An entry that fails cheaply and often should not look cheap.",
            Source(METHODOLOGY, "Tokens and cost"),
        ),
        Rule(
            "board",
            "setup-sheet",
            "One change makes a new entry",
            "Driver, car and engine map define an entry. Change any one of them and it "
            "races as a different entry.",
            "Pooling different setups would blur exactly the differences the board is there "
            "to show.",
            Source(CONTRIBUTING, "Submit an entry"),
        ),
        Rule(
            "board",
            "rulebook",
            "Prove which model ran",
            "If a run cannot show which model it really used, it does not count.",
            "A result is only useful if you know whose result it is.",
            Source(CONTRIBUTING, "Submit results: a run bundle"),
        ),
        Rule(
            "community",
            "scrutineering",
            "Badges for community results",
            "Results from forks show how far they have been checked: self-reported, "
            "re-graded, reproduced or run by Agent Paddock itself.",
            "The main standings count only reproduced results and the ones Agent Paddock "
            "ran. The community board shows them all, each with its badge.",
            Source(CONTRIBUTING, "Trust levels"),
        ),
        Rule(
            "community",
            "race-calendar",
            "New tracks start as exhibitions",
            "A community track is raced and shown, but it never enters the standings until "
            "a maintainer puts it on the calendar.",
            "A new race has to prove its stewards are fair before it can move anyone's score.",
            Source(CONTRIBUTING, "Submit a track"),
        ),
    ]


def _rule(rule: Rule, current: str) -> str:
    return (
        f'<li data-group="{esc(rule.group)}"><details class="rule">'
        f"<summary>{glyphs.icon(rule.glyph)}"
        f'<span class="rule-text"><span class="kicker">{esc(GROUPS[rule.group])}</span>'
        f"<b>{esc(rule.title)}</b><span>{esc(rule.rule)}</span></span>"
        '<span class="sign" aria-hidden="true"></span></summary>'
        f'<div class="why"><p><b>Why:</b> {esc(rule.why)}</p>'
        "</div></details></li>"
    )


def body(route: Route, data: Mapping[str, Any]) -> str:
    listed = rules(kit.calendar_races(data))
    root = layout.up(route.file)
    counts = {"all": len(listed)} | {
        group: sum(rule.group == group for rule in listed) for group in GROUPS
    }
    return (
        kit.hero(
            "rulebook",
            "Fair racing, written down.",
            f"{len(listed)} rules decide what counts. Tap a rule to see why it is there.",
        )
        + '<section data-filter aria-label="The rules">'
        + kit.chips(
            "group",
            "When the rule applies",
            [("all", "All")] + list(GROUPS.items()),
            counts,
        )
        + '<ol class="rules">'
        + "".join(_rule(rule, route.file) for rule in listed)
        + "</ol></section>"
        '<section class="link-cards">'
        + kit.link_card(
            f"{root}learn/index.html", "See it work", "Watch a race, sector by sector"
        )
        + kit.link_card(
            f"{root}dictionary/index.html",
            "Lost in the jargon?",
            "The Agent Paddock dictionary",
        )
        + "</section>"
        f'<p class="note">Every rule restates <a href="{root}{METHODOLOGY}">the method</a> '
        f'and <a href="{layout.REPO_URL}/blob/main/CONTRIBUTING.md">the contributing guide</a>, '
        "which ship with the open-source repository.</p>"
    )


def page(results: Any, data: Mapping[str, Any], routes: Sequence[Route]) -> str:
    route = kit.route(routes, PATH)
    return kit.page(route, routes, body(route, data))
