"""Shared pieces for the explainer pages (PDW-09): hero, chips, badges, styles, script.

Community, Race it yourself, Rulebook, Dictionary and Contact share a hero (the
page's glyph, the product eyebrow, a headline and one line), filter chips, the
trust badge and a small amount of vanilla JavaScript. Every page reads in full
without JavaScript: the script only adds tabs, filters, the step pager and the
copy buttons on top of content that is already there.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import glyphs, layout
from .layout import esc
from .routes import Route

#: Trust levels (CONTRIBUTING.md "Trust levels"), lowest first: the badge's
#: label, its class and what the level establishes, in visitor wording.
TRUST: dict[str, tuple[str, str, str]] = {
    "self-reported": (
        "Self-reported",
        "badge-self",
        "It validates and its costs are recomputed from committed prices.",
    ),
    "re-graded": (
        "Re-graded",
        "badge-regraded",
        "A maintainer graded the submitted answers with the pinned stewards.",
    ),
    "reproduced": (
        "Reproduced",
        "badge-reproduced",
        "A maintainer re-ran a sample and landed inside the entry's honest range.",
    ),
    "paddock-run": (
        "Paddock run",
        "badge-house",
        "Agent Paddock ran it on its own track.",
    ),
}


def badge(level: str, *, size: str = "") -> str:
    """A trust badge: the scrutineering glyph and the level's name."""
    label, css, _meaning = TRUST[level]
    extra = f" badge-{size}" if size else ""
    return (
        f'<span class="badge {css}{extra}">'
        + glyphs.icon("scrutineering")
        + f"{esc(label)}</span>"
    )


def ladder() -> str:
    """The trust ladder: every badge, lowest first, with what it establishes."""
    rows = "".join(
        f"<li>{badge(level)}<span>{esc(meaning)}</span></li>"
        for level, (_label, _css, meaning) in TRUST.items()
    )
    return f'<ol class="ladder">{rows}</ol>'


def hero(glyph: str, title: str, lede: str) -> str:
    """The page hero: glyph, the product eyebrow, the headline and one line."""
    return (
        '<div class="page-hero explain-hero">'
        + glyphs.icon(glyph, "hero-glyph")
        + f'<span class="eyebrow">{layout.PRODUCT}</span><h1>{esc(title)}</h1>'
        + f'<p class="lede">{lede}</p></div>'
    )


def chips(
    name: str,
    label: str,
    options: Sequence[tuple[str, str]],
    counts: Mapping[str, int] | None = None,
) -> str:
    """A row of filter chips (one radio group), hidden until the script runs.

    ``options`` is ``(value, label)`` pairs; the first is selected. The row
    scrolls sideways on a narrow screen and fades at its edge to say so.
    """
    buttons = "".join(
        f'<button type="button" role="radio" class="chip" data-value="{esc(value)}" '
        f'aria-checked="{"true" if index == 0 else "false"}">{esc(text)}'
        + (
            f' <span class="chip-n">{counts[value]}</span>'
            if counts is not None and value in counts
            else ""
        )
        + "</button>"
        for index, (value, text) in enumerate(options)
    )
    return (
        f'<div class="chips" role="radiogroup" aria-label="{esc(label)}" '
        f'data-chips="{esc(name)}" hidden>{buttons}</div>'
    )


def link_card(href: str, kicker: str, text: str) -> str:
    return (
        f'<a class="link-card" href="{esc(href)}"><span class="kicker">{esc(kicker)}</span>'
        f"<span>{esc(text)}</span></a>"
    )


def tick_list(items: Sequence[str]) -> str:
    """A short list with accent markers; ``items`` are already-escaped HTML."""
    return (
        '<ul class="ticks">' + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
    )


def page(
    route: Route, routes: Sequence[Route], body: str, *, main_class: str = ""
) -> str:
    return layout.page(
        routes=routes,
        current=route.file,
        title=route.title,
        body=body,
        url=route.url,
        main_class=("explain " + main_class).strip(),
    )


def route(routes: Sequence[Route], path: str) -> Route:
    return next(r for r in routes if r.path == path)


def calendar_races(data: Mapping[str, Any]) -> int:
    """How many races are on the calendar, from the site data (PDW-03)."""
    return int(data["data/races.json"]["basis"]["calendarRaces"])


CSS = """
/* ---------- explainer pages: community, race it yourself, rulebook, dictionary, contact (PDW-09) ---------- */
.explain { display: flex; flex-direction: column; gap: 40px; max-width: 880px; margin: 0 auto; }
.explain.wide { max-width: 1120px; }
.explain-hero { padding-bottom: 0; }
.explain-hero h1 { text-transform: none; font-size: clamp(44px, 11vw, 84px); line-height: .92; text-wrap: balance; }
.explain-hero .hero-glyph { width: 36px; height: 36px; color: var(--ink); }
.explain h2 { margin: 0 0 16px; font: 700 clamp(28px, 6vw, 36px)/1.05 var(--display); }
.explain section { min-width: 0; }
.find-row { display: grid; gap: 12px; margin-bottom: 16px; }
.explain .note { margin: 0; font-size: 14px; line-height: 1.5; color: var(--muted); }
.explain p { margin: 0; }
.muted { color: var(--muted); font-weight: 400; }
.kicker { font: 600 11.5px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.sr-title { margin: 0 0 12px; font: 700 24px/1.1 var(--display); }

/* filter chips: one radio group, scrolling sideways with a fade at the edge */
.chips {
  display: flex; gap: 8px; overflow-x: auto; padding: 2px 32px 4px 2px; scrollbar-width: none;
  -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 40px), transparent);
  mask-image: linear-gradient(90deg, #000 calc(100% - 40px), transparent);
}
.chips::-webkit-scrollbar { display: none; }
.chips[hidden] { display: none; }
[data-filter] > .chips { margin-bottom: 16px; }
.chip {
  flex: none; min-height: 44px; padding: 0 16px; border-radius: 999px; border: 1.5px solid var(--rule);
  background: var(--paper); color: var(--ink); font: 600 15px/1 var(--body); cursor: pointer; white-space: nowrap;
}
.chip[aria-checked="true"], .chip[aria-selected="true"], .chip[aria-current="step"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.chip-n { font: 500 12px var(--mono); opacity: .7; margin-left: 4px; }
@media (min-width: 1000px) { .chips { flex-wrap: wrap; -webkit-mask-image: none; mask-image: none; padding-right: 2px; } }

/* trust badges */
.badge {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px; flex: none;
  font: 600 14px/1.2 var(--body); white-space: nowrap; background: var(--soft); color: var(--ink);
}
.badge .glyph { width: 18px; height: 18px; }
.badge-sm { padding: 4px 10px; font-size: 13px; }
.badge-sm .glyph { width: 16px; height: 16px; }
.badge-regraded { background: var(--blue-soft); color: var(--blue-ink); }
.badge-reproduced { background: var(--blue); color: #FFFFFF; --pdk-accent: #FFFFFF; }
.badge-house { background: var(--ink); color: var(--paper); --pdk-accent: var(--paper); }
.ladder { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
.ladder li { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 12px; align-items: center; font-size: 14px; line-height: 1.45; color: var(--ink-2); }
.ladder .badge { justify-self: start; }
@media (max-width: 400px) { .ladder li { grid-template-columns: minmax(0, 1fr); gap: 6px; } }

/* cards */
.tile { display: flex; flex-direction: column; gap: 12px; padding: 20px; border-radius: 22px; border: 1.5px solid var(--rule); background: var(--paper); min-width: 0; }
.tile.example { border: 2px dashed var(--rule); }
.tag-example {
  align-self: flex-start; padding: 4px 10px; border-radius: 6px; background: #FFF4CC; color: #6B5200;
  font: 600 11px/1.2 var(--mono); letter-spacing: .12em;
}
.facts-list { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 6px 12px; margin: 0; font-size: 14px; line-height: 1.4; }
.facts-list dt { color: var(--muted); }
.facts-list dd { margin: 0; font: 400 13px/1.4 var(--mono); overflow-wrap: anywhere; }
.ticks { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.ticks li { position: relative; padding-left: 22px; font-size: 15px; line-height: 1.45; }
.ticks li::before { content: ""; position: absolute; left: 2px; top: .55em; width: 10px; height: 4px; border-radius: 2px; background: var(--blue); }
.link-cards { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
.link-card {
  display: flex; flex-direction: column; gap: 6px; padding: 18px 20px; min-height: 44px; border-radius: 20px;
  border: 1.5px solid var(--rule); color: var(--ink); text-decoration: none; font-weight: 600; font-size: 17px;
}
.link-card:hover { border-color: var(--ink); color: var(--ink); }
@media (min-width: 640px) { .link-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.glyph-box { width: 56px; height: 56px; border-radius: 16px; background: var(--soft); display: flex; align-items: center; justify-content: center; flex: none; }
.glyph-box .glyph { width: 30px; height: 30px; }

/* community */
.steps4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; list-style: none; margin: 0; padding: 0; }
.steps4 li { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; font-size: 14px; font-weight: 600; }
.steps4 .glyph-box { width: 64px; height: 64px; border-radius: 18px; }
.steps4 .glyph-box .glyph { width: 34px; height: 34px; }
.tabbar { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); padding: 4px; border-radius: 999px; background: var(--soft); margin-bottom: 18px; }
.tabbar[hidden] { display: none; }
.tabbar .chip { border: 0; background: transparent; padding: 0 8px; }
.tabbar .chip[aria-selected="true"] { background: var(--ink); color: var(--paper); }
.tabpanel { display: flex; flex-direction: column; gap: 16px; }
.tabpanel + .tabpanel { margin-top: 32px; }
.tabs-live .tabpanel + .tabpanel { margin-top: 0; }
.tabs-live .tab-title { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.tabpanel[hidden] { display: none; }
.tabpanel > p { font-size: 17px; line-height: 1.55; }
.car-number { width: 56px; height: 56px; border-radius: 14px; background: var(--car-opencode); color: var(--paper); display: flex; align-items: center; justify-content: center; font: italic 800 30px/1 "Saira", var(--display); flex: none; }
.who-line { display: flex; align-items: center; gap: 14px; min-width: 0; }
.who-line b { display: block; font-size: 18px; overflow-wrap: anywhere; }
.who-line span { font-size: 14px; color: var(--muted); }
.track-name { font: italic 800 28px/1 var(--display); display: block; }
.checks { display: grid; gap: 6px; font-size: 14px; }
.checks div { display: flex; justify-content: space-between; gap: 12px; }
.checks span + span { color: var(--muted); text-align: right; }
.score { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; }
.score b { font: 700 44px/1 var(--display); font-variant-numeric: tabular-nums; }
.score span { font-size: 14px; color: var(--muted); }
.card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.card-head b { font-size: 18px; overflow-wrap: anywhere; }
.card-head .mono { display: block; color: var(--muted); overflow-wrap: anywhere; }
.board-dark {
  display: flex; flex-direction: column; align-items: center; text-align: center; gap: 14px; padding: 32px 20px;
  border-radius: 28px; background: var(--tower); color: var(--tower-fg);
}
.board-dark .kicker { color: var(--tower-mute); }
.board-dark .big-line { font: italic 800 clamp(30px, 7vw, 44px)/1 var(--display); max-width: 560px; }
.board-dark .btn { margin-top: 6px; min-height: 52px; background: #3D9BFF; border-color: #3D9BFF; color: #0E1116; }
.board-dark .btn:hover { background: #7DBBFF; border-color: #7DBBFF; color: #0E1116; }
.board-cards { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
@media (min-width: 720px) { .board-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* race it yourself: six steps */
.pager { display: none; gap: 8px; margin-bottom: 16px; }
.steps-live .pager { display: flex; flex-wrap: wrap; }
.pager .chip { min-width: 0; max-width: 100%; padding: 0 12px; text-align: center; overflow: hidden; text-overflow: ellipsis; }
.pager-title { display: none; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
@media (min-width: 720px) {
  .pager .chip { padding: 0 16px; }
  .pager-title { display: inline; }
}
.pager .chip.done { background: var(--blue-soft); border-color: var(--blue-soft); }
.pager .chip[aria-current="step"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.steps { list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; }
.step { display: flex; flex-direction: column; gap: 14px; padding: 22px 20px; border-radius: 24px; border: 1.5px solid var(--rule); background: var(--paper); min-width: 0; }
.steps-live .step { cursor: pointer; animation: step-in .35s var(--ease); }
.steps-live .step[hidden] { display: none; }
.steps-live .step:hover { border-color: var(--ink-2); }
@keyframes step-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .steps-live .step { animation: none; } }
.step-head { display: flex; align-items: center; gap: 14px; }
.step-head .glyph { width: 40px; height: 40px; }
.step-head h3 { margin: 4px 0 0; font: italic 800 30px/1 var(--display); }
.step > p { font-size: 16px; line-height: 1.55; color: var(--ink-2); }
.step .notice { font-size: 14px; color: var(--ink-2); padding: 12px 14px; border-radius: 14px; background: var(--soft); }
.step-foot { display: none; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.steps-live .step-foot { display: flex; }
.code { position: relative; display: grid; grid-template-columns: minmax(0, 1fr); border-radius: 16px; background: #0E1116; color: #E6EAF0; cursor: auto; }
.code-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 10px 10px 0 18px; }
.code-tools[hidden] { display: none; }
.code pre { grid-area: 2 / 1; min-width: 0; margin: 0; padding: 18px; background: transparent; border-radius: 16px; font: 400 13px/1.6 var(--mono); white-space: pre; overflow-x: auto; color: inherit; }
.code.more::after {
  content: ""; grid-area: 2 / 1; justify-self: end; width: 48px; pointer-events: none;
  border-bottom-right-radius: 16px; background: linear-gradient(90deg, transparent, #0E1116);
}
.code-hint { display: none; font: 400 12px/1 var(--mono); color: #8B94A3; }
.code.more .code-hint { display: block; }
.copy {
  margin-left: auto; min-height: 44px; padding: 0 16px; border-radius: 12px;
  border: 1px solid #2A313C; background: #1A1F27; color: #F2F4F7; font: 600 14px/1 var(--body); cursor: pointer;
}
.copy[hidden] { display: none; }
.copy.ok { background: #1B3A2E; border-color: #2BD99A; }
.garage { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.garage span { padding: 10px 14px; border-radius: 999px; background: var(--soft); font-size: 15px; font-weight: 600; }
.aside { display: flex; gap: 12px; align-items: flex-start; font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.aside .glyph { margin-top: 1px; }

/* rulebook */
.rules { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.rules li[hidden] { display: none; }
.rule { border: 1.5px solid var(--rule); border-radius: 20px; padding: 0; margin: 0; background: var(--paper); }
.rule[open] { border-color: var(--blue); }
.rule summary { display: grid; grid-template-columns: 44px minmax(0, 1fr) 24px; gap: 14px; align-items: start; padding: 16px 18px; min-height: 44px; list-style: none; font-weight: 400; }
.rule summary::-webkit-details-marker { display: none; }
.rule summary .glyph { width: 30px; height: 30px; margin: 4px auto 0; }
.rule-text { display: grid; gap: 4px; min-width: 0; }
.rule-text b { font: italic 800 23px/1.05 var(--display); }
.rule-text span:last-child { font-size: 15px; line-height: 1.45; color: var(--ink-2); }
.rule .sign { font: 600 22px/1 var(--mono); color: var(--muted); text-align: center; margin-top: 4px; }
.rule .sign::before { content: "+"; }
.rule[open] .sign::before { content: "\\2212"; }
.rule .why { display: grid; gap: 8px; padding: 0 18px 18px 76px; font-size: 15px; line-height: 1.5; }
@media (max-width: 480px) { .rule .why { padding-left: 18px; } }

/* dictionary */
.find { display: grid; gap: 6px; max-width: 420px; font-size: 14px; font-weight: 600; }
.find[hidden] { display: none; }
.find input {
  min-height: 48px; padding: 0 16px; border-radius: 14px; border: 1.5px solid var(--rule);
  font: 400 16px var(--body); color: var(--ink); background: var(--paper);
}
.terms { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.terms li[hidden] { display: none; }
.term { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: 14px; align-items: start; padding: 18px; border-radius: 20px; border: 1.5px solid var(--rule); height: 100%; }
.term h3 { margin: 0; font: italic 800 24px/1.05 var(--display); }
.term div { display: grid; gap: 4px; min-width: 0; }
.term p { font-size: 15px; line-height: 1.45; color: var(--ink-2); }
.term a { display: inline-flex; align-items: center; min-height: 44px; font-size: 14px; font-weight: 600; }
.none[hidden] { display: none; }

/* contact */
.ways { display: grid; gap: 12px; }
.way { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: 16px; align-items: start; padding: 20px; border-radius: 22px; border: 1.5px solid var(--rule); }
.way h3 { margin: 0 0 6px; font: italic 800 26px/1.05 var(--display); }
.way p { font-size: 15px; line-height: 1.55; color: var(--ink-2); }
.way-links { display: flex; flex-wrap: wrap; gap: 0 18px; margin-top: 4px; }
.way-links a { display: inline-flex; align-items: center; min-height: 44px; font-weight: 600; }
@media (min-width: 900px) { .ways.two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
"""

#: Tabs, chips filters, the step pager and the copy buttons. Each block runs
#: only when its page holds the element it drives.
SCRIPT = r"""
(function () {
  function all(root, selector) { return Array.prototype.slice.call(root.querySelectorAll(selector)); }

  // Tabs (community): the tab bar shows, every panel but the chosen one hides.
  all(document, '[data-tabs]').forEach(function (box) {
    var bar = box.querySelector('.tabbar');
    var tabs = all(bar, '[role="tab"]');
    var panels = all(box, '[role="tabpanel"]');
    function pick(index, focus) {
      tabs.forEach(function (tab, k) {
        tab.setAttribute('aria-selected', k === index ? 'true' : 'false');
        tab.tabIndex = k === index ? 0 : -1;
        panels[k].hidden = k !== index;
      });
      if (focus) tabs[index].focus();
    }
    tabs.forEach(function (tab, k) {
      tab.addEventListener('click', function () { pick(k, false); });
      tab.addEventListener('keydown', function (event) {
        var step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        pick((k + step + tabs.length) % tabs.length, true);
      });
    });
    bar.hidden = false;
    box.classList.add('tabs-live');
    pick(0, false);
  });

  // Filters (rulebook, dictionary): chips and an optional search box.
  all(document, '[data-filter]').forEach(function (box) {
    var chips = box.querySelector('[data-chips]');
    var input = box.querySelector('input[type="search"]');
    var items = all(box, '[data-group]');
    var none = box.querySelector('.none');
    var group = 'all';
    function apply() {
      var q = input ? input.value.trim().toLowerCase() : '';
      var shown = 0;
      items.forEach(function (item) {
        var ok = (group === 'all' || item.getAttribute('data-group') === group) &&
          (!q || (item.getAttribute('data-text') || '').toLowerCase().indexOf(q) !== -1);
        item.hidden = !ok;
        if (ok) shown += 1;
      });
      if (none) none.hidden = shown !== 0;
    }
    if (chips) {
      var buttons = all(chips, '.chip');
      buttons.forEach(function (button) {
        button.addEventListener('click', function () {
          group = button.getAttribute('data-value');
          buttons.forEach(function (b) { b.setAttribute('aria-checked', b === button ? 'true' : 'false'); });
          apply();
        });
      });
      chips.hidden = false;
    }
    var find = box.querySelector('.find');
    if (find) find.hidden = false;
    if (input) input.addEventListener('input', apply);
    apply();
  });

  // Copy buttons: the clipboard, then the selection fallback, then
  // "Select and copy" with the code selected for the visitor to copy by hand.
  function select(node) {
    var range = document.createRange();
    range.selectNodeContents(node);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
  all(document, '.code').forEach(function (box) {
    var pre = box.querySelector('pre');
    var button = box.querySelector('.copy');
    if (!pre || !button) return;
    var timer = null;
    // A sideways-scrolling box fades at its edge until the end is in view.
    function edge() { box.classList.toggle('more', pre.scrollLeft + pre.clientWidth < pre.scrollWidth - 2); }
    pre.addEventListener('scroll', edge);
    window.addEventListener('resize', edge);
    edge();
    function mark(ok) {
      button.textContent = ok ? 'Copied' : 'Select and copy';
      button.classList.toggle('ok', ok);
      if (!ok) select(pre);
      clearTimeout(timer);
      timer = setTimeout(function () { button.textContent = 'Copy'; button.classList.remove('ok'); }, 1800);
    }
    function fallback() {
      try {
        select(pre);
        mark(document.execCommand('copy'));
      } catch (error) { mark(false); }
    }
    button.addEventListener('click', function () {
      var text = pre.textContent;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { mark(true); }, fallback);
        } else { fallback(); }
      } catch (error) { fallback(); }
    });
    button.hidden = false;
    button.parentElement.hidden = false;
  });

  // The step pager (race it yourself): one step at a time; a tap on the card
  // advances, except inside a code box, a link or a button.
  all(document, '[data-steps]').forEach(function (box) {
    var steps = all(box, '.step');
    var pills = all(box, '.pager .chip');
    var current = 0;
    function show(index, focus) {
      current = index;
      steps.forEach(function (step, k) { step.hidden = k !== index; });
      pills.forEach(function (pill, k) {
        if (k === index) pill.setAttribute('aria-current', 'step');
        else pill.removeAttribute('aria-current');
        pill.classList.toggle('done', k < index);
      });
      window.dispatchEvent(new Event('resize'));
      if (focus) steps[index].focus();
    }
    function next(focus) { show(current === steps.length - 1 ? 0 : current + 1, focus); }
    pills.forEach(function (pill, k) { pill.addEventListener('click', function () { show(k, false); }); });
    steps.forEach(function (step) {
      step.tabIndex = -1;
      step.addEventListener('click', function (event) {
        if (event.target.closest('.code, a, button, summary')) return;
        if (window.getSelection && String(window.getSelection())) return;
        next(false);
      });
      var button = step.querySelector('.next');
      if (button) button.addEventListener('click', function () { next(true); });
    });
    box.classList.add('steps-live');
    show(0, false);
  });
})();
"""


def code_box(code: str, label: str) -> str:
    """A code box with its copy button; the button shows once the script runs."""
    return (
        '<div class="code"><div class="code-tools" hidden>'
        '<span class="code-hint" aria-hidden="true">Scroll sideways →</span>'
        '<button type="button" class="copy" hidden aria-label="Copy the code to the clipboard">'
        "Copy</button></div>"
        f'<pre tabindex="0" aria-label="{esc(label)}"><code>{esc(code)}</code></pre></div>'
    )
