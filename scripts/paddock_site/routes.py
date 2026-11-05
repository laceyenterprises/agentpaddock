"""The page registry (PDW-02): every route the site builds, and its place in the nav.

A route is a URL path under the site root, its title, the function that builds
its page, and where it shows in the navigation. ``design/web/README.md`` holds
the same table for readers.

Routes whose page ticket has not landed yet have no builder: the site builds a
short placeholder page for them (``placeholder.py``) so every nav link
resolves. A page ticket replaces the placeholder by passing its builder to
:func:`registry` from ``scripts/build_site.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

#: The public site; canonical and share URLs are absolute on it.
SITE_URL = "https://agentpaddock.ai/"

#: A page builder takes the loaded results and returns the whole page.
Builder = Callable[[Any], str]

#: Nav placements: in the bar at 720 px and wider and in the menu panel, or in
#: the menu panel only. Every other route is reached from the footer.
BAR = "bar"
PANEL = "panel"


@dataclass(frozen=True)
class Route:
    #: URL path under the site root: ``""`` for home, ``"learn/"`` for a
    #: directory page, ``"matrix.html"`` for a page kept at its old name.
    path: str
    title: str
    builder: Builder | None = None
    nav_label: str = ""
    #: A symbol id from ``design/glyphs/paddock-glyphs.svg``.
    nav_glyph: str = ""
    nav: str = ""
    #: The PDW ticket that builds (or replaces) this page.
    ticket: str = ""
    #: One line saying what the page is for; the placeholder shows it.
    summary: str = ""
    #: Footer group: ``"site"`` for the new pages, ``"board"`` for today's board,
    #: ``""`` for home, which the lockup links instead.
    footer: str = "site"

    @property
    def file(self) -> str:
        """The output file, relative to the site root."""
        if not self.path or self.path.endswith("/"):
            return self.path + "index.html"
        return self.path

    @property
    def url(self) -> str:
        return SITE_URL + self.path

    @property
    def label(self) -> str:
        return self.nav_label or self.title


ROUTES: tuple[Route, ...] = (
    # Home (PDW-04); every page links it through the lockup in its header and footer.
    Route("", "Home", ticket="PDW-04", nav_label="Home", footer=""),
    Route(
        "learn/",
        "Learn",
        nav_label="Learn",
        nav_glyph="track-map",
        nav=BAR,
        ticket="PDW-05",
        summary="How a race works, in four short labs you can play with.",
    ),
    Route(
        "races/",
        "Races",
        nav_label="Races",
        nav_glyph="race-calendar",
        nav=BAR,
        ticket="PDW-06",
        summary="Every race on the calendar, by challenge, and how each one went.",
    ),
    Route(
        "standings/",
        "Standings",
        nav_label="Standings",
        nav_glyph="timing-tower",
        nav=BAR,
        ticket="PDW-07",
        summary="The timing tower: every entry across every race, with honest ranges.",
    ),
    Route(
        "garage/",
        "Garage",
        nav_label="Garage",
        nav_glyph="setup-sheet",
        nav=BAR,
        ticket="PDW-08",
        summary="Build an entry: pick a driver (model), a car (harness) and an engine map (effort).",
    ),
    Route(
        "pit-wall/",
        "Pit wall",
        nav_label="Pit wall",
        nav_glyph="pit-wall",
        nav=BAR,
        ticket="PDW-08",
        summary="Where each entry wins and slips, side by side.",
    ),
    Route(
        "community/",
        "Community",
        nav_label="Community",
        nav_glyph="entry",
        nav=PANEL,
        ticket="PDW-09",
        summary="Bring your own car or your own track; every submitted result shows its trust badge.",
    ),
    Route(
        "drivers/",
        "Drivers",
        nav_label="Drivers",
        nav_glyph="driver",
        nav=PANEL,
        ticket="PDW-08",
        summary="The drivers (models) and how each one does in every car.",
    ),
    Route(
        "entries/",
        "An entry's season",
        nav_label="An entry's season",
        nav_glyph="podium",
        nav=PANEL,
        ticket="PDW-07",
        summary="One entry's whole season: every race, one flag each.",
    ),
    Route(
        "rulebook/",
        "Rulebook",
        nav_label="Rulebook",
        nav_glyph="rulebook",
        nav=PANEL,
        ticket="PDW-09",
        summary="How races are run, graded and counted.",
    ),
    Route(
        "dictionary/",
        "Dictionary",
        nav_label="Dictionary",
        nav_glyph="briefing",
        nav=PANEL,
        ticket="PDW-09",
        summary="The racing words and what each one measures.",
    ),
    Route(
        "race-it-yourself/",
        "Race it yourself",
        nav_label="Race it yourself",
        nav_glyph="pit-lane",
        nav=PANEL,
        ticket="PDW-09",
        summary="Run the bench on your own machine and submit a run bundle.",
    ),
    Route(
        "contact/",
        "Contact and about",
        nav_label="Contact and about",
        nav_glyph="pit-board",
        nav=PANEL,
        ticket="PDW-09",
        summary="Ask about the program, challenge a result or suggest a race.",
    ),
    # Today's board pages keep their names and stay reachable from the footer
    # until the page tickets above replace them.
    Route("leaderboard.html", "Leaderboard", ticket="PDW-07", footer="board"),
    Route("matrix.html", "Harness × model", footer="board"),
    Route("tasks.html", "Tasks", footer="board"),
    Route("changelog.html", "Changelog", footer="board"),
    Route("methodology.html", "Methodology", footer="board"),
)


def registry(
    builders: Mapping[str, Builder], routes: tuple[Route, ...] = ROUTES
) -> tuple[Route, ...]:
    """``routes`` with each path in ``builders`` built by its builder.

    A builder for a path no route declares is a mistake, not a new page: add
    the route here first.
    """
    known = {route.path for route in routes}
    unknown = sorted(set(builders) - known)
    if unknown:
        raise KeyError(f"builders for unregistered routes: {unknown}")
    return tuple(
        replace(route, builder=builders[route.path])
        if route.path in builders
        else route
        for route in routes
    )


def nav_routes(routes: tuple[Route, ...], *placements: str) -> list[Route]:
    """Routes in the given nav placements, in registry order."""
    return [route for route in routes if route.nav in placements]
