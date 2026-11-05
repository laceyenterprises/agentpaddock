"""The Agent Paddock site package (PDW-02), stdlib only.

``scripts/build_site.py`` is the entry point; this package holds what every
page shares:

- ``routes``: the page registry (path, title, builder, nav label, nav glyph).
- ``layout``: the head, header, nav, menu panel and footer, and the stylesheet.
- ``glyphs``: the inlined glyph sprite, ``<use>`` helpers and the race-type roundel.
- ``placeholder``: the short page a route shows until its page ticket lands.
- ``data``: the site data layer (PDW-03), ``data/*.json`` computed from
  ``results/`` through ``scripts/leaderboard.py``, trust filter included.
- ``races``: the races grid and one page per calendar race (PDW-06), read
  from the site data.
- ``names``: the races' short names, plain words with acronyms spelled out.
- ``learn``: the Learn page (PDW-05), its labs and ``learn/learn.js``.

- ``views``: the parsed data files and display labels the data pages share.
- ``garage``, ``pit_wall``, ``drivers``: the data pages (PDW-08), built only
  from ``data/*.json``.

It is named ``paddock_site`` rather than ``site``: ``site`` is a standard
library module (imported at interpreter start, so a ``site`` package here
could never be imported), and the publication scan skips every directory
called ``site`` as built output.
"""
