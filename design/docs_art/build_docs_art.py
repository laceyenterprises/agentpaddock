#!/usr/bin/env python3
"""Build the illustrations for the Agent Paddock docs: design/guide/assets/*-light.svg and *-dark.svg.

Every picture is drawn from the published glyph set (design/glyphs/paddock_glyphs.py) and the colour tokens
(design/tokens.json); nothing here draws a new icon. Each illustration is written twice, once per theme, so the
docs can embed both with <picture>. Wording comes from design/vocabulary.md.

    python3 design/docs_art/build_docs_art.py      # writes design/guide/assets/
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "design" / "glyphs"))
import paddock_glyphs as pg  # noqa: E402

OUT = ROOT / "design" / "guide" / "assets"
TOKENS = json.loads((ROOT / "design" / "tokens.json").read_text(encoding="utf-8"))["color"]
GLYPHS = {g[0]: g for g in pg.GLYPHS}
SANS = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
DISPLAY = "Saira, 'Arial Narrow', Arial, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, Menlo, monospace"


def tok(name: str, theme: str) -> str:
    return TOKENS[name][theme]


def glyph(gid: str, x: float, y: float, size: float, theme: str) -> str:
    """One glyph at (x, y), size px square, coloured for the theme."""
    body = GLYPHS[gid][4]
    ink, acc = tok("ink", theme), tok("blue", theme)
    body = body.replace("var(--pdk-accent, currentColor)", acc)
    body = re.sub(r"var\(--pdk-([a-z]+), (#[0-9A-Fa-f]{6})\)",
                  lambda m: tok(m.group(1), theme) if m.group(1) in TOKENS else m.group(2), body)
    body = body.replace("currentColor", ink)
    s = size / 24
    return (f'<g transform="translate({x:.1f} {y:.1f}) scale({s:.4f})" fill="none" stroke="{ink}" stroke-width="2" '
            f'stroke-linecap="butt" stroke-linejoin="miter">{body}</g>')


def text(x, y, s, size, theme, colour="ink", weight=400, family=SANS, italic=False, anchor="start"):
    st = ' font-style="italic"' if italic else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}"{st} '
            f'fill="{tok(colour, theme)}" text-anchor="{anchor}">{html.escape(s)}</text>')


def wrap(s: str, width: int) -> list[str]:
    out, line = [], ""
    for w in s.split():
        if len(line) + len(w) + 1 > width and line:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    return out + ([line] if line else [])


def frame(w: int, h: int, theme: str, title: str, eyebrow: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{html.escape(title)}">'
            f'<rect width="{w}" height="{h}" rx="24" fill="{tok("paper", theme)}" stroke="{tok("rule", theme)}"/>'
            + text(48, 64, eyebrow.upper(), 16, theme, "muted", 500, MONO)
            + text(48, 110, title, 40, theme, "ink", 800, DISPLAY, italic=True)
            + body + "</svg>")


def card(x, y, w, h, gid, name, lines, theme, tag=None):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{tok("soft", theme)}"/>',
           glyph(gid, x + 24, y + 24, 48, theme),
           text(x + 92, y + 52, name, 24, theme, "ink", 700)]
    for k, ln in enumerate(lines):
        out.append(text(x + 92, y + 84 + 28 * k, ln, 19, theme, "ink-2"))
    if tag:
        out.append(text(x + w - 24, y + 52, tag, 15, theme, "muted", 500, MONO, anchor="end"))
    return "".join(out)


def grid(items, cols, x0, y0, w, h, gap, theme, width_chars):
    out = []
    for i, it in enumerate(items):
        gid, name, desc = it[0], it[1], it[2]
        tag = it[3] if len(it) > 3 else None
        x = x0 + (i % cols) * (w + gap)
        y = y0 + (i // cols) * (h + gap)
        out.append(card(x, y, w, h, gid, name, wrap(desc, width_chars), theme, tag))
    return "".join(out)


ART = {}


def art(name):
    def deco(fn):
        ART[name] = fn
        return fn
    return deco


@art("cast")
def cast(theme):
    W = 1200
    top = [("driver", "Driver", "The model."), ("car", "Car", "The harness the model runs inside."),
           ("entry", "Entry", "One driver in one car at one engine map.")]
    out = []
    for i, (gid, name, desc) in enumerate(top):
        x = 48 + i * 376
        out.append(card(x, 150, 344, 150, gid, name, wrap(desc, 22), theme))
    out.append(text(48 + 344 + 16, 236, "+", 34, theme, "muted", 700, anchor="middle"))
    out.append(text(48 + 376 + 344 + 16, 236, "=", 34, theme, "muted", 700, anchor="middle"))
    kit = [("engine-map", "Engine map", "The effort level, from low to max."),
           ("tires", "Tires", "The tools the car mounts."),
           ("team-radio", "Team radio", "Skills: coaching during the lap."),
           ("briefing", "Briefing", "The instructions before the lap.")]
    out.append(grid(kit, 2, 48, 330, 544, 128, 16, theme, 42))
    out.append(text(48, 642, "Change the driver, the car or the engine map and it is a different entry.", 20, theme, "ink-2"))
    return frame(W, 680, theme, "The cast.", "Who races", "".join(out))


@art("race")
def race(theme):
    W = 1200
    secs = [("briefing", "1. The briefing", "Every entry gets the same task and code, word for word."),
            ("tires", "2. On the track", "The model reads, runs and edits with its tools. Nobody helps it."),
            ("lap-time", "3. The hand-in", "It writes its answer to one file and stops. Nothing more counts."),
            ("stewards", "4. The stewards", "Hidden checks grade the answer. Pass them all: checkered flag.")]
    out = [grid(secs, 2, 48, 150, 544, 150, 16, theme, 42)]
    out.append(text(48, 500, "The answer is never in the car: hidden tests, planted defects and decoys stay outside.", 20, theme, "ink-2"))
    return frame(W, 540, theme, "A race in four sectors.", "Track map", "".join(out))


@art("flags")
def flags(theme):
    W = 1200
    items = [("flag-checkered", "Checkered", "Finished: passed every required check.", "COUNTED"),
             ("dnf", "DNF", "Did not finish: failed a check or handed in nothing.", "COUNTED"),
             ("flag-red", "Red", "Infrastructure fault, not the model. Raced again.", "LEFT OUT"),
             ("flag-black", "Black", "An integrity hold, set aside for a person to review.", "LEFT OUT"),
             ("flag-yellow", "Yellow", "An audit is pending.", "NOT YET"),
             ("flag-green", "Green", "Lights out: running now.", "NOT A RESULT")]
    return frame(W, 600, theme, "How a run ended.", "Flags", grid(items, 2, 48, 150, 544, 128, 16, theme, 42))


@art("board")
def board(theme):
    W = 1200
    items = [("timing-tower", "Timing tower", "Entries ranked by average pass rate."),
             ("whisker", "Whiskers", "The 95% interval around every pass rate."),
             ("photo-finish", "Photo finish", "Intervals overlap: the board calls a tie."),
             ("fuel-bill", "Fuel bill", "Dollars per race passed, failed runs included."),
             ("slipstream", "Slipstream", "Cached tokens: most of the fuel, a fraction of the price."),
             ("pit-wall", "Pit wall", "One car's results, every driver and engine map.")]
    return frame(W, 600, theme, "Reading the board.", "Results", grid(items, 2, 48, 150, 544, 128, 16, theme, 42))


@art("fair")
def fair(theme):
    W = 1200
    items = [("fence", "Sealed circuit", "Each run in its own sandbox, the same for everyone."),
             ("track-limits", "Track limits", "The same CPU, memory and time caps."),
             ("stewards", "Stewards", "A grader whose checks the driver never sees."),
             ("setup-sheet", "Setup sheet", "The run's configuration and the hash that pins it."),
             ("rulebook", "Rulebook", "How races are built, run and graded."),
             ("shakedown", "Shakedown", "A first lap that proves a car starts and finishes.")]
    return frame(W, 600, theme, "Fair racing.", "Rulebook", grid(items, 2, 48, 150, 544, 128, 16, theme, 42))


@art("contribute")
def contribute(theme):
    W = 1200
    steps = [("pit-lane", "Race it yourself", "Run the same races on your own machine."),
             ("setup-sheet", "Send the bundle", "Submit the run bundle as a pull request."),
             ("scrutineering", "Scrutineering", "Badged by how far it has been checked.")]
    out = [grid(steps, 3, 48, 150, 352, 150, 16, theme, 22)]
    ladder = ["self-reported", "re-graded", "reproduced", "paddock run"]
    x = 48
    for k, rung in enumerate(ladder):
        fill = tok("blue", theme) if k == len(ladder) - 1 else tok("soft", theme)
        fg = "paper" if k == len(ladder) - 1 else "ink"
        out.append(f'<rect x="{x}" y="340" width="262" height="56" rx="28" fill="{fill}"/>')
        out.append(text(x + 131, 376, rung, 20, theme, fg, 600, anchor="middle"))
        if k < len(ladder) - 1:
            out.append(text(x + 262 + 12, 376, "→", 22, theme, "muted", 600, anchor="middle"))
        x += 286
    out.append(text(48, 446, "The main standings count reproduced and paddock-run results; the community board shows them all.", 20, theme, "ink-2"))
    return frame(W, 490, theme, "Race it yourself.", "Pit lane", "".join(out))


@art("glyph-sheet")
def glyph_sheet(theme):
    groups = {"cast": "The cast", "grip": "Grip", "flags": "Flags", "race": "The race", "rules": "Rules",
              "fuel": "Fuel", "running": "Running it", "new": "On the board"}
    rows = []
    for key, label in groups.items():
        ids = [g[0] for g in pg.GLYPHS if g[2] == key and not g[0].startswith("engine-map-")]
        if ids:
            rows.append((label, ids))
    W, label_w, th, gap = 1320, 150, 112, 10
    n = max(len(ids) for _, ids in rows)
    tw = (W - 96 - label_w - gap * (n - 1)) / n
    out = []
    y = 150
    for label, ids in rows:
        out.append(text(48, y + th / 2 + 8, label, 20, theme, "ink", 700))
        for k, gid in enumerate(ids):
            x = 48 + label_w + k * (tw + gap)
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{tw:.1f}" height="{th}" rx="14" fill="{tok("soft", theme)}"/>')
            out.append(glyph(gid, x + tw / 2 - 20, y + 14, 40, theme))
            out.append(text(f"{x + tw / 2:.1f}", y + 84, gid, 14, theme, "ink-2", 500, MONO, anchor="middle"))
        y += th + gap
    total = sum(len(ids) for _, ids in rows)
    out.append(text(48, y + 28, "Engine map also comes in five levels: low, medium, high, xhigh and max.", 19, theme, "ink-2"))
    return frame(W, y + 64, theme, f"The glyph set: {total} glyphs.", "Design", "".join(out))


# Page banners and card tiles for the guide pages (design/guide/*.md) and the README home page.
PAGES = {
    "how-it-works": ("track-map", "How it works", "The paddock in pictures.", "The cast, a race, the flags and the board."),
    "races": ("race-calendar", "The races", "Seven challenges, one calendar.", "How a race is built and graded, and every corpus."),
    "hard-races": ("scrutineering", "Hard races", "Review, fix and the hardest races.", "Decoys, several targets, and how they are graded."),
    "board": ("timing-tower", "The board", "How the standings are made.", "Trust, pass rates, intervals and fuel, from results."),
    "race-it-yourself": ("pit-lane", "Race it yourself", "Run the bench on your machine.", "Runners, host services, the layout and the tests."),
    "the-gym": ("fence", "Under the hood", "The gym behind the races.", "The out-of-band idea, scheduled runs and locks."),
    "batteries": ("fuel-gauge", "Batteries", "Efficiency batteries and reports.", "Model efficiency, matrix reports and the fix audit."),
    "design": ("setup-sheet", "Design", "How Agent Paddock looks and talks.", "Colour, type, glyphs, pictures and words."),
}


def banner(key, theme):
    gid, eyebrow, title, sub = PAGES[key]
    W, H = 1200, 200
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="{html.escape(eyebrow)}: {html.escape(title)}">'
            f'<rect width="{W}" height="{H}" rx="24" fill="{tok("paper", theme)}" stroke="{tok("rule", theme)}"/>'
            f'<rect x="40" y="40" width="120" height="120" rx="24" fill="{tok("soft", theme)}"/>'
            + glyph(gid, 64, 64, 72, theme)
            + text(196, 76, eyebrow.upper(), 16, theme, "muted", 500, MONO)
            + text(196, 122, title, 40, theme, "ink", 800, DISPLAY, italic=True)
            + text(196, 158, sub, 20, theme, "ink-2") + "</svg>")


def tile(key, theme):
    gid = PAGES[key][0]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120" role="img" '
            f'aria-label="{html.escape(PAGES[key][1])}"><rect width="120" height="120" rx="26" fill="{tok("soft", theme)}"/>'
            + glyph(gid, 30, 30, 60, theme) + "</svg>")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for theme in ("light", "dark"):
        for name, fn in ART.items():
            (OUT / f"{name}-{theme}.svg").write_text(fn(theme) + "\n", encoding="utf-8")
            n += 1
        for key in PAGES:
            (OUT / f"banner-{key}-{theme}.svg").write_text(banner(key, theme) + "\n", encoding="utf-8")
            (OUT / f"tile-{key}-{theme}.svg").write_text(tile(key, theme) + "\n", encoding="utf-8")
            n += 2
    print(f"wrote {n} files to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
