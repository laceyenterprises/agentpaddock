"""The short page a route shows until its PDW page ticket lands (PDW-02).

It keeps every nav link resolving without building another ticket's page: the
route's glyph, its title, one line on what it will hold, and the way to
today's numbers. It carries no numbers of its own.
"""

from __future__ import annotations

from typing import Sequence

from . import glyphs, layout
from .routes import Route


def body(route: Route) -> str:
    root = layout.up(route.file)
    return (
        '<div class="page-hero">'
        + (glyphs.icon(route.nav_glyph, "hero-glyph") if route.nav_glyph else "")
        + f'<span class="eyebrow">{layout.PRODUCT}</span><h1>{layout.esc(route.title)}</h1>'
        + (f'<p class="lede">{layout.esc(route.summary)}</p>' if route.summary else "")
        + "</div>"
        '<div class="caveat soon">'
        + glyphs.icon("flag-yellow")
        + "<p><strong>Being built.</strong> This page is on its way. "
        "Until it lands, today's numbers are on the leaderboard.</p></div>"
        '<p class="soon-actions">'
        f'<a class="btn solid" href="{root}index.html">See the leaderboard</a>'
        f'<a class="btn ghost" href="{root}methodology.html">Read the methodology</a></p>'
    )


def page(route: Route, routes: Sequence[Route]) -> str:
    return layout.page(
        routes=routes,
        current=route.file,
        title=route.title,
        body=body(route),
        url=route.url,
    )
