"""Race it yourself (PDW-09): ``/race-it-yourself/``, rebuilt from ``design/web/RaceYourself.dc.html``.

Six steps from the README and CONTRIBUTING.md. Every command on the page is
read at build time from the fenced code block it restates (:data:`COMMANDS`):
clone and install from the README's quick start, the standalone config from
the README's runner section (``design/guide/race-it-yourself.md``), and the
``gym.cli run``, ``leaderboard.py from-run`` and validate commands from the
run-bundle section of CONTRIBUTING.md. A moved or deleted block fails the
build instead of shipping a stale command.

Each code box has a copy button that falls back to "Select and copy". With the
script, the steps show one at a time and a tap on the card advances, except
inside a code box; without it, all six read as one list.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

from . import glyphs, kit, layout
from .layout import esc
from .routes import Route

PATH = "race-it-yourself/"
REPO_ROOT = glyphs.DESIGN.parent


class Command(NamedTuple):
    #: A repository-relative Markdown file.
    document: str
    #: The ``#`` heading of the section that holds the block, exactly as written.
    section: str
    #: Which fenced block in that section, from 0.
    index: int

    @property
    def label(self) -> str:
        return f"{self.document} · {self.section}"

    @property
    def href(self) -> str:
        from .rulebook import slug

        section = self.section.lstrip("#").strip()
        return f"{layout.REPO_URL}/blob/main/{self.document}#{slug(section)}"


class SourceError(ValueError):
    """A command's source block is missing."""


def fenced_blocks(text: str, section: str) -> list[str]:
    """The fenced code blocks under the heading ``section`` (``"## Quick start"``).

    The section runs to the next heading of the same or a higher level outside
    a code block. A block indented inside a list item is dedented by its fence's
    indent, as Markdown renders it.
    """
    level = len(section) - len(section.lstrip("#"))
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.rstrip() == section]
    if len(starts) != 1:
        raise SourceError(f"{section!r}: found {len(starts)} times")
    blocks: list[str] = []
    fence: str | None = None
    current: list[str] = []
    for line in lines[starts[0] + 1 :]:
        stripped = line.strip()
        if fence is None:
            heading = len(line) - len(line.lstrip("#"))
            if 0 < heading <= level and line[heading : heading + 1] == " ":
                break
            if stripped.startswith("```"):
                fence = line[: len(line) - len(line.lstrip())]
                current = []
            continue
        if stripped == "```":
            blocks.append("\n".join(current))
            fence = None
            continue
        current.append(line[len(fence) :] if line.startswith(fence) else line.lstrip())
    return blocks


def command(source: Command, root: Path = REPO_ROOT) -> str:
    """The text of one command block, exactly as its document holds it."""
    text = (root / source.document).read_text(encoding="utf-8")
    blocks = fenced_blocks(text, source.section)
    if source.index >= len(blocks):
        raise SourceError(f"{source.label}: no code block {source.index}")
    return textwrap.dedent(blocks[source.index]).rstrip("\n")


RUN_BUNDLE = ("CONTRIBUTING.md", "## Submit results: a run bundle")

#: Every command the page shows, by name.
COMMANDS: dict[str, Command] = {
    "install": Command("README.md", "## Quick start", 0),
    "config": Command("design/guide/race-it-yourself.md", "## Runners (PDK-01)", 0),
    "run": Command(*RUN_BUNDLE, 0),
    "from-run": Command(*RUN_BUNDLE, 1),
    "validate": Command(*RUN_BUNDLE, 2),
}


class Step(NamedTuple):
    glyph: str
    title: str
    body: str
    commands: tuple[str, ...]
    notice: str
    #: Where the step's text comes from when it shows no command.
    source: Command | None = None


STEPS: tuple[Step, ...] = (
    Step(
        "pit-lane",
        "Get Agent Paddock",
        "The races, the stewards and the rulebook are one open-source repository. You "
        "need Python 3.11 or newer.",
        ("install",),
        "The board is built only from the results files people hand in, nothing else.",
    ),
    Step(
        "car",
        "Build your car",
        "Your car is a Docker image with your harness's command-line tool inside: Claude "
        "Code, Codex, OpenCode or Pi. The runner never downloads one for you: it starts "
        "the image with <code>--pull=never</code>, so the image must already be on your "
        "machine.",
        (),
        "Antigravity is not in the standalone runner yet.",
        Command("design/guide/race-it-yourself.md", "## Runners (PDK-01)", 0),
    ),
    Step(
        "setup-sheet",
        "Fill in the setup sheet",
        "One small file says which car, which image and which driver. Save it as "
        "<code>~/.config/paddock/config.toml</code>. Keys stay in your environment and "
        "reach the car by name, never on the command line.",
        ("config",),
        "Name a runner that is not installed and the run stops. It never quietly falls "
        "back to another.",
    ),
    Step(
        "start-lights",
        "Lights out",
        "Pick a suite and start. Each race runs in its own sealed container: 2 CPUs, "
        "2 GiB of memory, a read-only root and no extra privileges.",
        ("run",),
        "Review is a good first suite, with short laps. You pay for your own fuel.",
    ),
    Step(
        "flag-checkered",
        "Hand in your results",
        "Describe your car in <code>harness-config.json</code>, turn the run into one "
        "results file, check it, then open a pull request that adds it. That pull request "
        "is your run bundle: enough for anyone to reproduce the race. Two results with the "
        "same car description pool on the board; any difference splits them. Never commit "
        "the raw <code>run.json</code>: its grading reasons can reveal task answers.",
        ("from-run", "validate"),
        "The standalone runner does not count tokens yet, so your fuel bill shows as "
        "unknown for now.",
    ),
    Step(
        "scrutineering",
        "Earn your badge",
        "Every result shows how far it has been checked. The community board shows them "
        "all. The main standings count only results that were reproduced or that Agent "
        "Paddock ran itself.",
        (),
        "Soon: hand results in straight from the command line with a signing key.",
        Command("CONTRIBUTING.md", "## Trust levels", 0),
    ),
)


def _from(source: Command) -> str:
    section = source.section.lstrip("#").strip()
    return (
        f'<p class="from">From <a href="{esc(source.href)}">'
        f"{esc(source.document)} · {esc(section)}</a></p>"
    )


def _step(number: int, step: Step, root: Path) -> str:
    total = len(STEPS)
    boxes = ""
    sources: dict[str, Command] = {}
    for name in step.commands:
        source = COMMANDS[name]
        boxes += kit.code_box(command(source, root), f"Step {number}: {step.title}")
        sources.setdefault(source.href, source)
    if step.source is not None:
        sources.setdefault(step.source.href, step.source)
    extra = kit.ladder() if step.glyph == "scrutineering" else ""
    hint = "Tap to start over" if number == total else "Tap the card for the next step"
    button = "Start over" if number == total else "Next step"
    return (
        f'<li class="step" id="step-{number}" aria-label="Step {number} of {total}">'
        f'<div class="step-head">{glyphs.icon(step.glyph)}'
        f'<div><span class="kicker">Step {number} of {total}</span>'
        f"<h3>{esc(step.title)}</h3></div></div>"
        f"<p>{step.body}</p>"
        + boxes
        + extra
        + f'<p class="notice"><b>Notice:</b> {esc(step.notice)}</p>'
        + "".join(_from(source) for source in sources.values())
        + f'<div class="step-foot"><span class="hint">{hint}</span>'
        f'<button type="button" class="btn ghost sm next">{button}</button></div>'
        "</li>"
    )


def body(route: Route, root: Path = REPO_ROOT) -> str:
    up = layout.up(route.file)
    pager = "".join(
        f'<button type="button" class="chip" aria-controls="step-{k}" '
        f'aria-label="Step {k}">{k}</button>'
        for k in range(1, len(STEPS) + 1)
    )
    steps = "".join(_step(k, step, root) for k, step in enumerate(STEPS, start=1))
    return (
        kit.hero(
            "pit-lane",
            "Run the races on your own machine.",
            "Same races, same stewards, same rules. Bring your own car and your own fuel, "
            "then put your result on the board.",
        )
        + '<section data-steps aria-label="Six steps">'
        f'<div class="pager" aria-label="Steps">{pager}</div>'
        f'<ol class="steps">{steps}</ol></section>'
        '<section aria-labelledby="garage-title"><h2 id="garage-title">What you need in the garage.</h2>'
        '<div class="garage"><span>Python 3.11 or newer</span><span>Docker</span>'
        "<span>A harness in an image</span><span>Your own model key</span></div>"
        f'<p class="aside">{glyphs.icon("fence")}<span>Paid races never run in the '
        "repository's own checks. Your runs spend your tokens, nobody else's.</span></p>"
        "</section>"
        '<section class="link-cards">'
        + kit.link_card(f"{up}races/index.html", "First", "Pick a race to start with")
        + kit.link_card(
            f"{layout.REPO_URL}/blob/main/CONTRIBUTING.md",
            "The contributing guide",
            "How to hand in a result",
        )
        + "</section>"
    )


def page(results: Any, data: Mapping[str, Any], routes: Sequence[Route]) -> str:
    route = kit.route(routes, PATH)
    return kit.page(route, routes, body(route))
