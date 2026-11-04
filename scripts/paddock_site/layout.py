"""The one page layout every route shares (PDW-02): head, header, nav and footer.

Mobile first, as in ``design/web/Main.dc.html``: below 720 px the header holds
the lockup and a menu button that opens a solid panel with a glyph per item;
at 720 px and wider the main items sit inline in the bar. The current page is
marked with ``aria-current="page"`` and the accent colour wherever it is linked.

Every link is relative to the page, so the site works at its root and opened
straight from disk.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Sequence

from . import glyphs
from .routes import BAR, PANEL, SITE_URL, Route, nav_routes

PRODUCT = "Agent Paddock"
REPO_URL = "https://github.com/laceyenterprises/agentpaddock"
GTM_CONTAINER_ID = "GTM-P52SNL3D"
#: The activation probe reads this marker to tell the PDW site from the old one.
SITE_MARKER = '<meta name="paddock-site" content="pdw-1">'
DESCRIPTION = (
    "An open leaderboard for coding agents: model × harness on real repository tasks."
)
CARD_NAME = "social-card.png"
CARD_SIZE = (1200, 630)
CARD_ALT = (
    "Agent Paddock: an open leaderboard for coding agents. Model × harness on real "
    "repository tasks, graded by a checker the agent never sees. agentpaddock.ai"
)
TOKENS_PATH = glyphs.DESIGN / "tokens.css"
SCRIPT_NAME = "paddock.js"

#: The Broadcast mark, drawn once in the sprite and used by every lockup.
MARK = """<g transform="translate(9 0) skewX(-10)">
    <rect x="18" y="16" width="64" height="68" rx="7" fill="var(--ink)"/>
    <rect x="26" y="26" width="8" height="12" fill="#007BFF"/><rect x="38" y="26" width="36" height="12" fill="var(--paper)"/>
    <rect x="26" y="44" width="8" height="12" fill="#00C27A"/><rect x="38" y="44" width="26" height="12" fill="var(--paper)"/>
    <rect x="26" y="62" width="8" height="12" fill="#E0703A"/><rect x="38" y="62" width="16" height="12" fill="var(--paper)"/>
  </g>"""

FOOTER_DISCLAIMER = (
    "AI-run benchmarks: a narrow measurement on a fixed set of races, not a "
    "comprehensive ranking of any model or harness."
)
FOOTER_INDEPENDENCE = (
    "Independent and open source (MIT). Lacey Enterprises has no partnerships with, "
    "and no financial interest in, any product on the board."
)
ANALYTICS_DISCLOSURE = (
    "This site uses Google Analytics to count visits. "
    "Google Tag Manager loads on every page; analytics cookies are disabled for "
    "visitors in the EEA, the UK, Switzerland and listed territories."
)

#: The menu button's two states, drawn inline as plain strokes.
MENU_STROKES = (
    '<svg class="icon-menu" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="butt" aria-hidden="true" focusable="false">'
    '<path d="M3 6h18M3 12h18M3 18h18"/></svg>'
)
CLOSE_STROKES = (
    '<svg class="icon-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="butt" aria-hidden="true" focusable="false">'
    '<path d="M5.5 5.5l13 13M18.5 5.5l-13 13"/></svg>'
)

FONTS = (
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    "&amp;family=JetBrains+Mono:wght@400;500;600&amp;family=Saira:ital,wght@1,500;1,800"
    '&amp;family=Saira+Condensed:ital,wght@0,600;0,700;1,800&amp;display=swap" rel="stylesheet">\n'
)

CONSENT_AND_TAG_MANAGER = (
    "<!-- Google consent defaults: analytics storage denied in the EEA, UK, Switzerland and listed territories; no consent UI; ad storage off everywhere -->\n"
    "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}\n"
    "gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'granted'});\n"
    "gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'denied',region:['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH','AX','GF','GP','MQ','RE','YT','MF','GI','JE','GG','IM']});</script>\n"
    "<!-- Google Tag Manager -->\n"
    "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':\n"
    "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],\n"
    "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=\n"
    "'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);\n"
    f"}})(window,document,'script','dataLayer','{GTM_CONTAINER_ID}');</script>\n"
    "<!-- End Google Tag Manager -->\n"
)

#: The menu: the button opens and closes the panel; Escape closes it and returns
#: focus; widening past 720 px closes it. Without JavaScript the button stays
#: hidden and the footer lists every page.
SCRIPT = """(function () {
  var button = document.querySelector('.nav-burger');
  var panel = document.getElementById('site-menu');
  if (!button || !panel) return;
  function set(open) {
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
    button.setAttribute('aria-label', open ? 'Close menu' : 'Menu');
    panel.hidden = !open;
  }
  button.hidden = false;
  button.addEventListener('click', function () {
    set(button.getAttribute('aria-expanded') !== 'true');
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') {
      set(false);
      button.focus();
    }
  });
  var wide = window.matchMedia('(min-width: 720px)');
  var close = function (event) { if (event.matches) set(false); };
  if (wide.addEventListener) wide.addEventListener('change', close);
  else if (wide.addListener) wide.addListener(close);
})();
"""

#: Layout styles. Tokens come first in the stylesheet (design/tokens.css), then
#: these aliases, then the page styles, then this layout.
TOKEN_ALIASES = """
/* Site aliases for the design tokens (design/tokens.css is the source of truth) */
:root {
  --display: "Saira Condensed", "Arial Narrow", "Roboto Condensed", system-ui, sans-serif;
  --body: "Inter", -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --radius-card: var(--radius-xl);
  --radius-inner: var(--radius-lg);
  --ease: cubic-bezier(.2, .8, .2, 1);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { color-scheme: dark; }
}
:root[data-theme="dark"] { color-scheme: dark; }
"""

CSS = """
/* ---------- layout: header, nav, menu panel, footer (PDW-02) ---------- */
.symbols { position: absolute; width: 0; height: 0; overflow: hidden; }
.glyph { width: 24px; height: 24px; flex: none; }

.nav {
  position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20;
  background: color-mix(in srgb, var(--paper) 94%, transparent);
  backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--rule);
}
.nav-bar { display: flex; align-items: center; gap: 16px; min-height: 64px; }
.brand { display: inline-flex; align-items: center; gap: 10px; color: var(--ink); text-decoration: none; flex: none; min-height: 44px; }
.brand:hover { color: var(--ink); }
.brand svg { width: 34px; height: 34px; display: block; flex: none; }
/* stacked lockup: both lines take the brand colour; weight alone separates them */
.wordmark {
  display: inline-flex; flex-direction: column; align-items: flex-start;
  font-family: "Saira", var(--display); font-style: italic; line-height: .95; text-transform: uppercase;
}
.wordmark-agent { font-weight: 500; font-size: 13px; letter-spacing: .04em; }
.wordmark-name { font-weight: 800; font-size: 21px; letter-spacing: .01em; }

.nav-wide { display: none; gap: 4px; margin-left: auto; align-items: center; }
.nav-wide a {
  display: inline-flex; align-items: center; min-height: 44px; padding: 0 14px; border-radius: 999px;
  color: var(--ink); text-decoration: none; font-size: 15px; font-weight: 500; white-space: nowrap;
  transition: background .2s var(--ease), color .2s var(--ease);
}
.nav-wide a:hover { background: var(--soft); color: var(--ink); }
.nav-wide a[aria-current="page"] { background: var(--blue-soft); color: var(--blue-ink); }

.nav-burger {
  margin-left: auto; width: 48px; height: 48px; padding: 0; display: inline-flex; align-items: center;
  justify-content: center; border-radius: 14px; border: 1.5px solid var(--rule); background: var(--paper);
  color: var(--ink); cursor: pointer;
}
.nav-burger svg { width: 26px; height: 26px; }
.nav-burger .icon-close { display: none; }
.nav-burger[aria-expanded="true"] .icon-menu { display: none; }
.nav-burger[aria-expanded="true"] .icon-close { display: block; }

.nav-panel {
  display: flex; flex-direction: column; padding: 4px 0 16px; background: var(--paper);
  max-height: calc(100dvh - 64px); overflow-y: auto; overscroll-behavior: contain;
  animation: nav-panel-in .25s var(--ease);
}
.nav-panel a {
  display: flex; align-items: center; gap: 14px; min-height: 56px; padding: 0 4px;
  border-bottom: 1px solid var(--rule); color: var(--ink); text-decoration: none; font-size: 19px; font-weight: 500;
}
.nav-panel a:last-child { border-bottom: 0; }
.nav-panel .glyph { width: 28px; height: 28px; }
.nav-panel a[aria-current="page"] { color: var(--blue-ink); }
.nav-panel a[aria-current="page"] .glyph { color: var(--blue); }
.nav-burger[hidden], .nav-panel[hidden] { display: none; }
@keyframes nav-panel-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .nav-panel { animation: none; } }

@media (min-width: 720px) {
  .nav-wide { display: flex; }
  .nav-burger, .nav-burger[hidden], .nav-panel, .nav-panel[hidden] { display: none; }
}
@media (max-width: 640px) {
  .nav .brand svg { width: 32px; height: 32px; }
  .nav .wordmark-agent { font-size: 12px; }
  .nav .wordmark-name { font-size: 19px; }
}

/* the race-type roundel: the roundel frame with its race-type mark inside */
.roundel { width: 24px; height: 24px; flex: none; color: var(--ink); }
.roundel-sm { width: 20px; height: 20px; }
.roundel-lg { width: 48px; height: 48px; }

.footer { border-top: 1px solid var(--rule); color: var(--muted); padding-block: 32px 40px; font-size: 14px; line-height: 1.5; }
.footer .brand { color: var(--ink); margin-bottom: 8px; }
.footer .brand svg { width: 46px; height: 46px; }
.footer .wordmark-agent { font-size: 17px; }
.footer .wordmark-name { font-size: 28px; }
.footer-notes { display: grid; gap: 6px; max-width: 72ch; }
.footer-notes p { margin: 0; color: var(--muted); }
.footer-links { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; margin-top: 24px; }
.footer-links h2 { margin: 0 0 2px; font: 600 11.5px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.footer-links ul { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0 18px; }
.footer-links a {
  display: inline-flex; align-items: center; min-height: 44px; color: var(--ink-2); font-weight: 600; text-decoration: none;
}
.footer-links a:hover { color: var(--ink); text-decoration: underline; }
.footer-links a[aria-current="page"] { color: var(--blue-ink); }
.footer-base { margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--rule); font: 400 12px/1.6 var(--mono); color: var(--muted); }
.footer-base p { margin: 0 0 6px; }
.footer-base a { color: var(--ink-2); }
@media (min-width: 720px) {
  .footer-links { grid-template-columns: minmax(0, 2fr) minmax(0, 1.2fr) minmax(0, 1fr); }
}

/* a page whose ticket has not landed yet */
.page-hero .hero-glyph { width: 48px; height: 48px; display: block; margin-bottom: 14px; color: var(--ink); }
.soon { display: flex; gap: 12px; align-items: flex-start; }
.soon .glyph { margin-top: 1px; }
.soon-actions { display: flex; flex-wrap: wrap; gap: 12px; }
"""


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def tokens_css(path: Path = TOKENS_PATH) -> str:
    """``design/tokens.css`` as committed: the stylesheet carries it exactly once."""
    return path.read_text(encoding="utf-8")


def stylesheet(page_css: str) -> str:
    """The site's one stylesheet: tokens, aliases, page styles, then the layout."""
    return tokens_css() + TOKEN_ALIASES + page_css + CSS


def up(current: str) -> str:
    """The relative prefix from ``current`` (an output file) back to the site root."""
    return "../" * current.count("/")


def href(target: str, current: str) -> str:
    """A link from the page at ``current`` to the output file ``target``."""
    return up(current) + target


def brand(root: str = "") -> str:
    """The one brand lockup: the Broadcast mark beside AGENT stacked over PADDOCK.

    The header and the footer both render this; the stylesheet sizes each.
    ``root`` is the relative prefix back to the site root.
    """
    return (
        f'<a class="brand" href="{root}index.html" aria-label="Agent Paddock home">'
        '<svg viewBox="0 0 100 100" aria-hidden="true"><use href="#broadcast"/></svg>'
        '<span class="wordmark"><span class="wordmark-agent">AGENT</span>'
        '<span class="wordmark-name">PADDOCK</span></span></a>'
    )


def sprite() -> str:
    """The page's one inlined sprite: the brand mark, the glyph set, the race-type marks."""
    return glyphs.sprite(
        f'<symbol id="broadcast" viewBox="0 0 100 100">{MARK}</symbol>'
    )


def social_meta(title: str, url: str) -> str:
    """Open Graph and Twitter card tags; every URL is absolute on agentpaddock.ai."""
    image = SITE_URL + CARD_NAME
    width, height = CARD_SIZE
    tags = (
        ("property", "og:type", "website"),
        ("property", "og:site_name", PRODUCT),
        ("property", "og:title", title),
        ("property", "og:description", DESCRIPTION),
        ("property", "og:url", url),
        ("property", "og:image", image),
        ("property", "og:image:width", str(width)),
        ("property", "og:image:height", str(height)),
        ("property", "og:image:type", "image/png"),
        ("property", "og:image:alt", CARD_ALT),
        ("name", "twitter:card", "summary_large_image"),
        ("name", "twitter:title", title),
        ("name", "twitter:description", DESCRIPTION),
        ("name", "twitter:image", image),
        ("name", "twitter:image:alt", CARD_ALT),
    )
    return "".join(
        f'<meta {kind}="{key}" content="{esc(value)}">\n' for kind, key, value in tags
    )


def _current(route: Route, current: str) -> str:
    return ' aria-current="page"' if route.file == current else ""


def nav_bar(routes: Sequence[Route], current: str) -> str:
    """The inline nav, shown at 720 px and wider."""
    links = "".join(
        f'<a href="{href(route.file, current)}"{_current(route, current)}>{esc(route.label)}</a>'
        for route in nav_routes(tuple(routes), BAR)
    )
    return f'<nav class="nav-wide" aria-label="Main">{links}</nav>'


def nav_panel(routes: Sequence[Route], current: str) -> str:
    """The menu panel below 720 px: every nav item, each with its glyph."""
    links = "".join(
        f'<a href="{href(route.file, current)}"{_current(route, current)}>'
        f"{glyphs.icon(route.nav_glyph)}{esc(route.label)}</a>"
        for route in nav_routes(tuple(routes), BAR, PANEL)
    )
    return (
        f'<nav class="nav-panel" id="site-menu" aria-label="Menu" hidden>{links}</nav>'
    )


def header(routes: Sequence[Route], current: str) -> str:
    return (
        '<header class="nav"><div class="wrap">'
        f'<div class="nav-bar">{brand(up(current))}{nav_bar(routes, current)}'
        '<button type="button" class="nav-burger" aria-expanded="false" '
        f'aria-controls="site-menu" aria-label="Menu" hidden>{MENU_STROKES}{CLOSE_STROKES}</button>'
        f"</div>{nav_panel(routes, current)}</div></header>"
    )


def _footer_group(title: str, links: str) -> str:
    return f"<div><h2>{esc(title)}</h2><ul>{links}</ul></div>"


def footer(routes: Sequence[Route], current: str) -> str:
    """Disclaimer, independence line, every page and the source links."""

    def item(route: Route) -> str:
        return (
            f'<li><a href="{href(route.file, current)}"{_current(route, current)}>'
            f"{esc(route.label)}</a></li>"
        )

    site = "".join(item(route) for route in nav_routes(tuple(routes), BAR, PANEL))
    board = "".join(item(route) for route in routes if route.footer == "board")
    source = (
        f'<li><a href="{REPO_URL}">GitHub</a></li>'
        f'<li><a href="{REPO_URL}/blob/main/CONTRIBUTING.md">CONTRIBUTING</a></li>'
        f'<li><a href="{REPO_URL}/blob/main/LICENSE">MIT LICENSE</a></li>'
    )
    return (
        '<footer class="footer"><div class="wrap">'
        f"{brand(up(current))}"
        f'<div class="footer-notes"><p>{FOOTER_DISCLAIMER}</p><p>{FOOTER_INDEPENDENCE}</p></div>'
        '<nav class="footer-links" aria-label="Footer">'
        + _footer_group("The site", site)
        + _footer_group("Today's board", board)
        + _footer_group("Source", source)
        + "</nav>"
        '<div class="footer-base">'
        "<p>Built only from the committed <code>results/</code> of "
        f'<a href="{REPO_URL}">laceyenterprises/agentpaddock</a>. '
        f'Submit a run or a correction: <a href="{REPO_URL}/blob/main/CONTRIBUTING.md">'
        "CONTRIBUTING</a>.</p>"
        f"<p>{ANALYTICS_DISCLOSURE}</p>"
        "<p>A Lacey Enterprises project · MIT licensed · © 2026 Lacey Enterprises LLC</p>"
        "</div></div></footer>"
    )


def page(
    *,
    routes: Sequence[Route],
    current: str,
    title: str,
    body: str,
    url: str,
    canonical: bool = False,
    main_class: str = "",
) -> str:
    """One whole page in the shared layout.

    ``current`` is the page's output file (``"learn/index.html"``), which sets
    every relative link and the ``aria-current`` marks; ``url`` is its public
    address for the share tags.
    """
    root = up(current)
    head_link = f'<link rel="canonical" href="{SITE_URL}">' if canonical else ""
    inner_class = f' class="{main_class}"' if main_class else ""
    inner = f"<div{inner_class}>\n{body}\n</div>"
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{SITE_MARKER}\n"
        f"<title>{esc(title)} · {PRODUCT}</title>\n{head_link}\n"
        + social_meta(f"{title} · {PRODUCT}", url)
        + CONSENT_AND_TAG_MANAGER
        + f'<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">\n'
        + FONTS
        + f'<link rel="stylesheet" href="{root}paddock.css">\n'
        f'<script src="{root}{SCRIPT_NAME}" defer></script>\n</head>\n<body>\n'
        f"{sprite()}\n"
        f"{header(routes, current)}\n"
        f'<main class="wrap">{inner}</main>\n'
        f"{footer(routes, current)}\n</body>\n</html>\n"
    )
