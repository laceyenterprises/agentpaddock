#!/usr/bin/env python3
"""Agent Paddock glyphs: line glyphs drawn in code by Lacey Enterprises, in the racing language of Agent Paddock.

    python3 paddock_glyphs.py            # writes paddock-glyphs.svg and index.json next to this file

Grammar: a 24 px grid, 2 px strokes in currentColor, butt caps, miter joins, at most one accent.
The accent reads --pdk-accent (Lacey blue in the design tokens; ink when unset). Signal fills read --pdk-green,
--pdk-red, --pdk-yellow and --pdk-black, with the design tokens' values as fallbacks.
Standard library only.
"""
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

A = 'style="stroke:var(--pdk-accent, currentColor)"'               # accent stroke (the primary colour)
AF = 'stroke="none" style="fill:var(--pdk-accent, currentColor)"'    # accent fill (the primary colour)
INK = 'fill="currentColor" stroke="none"'                            # ink fill
RED = "var(--pdk-red, #E5202E)"
YELLOW = "var(--pdk-yellow, #F5B700)"


def seal(cx, cy, outer, inner, teeth):
    pts = []
    for i in range(teeth * 2):
        r = outer if i % 2 == 0 else inner
        a = math.pi * i / teeth - math.pi / 2
        pts.append(f"{cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}")
    return "M" + "L".join(pts) + "z"


def droplet(cx, fill=None):
    d = (f"M{cx} 6.5C{cx + .6} 8.6 {cx + 3} 10.6 {cx + 3} 13.8a3 3 0 0 1-6 0"
         f"C{cx - 3} 10.6 {cx - .6} 8.6 {cx} 6.5z")
    return f'<path d="{d}" {fill}/>' if fill else f'<path d="{d}"/>'


FLAG_BOX = "M5 4h14.5v9H5z"
FLAG_POLE = '<path d="M5 21.5V3"/>'


def flag(fill_attr):
    return f'<path d="{FLAG_BOX}" {fill_attr}/>{FLAG_POLE}'


def checkered():
    w, h = 14.5 / 4, 4.5
    cells = "".join(
        f'<rect x="{5 + c * w:.3f}" y="{4 + r * h}" width="{w:.3f}" height="{h}" {INK}/>'
        for r, c in ((0, 0), (0, 2), (1, 1), (1, 3)))
    return cells + f'<path d="{FLAG_BOX}"/>' + FLAG_POLE


ENGINE_LEVELS = {"low": (8, 13.5), "medium": (9.17, 10.67), "high": (12, 9.5), "xhigh": (14.83, 10.67),
                 "max": (16, 13.5)}


def engine(level):
    x, y = ENGINE_LEVELS[level]
    return ('<circle cx="12" cy="13.5" r="6.5"/>'
            '<path d="M12 2.5v2M4.6 6.1l1.4 1.4M19.4 6.1 18 7.5M2 13.5h2M20 13.5h2"/>'
            f'<path d="M12 13.5L{x} {y}" {A}/>')


# The Indy car, one closed outline in absolute coordinates (M/L/C/Z only), so smaller cars are exact scalings of it.
CAR_BODY = ("M2.6 16.2 L2.6 13.4 L6.6 13.4 L7.4 14 L10 14 C10.6 12.5 11.4 11.4 12.6 11.4 C13.4 11.4 13.8 12 13.9 12.8 "
            "L14.2 13.3 L16.8 13.3 L17.3 12.8 C19 13.2 21 14 22.6 15.6 L22.6 16.2 Z")
CAR_WING = "M1.5 7 L6.5 7 M2.6 7 L2.6 13.4"
CAR_WHEELS = ((6.3, 16.6, 2.9), (18.5, 16.8, 2.6))


def _xf(d, s, ox, oy):
    nums = iter(re.findall(r"-?\d*\.?\d+", d))
    out = re.sub(r"-?\d*\.?\d+", lambda m: "{}", d)
    vals = []
    for x in nums:
        y = next(nums)
        vals += [f"{ox + float(x) * s:.2f}".rstrip("0").rstrip("."), f"{oy + float(y) * s:.2f}".rstrip("0").rstrip(".")]
    return out.format(*vals)


def indy_car(s=1.0, ox=0.0, oy=0.0, helmet=None, wheels=INK):
    """The car at scale s, offset (ox, oy). helmet: (fill attr) or None to leave the cockpit empty."""
    body = f'<path d="{_xf(CAR_BODY, s, ox, oy)}"/><path d="{_xf(CAR_WING, s, ox, oy)}"/>'
    if helmet:
        body += f'<circle cx="{ox + 15.1 * s:.2f}" cy="{oy + 11.2 * s:.2f}" r="{2.1 * s:.2f}" {helmet}/>'
    for cx, cy, r in CAR_WHEELS:
        body += f'<circle cx="{ox + cx * s:.2f}" cy="{oy + cy * s:.2f}" r="{r * s:.2f}" {wheels}/>'
    return body



# (id, source glyph it was ported from or None, group, Paddock meaning, markup)
GLYPHS = [
    # The cast
    ("driver", "agent", "cast", "Driver: the model behind the wheel",
     '<path d="M3.5 18.5V12a8.5 8.5 0 0 1 17 0v3a3.5 3.5 0 0 1-3.5 3.5z"/>'
     f'<rect x="11" y="9" width="9.5" height="3.6" rx="1.8" {AF}/>'),
    ("car", "harness", "cast", "Car: the harness that runs the model", indy_car(helmet=AF)),
    ("entry", "agent+harness", "cast", "Entry: the driver in the car, one model in one harness",
     # Helmet just behind the cockpit hump, with a small clear gap. Maintainer-chosen position: do not recentre it
     # over the hump, where it merges with the hump at small sizes.
     '<path d="M14.6 13.2V11.18a2.59 2.59 0 0 1 5.18 0v.86a1.15 1.15 0 0 1-1.15 1.15z"/>'
     f'<rect x="16.98" y="10.03" width="2.81" height="1.3" rx=".65" {AF}/>'
     + indy_car(oy=3.0)),
    # Grip and coaching
    ("tires", "tool", "grip", "Tires: the tools, the car's grip on the environment",
     '<path d="M12 1.5a10.5 10.5 0 1 0 .01 0zM12 7a5 5 0 1 1-.01 0z" fill="currentColor" fill-rule="evenodd" stroke="none"/>'
     f'<circle cx="12" cy="12" r="7.8" stroke-width="1.6" {A}/>'
     f'<circle cx="12" cy="12" r="1.8" {INK}/>'),
    ("team-radio", "skill", "grip", "Team radio: skills, the crew's coaching during the lap",
     '<path d="M4.5 14v-1.5a7.5 7.5 0 0 1 15 0V14"/>'
     '<rect x="3" y="13.5" width="4" height="6" rx="1.5"/><rect x="17" y="13.5" width="4" height="6" rx="1.5"/>'
     '<path d="M19 19.5c0 1.4-1.2 2-3 2H13.5"/>'
     f'<circle cx="12" cy="21.5" r="1.7" {AF}/>'),
    ("briefing", "instructions", "grip", "Briefing: the instructions the driver is given before the lap",
     '<rect x="4.5" y="3" width="15" height="18" rx="2"/><path d="M8 7.5h8M8 11h5"/>'
     f'<path d="M8 18c0-2.6 1.8-4 4.5-4H16" {A}/>'),
    ("engine-map", "fine-tune", "cast", "Engine map: the effort level (this one: high)", engine("high")),
    ("engine-map-low", "fine-tune", "engine", "Effort low", engine("low")),
    ("engine-map-medium", "fine-tune", "engine", "Effort medium", engine("medium")),
    ("engine-map-high", "fine-tune", "engine", "Effort high", engine("high")),
    ("engine-map-xhigh", "fine-tune", "engine", "Effort xhigh", engine("xhigh")),
    ("engine-map-max", "fine-tune", "engine", "Effort max", engine("max")),
    # Flags
    ("flag-checkered", "checkpoint", "flags", "Finished: the task passed", checkered()),
    ("flag-green", "checkpoint", "flags", "Green: running, lights out", flag('style="fill:var(--pdk-green, #00C27A)"')),
    ("flag-yellow", "checkpoint", "flags", "Yellow: audit pending", flag(f'style="fill:{YELLOW}"')),
    ("flag-red", "checkpoint", "flags", "Red: infrastructure fault, rerun", flag(f'style="fill:{RED}"')),
    ("flag-black", "checkpoint", "flags", "Black: integrity hold", flag('style="fill:var(--pdk-black, #111111)"')),
    ("dnf", "state-failed", "flags", "DNF: the task failed",
     f'<circle cx="12" cy="12" r="8"/><path d="M9 9l6 6M15 9l-6 6" style="stroke:{RED}"/>'),
    # The race and the results
    ("race", "eval", "race", "A race: one task",
     '<path d="M7 4.5h8.5a4.5 4.5 0 0 1 0 9H13a2.5 2.5 0 0 0 0 5h3.5a1.5 1.5 0 0 1 0 3H7'
     'a4.5 4.5 0 0 1-4.5-4.5v-8A4.5 4.5 0 0 1 7 4.5z"/>'
     f'<path d="M10 2v5" {A}/>'),
    ("track-map", "dag-walker", "race", "Track map: how a run works, in four sectors",
     '<path d="M4 17.7C4 15 7 15 7 11.5M7 11.5C7 8 10.5 6.5 13 8.5M18.5 7.5c.4-2 1.5-3 3-3"/>'
     f'<path d="M13 8.5c2.5 2 5 1.5 5.5-1" {A}/>'
     '<circle cx="4" cy="19.5" r="1.8"/>'
     f'<circle cx="7" cy="11.5" r="1.6" {INK}/><circle cx="13" cy="8.5" r="1.6" {INK}/>'
     f'<circle cx="18.5" cy="7.5" r="1.6" {INK}/>'),
    ("race-calendar", "maintenance-window", "race", "Race calendar: the suites and their tasks",
     '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 2.5V6M16 2.5V6"/>'
     + "".join(f'<rect x="{x}" y="{y}" width="2" height="2" {AF}/>'
               for x, y in ((7, 12.5), (11, 12.5), (9, 14.5), (13, 14.5), (7, 16.5), (11, 16.5)))),
    ("lap-time", "schedule", "race", "Lap time: wall-clock time for a task",
     '<circle cx="12" cy="13.5" r="7.5"/><path d="M9.5 2.5h5M12 2.5V6M18.2 6.8l1.6-1.6"/>'
     f'<path d="M12 13.5l3.2-3.6" {A}/>'),
    ("whisker", "latency", "race", "Whiskers: the 95% interval around a pass rate",
     f'<path d="M3.5 7v10M20.5 7v10M3.5 12h17"/><circle cx="14" cy="12" r="2.8" {AF}/>'),
    ("photo-finish", "snapshot", "race", "Photo finish: a statistical tie",
     '<path d="M3 8V4h4M17 4h4v4M21 16v4h-4M7 20H3v-4M5.5 10h5M5.5 14h5"/>'
     f'<path d="M12 6.5v11" {A}/>'),
    ("timing-tower", "logs", "race", "Timing tower: the standings",
     '<rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M8 12h1.5M12 12h4M8 16.5h1.5M12 16.5h4"/>'
     f'<path d="M8 7.5h1.5M12 7.5h4" {A}/>'),
    ("podium", "usage", "race", "Podium: the top three",
     f'<rect x="9" y="7.5" width="6" height="13.5" {AF}/><path d="M3 21v-8.5h6v-5h6v8h6V21z"/>'),
    # Rules and fairness
    ("rulebook", "knowledge-base", "rules", "Rulebook: the methodology",
     '<path d="M12 6.5C10 5 7 4.5 3 4.5v14c4 0 7 .5 9 2 2-1.5 5-2 9-2v-14c-4 0-7 .5-9 2zM12 6.5v14"/>'
     f'<path d="M15.5 5.6v5.9l1.5-1.5 1.5 1.5V5.2" {A}/>'),
    ("setup-sheet", "settings", "rules", "Setup sheet: the run's configuration and its hash",
     '<rect x="4.5" y="4.5" width="15" height="16.5" rx="2"/><path d="M9 3h6v3H9zM8 11h8M8 16h8"/>'
     f'<circle cx="10" cy="11" r="1.7" {INK}/><circle cx="14" cy="16" r="1.7" {AF}/>'),
    ("fence", "sandbox", "rules", "Fence: the containment around the whole circuit",
     '<path d="M5 4v16M12 4v16M19 4v16M2 15h20"/>'
     f'<path d="M2 9h20" {A}/>'),
    ("scrutineering", "attestation", "rules", "Scrutineering: the car passed its checks",
     f'<path d="{seal(12, 10, 7.2, 6, 12)}"/><path d="M8.8 15.6 7.5 21.5l4.5-2 4.5 2-1.3-5.9"/>'
     f'<path d="M9.3 10.2l1.8 1.8 3.6-3.6" {A}/>'),
    ("stewards", "decision-model", "rules", "Stewards: the grader that rules on every finish",
     '<path d="M4 21.5V8M20 21.5V8"/><rect x="2.5" y="3" width="19" height="5"/>'
     '<path d="M4 18h16" stroke-dasharray="2 2"/>'
     f'<path d="M8.5 12.2l2.5 2.5 4.5-4.5" {A}/>'),
    ("track-limits", "guardrail", "rules", "Track limits: the CPU, memory and time caps",
     '<path d="M5 3.5h16.5M5 9.5h16.5M5 3.5c-2.8 0-2.8 6 0 6"/>'
     f'<path d="M4 6.5h17.5" {A}/>'
     + "".join(f'<rect x="{2 + i * 4:.1f}" y="15.5" width="4" height="4" {INK}/>' for i in (0, 2, 4))
     + '<path d="M2 15.5h20M2 19.5h20"/>'),
    # Fuel
    ("fuel-gauge", "budget", "fuel", "Fuel gauge: a spend cap",
     '<path d="M3.5 16a8.5 8.5 0 0 1 17 0M5.3 10.3l1.4 1M12 7.5v2.2M18.7 10.3l-1.4 1"/>'
     f'<path d="M12 16l4.2-5.6" {A}/><circle cx="12" cy="16" r="1.6" {INK}/>'
     f'<path d="M12 18.4c-.9 1.1-1.4 1.8-1.4 2.4a1.4 1.4 0 0 0 2.8 0c0-.6-.5-1.3-1.4-2.4z" {INK}/>'),
    ("fuel-bill", "cost", "fuel", "Fuel bill: dollars per pass",
     '<path d="M4 21.5v-17A1.5 1.5 0 0 1 5.5 3h7A1.5 1.5 0 0 1 14 4.5v17M2.5 21.5h13'
     'M14 9.5h2a1.5 1.5 0 0 1 1.5 1.5v5.5a1.5 1.5 0 0 0 3 0V8.5L18 6"/>'
     f'<rect x="6.5" y="6" width="5" height="4.5" {AF}/>'),
    ("fuel", "tokens", "fuel", "Fuel: tokens",
     droplet(5) + droplet(12, AF) + droplet(19)),
    ("slipstream", "prompt-cache", "fuel", "Slipstream: cached tokens, read at a fraction of the price",
     indy_car(s=0.62, ox=8.4, oy=3.0, helmet=INK) + f'<path d="M2 8.5h4.5M2 11.5h5M2 14.5h4" {A}/>'),
    # Running it
    ("pit-lane", "priority-lane", "running", "Pit lane: run the bench yourself",
     '<path d="M3 3.5h18M3 20.5h18M8 20.5V18M13 20.5V18M18 20.5V18"/>'
     '<path d="M3 9h18" stroke-dasharray="3 2.5"/>'
     f'<path d="M5 13.5h11M13.5 11l2.5 2.5-2.5 2.5" {A}/>'),
    ("pit-wall", "observe", "running", "Pit wall: telemetry from a run",
     '<rect x="2.5" y="3.5" width="8.5" height="8" rx="1.5"/><rect x="13" y="3.5" width="8.5" height="8" rx="1.5"/>'
     '<path d="M2 14.5h20M5 14.5v7M19 14.5v7M15.3 6.8h4M15.3 9h2.6"/>'
     f'<path d="M4.6 9.2l1.7-2 1.5 1.3 1.7-2.2" {A}/>'),
    ("shakedown", "smoke", "running", "Shakedown: the bench doubles as the fleet's smoke test",
     f'<circle cx="16.5" cy="15.5" r="5"/><circle cx="16.5" cy="15.5" r="1.6" {INK}/><path d="M2 21.5h20"/>'
     '<path d="M3 10.5c1.2-1.3 2.4 1.3 3.6 0s2.4 1.3 3.6 0"/>'
     f'<path d="M2 15.5c1.2-1.3 2.4 1.3 3.6 0s2.4 1.3 3.6 0" {A}/>'),
    # New
    (
        "info",
        None,
        "new",
        "a note about this value",
        '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/>'
        f'<circle cx="12" cy="7.5" r="1" {INK}/>',
    ),
    ("start-lights", None, "new", "Lights out: a run starts",
     '<rect x="2" y="4" width="20" height="7.5" rx="2"/><path d="M12 11.5v10M8.5 21.5h7"/>'
     + "".join(f'<circle cx="{x}" cy="7.75" r="1.3" {AF}/>' for x in (5.2, 8.6, 12, 15.4, 18.8))),
    ("trophy", None, "new", "Trophy: a category winner",
     '<path d="M7 3.5h10V8a5 5 0 0 1-10 0zM7 5.5H4.5V7a3 3 0 0 0 3 3M17 5.5h2.5V7a3 3 0 0 1-3 3M12 13v4"/>'
     f'<rect x="8.5" y="17" width="7" height="4.5"/><circle cx="12" cy="7.6" r="1.6" {AF}/>'),
    ("roundel", None, "new", "Roundel: the frame a race-type glyph sits in",
     f'<circle cx="12" cy="12" r="9.5"/><path d="M12 2.5a9.5 9.5 0 0 1 9.5 9.5" {A}/>'),
    # Contact gets its own glyph so team radio keeps meaning "skills". The crew's sign held over the wall; its angled
    # pole also reads as a message bubble at small sizes.
    ("pit-board", None, "new", "Pit board: contact the paddock, for questions, result challenges and feedback",
     '<rect x="6.5" y="3" width="15" height="11" rx="1.5"/><path d="M10 14L5.5 21.5M10 10.5h8"/>'
     f'<path d="M10 7h5" {A}/>'),
]

SYMBOL = ('<symbol id="{id}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
          'stroke-linecap="butt" stroke-linejoin="miter">{body}</symbol>')


def sprite():
    return ('<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n'
            + "\n".join(SYMBOL.format(id=g[0], body=g[4]) for g in GLYPHS) + "\n</svg>\n")


def main():
    (HERE / "paddock-glyphs.svg").write_text(sprite(), encoding="utf-8")
    index = [{"id": g[0], "group": g[2], "meaning": g[3]} for g in GLYPHS]
    (HERE / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"{len(GLYPHS)} glyphs -> paddock-glyphs.svg, index.json")


if __name__ == "__main__":
    main()
