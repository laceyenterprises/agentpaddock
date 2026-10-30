"""Render the Agent Paddock share card to scripts/social-card.png with headless Chrome.

The source is ``scripts/social_card.html`` with the site's Broadcast mark filled
in (``build_site.card_source``). The PNG is exactly 1200 x 630 and carries the
SHA-256 of that source in a tEXt chunk, so ``python3 scripts/build_site.py
--check`` can tell a stale card without a browser. Run this whenever the check
reports the card as stale, and commit the PNG.

Needs Google Chrome or Chromium, and network access for the web fonts. Set
CHROME to an executable path or PATH command to override automatic discovery.
An invalid override fails immediately. Browser failures include stderr diagnostics.

    python3 scripts/render_social_card.py
    python3 scripts/render_social_card.py --out DIR/social-card.png
    python3 scripts/render_social_card.py --no-sandbox  # inside a container or an outer sandbox
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import build_site

IEND = b"IEND\xaeB`\x82"
CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> str:
    override = os.environ.get("CHROME")
    if override is not None:
        path = shutil.which(override)
        if path:
            return path
        raise SystemExit(f"CHROME does not name an executable browser: {override!r}")
    for candidate in CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    raise SystemExit("Chrome or Chromium not found; set CHROME to the browser binary")


def render(
    source: str, chrome: str, timeout: float = 120.0, *, sandbox: bool = True
) -> bytes:
    """Screenshot ``source`` at the card size.

    Headless Chrome on macOS can keep running after it writes the screenshot,
    so wait for a complete PNG and then stop the browser's process group.
    """
    width, height = build_site.CARD_SIZE
    with tempfile.TemporaryDirectory() as scratch, tempfile.TemporaryFile() as errors:

        def failure(message: str) -> SystemExit:
            # A file avoids pipe backpressure; keep the reported tail bounded.
            errors.seek(0, os.SEEK_END)
            errors.seek(max(0, errors.tell() - 8192))
            detail = errors.read().decode("utf-8", errors="replace").strip()
            return SystemExit(f"{message}\n{detail}" if detail else message)

        page = Path(scratch) / "social-card.html"
        page.write_text(source, encoding="utf-8")
        shot = Path(scratch) / "card.png"
        profile = Path(scratch) / "profile"
        proc = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                f"--user-data-dir={profile}",
                "--force-device-scale-factor=1",
                f"--window-size={width},{height}",
                "--virtual-time-budget=10000",
                f"--screenshot={shot}",
                *([] if sandbox else ["--no-sandbox"]),
                page.as_uri(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=errors,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if shot.is_file() and shot.read_bytes().endswith(IEND):
                    return shot.read_bytes()
                if proc.poll() is not None:
                    raise failure(
                        f"Chrome exited ({proc.returncode}) without a complete screenshot"
                    )
                time.sleep(0.25)
            raise failure(f"no screenshot after {timeout:.0f} s")
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=build_site.CARD_PNG)
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="pass --no-sandbox to Chrome, where an outer sandbox forbids its own",
    )
    args = parser.parse_args(argv)
    source = build_site.card_source()
    data = render(source, find_chrome(), sandbox=not args.no_sandbox)
    if build_site.png_size(data) != build_site.CARD_SIZE:
        raise SystemExit(
            f"rendered {build_site.png_size(data)}, expected {build_site.CARD_SIZE}"
        )
    stamped = build_site.stamp_png(
        data, build_site.CARD_KEY, build_site.card_source_hash(source)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(stamped)
    width, height = build_site.CARD_SIZE
    print(f"Rendered {args.out} ({width}x{height}, {len(stamped) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
