"""PDK-05: the publication scan refuses each category and never echoes a match.

Fixtures are assembled from fragments, so this file never holds a value the
scan refuses: it ships in the tree the scan gates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
