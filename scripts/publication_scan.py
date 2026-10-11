"""Publication scan: refuse a Paddock tree that leaks private material (PDK-05, R7).

Paddock is public from its first commit, so every push is scanned and the
export that seeds the repository refuses to emit a tree with any finding.
Each category below is something a public tree must never hold:

``secret``             secret-shaped strings: private keys, provider and
                       platform tokens, credentials embedded in a URL.
``op-reference``       secret-manager references (the ``op`` scheme with a vault/item path).
``home-path``          absolute home-directory paths (``/Users/<name>``, ``/home/<name>``).
``tailnet``            tailnet addresses: the 100.64/10 IP range and ``*.ts.net`` names.
``internal-url``       URLs to private networks (RFC 1918 addresses, ``.internal``,
                       ``.local``, ``.lan``, ``.corp``, ``.home.arpa``).
``hostname``           machine hostnames, by shape (``*-mbp``) or by fingerprint.
``private-repo``       repositories of the publishing organization other than
                       the public ones in :data:`PUBLIC_REPOSITORIES`.
``operator-identity``  the operator's name, emails and account names, by fingerprint.
``pages-link``         ``*.github.io`` links: the site is served only from its own
                       domain, so such a link is always dead.

Private identities are matched by salted SHA-256 fingerprints, so this file
names none of them. A finding prints its location and category and never the
matched text, because public CI logs must not repeat a leak.

A line may carry ``publication-scan: allow(<category>): <reason>`` to keep a
deliberate value of that one category, such as a test that proves a URL with
credentials is refused. The reason is required.

    python3 scripts/publication_scan.py [ROOT] [--json]

Exit status: 0 clean, 1 findings, 2 usage or read error. Stdlib only.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

#: The organization that publishes Paddock, and its repositories that are public.
PUBLIC_OWNER = "laceyenterprises"
PUBLIC_REPOSITORIES = frozenset({"agentpaddock"})

#: Built output, caches and VCS metadata are not part of the published tree.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "_site",
        "build",
        "dist",
        "node_modules",
        "site",
        "venv",
    }
)

# Fingerprints share the salt and normalization of the Agent OS OSS-readiness
# audit, so a hostname or handle fingerprinted there is caught here too.
FINGERPRINT_SALT = "osw-01-operator-identity-v1"


@functools.lru_cache(maxsize=1 << 16)
def fingerprint(token: str) -> str:
    """Salted SHA-256 of a lower-cased token (``github.com/`` and ``@`` prefixes dropped)."""
    normalized = token.lower().removeprefix("github.com/").removeprefix("@")
    return hashlib.sha256(f"{FINGERPRINT_SALT}\0{normalized}".encode()).hexdigest()


@dataclass(frozen=True)
class Identities:
    """Fingerprints of private identifiers, by the token shape they match."""

    #: Single words: account names, handles (``hostname`` category: machine names).
    words: frozenset[str] = frozenset()
    hostnames: frozenset[str] = frozenset()
    #: Two consecutive words: full names.
    names: frozenset[str] = frozenset()
    #: Whole email addresses, and email domains whose addresses are private.
    emails: frozenset[str] = frozenset()
    email_domains: frozenset[str] = frozenset()


IDENTITIES = Identities(
    words=frozenset(
        {
            "4fe9980116218a279cdff5648b9624483b6f6a0439814a95f6054f2d4ced5f14",
            "87b7683eaf08b3fdbded947d46ed74cf0140087ac9feb008ab2b417bfc4659d5",
            "1b29faad7b08b37c3b21b5ca5daed04d6fc70441525bf113fee210d283beb09e",
        }
    ),
    hostnames=frozenset(
        {
            "4729480ac0b11e2dd3c8b8bdd7f324353659cdcb0c6a67eee12270dd3ba71dd7",
            "52f7ed18e4c14ee18ad2ecfdc65fdc2c46f2e40cfa66f3b77ebf2b9e97b0637d",
        }
    ),
    names=frozenset({"3bea4c1225f1d3346c737bab364ae6d65d2c2090b5f8fdef991a08fc9a51fe44"}),
    emails=frozenset({"25d07f79b8c51bbc14d5d9d7b7eca64a927a673cc5db848cbb733ace48ac82f0"}),
    email_domains=frozenset({"e39f19cdd71c4b12e95960cad9d4e50fc071a574ae50529f405d4f9e843bb2f7"}),
)

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"

#: ``(category, rule, regex)``; every match is a finding.
PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "secret",
        "private-key",
        re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ),
    ("secret", "aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("secret", "github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})")),
    ("secret", "anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("secret", "openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    ("secret", "slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("secret", "google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("secret", "fireworks-key", re.compile(r"\bfw_[A-Za-z0-9]{20,}")),
    (
        "secret",
        "url-credentials",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"`<>]+:[^\s/@'\"`<>]+@[^\s/'\"`<>]+"),
    ),
    ("op-reference", "op-uri", re.compile(r"\bop://[^\s'\"`<>()/]+/[^\s'\"`<>()]+")),
    (
        "home-path",
        "unix-home",
        # `/home/runner` is the GitHub Actions runner; `/home/user` a placeholder.
        re.compile(r"(?<![\w.-])/(?:Users|home)/(?!(?:runner|user|username)\b)[A-Za-z0-9._-]+"),
    ),
    ("home-path", "windows-home", re.compile(r"\b[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9._-]+")),
    (
        "tailnet",
        "cgnat-ip",
        re.compile(rf"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.{_OCTET}\.{_OCTET}\b"),
    ),
    ("tailnet", "ts-net-name", re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b", re.I)),
    (
        "internal-url",
        "private-address",
        re.compile(
            rf"\b[a-z][a-z0-9+.-]*://(?:[^\s/@'\"`<>]*@)?"
            rf"(?:10\.{_OCTET}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.{_OCTET}\.{_OCTET}\b",
            re.I,
        ),
    ),
    (
        "internal-url",
        "private-domain",
        re.compile(
            r"\b[a-z][a-z0-9+.-]*://(?:[^\s/@'\"`<>]*@)?[a-z0-9.-]+"
            r"\.(?:internal|local|lan|corp|intranet|home\.arpa)\b",
            re.I,
        ),
    ),
    (
        "hostname",
        "machine-shape",
        re.compile(
            r"\b[a-z][a-z0-9-]*(?:-mbp(?:ro)?|-macbook(?:-pro)?|-workstation|-build-host)\b", re.I
        ),
    ),
    # Lower case only: an upper-case M-series suffix is a ticket id, not a machine.
    ("hostname", "apple-silicon-shape", re.compile(r"\b[a-z][a-z0-9-]*-m[1-9](?:max|pro)?\b")),
    ("pages-link", "github-io", re.compile(r"\b[a-z0-9-]+\.github\.io\b", re.I)),
)

_REPOSITORY = re.compile(rf"\b{re.escape(PUBLIC_OWNER)}/([A-Za-z0-9._-]+)", re.I)
_WORD = re.compile(r"\b[a-z][a-z0-9-]{2,63}\b", re.I)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
_NAME_WORD = re.compile(r"[A-Za-z]{2,20}")
_ALLOW = re.compile(r"publication-scan:\s*allow\(([a-z-]+)\):\s*\S")

CATEGORIES = (
    *dict.fromkeys(category for category, _, _ in PATTERNS),
    "private-repo",
    "operator-identity",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    category: str
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.category} ({self.rule})"


def _identity_matches(line: str, identities: Identities) -> Iterator[tuple[int, str, str]]:
    for match in _WORD.finditer(line):
        digest = fingerprint(match.group())
        if digest in identities.words:
            yield match.start(), "operator-identity", "account-or-handle"
        elif digest in identities.hostnames:
            yield match.start(), "hostname", "fingerprint"
    for match in _EMAIL.finditer(line):
        if fingerprint(match.group()) in identities.emails:
            yield match.start(), "operator-identity", "email"
        elif fingerprint(match.group(1)) in identities.email_domains:
            yield match.start(), "operator-identity", "email-domain"
    if identities.names:
        words = list(_NAME_WORD.finditer(line))
        for first, second in zip(words, words[1:]):
            if line[first.end() : second.start()] not in (" ", "\t"):
                continue
            if fingerprint(f"{first.group()} {second.group()}") in identities.names:
                yield first.start(), "operator-identity", "name"


def scan_text(
    text: str, path: str = "<text>", *, identities: Identities = IDENTITIES
) -> list[Finding]:
    """Every finding in ``text``, in line order, without the allowed ones."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        allowed = set(_ALLOW.findall(line))
        hits: list[tuple[int, str, str]] = [
            (match.start(), category, rule)
            for category, rule, regex in PATTERNS
            for match in regex.finditer(line)
        ]
        hits.extend(
            (match.start(), "private-repo", "owner-repository")
            for match in _REPOSITORY.finditer(line)
            if match.group(1).lower().removesuffix(".git") not in PUBLIC_REPOSITORIES
        )
        hits.extend(_identity_matches(line, identities))
        findings.extend(
            Finding(path, number, column + 1, category, rule)
            for column, category, rule in sorted(set(hits))
            if category not in allowed
        )
    return findings


def iter_files(root: Path) -> Iterator[Path]:
    """Files under ``root`` in sorted order, outside :data:`SKIP_DIRS` and egg-info."""
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = sorted(
            name for name in subdirs if name not in SKIP_DIRS and not name.endswith(".egg-info")
        )
        for name in sorted(files):
            if not name.endswith((".pyc", ".pyo")):
                yield Path(directory) / name


def scan_tree(root: Path, *, identities: Identities = IDENTITIES) -> list[Finding]:
    """Every finding in the tree at ``root``; paths are relative and POSIX."""
    findings: list[Finding] = []
    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        # Binary files are read as text too: a key does not stop leaking in a blob.
        text = path.read_bytes().decode("utf-8", errors="replace")
        findings.extend(scan_text(text, relative, identities=identities))
        findings.extend(
            Finding(relative, 0, 0, category, rule)
            for _, category, rule in _identity_matches(relative, identities)
        )
    return findings


def report(findings: Iterable[Finding]) -> list[dict[str, object]]:
    return [asdict(finding) for finding in findings]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"publication scan: {args.root} is not a directory", file=sys.stderr)
        return 2
    try:
        findings = scan_tree(args.root)
    except OSError as exc:
        print(f"publication scan: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report(findings), indent=2))
    else:
        for finding in findings:
            print(finding)
        print(
            f"publication scan: {len(findings)} finding(s) in {args.root}"
            if findings
            else f"publication scan: {args.root} is clean"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
