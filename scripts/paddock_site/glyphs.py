"""The glyph sprite, ``<use>`` helpers and the race-type roundel (PDW-02).

Every icon on the site comes from ``design/glyphs/paddock-glyphs.svg``. A page
inlines that sprite exactly once (:func:`sprite`) and then points at a symbol
with :func:`icon`; nothing else draws an icon.

The one addition is the race-type roundel: the published ``roundel`` frame
with a race-type mark inside it, as drawn in the approved prototype
(``design/web/Races.dc.html``). The seven marks below are copied from that
prototype unchanged; they are not in the published set, so they ride in the
same inlined sprite and are only ever drawn inside a roundel.
"""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

DESIGN = Path(__file__).resolve().parents[2] / "design"
SPRITE_PATH = DESIGN / "glyphs" / "paddock-glyphs.svg"

#: The symbol markup the published glyph sprite uses for every glyph.
_SYMBOL = (
    '<symbol id="{id}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="butt" stroke-linejoin="miter">{body}</symbol>'
)
_ACCENT = 'style="stroke:var(--pdk-accent, currentColor)"'
_ACCENT_FILL = 'stroke="none" style="fill:var(--pdk-accent, currentColor)"'
_INK_FILL = 'fill="currentColor" stroke="none"'
_RED = 'style="stroke:var(--pdk-red, #E5202E)"'

#: Race type -> (label, mark id, mark body). Order is the races calendar's order.
#: Hard fix and hard review draw their signal stroke in red.
RACE_TYPES: dict[str, tuple[str, str, str]] = {
    "coding": (
        "Coding",
        "coding-worker",
        '<path d="M7 4H5.5A1.5 1.5 0 0 0 4 5.5V10l-1.5 2L4 14v4.5A1.5 1.5 0 0 0 5.5 20H7'
        'M17 4h1.5A1.5 1.5 0 0 1 20 5.5V10l1.5 2-1.5 2v4.5a1.5 1.5 0 0 1-1.5 1.5H17"/>'
        f'<path d="M8.5 9h5M8.5 12.5h3"/><rect x="13" y="11" width="3" height="3" {_ACCENT_FILL}/>',
    ),
    "fix": (
        "Fix",
        "diff",
        f'<path d="M8 3v8M4 7h8" {_ACCENT}/><path d="M12 17h8"/><path d="M4.5 20.5 19.5 3.5"/>',
    ),
    "hard-fix": (
        "Hard fix",
        "ci-failed",
        f'<rect x="2" y="9.5" width="5" height="5" rx="1" {_INK_FILL}/>'
        f'<rect x="9.5" y="9.5" width="5" height="5" rx="1" {_INK_FILL}/>'
        f'<path d="M7 12h2.5M14.5 12h1.8"/><path d="M17 9.5l4.5 5M21.5 9.5 17 14.5" {_RED}/>',
    ),
    "review": (
        "Review",
        "review",
        '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.2 15.2 21 21"/>'
        f'<path d="M7.6 10.7l2 2 3.6-3.6" {_ACCENT}/>',
    ),
    "hard-review": (
        "Hard review",
        "red-team",
        '<circle cx="9" cy="15" r="6.5"/><circle cx="9" cy="15" r="2"/>'
        f'<path d="M11.8 12.2 21 3M11.8 7.2v5h5" {_RED}/>',
    ),
    "docs": (
        "Docs",
        "documents",
        '<rect x="7" y="3" width="13" height="15" rx="2"/><path d="M4 7v12a2 2 0 0 0 2 2h10"/>'
        '<path d="M10.5 8h6M10.5 12h6"/>',
    ),
    "rca": (
        "Root cause",
        "search",
        '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.2 15.2 21 21"/>',
    ),
}

#: Race types whose mark carries the red signal stroke.
RED_RACE_TYPES = frozenset({"hard-fix", "hard-review"})

_SYMBOL_RE = re.compile(r"<symbol\b[^>]*>.*?</symbol>", re.DOTALL)
_ID_RE = re.compile(r'<symbol\b[^>]*\bid="([^"]+)"')


@lru_cache(maxsize=1)
def _published_symbols(path: Path) -> tuple[str, ...]:
    """Read and parse a sprite once per build process."""
    return tuple(_SYMBOL_RE.findall(path.read_text(encoding="utf-8")))


def published_symbols(path: Path = SPRITE_PATH) -> list[str]:
    """The published symbols in file order, as a fresh list of cached markup."""
    return list(_published_symbols(path))


def published_ids(path: Path = SPRITE_PATH) -> list[str]:
    """The symbol ids of the published sprite, in file order."""
    return [_ID_RE.match(symbol)[1] for symbol in published_symbols(path)]


def race_type_symbols() -> list[str]:
    return [
        _SYMBOL.format(id=mark, body=body) for _label, mark, body in RACE_TYPES.values()
    ]


def sprite(extra_symbols: str = "", path: Path = SPRITE_PATH) -> str:
    """The one inlined sprite for a page: the published glyphs, the race-type marks
    and ``extra_symbols`` (the brand mark), in a zero-size, hidden ``<svg>``.

    It is sized to zero rather than ``display:none`` so every browser still
    renders the symbols it holds.
    """
    published = published_symbols(path)
    clash = set(published_ids(path)) & {mark for _l, mark, _b in RACE_TYPES.values()}
    if clash:
        raise ValueError(
            f"race-type marks clash with published glyphs: {sorted(clash)}"
        )
    return (
        '<svg class="symbols" width="0" height="0" aria-hidden="true" focusable="false">'
        "<defs>"
        + extra_symbols
        + "".join(published)
        + "".join(race_type_symbols())
        + "</defs></svg>"
    )


def icon(glyph_id: str, css_class: str = "glyph", label: str = "") -> str:
    """``<svg><use href="#id"></svg>``: decorative unless ``label`` names it."""
    a11y = (
        f'role="img" aria-label="{html.escape(label, quote=True)}"'
        if label
        else 'aria-hidden="true" focusable="false"'
    )
    return (
        f'<svg class="{css_class}" viewBox="0 0 24 24" {a11y}>'
        f'<use href="#{html.escape(glyph_id, quote=True)}"/></svg>'
    )


def roundel(race_type: str, *, labelled: bool = True, size: str = "") -> str:
    """A race type's roundel: the published ``roundel`` frame with its mark inside.

    ``labelled`` gives it the race type's name for assistive technology; pass
    ``False`` when the label is already written beside it. ``size`` adds a
    ``roundel-<size>`` class (``sm``, ``lg``).
    """
    label, mark, _body = RACE_TYPES[race_type]
    classes = f"roundel roundel-{race_type}" + (f" roundel-{size}" if size else "")
    if race_type in RED_RACE_TYPES:
        classes += " roundel-red"
    a11y = (
        f'role="img" aria-label="{html.escape(label, quote=True)}"'
        if labelled
        else 'aria-hidden="true" focusable="false"'
    )
    return (
        f'<svg class="{classes}" viewBox="0 0 24 24" {a11y}><use href="#roundel"/>'
        f'<g transform="translate(5.4 5.4) scale(0.55)"><use href="#{mark}"/></g></svg>'
    )
