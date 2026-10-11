"""notifyctl: inspect the notifier's quiet hours and held digest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from alerts import SEVERITIES, Alert, AlertError, format_alert
from digest import Digest
from notify import DIGEST_FILE, dedupe_key
from quiet_hours import QuietHours, QuietHoursError


def _digest(state_dir: Path) -> Digest:
    return Digest(state_dir / DIGEST_FILE)


def cmd_status(args: argparse.Namespace) -> int:
    """Print the quiet-hours window, whether it is active now, and the held count."""
    quiet = QuietHours.parse(args.quiet_hours, args.zone)
    now = datetime.now(timezone.utc)
    status = {
        "quiet_hours": None if quiet is None else quiet.describe(),
        "in_quiet_hours": quiet is not None and quiet.contains(now),
        "held": len(_digest(args.state_dir)),
    }
    print(json.dumps(status, sort_keys=True))
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Print the digest that would be sent when quiet hours end. Sends nothing."""
    digest = _digest(args.state_dir)
    print(digest.render() if len(digest) else "digest is empty")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate an alert and print how it would be formatted and deduplicated."""
    try:
        alert = Alert(args.source, args.key, args.severity, args.text, args.raised_at)
    except AlertError as error:
        print(f"notifyctl: {error}", file=sys.stderr)
        return 2
    print(format_alert(alert))
    print(f"dedupe key {dedupe_key(alert)}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="notifyctl")
    root.add_argument("--state-dir", type=Path, required=True, help="notifier state directory")
    commands = root.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="quiet hours and held-alert count")
    status.add_argument("--quiet-hours", default="", help="HH:MM-HH:MM, empty to disable")
    status.add_argument("--zone", default="UTC", help="IANA time zone for quiet hours")
    status.set_defaults(handler=cmd_status)

    preview = commands.add_parser("preview", help="print the held digest")
    preview.set_defaults(handler=cmd_preview)

    check = commands.add_parser("check", help="validate and format one alert")
    check.add_argument("--source", required=True)
    check.add_argument("--key", required=True)
    check.add_argument("--severity", choices=SEVERITIES, required=True)
    check.add_argument("--text", required=True)
    check.add_argument("--raised-at", type=float, default=0.0)
    check.set_defaults(handler=cmd_check)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except QuietHoursError as error:
        print(f"notifyctl: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
