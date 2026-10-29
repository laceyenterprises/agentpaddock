"""PDK-05: the publication scan refuses each category and never echoes a match.

Fixtures are assembled from fragments, so this file never holds a value the
scan refuses: it ships in the tree the scan gates.
"""

from __future__ import annotations

import errno
import gzip
import json
import shlex
import subprocess
from pathlib import Path

import pytest

import leaderboard as lb

import publication_scan as scan

MODULE_ROOT = Path(__file__).resolve().parents[1]

# One line per (category, rule), each built so no literal reaches this file.
OFFENDING = [
    ("secret", "private-key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
    ("secret", "aws-access-key", "key = " + "AKIA" + "Q" * 16),
    ("secret", "github-token", "token: " + "gh" + "p_" + "a1B2" * 9),
    ("secret", "github-token", "github" + "_pat_" + "x" * 30),
    ("secret", "anthropic-key", "sk-" + "ant-" + "api03-" + "z" * 24),
    ("secret", "openai-key", "sk-" + "proj-" + "Q" * 40),
    ("secret", "slack-token", "xo" + "xb-" + "1234567890-abc"),
    ("secret", "google-api-key", "AI" + "za" + "S" * 35),
    ("secret", "fireworks-key", "fw" + "_" + "k" * 24),
    ("secret", "url-credentials", "https://" + "bot:hunter2" + "@router.example.com/v1"),
    ("op-reference", "op-uri", "ref = " + "op:" + "//Private/router/credential"),
    ("home-path", "unix-home", "cd " + "/Users" + "/someone/src"),
    ("home-path", "unix-home", "cache in " + "/home" + "/someone/.cache"),
    ("home-path", "windows-home", "C:" + "\\Users\\someone\\repo"),
    ("tailnet", "cgnat-ip", "ssh " + "100." + "101.7.9"),
    ("tailnet", "ts-net-name", "http://box.tail1234" + ".ts" + ".net"),
    ("internal-url", "private-address", "http://" + "192.168" + ".1.20:8080/hook"),
    ("internal-url", "private-address", "https://" + "10." + "0.0.5/api"),
    ("internal-url", "private-domain", "https://" + "wiki" + ".internal/page"),
    ("internal-url", "private-domain", "http://build" + ".local:4000"),
    ("hostname", "machine-shape", "ran on " + "ops-" + "macbook-pro"),
    ("hostname", "apple-silicon-shape", "ran on " + "studio-" + "m2max"),
    ("private-repo", "owner-repository", scan.PUBLIC_OWNER + "/" + "internal-tools"),
    ("pages-link", "github-io", "https://someone" + ".github" + ".io/paddock/"),
]


@pytest.mark.parametrize(("category", "rule", "line"), OFFENDING, ids=lambda v: str(v)[:24])
def test_each_category_is_refused(category, rule, line):
    findings = scan.scan_text(f"prefix\n{line}\n", "fixture.txt")
    assert (category, rule) in {(f.category, f.rule) for f in findings}
    assert all(f.line == 2 and f.path == "fixture.txt" for f in findings)


@pytest.mark.parametrize(
    ("category", "rule", "line"), OFFENDING, ids=[r for _, r, _ in OFFENDING]
)
def test_gzip_content_has_the_same_findings_as_plain_text(
    tmp_path, category, rule, line
):
    text = f"prefix\n{line}\n"
    path = tmp_path / "fixture.gz"
    path.write_bytes(gzip.compress(text.encode("utf-8")))
    assert scan.scan_tree(tmp_path) == scan.scan_text(text, path.name)


@pytest.mark.parametrize("storage", ["tree", "index", "commit"])
@pytest.mark.parametrize("suffix", [".gz", ".bin"])
def test_gzip_forbidden_content_is_scanned_by_magic(tmp_path, storage, suffix):
    path = tmp_path / ("fixture" + suffix)
    text = "prefix\n" + "gh" + "p_" + "Q" * 40 + "\n"
    path.write_bytes(gzip.compress(text.encode()))
    if storage != "tree":
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", path.name], check=True)
        if storage == "commit":
            _commit(tmp_path, "synthetic compressed fixture")
            subprocess.run(
                ["git", "-C", str(tmp_path), "rm", "-q", path.name], check=True
            )
            _commit(tmp_path, "remove synthetic fixture")
        else:
            path.unlink()
    findings = scan.scan_tree(tmp_path, tracked_only=storage != "tree")
    assert len(findings) == 1
    finding = findings[0]
    assert (
        finding.path,
        finding.line,
        finding.column,
        finding.category,
        finding.rule,
    ) == (path.name, 2, 1, "secret", "github-token")
    assert (finding.blob is not None) == (storage != "tree")


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize(
    "content", [b"not gzip", b"\x1f\x8bcorrupt", gzip.compress(b"ok")[:-1]]
)
def test_corrupt_gzip_is_a_finding(tmp_path, content, tracked):
    path = tmp_path / "fixture.gz"
    path.write_bytes(content)
    if tracked:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", path.name], check=True)
    findings = scan.scan_tree(tmp_path, tracked_only=tracked)
    assert len(findings) == 1
    assert findings[0].category == "gzip"
    assert findings[0].rule == "invalid-or-oversized-gzip"
    assert (findings[0].blob is not None) == tracked


def test_gzip_cap_covers_concatenated_members(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, "MAX_DECOMPRESSED_BYTES", 32)
    path = tmp_path / "fixture.gz"
    path.write_bytes(gzip.compress(b"x" * 16) + gzip.compress(b"y" * 16))
    assert scan.scan_tree(tmp_path) == []
    path.write_bytes(path.read_bytes() + gzip.compress(b"z"))
    findings = scan.scan_tree(tmp_path)
    assert len(findings) == 1 and findings[0].category == "gzip"


@pytest.mark.parametrize(
    "line",
    [
        "https://github.com/" + scan.PUBLIC_OWNER + "/agentpaddock",
        "git clone git@github.com:" + scan.PUBLIC_OWNER + "/agentpaddock.git",
        "https://agentpaddock.ai/methodology.html",
        "http://127.0.0.1:4000 and http://localhost:8000",
        "runs-on: ubuntu-latest; HOME=/home/runner",
        'if ref.startswith("op://"):',
        "a sha256 of 64 hex: " + "ab12" * 16,
        "GYM-M1 and HRX-04 are ticket ids",
        "the 100.64/10 range is reserved",
    ],
)
def test_public_values_pass(line):
    assert scan.scan_text(line) == []


def test_identities_match_by_fingerprint_only():
    identities = scan.Identities(
        words=frozenset({scan.fingerprint("jdoe")}),
        hostnames=frozenset({scan.fingerprint("lab-box")}),
        names=frozenset({scan.fingerprint("jane doe")}),
        emails=frozenset({scan.fingerprint("jane@example.org")}),
        email_domains=frozenset({scan.fingerprint("corp.example")}),
    )
    text = "\n".join(
        [
            "maintained by Jane Doe",
            "mail jane@example.org",
            "or anyone@corp.example",
            "see github.com/JDoe",
            "built on lab-box",
            "Jane and Doe, separately, are fine",
        ]
    )
    found = [(f.line, f.category, f.rule) for f in scan.scan_text(text, identities=identities)]
    assert found == [
        (1, "operator-identity", "name"),
        (2, "operator-identity", "email"),
        (3, "operator-identity", "email-domain"),
        (4, "operator-identity", "account-or-handle"),
        (5, "hostname", "fingerprint"),
    ]


def test_shipped_fingerprints_are_digests_not_names():
    for digests in vars(scan.IDENTITIES).values():
        assert digests and all(len(d) == 64 and int(d, 16) >= 0 for d in digests)


def test_allow_marker_keeps_only_its_category_and_needs_a_reason():
    value = "op:" + "//Private/router/credential"
    marker = "publication-scan: " + "allow(op-reference)"
    assert scan.scan_text(f"{value}  # {marker}: synthetic") == []
    assert scan.scan_text(f"{value}  # {marker}:") != []
    assert scan.scan_text(f"{value}  # publication-scan: allow(secret): wrong category") != []


def test_scan_tree_reports_locations_never_values(tmp_path, capsys):
    secret = "gh" + "p_" + "S3cr3tValue" * 4
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text(f"ok\ntoken {secret}\n", encoding="utf-8")
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text(secret, encoding="utf-8")  # built output
    (tmp_path / "blob.bin").write_bytes(b"\x00\xff" + secret.encode())

    assert scan.main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "docs/notes.md:2:7: secret (github-token)" in output
    assert "blob.bin:1:" in output
    assert "site/" not in output
    assert secret not in output

    assert scan.main([str(tmp_path), "--json"]) == 1
    records = json.loads(capsys.readouterr().out.strip())
    assert {r["path"] for r in records} == {"docs/notes.md", "blob.bin"}
    assert secret not in json.dumps(records)


def test_clean_tree_and_usage_errors(tmp_path, capsys):
    (tmp_path / "README.md").write_text("# Paddock\n", encoding="utf-8")
    assert scan.main([str(tmp_path)]) == 0
    assert "is clean" in capsys.readouterr().out
    assert scan.main([str(tmp_path / "absent")]) == 2


@pytest.mark.parametrize("check_name", ["publication scan", "internal-note guard"])
def test_mirror_scans_only_tracked_files(tmp_path, check_name):
    manifest = json.loads((MODULE_ROOT / "scripts/ci-mirror/checks.json").read_text())
    command = next(
        shlex.split(check["command"])
        for check in manifest["path-glob"]["**"]
        if check["name"] == check_name
    )

    def run_scan(argv):
        return subprocess.run(
            [*argv, str(tmp_path)], cwd=MODULE_ROOT, capture_output=True, timeout=10
        )

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "README.md").write_text("# Paddock\n")
    (tmp_path / ".gitignore").write_text(".agent-os/\n__pycache__/\n*.py[cod]\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md", ".gitignore"], check=True)
    payload = "gh" + "p_" + "Q" * 40 + "\nReviewer " + "verdict: approve\n"
    for directory in (".claude", ".agent-os", "__pycache__"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "local metadata\nfile.pyc").write_bytes(
            b"\x00\xff" + payload.encode()
        )
        (tmp_path / directory / "metadata.json").write_text(payload)

    # The actual manifest commands ignore both untracked and ignored metadata.
    assert run_scan(command).returncode == 0
    full_tree_command = [arg for arg in command if arg != "--tracked-only"]
    assert run_scan(full_tree_command).returncode == 1

    # Staged files must be checked even under a normally skipped directory,
    # with a cache suffix, binary bytes, spaces and a newline in the filename.
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-f", "__pycache__/local metadata\nfile.pyc"],
        check=True,
    )
    result = run_scan(command)
    assert result.returncode == 1
    assert b"__pycache__/" in result.stdout

    # Deleting a local file cannot hide its staged blob.
    (tmp_path / "__pycache__" / "local metadata\nfile.pyc").unlink()
    assert run_scan(command).returncode == 1


def _commit(root, message):
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            message,
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("script", ["publication_scan.py", "internal_note_guard.py"])
@pytest.mark.parametrize("cleanup", ["edit", "delete", "stage", "commit"])
@pytest.mark.parametrize("compressed", [False, True])
def test_committed_content_cannot_be_hidden(tmp_path, script, cleanup, compressed):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    path = tmp_path / "tracked\tfile\n.bin"
    payload = "gh" + "p_" + "Q" * 40 + "\nReviewer " + "verdict: approve\n"
    content = b"\x00\xff" + payload.encode()
    path.write_bytes(gzip.compress(content) if compressed else content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    _commit(tmp_path, "fixture")
    if cleanup == "delete":
        path.unlink()
    else:
        path.write_text("clean\n")
    if cleanup in {"stage", "commit"}:
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    if cleanup == "commit":
        _commit(tmp_path, "clean fixture")
    result = subprocess.run(
        ["python3", str(MODULE_ROOT / "scripts" / script), str(tmp_path), "--tracked-only"],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1, result.stderr
    assert b"tracked" in result.stdout
    assert payload.encode() not in result.stdout + result.stderr


@pytest.mark.parametrize("initialized", [False, True])
def test_tracked_scans_skip_gitlinks(tmp_path, initialized):
    import internal_note_guard as guard

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "README.md").write_text("clean\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    _commit(tmp_path, "fixture")
    oid = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"]).strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            oid.decode(),
            "tools/child",
        ],
        check=True,
    )
    _commit(tmp_path, "gitlink fixture")
    if initialized:
        child = tmp_path / "tools" / "child"
        subprocess.run(["git", "init", "-q", str(child)], check=True, capture_output=True)
        (child / "private.txt").write_text("gh" + "p_" + "Q" * 40 + "\nReviewer " + "verdict: ok")
    assert [p.name for p in scan.iter_files(tmp_path, tracked_only=True)] == ["README.md"]
    assert scan.main([str(tmp_path), "--tracked-only"]) == 0
    assert guard.main([str(tmp_path), "--tracked-only"]) == 0


@pytest.mark.parametrize("failure", ["stderr", "spawn"])
@pytest.mark.parametrize("recover", [False, True])
def test_git_transient_retries_are_bounded(monkeypatch, tmp_path, failure, recover):
    attempts, delays = [], []
    exceptions = {
        "stderr": subprocess.CalledProcessError(
            128, ["git"], stderr=b"resource temporarily unavailable"
        ),
        "spawn": OSError(errno.EAGAIN, "resource temporarily unavailable"),
        "timeout": subprocess.TimeoutExpired(["git"], 30),
    }

    def run(command, **kwargs):
        attempts.append((command, kwargs))
        if not recover or len(attempts) < 3:
            raise exceptions[failure]
        return subprocess.CompletedProcess(command, 0, stdout=b"ok")

    monkeypatch.setattr(scan.subprocess, "run", run)
    monkeypatch.setattr(scan.time, "sleep", delays.append)
    if recover:
        assert scan._git(tmp_path, "ls-files") == b"ok"
    else:
        with pytest.raises(type(exceptions[failure])):
            scan._git(tmp_path, "ls-files")
    assert len(attempts) == 3
    assert attempts[0] == attempts[1] == attempts[2]
    assert attempts[0][1]["timeout"] == 30
    assert delays == [0.25, 0.5]


@pytest.mark.parametrize("script", ["publication_scan", "internal_note_guard"])
@pytest.mark.parametrize("failure", ["permanent", "transient"])
def test_git_failures_return_read_error(monkeypatch, tmp_path, script, failure):
    import internal_note_guard as guard

    calls, delays = [], []

    def run(command, **kwargs):
        calls.append(command)
        stderr = b"not a git repository" if failure == "permanent" else b"temporary failure"
        raise subprocess.CalledProcessError(128, command, stderr=stderr)

    monkeypatch.setattr(scan.subprocess, "run", run)
    monkeypatch.setattr(scan.time, "sleep", delays.append)
    module = scan if script == "publication_scan" else guard
    assert module.main([str(tmp_path), "--tracked-only"]) == 2
    assert len(calls) == (1 if failure == "permanent" else 3)
    assert delays == ([] if failure == "permanent" else [0.25, 0.5])


@pytest.mark.parametrize("failure", ["missing", "header", "size", "content"])
def test_unreadable_git_blob_fails_closed(monkeypatch, tmp_path, failure):
    import internal_note_guard as guard

    oid = b"a" * 40

    def git(root, *args, input=None):
        if args[0] == "ls-files":
            return b"100644 " + oid + b" 0\tfile.txt\0"
        if args == ("rev-parse", "--show-prefix"):
            return b""
        if args[0] == "rev-parse":
            raise subprocess.CalledProcessError(1, ["git"])
        assert args == ("cat-file", "--batch")
        assert input == oid + b"\n"
        return {
            "missing": oid + b" missing\n",
            "header": oid,
            "size": oid + b" blob unknown\n",
            "content": oid + b" blob 10\nshort\n",
        }[failure]

    monkeypatch.setattr(scan, "_git", git)
    assert scan.main([str(tmp_path), "--tracked-only"]) == 2
    assert guard.main([str(tmp_path), "--tracked-only"]) == 2


@pytest.mark.parametrize("script", ["publication_scan.py", "internal_note_guard.py"])
def test_tracked_scan_requires_git_repository(tmp_path, script):
    result = subprocess.run(
        [
            "python3",
            str(MODULE_ROOT / "scripts" / script),
            str(tmp_path),
            "--tracked-only",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stderr


def test_the_gates_and_their_rules_pass_their_own_scan():
    # The full shipped tree is scanned by the export and by `ci / fast`; these
    # files hold the rules, so they are the likeliest to trip over themselves.
    for name in (
        "scripts/publication_scan.py",
        "scripts/internal_note_guard.py",
        "tests/test_publication_scan.py",
        "tests/test_internal_note_guard.py",
        "tests/test_ci_policy.py",
        "AGENTS.md",
        ".github/workflows/ci.yml",
        ".github/workflows/pages.yml",
    ):
        path = MODULE_ROOT / name
        assert scan.scan_text(path.read_text(encoding="utf-8"), name) == [], name


def test_git_timeouts_are_not_retried(monkeypatch, tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(scan.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        scan._git(tmp_path, "cat-file", "--batch", input=b"")
    assert len(calls) == 1


def test_published_history_is_excluded_and_unpublished_blob_is_named(tmp_path):
    import internal_note_guard as guard
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    path = tmp_path / "old.txt"
    path.write_text("gh" + "p_" + "Q" * 40 + "\nReviewer " + "verdict: approve\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    _commit(tmp_path, "published fixture")
    subprocess.run(["git", "-C", str(tmp_path), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    path.unlink()
    subprocess.run(["git", "-C", str(tmp_path), "add", "-u"], check=True)
    _commit(tmp_path, "cleanup")
    assert scan.scan_tree(tmp_path, tracked_only=True) == []
    assert guard.check_tree(tmp_path, tracked_only=True) == []
    path.write_text("gh" + "p_" + "R" * 40 + "\nReviewer " + "verdict: approve\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    _commit(tmp_path, "unpublished fixture")
    oid = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD:old.txt"]).decode().strip()
    path.unlink()
    subprocess.run(["git", "-C", str(tmp_path), "add", "-u"], check=True)
    _commit(tmp_path, "unpublished cleanup")
    for findings in (scan.scan_tree(tmp_path, tracked_only=True), guard.check_tree(tmp_path, tracked_only=True)):
        assert findings
        assert all(f.blob == oid for f in findings)
        assert oid in str(findings[0])
    child = tmp_path / "child"
    child.mkdir()
    assert scan.main([str(child), "--tracked-only"]) == 2
    assert guard.main([str(child), "--tracked-only"]) == 2
