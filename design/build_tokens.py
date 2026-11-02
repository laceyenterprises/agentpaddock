#!/usr/bin/env python3
"""Write tokens.json from tokens.css, so the two never drift.

    python3 design/build_tokens.py

tokens.css is the source. Colours carry a light and a dark value; fonts, space and radius have one. Stdlib only.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def pairs(block):
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return {k: v.strip() for k, v in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block)}


def block_pairs(css, selector):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    match = re.search(selector + r"\s*\{([^{}]*)\}", css)
    if match is None:
        raise ValueError(f"Missing token block matching {selector!r}")
    return pairs(match.group(1))


def main():
    css = (HERE / "tokens.css").read_text(encoding="utf-8")
    light = block_pairs(css, r":root")
    dark = block_pairs(css, r""":root\s*\[\s*data-theme\s*=\s*["']dark["']\s*\]""")
    out = {
        "$description": "Agent Paddock design tokens. Generated from tokens.css by build_tokens.py; edit the CSS.",
        "color": {k: {"light": v, "dark": dark.get(k, v)} for k, v in light.items() if v.startswith(("#", "var("))},
        "font": {k.removeprefix("font-"): v for k, v in light.items() if k.startswith("font-")},
        "space": {k.removeprefix("space-"): v for k, v in light.items() if k.startswith("space-")},
        "radius": {k.removeprefix("radius-"): v for k, v in light.items() if k.startswith("radius-")},
    }
    (HERE / "tokens.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"{len(out['color'])} colours -> tokens.json")


if __name__ == "__main__":
    main()
