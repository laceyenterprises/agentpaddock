"""The submission API (lib/api) against in-memory fakes: every contract row.

No network, no AWS, no credentials: GitHub is a fake resolver, storage is the
in-memory fake, and the AWS store classes run against a fake boto3 client.
Signing keys are derived from fixed synthetic seeds.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "lib" / "api"
sys.path.insert(0, str(API_DIR / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from paddock_api import ed25519  # noqa: E402
from paddock_api.app import ERRORS, Limits, Request, SubmissionApi  # noqa: E402
from paddock_api.auth import AuthUnavailable, GitHubUserResolver, bearer_token  # noqa: E402
from paddock_api.canonical import canonical_json  # noqa: E402
from paddock_api.handler import handle_event, maintainers_from  # noqa: E402
from paddock_api.storage import (  # noqa: E402
    BundleRecord,
    DynamoBundleIndex,
    DynamoKeyStore,
    KeyRecord,
    S3BundleBlobs,
    memory_storage,
)

import leaderboard  # noqa: E402

# Synthetic tokens: shaped like nothing GitHub issues, so no scan mistakes them.
ALICE, BOB, MAINTAINER = "alice-example", "bob-example", "maint-example"
TOKENS = {
    "tok-alice-0001": ALICE,
    "tok-bob-0002": BOB,
    "tok-maint-0003": MAINTAINER,
}
DOWN = "tok-github-down"
ANSWER_TEXT = b'{"verdict": "SECRET-ANSWER-MARKER-7f3a"}\n'
COMMIT = "0123456789abcdef0123456789abcdef01234567"


# -- helpers -------------------------------------------------------------------


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(h[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, h[32:]


def public_key(seed: bytes) -> bytes:
    scalar, _ = _expand(seed)
    return ed25519.compress(ed25519._mul(scalar, ed25519.BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """RFC 8032 signing, test-only: the Lambda never signs."""
    scalar, prefix = _expand(seed)
    pub = public_key(seed)
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % ed25519.L
    r_bytes = ed25519.compress(ed25519._mul(r, ed25519.BASE))
    h = (
        int.from_bytes(hashlib.sha512(r_bytes + pub + message).digest(), "little")
        % ed25519.L
    )
    return r_bytes + ((r + h * scalar) % ed25519.L).to_bytes(32, "little")


SEED_A = hashlib.sha256(b"paddock synthetic seed a").digest()
SEED_B = hashlib.sha256(b"paddock synthetic seed b").digest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, token: str) -> str | None:
        self.calls.append(token)
        if token == DOWN:
            raise AuthUnavailable("down")
        return TOKENS.get(token)


@pytest.fixture
def api() -> SubmissionApi:
    return SubmissionApi(
        storage=memory_storage(),
        resolver=FakeResolver(),
        maintainers=frozenset({"Maint-Example"}),
        limits=Limits(max_body_bytes=64 * 1024, max_files=8),
        now=lambda: "2026-11-04T00:00:00Z",
    )


def auth(token: str = "tok-alice-0001") -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def post_key(api, body, token="tok-alice-0001"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return api.handle(
        Request(
            "POST",
            "/submission/keys",
            {**auth(token), "content-type": "application/json"},
            raw,
        )
    )


def register(api, seed=SEED_A, token="tok-alice-0001") -> str:
    response = post_key(
        api, {"algorithm": "Ed25519", "public_key": b64(public_key(seed))}, token
    )
    assert response.status == 201, response.body
    return response.body["key_id"]


def run(run_id="run-001", path="answers/run-001.json", data=ANSWER_TEXT, **overrides):
    entry = {
        "run_id": run_id,
        "answer_file": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "results_document": "results/2026-01-01-example.json",
        "fork_commit": COMMIT,
        "verifier": {"task": "coding-cache-context", "task_version": 1},
    }
    entry.update(overrides)
    return entry


def envelope(key_id, runs, seed=SEED_A, signature=None):
    manifest = {"key_id": key_id, "runs": runs}
    if signature is None:
        signature = b64(sign(seed, canonical_json(manifest).encode("utf-8")))
    return {"manifest": manifest, "signature": signature}


def multipart(
    fields: list[tuple[str, str | None, bytes]], boundary="paddockBoundary42"
):
    out = io.BytesIO()
    for name, filename, data in fields:
        out.write(f"--{boundary}\r\n".encode())
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        out.write(f"Content-Disposition: {disposition}\r\n".encode())
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def post_bundle(api, env, files=None, token="tok-alice-0001", extra=()):
    bundle = env if isinstance(env, bytes) else json.dumps(env).encode("utf-8")
    if files is None:
        files = {"answers/run-001.json": ANSWER_TEXT}
    fields = [("bundle", "bundle.json", bundle)]
    fields += [("answers", path, data) for path, data in files.items()]
    fields += list(extra)
    body, ctype = multipart(fields)
    return api.handle(
        Request(
            "POST", "/submission/bundles", {**auth(token), "content-type": ctype}, body
        )
    )


def get_bundle(api, digest, token="tok-alice-0001"):
    return api.handle(Request("GET", f"/submission/bundles/{digest}", auth(token)))


def expect(response, status, code=None):
    assert response.status == status, response.body
    if code is not None:
        assert response.body["error"] == code
        assert ERRORS[code][0] == status
        assert response.body["message"] == ERRORS[code][1]


# -- Ed25519 and canonical JSON ---------------------------------------------------

RFC8032 = [  # RFC 8032 section 7.1, tests 1-3: (secret, public, message, signature)
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


@pytest.mark.parametrize("secret, public, message, signature", RFC8032)
def test_rfc8032_vectors(secret, public, message, signature):
    seed, pub, msg, sig = (
        bytes.fromhex(x) for x in (secret, public, message, signature)
    )
    assert public_key(seed) == pub
    assert sign(seed, msg) == sig
    assert ed25519.verify(pub, msg, sig)
    assert not ed25519.verify(pub, msg + b"x", sig)
    tampered = bytearray(sig)
    tampered[5] ^= 1
    assert not ed25519.verify(pub, msg, bytes(tampered))


def test_signature_with_s_not_below_group_order_is_rejected():
    seed, pub, _, sig = (bytes.fromhex(x) for x in RFC8032[0])
    s = int.from_bytes(sig[32:], "little") + ed25519.L
    assert not ed25519.verify(pub, b"", sig[:32] + s.to_bytes(32, "little"))


def test_matches_openssl_when_cryptography_is_installed():
    primitives = pytest.importorskip(
        "cryptography.hazmat.primitives.asymmetric.ed25519"
    )
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = primitives.Ed25519PrivateKey.from_private_bytes(SEED_A)
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert raw == public_key(SEED_A)
    message = canonical_json({"ü": [1, "/"], "a": {"z": 2}}).encode()
    assert ed25519.verify(raw, message, private.sign(message))
    assert sign(SEED_A, message) == private.sign(message)


def test_small_order_and_non_canonical_keys_are_invalid():
    identity = (1).to_bytes(32, "little")
    assert not ed25519.is_valid_public_key(identity)
    non_canonical = (ed25519.P + 1).to_bytes(32, "little")  # y >= p
    assert not ed25519.is_valid_public_key(non_canonical)
    assert ed25519.is_valid_public_key(public_key(SEED_A))


@pytest.mark.parametrize(
    "value",
    [
        {"b": 1, "a": [3, {"y": "é", "x": "/"}]},
        {"key_id": "k", "runs": [{"run_id": "ü\u0301", "n": 10**30}]},
        ["</script>", "\u2028", "tab\t"],
    ],
)
def test_canonical_json_is_the_leaderboard_encoding(value):
    assert canonical_json(value) == leaderboard.canonical_json(value)


# -- POST /submission/keys -----------------------------------------------------


def test_register_key_201(api):
    response = post_key(
        api, {"algorithm": "Ed25519", "public_key": b64(public_key(SEED_A))}
    )
    expect(response, 201)
    assert response.body == {
        "key_id": response.body["key_id"],
        "github_account": ALICE,
        "algorithm": "Ed25519",
    }
    assert re.fullmatch(r"ed25519-[0-9a-f]{32}", response.body["key_id"])


def test_register_key_409_same_account_returns_existing_id(api):
    key_id = register(api)
    response = post_key(
        api, {"algorithm": "Ed25519", "public_key": b64(public_key(SEED_A))}
    )
    expect(response, 409, "key_already_registered")
    assert response.body["key_id"] == key_id
    assert response.body["github_account"] == ALICE


def test_register_key_409_other_account_reveals_no_id(api):
    register(api)
    response = post_key(
        api,
        {"algorithm": "Ed25519", "public_key": b64(public_key(SEED_A))},
        "tok-bob-0002",
    )
    expect(response, 409, "key_already_registered")
    assert "key_id" not in response.body and "github_account" not in response.body


@pytest.mark.parametrize(
    "body, code",
    [
        (b"not json", "invalid_json"),
        (
            b'{"algorithm":"Ed25519","algorithm":"Ed25519","public_key":"x"}',
            "invalid_json",
        ),
        ({"algorithm": "RSA", "public_key": "AAAA"}, "unsupported_algorithm"),
        ({"algorithm": "ed25519", "public_key": "AAAA"}, "unsupported_algorithm"),
        ({"public_key": "AAAA"}, "unsupported_algorithm"),
        (["Ed25519"], "malformed_key"),
        ({"algorithm": "Ed25519"}, "malformed_key"),
        ({"algorithm": "Ed25519", "public_key": "!!!"}, "malformed_key"),
        ({"algorithm": "Ed25519", "public_key": b64(b"\x01" * 31)}, "malformed_key"),
        ({"algorithm": "Ed25519", "public_key": b64(b"\x01" * 33)}, "malformed_key"),
        (
            {"algorithm": "Ed25519", "public_key": b64((1).to_bytes(32, "little"))},
            "malformed_key",
        ),
        (
            {"algorithm": "Ed25519", "public_key": b64(public_key(SEED_A)).rstrip("=")},
            "malformed_key",
        ),
        ({"algorithm": "Ed25519", "public_key": 7}, "malformed_key"),
        (
            {
                "algorithm": "Ed25519",
                "public_key": b64(public_key(SEED_A)),
                "note": "x",
            },
            "malformed_key",
        ),
    ],
)
def test_register_key_400(api, body, code):
    expect(post_key(api, body), 400, code)
    assert api.storage.keys.items == {}


# -- authentication (every operation) ----------------------------------------------

OPERATIONS = [
    ("POST", "/submission/keys"),
    ("POST", "/submission/bundles"),
    ("GET", "/submission/bundles/" + "0" * 64),
]


@pytest.mark.parametrize("method, path", OPERATIONS)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": ""},
        {"authorization": "Bearer"},
        {"authorization": "Basic tok-alice-0001"},
        {"authorization": "token tok-alice-0001"},
        {"authorization": "Bearer unknown-token"},
        {"authorization": "Bearer tok alice"},
    ],
)
def test_401_unauthenticated(api, method, path, headers):
    expect(api.handle(Request(method, path, headers, b"{}")), 401, "unauthenticated")


def test_github_outage_is_503_not_401(api):
    expect(
        api.handle(Request("GET", OPERATIONS[2][1], auth(DOWN))),
        503,
        "auth_unavailable",
    )


def test_bearer_token_parsing():
    assert bearer_token("Bearer abc_123") == "abc_123"
    assert bearer_token("bearer  abc ") == "abc"
    assert bearer_token("Bearer a\r\nX-Injected: 1") is None
    assert (
        bearer_token("Bearer abc\n") == "abc"
    )  # stripped, never sent with the newline
    assert bearer_token("Bearer ab\ncd") is None
    assert bearer_token(None) is None


class _FakeHTTPResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_github_resolver_sends_token_once_and_keeps_nothing():
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization"), timeout))
        return _FakeHTTPResponse(b'{"login": "alice-example", "id": 1}')

    resolver = GitHubUserResolver(opener=opener)
    assert resolver.resolve("tok-alice-0001") == ALICE
    assert seen == [("https://api.github.com/user", "Bearer tok-alice-0001", 5.0)]
    assert "tok-alice-0001" not in repr(vars(resolver))


@pytest.mark.parametrize(
    "status, remaining, outcome",
    [
        (401, None, None),
        (403, "42", None),
        (403, "0", "unavailable"),
        (500, None, "unavailable"),
    ],
)
def test_github_resolver_maps_errors(status, remaining, outcome):
    def opener(request, timeout):
        headers = {"x-ratelimit-remaining": remaining} if remaining else {}
        raise urllib.error.HTTPError(
            request.full_url, status, "x", headers, io.BytesIO(b"")
        )

    resolver = GitHubUserResolver(opener=opener)
    if outcome is None:
        assert resolver.resolve("tok-alice-0001") is None
    else:
        with pytest.raises(AuthUnavailable) as caught:
            resolver.resolve("tok-alice-0001")
        assert "tok-alice-0001" not in str(caught.value)


def test_github_resolver_network_failure_is_unavailable():
    def opener(request, timeout):
        raise urllib.error.URLError("unreachable")

    with pytest.raises(AuthUnavailable):
        GitHubUserResolver(opener=opener).resolve("tok-alice-0001")


# -- POST /submission/bundles ----------------------------------------------------


def test_submit_bundle_202_stores_canonical_envelope_and_answers(api):
    key_id = register(api)
    env = envelope(key_id, [run()])
    response = post_bundle(api, env)
    expect(response, 202)
    digest = hashlib.sha256(canonical_json(env).encode("utf-8")).hexdigest()
    assert response.body == {"sha256": digest, "key_id": key_id, "status": "pending"}
    objects = api.storage.blobs.objects
    assert objects[f"bundles/{digest}/bundle.json"] == canonical_json(env).encode(
        "utf-8"
    )
    assert objects[f"bundles/{digest}/answers/answers/run-001.json"] == ANSWER_TEXT
    record = api.storage.bundles.items[digest]
    assert (record.github_account, record.status) == (ALICE, "pending")


def test_submit_bundle_digest_ignores_submitted_whitespace_and_key_order(api):
    key_id = register(api)
    env = envelope(key_id, [run()])
    pretty = json.dumps(
        {"signature": env["signature"], "manifest": env["manifest"]}, indent=2
    )
    response = post_bundle(api, pretty.encode())
    expect(response, 202)
    assert (
        response.body["sha256"]
        == hashlib.sha256(canonical_json(env).encode()).hexdigest()
    )


def test_submit_bundle_with_several_runs_and_unicode(api):
    key_id = register(api)
    other = b"second answer\n"
    runs = [
        run(run_id="run-ü-1"),
        run(run_id="run-2", path="answers/sub/run-2.txt", data=other),
    ]
    response = post_bundle(
        api,
        envelope(key_id, runs),
        files={"answers/run-001.json": ANSWER_TEXT, "answers/sub/run-2.txt": other},
    )
    expect(response, 202)


def test_submit_bundle_409_duplicate_returns_digest_and_status(api):
    key_id = register(api)
    env = envelope(key_id, [run()])
    first = post_bundle(api, env)
    expect(first, 202)
    api.storage.bundles.items[first.body["sha256"]] = BundleRecord(
        **{
            **vars(api.storage.bundles.items[first.body["sha256"]]),
            "status": "re-grading",
        }
    )
    second = post_bundle(api, env)
    expect(second, 409, "duplicate_bundle")
    assert second.body["sha256"] == first.body["sha256"]
    assert second.body["status"] == "re-grading"


def test_submit_bundle_409_when_index_write_races(api):
    key_id = register(api)
    env = envelope(key_id, [run()])
    digest = hashlib.sha256(canonical_json(env).encode()).hexdigest()
    real_get = api.storage.bundles.get
    calls = []

    def racing_get(sha):  # first lookup misses, a concurrent writer wins the put
        calls.append(sha)
        if len(calls) == 1:
            api.storage.bundles.items[sha] = BundleRecord(
                sha, key_id, ALICE, "pending", "t"
            )
            return None
        return real_get(sha)

    api.storage.bundles.get = racing_get
    response = post_bundle(api, env)
    expect(response, 409, "duplicate_bundle")
    assert response.body["sha256"] == digest


def test_submit_bundle_401_before_anything_else(api):
    response = post_bundle(api, b"garbage", token="nobody")
    expect(response, 401, "unauthenticated")


def test_submit_bundle_404_unknown_key(api):
    expect(
        post_bundle(api, envelope("ed25519-" + "0" * 32, [run()])), 404, "unknown_key"
    )


def test_submit_bundle_403_key_not_owned(api):
    key_id = register(api)  # alice's key
    expect(
        post_bundle(api, envelope(key_id, [run()]), token="tok-bob-0002"),
        403,
        "key_not_owned",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda env: env["manifest"]["runs"][0].update(run_id="run-tampered"),
        lambda env: env.update(signature=b64(b"\x00" * 64)),
        lambda env: env.update(signature="not base64!"),
        lambda env: env.update(signature=b64(b"\x01" * 63)),
        lambda env: env.update(signature=env["signature"].rstrip("=")),
    ],
)
def test_submit_bundle_422_invalid_signature(api, mutate):
    key_id = register(api)
    env = envelope(key_id, [run()])
    mutate(env)
    expect(post_bundle(api, env), 422, "invalid_signature")
    assert api.storage.bundles.items == {}


def test_submit_bundle_422_signed_by_another_key(api):
    key_id = register(api)
    expect(
        post_bundle(api, envelope(key_id, [run()], seed=SEED_B)),
        422,
        "invalid_signature",
    )


def test_submit_bundle_422_signature_must_cover_key_id(api):
    key_a = register(api)
    key_b = register(api, seed=SEED_B)
    env = envelope(key_a, [run()])
    env["manifest"]["key_id"] = key_b  # signature bound key_a
    expect(post_bundle(api, env), 422, "invalid_signature")


def test_submit_bundle_422_answer_digest_mismatch(api):
    key_id = register(api)
    response = post_bundle(
        api,
        envelope(key_id, [run()]),
        files={"answers/run-001.json": ANSWER_TEXT + b" "},
    )
    expect(response, 422, "answer_digest_mismatch")
    assert api.storage.blobs.objects == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"results_document": "results/sub/2026-01-01-example.json"},
        {"results_document": "results/templates/results.json"},
        {"results_document": "../results/2026-01-01-example.json"},
        {"fork_commit": COMMIT[:12]},
        {"fork_commit": COMMIT.upper()},
        {"results_document": "results/\u0662026-01-01-example.json"},
        {"verifier": {"task": "Bad Task", "task_version": 1}},
        {"verifier": {"task": "coding-cache-context", "task_version": 0}},
    ],
)
def test_submit_bundle_422_incompatible_pin(api, overrides):
    key_id = register(api)
    expect(
        post_bundle(api, envelope(key_id, [run(**overrides)])), 422, "incompatible_pin"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/abs/run.json",
        "../run.json",
        "answers/../run.json",
        "answers/./run.json",
        "answers//run.json",
        "answers/",
        "answers\\run.json",
        "C:/answers/run.json",
        ".hidden",
        "answers/-flag",
        "answers/run\x00.json",
        "answers/run one.json",
        "answers/run.json\n",
        "a/" * 20 + "x",
    ],
)
def test_submit_bundle_400_unsafe_path(api, path):
    key_id = register(api)
    response = post_bundle(api, envelope(key_id, [run(path=path)]), files={})
    expect(response, 400, "unsafe_path")


def test_submit_bundle_400_path_that_is_another_paths_directory(api):
    key_id = register(api)
    runs = [run(path="answers/a"), run(run_id="run-2", path="answers/a/b")]
    response = post_bundle(
        api,
        envelope(key_id, runs),
        files={"answers/a": ANSWER_TEXT, "answers/a/b": ANSWER_TEXT},
    )
    expect(response, 400, "unsafe_path")


def test_submit_bundle_400_duplicate_run_id(api):
    key_id = register(api)
    runs = [run(), run(path="answers/run-002.json")]
    files = {"answers/run-001.json": ANSWER_TEXT, "answers/run-002.json": ANSWER_TEXT}
    expect(
        post_bundle(api, envelope(key_id, runs), files=files), 400, "duplicate_run_id"
    )


@pytest.mark.parametrize("second", ["answers/run-001.json", "Answers/Run-001.json"])
def test_submit_bundle_400_duplicate_answer_file(api, second):
    key_id = register(api)
    runs = [run(), run(run_id="run-002", path=second)]
    expect(post_bundle(api, envelope(key_id, runs)), 400, "duplicate_answer_file")


def test_submit_bundle_400_same_file_uploaded_twice(api):
    key_id = register(api)
    response = post_bundle(
        api,
        envelope(key_id, [run()]),
        extra=[("answers", "answers/run-001.json", ANSWER_TEXT)],
    )
    expect(response, 400, "duplicate_answer_file")


def test_submit_bundle_400_missing_file(api):
    key_id = register(api)
    expect(post_bundle(api, envelope(key_id, [run()]), files={}), 400, "missing_files")


@pytest.mark.parametrize(
    "extra",
    [
        [("answers", "answers/unlisted.json", b"x")],
        [("notes", None, b"x")],
        [("notes", "notes.txt", b"x")],
    ],
)
def test_submit_bundle_400_extra_files(api, extra):
    key_id = register(api)
    expect(post_bundle(api, envelope(key_id, [run()]), extra=extra), 400, "extra_files")


def _signed_raw(key_id, manifest_text: str) -> bytes:
    """An envelope whose manifest is given as raw JSON text."""
    return (
        '{"manifest":' + manifest_text + ',"signature":"' + b64(b"\x00" * 64) + '"}'
    ).encode()


@pytest.mark.parametrize(
    "bundle",
    [
        b"not json",
        b"\xff\xfe",
        b'{"manifest": {"key_id": "k", "runs": []}, "signature": "x"}',
        b'{"manifest": {"key_id": "k", "key_id": "k", "runs": [{}]}, "signature": "x"}',
        b'{"manifest": {"key_id": "k", "runs": [{}]}, "signature": "x", "extra": 1}',
        b'{"manifest": {"key_id": "k", "runs": [{}]}}',
        b'["manifest"]',
        b'{"manifest": {"key_id": "\\ud800", "runs": [{}]}, "signature": "x"}',
        b'{"manifest": {"key_id": "k", "runs": [1]}, "signature": "x"}',
    ],
)
def test_submit_bundle_400_malformed_manifest(api, bundle):
    register(api)
    expect(post_bundle(api, bundle), 400, "malformed_manifest")


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "A" * 64},
        {"sha256": "0" * 63},
        {"sha256": "0" * 64 + "\n"},
        {"fork_commit": COMMIT + "\n"},
        {"results_document": "results/2026-01-01-example.json\n"},
        {"verifier": {"task": "coding-cache-context\n", "task_version": 1}},
        {"run_id": ""},
        {"run_id": 7},
        {"answer_file": None},
        {"fork_commit": 1},
        {"verifier": {"task": "t", "task_version": 1.0}},
        {"verifier": {"task": "t", "task_version": True}},
        {"verifier": {"task": "t", "task_version": "1"}},
        {"verifier": {"task": "t"}},
        {"extra": "field"},
    ],
)
def test_submit_bundle_400_malformed_run(api, change):
    key_id = register(api)
    expect(
        post_bundle(api, envelope(key_id, [run(**change)])), 400, "malformed_manifest"
    )


def test_submit_bundle_400_float_in_manifest(api):
    key_id = register(api)
    env = envelope(key_id, [run()])
    raw = json.dumps(env).replace('"task_version": 1', '"task_version": 1e0').encode()
    expect(post_bundle(api, raw), 400, "malformed_manifest")


def test_submit_bundle_400_signature_not_a_string(api):
    key_id = register(api)
    env = envelope(key_id, [run()], signature=None)
    env["signature"] = 5
    expect(post_bundle(api, env), 400, "malformed_manifest")


@pytest.mark.parametrize(
    "content_type, body",
    [
        ("application/json", b"{}"),
        ("multipart/form-data", b"--x\r\n\r\n--x--"),
        ("multipart/form-data; boundary=x", b"no delimiter at all"),
        (
            "multipart/form-data; boundary=x",
            b"--x\r\nContent-Disposition: form-data\r\n\r\nv\r\n--x--",
        ),
        (
            "multipart/form-data; boundary=x",
            b"--x\r\nContent-Disposition: attachment; name=bundle\r\n\r\nv\r\n--x--",
        ),
        (
            "multipart/form-data; boundary=x",
            b"--x\r\nContent-Disposition: form-data; name=bundle\r\n\r\nv\r\n--x",
        ),
    ],
)
def test_submit_bundle_400_malformed_multipart(api, content_type, body):
    request = Request(
        "POST", "/submission/bundles", {**auth(), "content-type": content_type}, body
    )
    expect(api.handle(request), 400, "malformed_request")


def test_submit_bundle_400_missing_or_repeated_bundle_part(api):
    key_id = register(api)
    env = json.dumps(envelope(key_id, [run()])).encode()
    for fields in (
        [("answers", "answers/run-001.json", ANSWER_TEXT)],
        [("bundle", None, env)] * 2,
    ):
        body, ctype = multipart(fields)
        request = Request(
            "POST", "/submission/bundles", {**auth(), "content-type": ctype}, body
        )
        expect(api.handle(request), 400, "malformed_request")


def test_submit_bundle_400_answers_part_without_filename(api):
    key_id = register(api)
    response = post_bundle(
        api, envelope(key_id, [run()]), files={}, extra=[("answers", None, b"x")]
    )
    expect(response, 400, "malformed_request")


def test_submit_bundle_413_too_large_and_too_many_files(api):
    key_id = register(api)
    big = b"x" * (64 * 1024)
    response = post_bundle(
        api, envelope(key_id, [run(data=big)]), files={"answers/run-001.json": big}
    )
    expect(response, 413, "payload_too_large")
    runs = [run(run_id=f"r{i}", path=f"answers/r{i}.json") for i in range(9)]
    files = {f"answers/r{i}.json": ANSWER_TEXT for i in range(9)}
    expect(
        post_bundle(api, envelope(key_id, runs), files=files), 413, "payload_too_large"
    )


# -- GET /submission/bundles/{sha256} ------------------------------------------------


@pytest.fixture
def submitted(api):
    key_id = register(api)
    response = post_bundle(api, envelope(key_id, [run()]))
    expect(response, 202)
    return response.body["sha256"], key_id


def test_get_bundle_200_owner(api, submitted):
    digest, key_id = submitted
    response = get_bundle(api, digest)
    expect(response, 200)
    assert response.body == {
        "sha256": digest,
        "key_id": key_id,
        "status": "pending",
        "verdict_agreement": None,
        "evidence": [],
    }


@pytest.mark.parametrize("token", ["tok-maint-0003"])
def test_get_bundle_200_maintainer_reads_all(api, submitted, token):
    digest, _ = submitted
    expect(get_bundle(api, digest, token=token), 200)


def test_get_bundle_200_complete_and_rejected(api, submitted):
    digest, key_id = submitted
    items = api.storage.bundles.items
    items[digest] = BundleRecord(
        digest, key_id, ALICE, "complete", "t", True, ("https://agentpaddock.ai/e/1",)
    )
    response = get_bundle(api, digest)
    assert response.body["verdict_agreement"] is True
    assert response.body["evidence"] == ["https://agentpaddock.ai/e/1"]
    assert "error" not in response.body
    items[digest] = BundleRecord(
        digest, key_id, ALICE, "rejected", "t", None, (), "signature_revoked"
    )
    response = get_bundle(api, digest)
    assert response.body["status"] == "rejected"
    assert response.body["verdict_agreement"] is None
    assert response.body["error"] == "signature_revoked"


@pytest.mark.parametrize(
    "digest", ["abc", "G" * 64, "A" * 64, "0" * 65, "0" * 64 + "\n", ""]
)
def test_get_bundle_400_malformed_digest(api, digest):
    expect(get_bundle(api, digest), 400, "malformed_digest")


def test_get_bundle_403_not_owned(api, submitted):
    digest, _ = submitted
    expect(get_bundle(api, digest, token="tok-bob-0002"), 403, "bundle_not_owned")


def test_get_bundle_404_unknown(api):
    expect(get_bundle(api, "f" * 64), 404, "unknown_bundle")


def test_get_bundle_401(api, submitted):
    digest, _ = submitted
    expect(get_bundle(api, digest, token="bad"), 401, "unauthenticated")


# -- routing and the Lambda event shape ------------------------------------------


@pytest.mark.parametrize(
    "method, path, status, code",
    [
        ("GET", "/submission/keys", 405, "method_not_allowed"),
        ("DELETE", "/submission/bundles/" + "0" * 64, 405, "method_not_allowed"),
        ("POST", "/submission", 404, "not_found"),
        ("GET", "/submission/bundles/a/b", 404, "not_found"),
    ],
)
def test_routes_outside_the_contract(api, method, path, status, code):
    expect(api.handle(Request(method, path, auth())), status, code)


def test_lambda_event_round_trip_with_base64_multipart(api):
    key_id = register(api)
    body, ctype = multipart(
        [
            ("bundle", "bundle.json", json.dumps(envelope(key_id, [run()])).encode()),
            ("answers", "answers/run-001.json", ANSWER_TEXT),
        ]
    )
    event = {
        "rawPath": "/submission/bundles",
        "requestContext": {"http": {"method": "POST", "path": "/submission/bundles"}},
        "headers": {"Authorization": "Bearer tok-alice-0001", "Content-Type": ctype},
        "body": base64.b64encode(body).decode(),
        "isBase64Encoded": True,
    }
    result = handle_event(api, event, "req-1")
    assert result["statusCode"] == 202
    assert result["headers"]["content-type"] == "application/json"
    assert result["headers"]["cache-control"] == "no-store"
    assert json.loads(result["body"])["status"] == "pending"


def test_lambda_event_with_undecodable_body_is_400(api):
    event = {
        "rawPath": "/submission/bundles",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {},
        "body": "***",
        "isBase64Encoded": True,
    }
    assert handle_event(api, event)["statusCode"] == 400


def test_maintainer_parameter_parsing():
    assert maintainers_from(" a-one, B-two ,,") == frozenset({"a-one", "B-two"})
    assert maintainers_from("") == frozenset()


def test_unhandled_storage_failure_is_a_bare_500(api, caplog):
    key_id = register(api)

    def boom(*_args):
        raise RuntimeError("tok-alice-0001 " + ANSWER_TEXT.decode())

    api.storage.blobs.put_bundle = boom
    with caplog.at_level(logging.INFO, logger="paddock_api"):
        response = post_bundle(api, envelope(key_id, [run()]))
    expect(response, 500, "internal_error")
    assert (
        "tok-alice-0001" not in caplog.text
        and "SECRET-ANSWER-MARKER" not in caplog.text
    )


# -- no token or answer text in logs or responses ------------------------------------


def test_no_token_or_answer_text_in_any_log_or_response(api, caplog):
    with caplog.at_level(logging.DEBUG):
        responses = []
        key_id = register(api)
        env = envelope(key_id, [run()])
        responses.append(post_bundle(api, env))
        responses.append(post_bundle(api, env))  # 409
        responses.append(post_bundle(api, env, token="tok-bob-0002"))  # 403
        responses.append(
            post_bundle(api, env, files={"answers/run-001.json": ANSWER_TEXT + b"!"})
        )  # 422
        responses.append(
            post_bundle(api, env, extra=[("answers", "x.json", ANSWER_TEXT)])
        )
        responses.append(get_bundle(api, responses[0].body["sha256"]))
        responses.append(
            get_bundle(api, responses[0].body["sha256"], token="tok-bob-0002")
        )
        responses.append(get_bundle(api, "0" * 64, token=DOWN))
        responses.append(api.handle(Request("GET", "/x", auth("tok-secretish-9999"))))
    text = caplog.text + "".join(json.dumps(r.body) for r in responses)
    for token in [*TOKENS, DOWN]:
        assert token not in text
    assert "SECRET-ANSWER-MARKER" not in text
    request_lines = [
        r for r in caplog.records if '"event": "request"' in r.getMessage()
    ]
    assert len(request_lines) >= len(responses)
    for record in request_lines:
        logged = json.loads(record.getMessage())
        assert set(logged) == {
            "event",
            "request_id",
            "route",
            "status",
            "error",
            "account",
        }


# -- AWS store implementations against a fake boto3 client ---------------------------


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamo:
    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    def get_item(self, TableName, Key, ConsistentRead):
        self.calls.append(
            ("get_item", {"TableName": TableName, "ConsistentRead": ConsistentRead})
        )
        ((name, value),) = Key.items()
        item = self.tables.get(TableName, {}).get(value["S"])
        return {"Item": item} if item else {}

    def put_item(self, TableName, Item, ConditionExpression):
        self.calls.append(
            ("put_item", {"TableName": TableName, "Condition": ConditionExpression})
        )
        name = re.fullmatch(
            r"attribute_not_exists\((\w+)\)", ConditionExpression
        ).group(1)
        table = self.tables.setdefault(TableName, {})
        if Item[name]["S"] in table:
            raise ConditionalCheckFailed()
        table[Item[name]["S"]] = Item


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put_object(self, **kwargs):
        digest = base64.b64encode(hashlib.sha256(kwargs["Body"]).digest()).decode()
        assert kwargs["ChecksumSHA256"] == digest
        self.objects[kwargs["Key"]] = kwargs


def test_dynamo_key_store_round_trip_and_conditional_conflict():
    client = FakeDynamo()
    store = DynamoKeyStore(client, "keys")
    record = KeyRecord("ed25519-1", ALICE, "Ed25519", "AAAA", "t")
    assert store.put_if_absent(record) is None
    assert store.get("ed25519-1") == record
    assert (
        store.put_if_absent(KeyRecord("ed25519-1", BOB, "Ed25519", "BBBB", "t2"))
        == record
    )
    assert store.get("missing") is None
    assert all(call[1].get("ConsistentRead", True) for call in client.calls)


def test_dynamo_bundle_index_round_trip_and_conflict():
    client = FakeDynamo()
    index = DynamoBundleIndex(client, "bundles")
    pending = BundleRecord("a" * 64, "k", ALICE, "pending", "t")
    assert index.put_if_absent(pending) is None
    assert index.get("a" * 64) == pending
    assert (
        index.put_if_absent(BundleRecord("a" * 64, "k", ALICE, "complete", "t2"))
        == pending
    )
    done = BundleRecord(
        "b" * 64, "k", ALICE, "rejected", "t", False, ("ref",), "bad_pin"
    )
    index.put_if_absent(done)
    assert index.get("b" * 64) == done


def test_dynamo_errors_other_than_conflict_propagate():
    class Broken(FakeDynamo):
        def put_item(self, **kwargs):
            raise RuntimeError("throttled")

    with pytest.raises(RuntimeError):
        DynamoKeyStore(Broken(), "keys").put_if_absent(
            KeyRecord("k", ALICE, "Ed25519", "A", "t")
        )


def test_s3_blobs_write_answers_then_envelope_with_checksums():
    client = FakeS3()
    S3BundleBlobs(client, "bucket").put_bundle(
        "c" * 64, b"{}", {"answers/a.json": b"A"}
    )
    keys = list(client.objects)
    assert keys == [
        f"bundles/{'c' * 64}/answers/answers/a.json",
        f"bundles/{'c' * 64}/bundle.json",
    ]
    assert {o["Bucket"] for o in client.objects.values()} == {"bucket"}


def test_full_flow_on_aws_store_classes():
    from paddock_api.storage import aws_storage

    api = SubmissionApi(
        storage=aws_storage(FakeDynamo(), FakeS3(), "keys", "bundles", "bucket"),
        resolver=FakeResolver(),
    )
    key_id = register(api)
    response = post_bundle(api, envelope(key_id, [run()]))
    expect(response, 202)
    expect(get_bundle(api, response.body["sha256"]), 200)
    expect(post_bundle(api, envelope(key_id, [run()])), 409, "duplicate_bundle")


# -- the deploy template -------------------------------------------------------------

#: CloudFormation types that accept no tags; every other resource must carry the
#: cost-allocation tag.
UNTAGGABLE = {
    "AWS::ApiGatewayV2::ApiMapping",
    "AWS::S3::BucketPolicy",
}


def _template():
    yaml = pytest.importorskip("yaml")

    class Loader(yaml.SafeLoader):
        pass

    def any_tag(loader, suffix, node):
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            value = loader.construct_mapping(node, deep=True)
        return {f"!{suffix}": value}

    Loader.add_multi_constructor("!", any_tag)
    return yaml.load((API_DIR / "template.yaml").read_text(), Loader=Loader)


COST_TAG = "paddock-cost-center"
COST_VALUE = {"!Ref": "CostTagValue"}


def test_template_tags_every_taggable_resource_with_the_cost_tag():
    resources = _template()["Resources"]
    assert {r["Type"] for r in resources.values()} >= {
        "AWS::Serverless::Function",
        "AWS::Serverless::HttpApi",
        "AWS::S3::Bucket",
        "AWS::DynamoDB::Table",
        "AWS::Budgets::Budget",
    }
    for name, resource in resources.items():
        if resource["Type"] in UNTAGGABLE:
            continue
        properties = resource.get("Properties", {})
        tags = properties.get("Tags") or properties.get("ResourceTags")
        if isinstance(tags, dict):  # SAM and API Gateway v2 take a map
            assert tags.get(COST_TAG) == COST_VALUE, name
        else:
            assert {"Key": COST_TAG, "Value": COST_VALUE} in (tags or []), name


def test_template_budget_is_ten_dollars_scoped_to_the_cost_tag():
    budget = _template()["Resources"]["CostBudget"]["Properties"]["Budget"]
    assert budget["BudgetLimit"] == {"Amount": 10, "Unit": "USD"}
    assert budget["TimeUnit"] == "MONTHLY"
    assert budget["CostFilters"] == {
        "TagKeyValue": [{"!Join": ["", [f"user:{COST_TAG}$", COST_VALUE]]}]
    }


def test_template_parameters_and_runtime():
    template = _template()
    parameters = template["Parameters"]
    assert parameters["Region"]["Default"] == "us-east-2"
    assert parameters["MaintainerAccounts"].get("Default", "") == ""
    assert "Default" not in parameters["BudgetAlertEmail"]
    function = template["Resources"]["SubmissionFunction"]["Properties"]
    assert function["Runtime"] == "python3.12"
    assert function["Handler"] == "paddock_api.handler.lambda_handler"
    text = (API_DIR / "template.yaml").read_text()
    assert "us-east-1" not in text and "Edge" not in text and "Replica" not in text


def test_template_iam_is_least_privilege():
    role = _template()["Resources"]["SubmissionRole"]["Properties"]
    actions = set()
    for policy in role["Policies"]:
        for statement in policy["PolicyDocument"]["Statement"]:
            assert statement["Resource"] != "*" and "*" not in str(statement["Action"])
            actions.update(
                statement["Action"]
                if isinstance(statement["Action"], list)
                else [statement["Action"]]
            )
    assert actions == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "s3:PutObject",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    assert "ManagedPolicyArns" not in role
