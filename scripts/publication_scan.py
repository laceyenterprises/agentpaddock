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

    python3 scripts/publication_scan.py [ROOT] [--json] [--tracked-only]

The pre-push mirror uses ``--tracked-only`` to scan index blobs and every
unpublished commit reachable from HEAD, excluding all remote-tracking refs,
without reading local untracked metadata. Only HEAD is checked, not other pushed
refs; the index also includes staged work that a push does not publish. Local
edits and deletions cannot hide committed content. Gitlinks are not file blobs.
The default full-tree mode also works on exports without Git metadata.

Exit status: 0 clean, 1 findings, 2 usage or read error. Stdlib only.
"""

from __future__ import annotations

import argparse
import errno
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, replace
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
    blob: str | None = None

    def __str__(self) -> str:
        return f"{self.path}" + (f" [blob {self.blob}]" if self.blob else "") + f":{self.line}:{self.column}: {self.category} ({self.rule})"


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


def _git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    """Read Git with at most three attempts for transient host failures."""
    for attempt in range(3):
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                input=input,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            transient = (
                isinstance(exc, OSError)
                and exc.errno in {errno.EAGAIN, errno.ENOMEM, errno.EINTR, errno.ETIMEDOUT}
                or isinstance(exc, subprocess.CalledProcessError)
                and re.search(
                    rb"resource temporarily unavailable|temporary failure|cannot fork|"
                    rb"interrupted system call",
                    exc.stderr or b"",
                    re.I,
                )
            )
            if not transient or attempt == 2:
                raise
            time.sleep((0.25, 0.5)[attempt])
    raise AssertionError("unreachable")


def _index_entries(root: Path) -> Iterator[tuple[bytes, bytes]]:
    for entry in _git(root, "ls-files", "--cached", "--stage", "-z").split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            mode, oid, _stage = metadata.split()
            if mode != b"160000":  # A gitlink references a commit, not a file.
                yield name, oid


def iter_files(root: Path, *, tracked_only: bool = False) -> Iterator[Path]:
    """Tracked files, or all files outside :data:`SKIP_DIRS` and egg-info."""
    if tracked_only:
        # NUL separation preserves spaces, newlines and non-UTF-8 file names.
        # Tracked files must be scanned even in normally skipped cache directories.
        for name in sorted({name for name, _oid in _index_entries(root)}):
            yield root / os.fsdecode(name)
        return
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = sorted(
            name for name in subdirs if name not in SKIP_DIRS and not name.endswith(".egg-info")
        )
        for name in sorted(files):
            if not name.endswith((".pyc", ".pyo")):
                yield Path(directory) / name


def iter_file_contents(root: Path, *, tracked_only: bool = False) -> Iterator[tuple[Path, bytes, str | None]]:
    """Read exports, or index and unpublished HEAD blobs, one blob at a time."""
    if not tracked_only:
        for path in iter_files(root):
            yield path, path.read_bytes(), None
        return

    if _git(root, "rev-parse", "--show-prefix").strip():
        raise OSError("--tracked-only requires the repository root")
    entries = set(_index_entries(root))
    try:
        head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD").decode().strip()
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 1:
            raise
        head = None
    if head:
        # Remote-reachable content is already published; only new history is gated.
        for commit in _git(root, "rev-list", head, "--not", "--remotes").decode().splitlines():
            for entry in _git(root, "ls-tree", "--full-tree", "-r", "-z", commit).split(b"\0"):
                if entry:
                    metadata, name = entry.split(b"\t", 1)
                    _mode, kind, oid = metadata.split()
                    if kind == b"blob":
                        entries.add((name, oid))

    # Group paths by object; each bounded read holds only one blob in memory.
    paths = {}
    for name, oid in sorted(entries):
        paths.setdefault(oid, []).append(name)
    for oid, names in paths.items():
        output = _git(root, "cat-file", "--batch", input=oid + b"\n")
        end = output.find(b"\n")
        if end < 0:
            raise OSError("Git returned a truncated file blob header")
        header = output[:end].split()
        if len(header) != 3 or header[:2] != [oid, b"blob"] or not header[2].isdigit():
            raise OSError("Git returned an unreadable file blob")
        size = int(header[2])
        offset = end + 1
        if len(output) != offset + size + 1 or output[-1:] != b"\n":
            raise OSError("Git returned a truncated file blob")
        content = output[offset:offset + size]
        del output
        for name in names:
            yield root / os.fsdecode(name), content, oid.decode("ascii")


def scan_tree(
    root: Path, *, identities: Identities = IDENTITIES, tracked_only: bool = False
) -> list[Finding]:
    """Every finding in the tree at ``root``; paths are relative and POSIX."""
    findings: list[Finding] = []
    for path, content, oid in iter_file_contents(root, tracked_only=tracked_only):
        relative = path.relative_to(root).as_posix()
        # Binary files are read as text too: a key does not stop leaking in a blob.
        text = content.decode("utf-8", errors="replace")
        findings.extend(replace(finding, blob=oid) for finding in scan_text(text, relative, identities=identities))
        findings.extend(
            Finding(relative, 0, 0, category, rule, oid)
            for _, category, rule in _identity_matches(relative, identities)
        )
    return findings


def report(findings: Iterable[Finding]) -> list[dict[str, object]]:
    return [asdict(finding) for finding in findings]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    parser.add_argument(
        "--tracked-only", action="store_true", help="scan index and unpublished HEAD-history blobs (repository root only)"
    )
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"publication scan: {args.root} is not a directory", file=sys.stderr)
        return 2
    try:
        findings = scan_tree(args.root, tracked_only=args.tracked_only)
    except (OSError, subprocess.SubprocessError) as exc:
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
