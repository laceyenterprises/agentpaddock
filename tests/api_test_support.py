"""Shared synthetic API clients and signing helpers; no network or credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "lib" / "api"
sys.path.insert(0, str(API_DIR / "src"))

from paddock_api import ed25519  # noqa: E402
from paddock_api.app import ERRORS, Limits, Request, SubmissionApi  # noqa: E402
from paddock_api.auth import AuthUnavailable  # noqa: E402
from paddock_api.storage import memory_storage  # noqa: E402

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


def make_api() -> SubmissionApi:
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


def expect(response, status, code=None):
    assert response.status == status, response.body
    if code is not None:
        assert response.body["error"] == code
        assert ERRORS[code][0] == status
        assert response.body["message"] == ERRORS[code][1]


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put_object(self, **kwargs):
        digest = base64.b64encode(hashlib.sha256(kwargs["Body"]).digest()).decode()
        assert kwargs["ChecksumSHA256"] == digest
        self.objects[kwargs["Key"]] = kwargs


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
