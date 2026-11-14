"""What the data pages share (PDW-08): the parsed site data and display labels.

A data page reads only the JSON files the data layer writes (``data.py``), so
every number it shows came from ``scripts/leaderboard.py``. This module parses
those files once per build (:class:`SiteData`) and turns their ids into words:
a driver's provider id into a model name, a car's harness name into its product
name, an engine map into its label and glyph, and a default engine map into
the setting it ran at (:data:`DEFAULT_SETTINGS`). It holds no result of its own.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from . import glyphs

#: The six engine maps the garage offers, in ladder order.
LEVELS = ("default", "low", "medium", "high", "xhigh", "max")

#: Car (harness name) -> product name, where title case would get it wrong.
CAR_NAMES = {"claude-code": "Claude Code", "opencode": "OpenCode", "pi": "Pi"}
#: Route -> the reasoning setting it runs when a request sends none, from the
#: route's documentation. ``None``: the route documents no default.
ROUTE_DEFAULTS: dict[str, str | None] = {
    "anthropic/claude-opus-5-5": "adaptive, medium",
    "anthropic/claude-sonnet-5-5": "adaptive, high",
    "openai/gpt-6.1-sol": "medium",
    "google/gemini-3.8-flash": "medium",
    "moonshot/kimi-k3": "max",
    "fireworks/kimi-k3": "max",
    "fireworks/qwen3.8-max": "xhigh",
    "zai/glm-5.2": "dynamic thinking, max",
    "baseten/glm-5.3-flash": "high",
    "together/deepseek-v4.1-flash": None,
    "openrouter/qwen3.8-27b": None,
    "fireworks/qwen3.8-27b": None,
}
#: A ``DEFAULT_SETTINGS`` route that stands for every route of its car.
ANY_ROUTE = "*"
#: ``(car, route)`` -> ``(level, who)``: the reasoning setting an entry at the
#: default engine map ran at. ``who`` is ``"car"`` when the car sent ``level``
#: and ``"route"`` when the car sent nothing, so the route's own default
#: applied. A level of ``None``, or a pair missing here, is "setting not
#: recorded": the site claims no setting it has no evidence for.
DEFAULT_SETTINGS: dict[tuple[str, str], tuple[str | None, str]] = {
    # Codex, from the runs' own session records (turn context effort): these
    # routes sent medium in every recorded run, except the two version splits
    # below. On fireworks/qwen3.8-max, Codex CLI 0.159.1 (Oct 11 onward) sent
    # medium in 101 runs; 0.124.0 (Oct 3 and Oct 6) sent none in 22 runs.
    # On zai/glm-5.2, the same versions sent medium in 90 runs and none in 42.
    **{
        ("codex", route): ("medium", "car")
        for route in (
            "anthropic/claude-opus-5-5",
            "anthropic/claude-sonnet-5-5",
            "baseten/glm-5.3-flash",
            "fireworks/kimi-k3",
            "fireworks/qwen3.8-max",
            "google/gemini-3.8-flash",
            "moonshot/kimi-k3",
            "together/deepseek-v4.1-flash",
            "zai/glm-5.2",
        )
    },
    # Codex, from the car's provider config: it sends none on this route, so
    # the route's documented default applies. openai/gpt-6.1-sol is absent:
    # what Codex sent there is not recorded.
    ("codex", "openrouter/qwen3.8-27b"): (
        ROUTE_DEFAULTS["openrouter/qwen3.8-27b"],
        "route",
    ),
    # OpenCode and Pi, from the car's provider config: they send none, so the
    # route's documented default applies.
    **{
        (car, route): (level, "route")
        for car in ("opencode", "pi")
        for route, level in ROUTE_DEFAULTS.items()
    },
    # Claude Code, from the car's documentation (model configuration): it sends
    # its own default, medium, to the 5.5 models. For any other route what
    # reached the model is not recorded.
    **{
        ("claude-code", route): ("medium", "car")
        for route in (
            "anthropic/claude-opus-5-5",
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-haiku-5-5",
        )
    },
    # Hermes, from the car's documentation: an empty agent.reasoning_effort
    # sends medium, on every route.
    ("hermes", ANY_ROUTE): ("medium", "car"),
}
#: Pair overrides and car/case explanations, filled with route and setting data.
DEFAULT_TIPS = {
    (
        "codex",
        "fireworks/qwen3.8-max",
    ): "Codex 0.159 sends its default thinking level, medium, when an entry sets none. 22 earlier runs on Codex 0.124 sent none, so Fireworks' default, xhigh, applied to those.",
    (
        "codex",
        "zai/glm-5.2",
    ): "Codex 0.159 sends its default thinking level, medium, when an entry sets none. 42 earlier runs on Codex 0.124 sent none, so Z.ai's default, dynamic thinking at max effort, applied to those.",
    (
        "codex",
        "car",
    ): "Codex sends its own default thinking level, {setting}, when an entry sets none.",
    (
        "claude-code",
        "car",
    ): "Claude Code sends its own default effort, {setting}, when an entry sets none.",
    (
        "hermes",
        "car",
    ): "Hermes sends its default thinking level, {setting}, when its setting is left empty.",
    (
        "pi",
        "route",
    ): "Pi sends no thinking level for this model, so {host}'s default applies: {setting}.",
    (
        "opencode",
        "route",
    ): "OpenCode's setup for this model sets no thinking level, so {host}'s default applies: {setting}.",
    (
        "codex",
        "route",
    ): "Codex has no thinking-level default for this model, so it sends none and {host}'s default applies: {setting}.",
    "no-default": "{car} sends no thinking level for this model, and {host} publishes no default, so the level that ran is not recorded.",
    "no-evidence": "The bench did not record which thinking level {car} sent to this model.",
    "no-route": "The bench did not record which thinking level {car} sent by default.",
    "no-car": "The car's default applies when an entry sets no thinking level.",
    "car": "{car} sends its own default thinking level, {setting}, when an entry sets none.",
    "route": "{car} sends no thinking level for this model, so {host}'s default applies: {setting}.",
}
FULL_SETTINGS = {
    "adaptive, medium": "adaptive thinking at medium effort",
    "adaptive, high": "adaptive thinking at high effort",
    "dynamic thinking, max": "dynamic thinking at max effort",
}
#: The label of a default engine map whose setting the bench cannot name.
NOT_RECORDED = "not recorded"
#: Car -> livery token in ``design/tokens.css``.
LIVERIES = {
    "claude-code": "car-cc",
    "codex": "car-codex",
    "opencode": "car-opencode",
    "pi": "car-pi",
    "antigravity": "car-agy",
}
#: Hosts that make the model they serve; any other host is a route to it.
MAKERS = frozenset({"anthropic", "google", "moonshot", "openai", "zai"})
#: Brand casing title case would get wrong.
BRANDS = {
    "deepseek": "DeepSeek",
    "glm": "GLM",
    "gpt": "GPT",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "zai": "Z.ai",
}


# ---------------------------------------------------------------- the data


@dataclass(frozen=True)
class SiteData:
    """The parsed data files of one build, by path under the site root."""

    files: Mapping[str, Any]

    @classmethod
    def parse(cls, files: Mapping[str, str]) -> "SiteData":
        return cls(
            {
                name: json.loads(text)
                for name, text in files.items()
                if name.startswith("data/")
            }
        )

    @property
    def standings(self) -> Mapping[str, Any]:
        return self.files["data/standings.json"]

    @property
    def races(self) -> Mapping[str, Any]:
        return self.files["data/races.json"]

    @property
    def community(self) -> Mapping[str, Any]:
        return self.files["data/community.json"]

    @property
    def live(self) -> bool:
        return self.standings["state"] == "live"

    @property
    def placed(self) -> list[Mapping[str, Any]]:
        """Placed entries, in standings order."""
        return list(self.standings["entries"])

    @property
    def partial(self) -> list[Mapping[str, Any]]:
        return list(self.standings["partial"])

    @property
    def driver_names(self) -> dict[str, str]:
        """Consistent labels across trusted and community entries."""
        return driver_names(
            entry["driver"]
            for entry in self.placed + self.partial + list(self.community["entries"])
        )

    @property
    def calendar(self) -> list[Mapping[str, Any]]:
        return [race for race in self.races["races"] if race["calendar"]]

    def challenges(self) -> list[tuple[str, int]]:
        """``(challenge, calendar races)`` in the calendar's race-type order."""
        counts: dict[str, int] = {}
        for race in self.calendar:
            counts[race["challenge"]] = counts.get(race["challenge"], 0) + 1
        return [(challenge, counts[challenge]) for challenge in challenge_order(counts)]


def challenge_order(challenges: Iterable[str]) -> list[str]:
    """Known race types in the calendar's order, then any others by name."""
    known = list(glyphs.RACE_TYPES)
    return sorted(
        set(challenges),
        key=lambda c: (known.index(c) if c in known else len(known), c),
    )


# ---------------------------------------------------------------- labels


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _word(word: str) -> str:
    if word in BRANDS:
        return BRANDS[word]
    if re.fullmatch(r"\d+b", word) or re.fullmatch(r"[a-z]\d+[a-z]", word):
        return word.upper()  # 27b -> 27B, a3b -> A3B
    if match := re.fullmatch(r"(\d+)bit", word):
        return f"{match[1]}-bit"
    if match := re.fullmatch(r"([a-z]+)(\d[\d.]*)", word):
        letters, number = match.groups()
        return (
            letters.upper() + number
            if len(letters) == 1  # k3 -> K3, v4.1 -> V4.1
            else f"{_word(letters)} {number}"  # qwen3.8 -> Qwen 3.8
        )
    return word[:1].upper() + word[1:]


def model_name(provider_id: str) -> str:
    """``openai/gpt-6.1-sol`` -> ``GPT-6.1 Sol``; ``anthropic/claude-opus-5-5`` ->
    ``Claude Opus 5.5``. Version digits split by dashes join with dots."""
    model = provider_id.partition("/")[2] or provider_id
    words: list[str] = []
    for part in model.split("-"):
        if part.isdigit() and words and re.fullmatch(r"\d[\d.]*", words[-1]):
            words[-1] += "." + part
        else:
            words.append(part)
    out = [_word(word) for word in words]
    if len(out) > 1 and out[0] == "GPT" and re.match(r"\d", out[1]):
        out[:2] = [f"GPT-{out[1]}"]
    return " ".join(out)


def host(provider_id: str) -> str:
    return provider_id.partition("/")[0]


def host_name(provider_id: str) -> str:
    name = host(provider_id)
    return BRANDS.get(name, name[:1].upper() + name[1:])


def routed(provider_id: str) -> bool:
    """Reached through a host that serves another maker's model."""
    return host(provider_id) not in MAKERS


def driver_names(provider_ids: Iterable[str]) -> dict[str, str]:
    """Each driver's display name; two that would read the same add their host."""
    ids = sorted(set(provider_ids))
    names = {provider_id: model_name(provider_id) for provider_id in ids}
    seen: dict[str, int] = {}
    for name in names.values():
        seen[name] = seen.get(name, 0) + 1
    return {
        provider_id: f"{name} · {host_name(provider_id)}" if seen[name] > 1 else name
        for provider_id, name in names.items()
    }


def driver_slug(provider_id: str) -> str:
    return slug(provider_id)


def car_name(car: str) -> str:
    return CAR_NAMES.get(car, " ".join(w[:1].upper() + w[1:] for w in car.split("-")))


def livery(car: str) -> str:
    """The car's livery colour as a CSS value."""
    token = LIVERIES.get(car)
    return f"var(--{token})" if token else "var(--viz-other)"


def default_setting(car: str, route: str | None) -> tuple[str | None, str] | None:
    """``(level, who)`` from :data:`DEFAULT_SETTINGS` for a default-map entry."""
    if route is not None and (car, route) in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[(car, route)]
    return DEFAULT_SETTINGS.get((car, ANY_ROUTE))


def map_label(engine_map: str, car: str | None = None, route: str | None = None) -> str:
    """Name the level and whose default applied, keeping the explanation in the tip."""
    if engine_map != "default":
        return engine_map
    if car is None:
        return "the car's default"
    setting = default_setting(car, route)
    if setting is None or setting[0] is None:
        return NOT_RECORDED
    level, who = setting
    if who == "car":
        return f"{level} ({car_name(car)} default)"
    return f"{level.split(', ')[-1]} ({host_name(route or '')} default)"


def map_tip(car: str | None = None, route: str | None = None) -> str:
    """The evidence behind a default-map label."""
    setting = default_setting(car, route) if car else None
    if car is None:
        template = DEFAULT_TIPS["no-car"]
    elif setting is None:
        template = DEFAULT_TIPS["no-route" if route is None else "no-evidence"]
    elif setting[0] is None:
        template = DEFAULT_TIPS["no-default"]
    else:
        template = DEFAULT_TIPS.get(
            (car, route),
            DEFAULT_TIPS.get((car, setting[1]), DEFAULT_TIPS[setting[1]]),
        )
    level = setting[0] if setting else None
    return template.format(
        car=car_name(car or ""),
        host=host_name(route or ""),
        setting=FULL_SETTINGS.get(level, level),
    )


def map_info(note: str, *, context: str = "", hidden: bool = False) -> str:
    """A keyboard reachable info button and its native, viewport bounded popover."""
    label = "About this engine map" + (f" for {context}" if context else "")
    return (
        f'<span class="map-info"{" hidden" if hidden else ""}>'
        f'<button type="button" class="map-info-button" aria-label="{esc(label)}" '
        f'aria-expanded="false">{glyphs.icon("info")}</button>'
        f'<span class="map-tip" popover="auto">{esc(note)}</span></span>'
    )


def tag(glyph: str, text: str, *, info: str = "") -> str:
    """``text`` in bold behind ``glyph``. The glyph stays on the line of the first
    word; the rest wraps like any text, so a narrow column never overflows."""
    first, space, rest = text.partition(" ")
    if info:
        if space:
            before, gap, last = rest.rpartition(" ")
            rest = f'{esc(before)}{gap}<span class="map-tail">{esc(last)}{info}</span>'
        else:
            first = esc(first)
            return (
                f'<b class="ent"><span class="ent-lead map-tail">'
                f"{glyphs.icon(glyph)}{first}{info}</span></b>"
            )
    else:
        rest = esc(rest)
    return (
        f'<b class="ent"><span class="ent-lead">{glyphs.icon(glyph)}{esc(first)}</span>'
        f"{space}{rest}</b>"
    )


def car_tag(car: str) -> str:
    """The car's name in bold behind the car glyph, so it reads as a car."""
    return tag("car", car_name(car))


def map_tag(engine_map: str, car: str | None = None, route: str | None = None) -> str:
    """The engine map's label in bold behind its engine-map glyph."""
    context = car_name(car) if car else ""
    if route:
        context += f" on {model_name(route)} via {host_name(route)}"
    return tag(
        map_glyph(engine_map),
        map_label(engine_map, car, route),
        info=map_info(map_tip(car, route), context=context.strip())
        if engine_map == "default"
        else "",
    )


def driver_tag(route: str, name: str | None = None) -> str:
    """The driver's display name in bold behind the driver glyph, so it reads as
    the driver. ``name`` overrides :func:`model_name`, for a page that has
    told two routes to one model apart (:func:`driver_names`)."""
    return tag("driver", name or model_name(route))


def entry_tags(car: str, engine_map: str, route: str | None = None) -> str:
    """``car at engine map level``, both parts tagged: how a page names an entry."""
    return f"{car_tag(car)} at engine map {map_tag(engine_map, car, route)}"


def map_glyph(engine_map: str) -> str:
    """The engine-map glyph whose needle shows the level; the plain gauge otherwise."""
    return f"engine-map-{engine_map}" if engine_map in LEVELS[1:] else "engine-map"


def pct(rate: float | None) -> str:
    return "–" if rate is None else f"{rate * 100:.1f}%"


def usd(value: float | None) -> str:
    return "no price" if value is None else f"${value:.3f}"


def ordinal(number: int) -> str:
    suffix = (
        "th"
        if 10 <= number % 100 <= 20
        else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    )
    return f"{number}{suffix}"


def place(entry: Mapping[str, Any]) -> str:
    if entry.get("place") is None:
        return "not placed"
    return ("=" if entry.get("tie") else "") + ordinal(entry["place"])


def plural(count: int, singular: str, plural_form: str = "") -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def join_words(words: list[str]) -> str:
    if len(words) < 2:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def challenge_label(challenge: str) -> str:
    known = glyphs.RACE_TYPES.get(challenge)
    return known[0] if known else challenge.replace("-", " ").capitalize()


def caveat(glyph: str, strong: str, text: str) -> str:
    """A short notice with its flag glyph, as the placeholder pages draw one."""
    return (
        f'<div class="caveat soon">{glyphs.icon(glyph)}'
        f"<p><strong>{esc(strong)}</strong> {text}</p></div>"
    )


def hero(glyph: str, title: str, lede: str) -> str:
    """The page hero every page shares: glyph, product eyebrow, title, one line."""
    return (
        '<div class="page-hero">'
        + glyphs.icon(glyph, "hero-glyph")
        + f'<span class="eyebrow">Agent Paddock</span><h1>{esc(title)}</h1>'
        + f'<p class="lede">{lede}</p></div>'
    )


def json_script(element_id: str, value: Any) -> str:
    """``value`` as an inert JSON block the page script reads; ``<`` is escaped
    so no string inside can close the element."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/json" id="{esc(element_id)}">{text}</script>'


#: Shared styles of the data pages: chips, the sideways-scroll cue and panels.
CSS = """
/* ---------- data pages: shared (PDW-08) ---------- */
.label { display: inline-flex; align-items: center; gap: 10px; font: 600 12px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--ink-2); }
.label .glyph { width: 26px; height: 26px; color: var(--ink); }
/* an entry's car and engine map: bold, the glyph kept on the line of the first word */
.ent { font-weight: 700; }
.ent-lead { display: inline-block; max-width: 100%; }
.ent svg.glyph { display: inline-block; width: 1.15em; height: 1.15em; margin-right: .2em; vertical-align: -.22em; }
.map-info { display: inline; }
.map-info[hidden] { display: none; }
.map-tail { white-space: nowrap; }
.map-info-button { display: inline-flex; vertical-align: -.15em; align-items: center; justify-content: center;
  box-sizing: content-box; font: inherit; line-height: 1; width: 1em; height: 1em;
  min-width: 0; min-height: 0; padding: max(0px, calc((24px - 1em) / 2));
  margin: calc(-1 * max(0px, (24px - 1em) / 2)) 0;
  border: 0; border-radius: 50%; background: transparent;
  color: inherit; cursor: pointer; }
.map-info-button svg.glyph { width: 1em; height: 1em; margin: 0; vertical-align: 0; }
.map-info-button:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
.map-tip { position: fixed; inset: auto 0 16px; margin: 0 auto; box-sizing: border-box;
  width: min(360px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); overflow: auto;
  white-space: normal; overflow-wrap: anywhere; padding: 14px 18px; border: 1px solid var(--rule);
  border-radius: 12px; background: var(--paper); color: var(--ink); font: 400 15px/1.5 var(--body);
  text-align: left; letter-spacing: normal; text-transform: none; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 14px; border-radius: 999px;
  border: 1.5px solid var(--rule); background: var(--paper); color: var(--ink); font: 600 15px/1.2 var(--body);
  text-decoration: none; cursor: pointer; white-space: nowrap;
}
.chip:hover { border-color: var(--ink-2); color: var(--ink); }
.chip[aria-pressed="true"], .chip[aria-current="page"], .chip[aria-checked="true"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.chip .stripe { width: 5px; height: 22px; border-radius: 2px; background: var(--c, transparent); flex: none; }
.chip .glyph { width: 26px; height: 26px; }
.chip[aria-disabled="true"] { opacity: .45; border-style: dashed; cursor: not-allowed; }
.chip[aria-disabled="true"]:hover { border-color: var(--rule); }
.hscroll { position: relative; }
.hscroll-track { display: flex; gap: 8px; overflow-x: auto; scrollbar-width: thin; padding-bottom: 4px; -webkit-overflow-scrolling: touch; }
.hscroll::after {
  content: ""; position: absolute; top: 0; right: 0; bottom: 4px; width: 40px; pointer-events: none;
  background: linear-gradient(to right, transparent, var(--paper)); transition: opacity .2s var(--ease);
}
.hscroll.at-end::after { opacity: 0; }
.scroll-cue { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.scroll-cue::after { content: "→"; }
.panel { padding: 22px; border-radius: 24px; background: var(--soft); display: grid; gap: 14px; }
.panel.dark { background: var(--tower); color: var(--tower-fg); }
.panel.dark .label { color: var(--tower-mute); }
.panel.dark .label .glyph { color: var(--tower-mute); }
.big-num { font: 700 64px/1 var(--display); font-variant-numeric: tabular-nums; }
.next-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.next-cards a { display: grid; gap: 6px; padding: 24px; border-radius: 22px; background: var(--soft); color: var(--ink); text-decoration: none; min-height: 44px; }
.next-cards a:first-child { background: var(--tower); color: var(--tower-fg); }
.next-cards a span { font: 600 12px/1.2 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.next-cards a:first-child span { color: var(--tower-mute); }
.next-cards a b { font: italic 800 32px/1 var(--display); }
.footnote { font-size: 14px; color: var(--muted); max-width: 72ch; }
@keyframes data-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.data-in { animation: data-in .4s var(--ease); }
@media (prefers-reduced-motion: reduce) { .data-in { animation: none; } .hscroll::after { transition: none; } }
"""

#: The sideways-scroll cue: fade the edge out once a strip reaches its end.
SCRIPT = """(function () {
  function finePointer() {
    return window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  }
  function showMapTip(button) {
    var note = button.nextElementSibling;
    note.showPopover({ source: button });
    if (!finePointer()) return;
    // Touching edges let the pointer reach the tip without crossing a dismissal gap.
    var anchor = button.getBoundingClientRect();
    var rect = note.getBoundingClientRect();
    var top = anchor.bottom + rect.height <= innerHeight - 16
      ? anchor.bottom : anchor.top - rect.height;
    note.style.inset = 'auto';
    note.style.margin = '0';
    note.style.left = Math.max(16, Math.min(anchor.left, innerWidth - rect.width - 16)) + 'px';
    note.style.top = Math.max(16, Math.min(top, innerHeight - rect.height - 16)) + 'px';
  }
  document.addEventListener('click', function (event) {
    var button = event.target.closest('.map-info-button');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    var note = button.nextElementSibling;
    var pinned = button.dataset.pinned === 'true';
    button.dataset.pinned = pinned ? 'false' : 'true';
    if (pinned) note.hidePopover();
    else showMapTip(button);
  });
  document.addEventListener('toggle', function (event) {
    if (!event.target.matches('.map-tip')) return;
    var button = event.target.previousElementSibling;
    button.setAttribute('aria-expanded', event.newState === 'open' ? 'true' : 'false');
    if (event.newState === 'closed') button.dataset.pinned = 'false';
  }, true);
  ['pointerover', 'pointerout'].forEach(function (kind) {
    document.addEventListener(kind, function (event) {
      if (!finePointer()) return;
      var info = event.target.closest('.map-info');
      if (!info || info.contains(event.relatedTarget)) return;
      var button = info.querySelector('.map-info-button');
      var note = button.nextElementSibling;
      if (kind === 'pointerover') {
        if (!document.querySelector('.map-info-button[data-pinned="true"]')) showMapTip(button);
      }
      else if (button.dataset.pinned !== 'true') note.hidePopover();
    });
  });
  var strips = document.querySelectorAll('[data-hscroll]');
  Array.prototype.forEach.call(strips, function (strip) {
    var track = strip.querySelector('[data-hscroll-track]') || strip;
    function update() {
      var end = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
      strip.classList.toggle('at-end', end);
    }
    track.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    update();
  });
})();
"""
