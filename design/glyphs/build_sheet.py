#!/usr/bin/env python3
"""Build the glyph catalogue page from the sprite.

    python3 design/glyphs/paddock_glyphs.py     # the sprite and index.json
    python3 design/glyphs/build_sheet.py        # glyphs.html, next to this file

Every glyph at 48, 32, 24 and 16 px on the light and the dark ground, with its id and meaning. The page links
../tokens.css, so it shows the tokens' accent. Standard library only.
"""
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUPS = [
    ("cast", "The cast", "Who races. The model drives, the harness is the car, and the entry puts one model in one "
     "harness. The effort level is the engine map."),
    ("grip", "Grip and coaching", "Tires are the tools the car mounts: its grip on the environment. The briefing and the "
     "team radio are what the driver is given: instructions before the lap, skills during it."),
    ("engine", "Engine maps", "One knob, five settings. The pointer is the only accent, so the level reads at 16 px."),
    ("flags", "Flags", "How a task ended. One flag shape for every outcome; the fill carries the meaning."),
    ("race", "The race and the results", "Tasks, timing and standings, including a tie at the top of the board."),
    ("rules", "Rules and fairness", "What keeps a result honest: the rulebook, the fence around the circuit and the grader."),
    ("fuel", "Fuel", "Tokens and dollars. Cached tokens are the slipstream: most of the fuel, a fraction of the price."),
    ("running", "Running it", "Running the bench yourself: the pit lane, the pit wall and the shakedown."),
    ("new", "Drawn new", "Glyphs drawn for Paddock from scratch, in the same grammar."),
]
CSS = """
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-body); }
.wrap { max-width: 1180px; margin: 0 auto; padding: 48px 24px 80px; }
.lockup { display: flex; flex-direction: column; line-height: .9; font-family: var(--font-display); font-style: italic; }
.lockup .a { font-weight: 500; font-size: 22px; letter-spacing: .02em; }
.lockup .p { font-weight: 800; font-size: 40px; }
h1 { font-family: var(--font-display); font-size: 44px; margin: 24px 0 8px; }
h2 { font-family: var(--font-display); font-size: 30px; margin: 48px 0 4px; }
.lede, .sub { color: var(--ink-2); max-width: 760px; line-height: 1.5; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }
.card { border: 1px solid var(--rule); border-radius: 12px; overflow: hidden; background: var(--paper); }
.grounds { display: grid; grid-template-columns: 1fr 1fr; }
.ground { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 18px 8px; }
.ground.light { background: #FFFFFF; color: #111111; --pdk-accent: #007BFF; }
.ground.dark { background: #0C0F13; color: #F2F4F7; --pdk-accent: #3D9BFF; }
.ground svg { flex: none; }
.s48 { width: 48px; height: 48px; } .s32 { width: 32px; height: 32px; } .s24 { width: 24px; height: 24px; } .s16 { width: 16px; height: 16px; }
.meta { padding: 10px 14px 14px; border-top: 1px solid var(--rule); }
.id { font-family: var(--font-mono); font-size: 13px; font-weight: 500; }
.meaning { margin: 4px 0 0; font-size: 14px; color: var(--ink-2); line-height: 1.4; }
"""


def main():
    sprite = (HERE / "paddock-glyphs.svg").read_text(encoding="utf-8").replace(
        'style="display:none"', 'style="position:absolute;width:0;height:0" aria-hidden="true"')
    index = json.loads((HERE / "index.json").read_text(encoding="utf-8"))

    def use(gid, size):
        return f'<svg class="s{size}" aria-hidden="true"><use href="#{gid}"/></svg>'

    parts = ['<!doctype html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">', '<title>Agent Paddock glyphs</title>',
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&'
             'family=JetBrains+Mono:wght@400;500&family=Saira+Condensed:ital,wght@0,700;1,500;1,800&display=swap">',
             '<link rel="stylesheet" href="../tokens.css">', f'<style>{CSS}</style>', '</head>', '<body>', sprite,
             '<div class="wrap">', '<div class="lockup" aria-label="Agent Paddock"><span class="a">AGENT</span>'
             '<span class="p">PADDOCK</span></div>', '<h1>Glyphs</h1>',
             f'<p class="lede">{len(index)} line glyphs on a 24 px grid: 2 px strokes, square ends, mitred joins and at '
             'most one accent, in Lacey blue. Each one at 48, 32, 24 and 16 px on the light and the dark ground.</p>']
    for key, title, sub in GROUPS:
        members = [g for g in index if g["group"] == key]
        if not members:
            continue
        parts.append(f'<h2>{html.escape(title)}</h2><p class="sub">{html.escape(sub)}</p><div class="grid">')
        for g in members:
            sizes = "".join(use(g["id"], s) for s in (48, 32, 24, 16))
            parts.append(f'<div class="card"><div class="grounds"><div class="ground light">{sizes}</div>'
                         f'<div class="ground dark">{sizes}</div></div><div class="meta"><div class="id">'
                         f'{html.escape(g["id"])}</div><p class="meaning">{html.escape(g["meaning"])}</p></div></div>')
        parts.append('</div>')
    parts += ['</div>', '</body>', '</html>', '']
    (HERE / "glyphs.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(index)} glyphs -> glyphs.html")


if __name__ == "__main__":
    main()
