"""Submission operations and the signed public live ticker.

``POST /submission/keys``, ``POST /submission/bundles`` and
``GET /submission/bundles/{sha256}``, with exactly the contract's success and
error rows. Every error body is ``{"error": "<code>", "message": "<text>"}``
with a fixed message per code: no response or log line ever repeats request
content, so neither a token nor answer text can leak through one. Codes are
listed in :data:`ERRORS` and published in ``lib/api/README.md``.

Checks run in this order, so one request always gets the same answer: route,
size, authentication, request shape (``400``), key lookup (``404``), key
ownership (``403``), signature, answer digests and pins (``422``), duplicate
bundle (``409``).
"""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from . import ed25519, live, multipart
from .auth import AccountResolver, AuthUnavailable, bearer_token
from .canonical import StrictJSONError, canonical_bytes, strict_loads
from .storage import BundleRecord, KeyRecord, Storage

LOG = logging.getLogger("paddock_api")

#: code -> (HTTP status, fixed public message)
ERRORS: dict[str, tuple[int, str]] = {
    "malformed_live": (400, "The live envelope or document is malformed."),
    "maintainer_required": (403, "A registered maintainer signing key is required."),
    "live_unavailable": (404, "No live document has been published."),
    "invalid_live_signature": (
        422,
        "The signature does not verify against the live document.",
    ),
    "invalid_json": (400, "The request body is not valid JSON."),
    "malformed_key": (
        400,
        "The public key is not a Base64 32-byte Ed25519 public key.",
    ),
    "unsupported_algorithm": (400, "Only the Ed25519 algorithm is supported."),
    "malformed_request": (
        400,
        "The request is not multipart form data with one bundle part.",
    ),
    "malformed_manifest": (400, "The bundle envelope or manifest is malformed."),
    "duplicate_run_id": (400, "Run ids must be unique within a manifest."),
    "duplicate_answer_file": (
        400,
        "Answer file paths must be unique within a manifest.",
    ),
    "unsafe_path": (
        400,
        "An answer file path is absolute, traverses parents or is not portable.",
    ),
    "missing_files": (400, "A manifest answer file was not uploaded."),
    "extra_files": (
        400,
        "An uploaded file or form field is not listed in the manifest.",
    ),
    "malformed_digest": (
        400,
        "The bundle digest must be 64 lowercase hexadecimal characters.",
    ),
    "unauthenticated": (
        401,
        "A valid GitHub token is required as a Bearer credential.",
    ),
    "key_not_owned": (403, "The signing key is registered to another account."),
    "bundle_not_owned": (403, "The bundle belongs to another account."),
    "not_found": (404, "No such operation."),
    "unknown_key": (404, "The signing key id is not registered."),
    "unknown_bundle": (404, "No bundle has this digest."),
    "method_not_allowed": (405, "The operation does not accept this method."),
    "key_already_registered": (409, "This public key is already registered."),
    "duplicate_bundle": (409, "This bundle was already submitted."),
    "payload_too_large": (
        413,
        "The request exceeds the upload size or file count limit.",
    ),
    "invalid_signature": (
        422,
        "The signature does not verify against the canonical manifest.",
    ),
    "answer_digest_mismatch": (
        422,
        "An answer file does not match its manifest digest.",
    ),
    "incompatible_pin": (
        422,
        "A results document, fork commit or verifier pin is invalid.",
    ),
    "internal_error": (500, "The request could not be completed."),
    "auth_unavailable": (503, "GitHub could not confirm the account; retry later."),
}

ALGORITHM = "Ed25519"
RUN_FIELDS = frozenset(
    {"run_id", "answer_file", "sha256", "results_document", "fork_commit", "verifier"}
)
HEX64 = re.compile(r"[0-9a-f]{64}")
COMMIT40 = re.compile(r"[0-9a-f]{40}")
# Results discovery reads only top-level results/<date>-<id>.json(.gz)
# (RESULT_NAME in scripts/leaderboard.py).
RESULTS_DOCUMENT = re.compile(
    r"results/[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9][a-z0-9.-]*\.json(?:\.gz)?"
)
TASK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
# Portable path segments: no leading dot (so no "." or ".."), no separators,
# drive letters, control characters or shell-flag lookalikes.
PATH_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
MAX_PATH = 512
MAX_SEGMENTS = 16
MAX_ID = 256


@dataclass(frozen=True)
class Limits:
    max_body_bytes: int = 4 * 1024 * 1024
    max_files: int = 256


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: dict[str, str]  # lower-case names
    body: bytes = b""
    request_id: str = ""


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class ApiError(Exception):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.extra = extra

    def response(self) -> Response:
        status, message = ERRORS[self.code]
        return Response(status, {"error": self.code, "message": message, **self.extra})


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64_exact(text: Any, length: int) -> bytes | None:
    """Raw bytes of standard padded Base64 text of exactly ``length`` bytes."""
    if not isinstance(text, str) or not text.isascii():
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != length or base64.b64encode(raw).decode("ascii") != text:
        return None
    return raw


def _same_account(left: str, right: str) -> bool:
    # GitHub logins are case-insensitive.
    return left.casefold() == right.casefold()


def _short_text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID and value.isprintable()


def safe_path(path: Any) -> bool:
    if not isinstance(path, str) or not 0 < len(path) <= MAX_PATH:
        return False
    segments = path.split("/")
    return len(segments) <= MAX_SEGMENTS and all(
        PATH_SEGMENT.fullmatch(s) for s in segments
    )


@dataclass(frozen=True)
class _Run:
    run_id: str
    answer_file: str
    sha256: str
    results_document: str
    fork_commit: str
    task: str
    task_version: int


def _parse_envelope(data: bytes) -> tuple[dict[str, Any], str, list[_Run], Any]:
    """(envelope, key_id, runs, signature) or ``400 malformed_manifest``."""
    try:
        envelope = strict_loads(data)
    except StrictJSONError:
        raise ApiError("malformed_manifest") from None
    if not isinstance(envelope, dict) or set(envelope) != {"manifest", "signature"}:
        raise ApiError("malformed_manifest")
    manifest = envelope["manifest"]
    if not isinstance(manifest, dict) or set(manifest) != {"key_id", "runs"}:
        raise ApiError("malformed_manifest")
    key_id, runs = manifest["key_id"], manifest["runs"]
    if not _short_text(key_id) or not isinstance(runs, list) or not runs:
        raise ApiError("malformed_manifest")
    parsed = []
    for run in runs:
        if not isinstance(run, dict) or set(run) != RUN_FIELDS:
            raise ApiError("malformed_manifest")
        verifier = run["verifier"]
        if not isinstance(verifier, dict) or set(verifier) != {"task", "task_version"}:
            raise ApiError("malformed_manifest")
        version = verifier["task_version"]
        texts = (
            run["run_id"],
            run["results_document"],
            run["fork_commit"],
            verifier["task"],
        )
        if (
            not all(_short_text(value) for value in texts)
            or not isinstance(run["answer_file"], str)
            or not isinstance(run["sha256"], str)
            or not HEX64.fullmatch(run["sha256"])
            or not isinstance(version, int)
            or isinstance(version, bool)
        ):
            raise ApiError("malformed_manifest")
        parsed.append(
            _Run(
                run_id=run["run_id"],
                answer_file=run["answer_file"],
                sha256=run["sha256"],
                results_document=run["results_document"],
                fork_commit=run["fork_commit"],
                task=verifier["task"],
                task_version=version,
            )
        )
    signature = envelope["signature"]
    if not isinstance(signature, str):
        raise ApiError("malformed_manifest")
    return envelope, key_id, parsed, signature


def _check_paths(runs: list[_Run]) -> None:
    run_ids: set[str] = set()
    folded: set[str] = set()
    for run in runs:
        if run.run_id in run_ids:
            raise ApiError("duplicate_run_id")
        run_ids.add(run.run_id)
    for run in runs:
        if not safe_path(run.answer_file):
            raise ApiError("unsafe_path")
        # Case-folded: two names one case-insensitive file system merges.
        key = run.answer_file.casefold()
        if key in folded:
            raise ApiError("duplicate_answer_file")
        folded.add(key)
    for path in folded:
        segments = path.split("/")
        if any("/".join(segments[:i]) in folded for i in range(1, len(segments))):
            # One answer file would have to be a directory of another.
            raise ApiError("unsafe_path")


def _check_pins(runs: Iterable[_Run]) -> None:
    for run in runs:
        if (
            not RESULTS_DOCUMENT.fullmatch(run.results_document)
            or not COMMIT40.fullmatch(run.fork_commit)
            or not TASK_ID.fullmatch(run.task)
            or run.task_version < 1
        ):
            raise ApiError("incompatible_pin")


@dataclass
class SubmissionApi:
    storage: Storage
    resolver: AccountResolver
    maintainers: frozenset[str] = frozenset()
    limits: Limits = field(default_factory=Limits)
    now: Callable[[], str] = _utc_now

    def __post_init__(self) -> None:
        self.maintainers = frozenset(login.casefold() for login in self.maintainers)

    # -- dispatch --------------------------------------------------------------

    def handle(self, request: Request) -> Response:
        account: str | None = None
        route = "unknown"
        try:
            route, operation, argument = self._route(request)
            if len(request.body) > self.limits.max_body_bytes:
                raise ApiError("payload_too_large")
            if route not in {"GET /live", "OPTIONS /live"}:
                account = self._authenticate(request)
            response = operation(account, request, argument)
        except ApiError as exc:
            response = exc.response()
        except Exception as exc:  # never echo internals or request content
            LOG.error(
                json.dumps({"event": "unhandled", "exception": type(exc).__name__})
            )
            response = ApiError("internal_error").response()
        LOG.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": request.request_id,
                    "route": route,
                    "status": response.status,
                    "error": response.body.get("error"),
                    "account": account,
                },
                sort_keys=True,
            )
        )
        return self.response_headers(request, response)

    def response_headers(self, request: Request, response: Response) -> Response:
        if request.path != "/live":
            return response
        headers = {
            "access-control-allow-origin": "https://agentpaddock.ai",
            "cache-control": "no-store",
        }
        if request.method.upper() == "GET" and response.status == 200:
            headers["cache-control"] = "public, max-age=60"
        if request.method.upper() == "OPTIONS":
            headers["access-control-allow-methods"] = "GET, PUT, OPTIONS"
            headers["access-control-allow-headers"] = "authorization, content-type"
        return Response(response.status, response.body, headers)

    def _route(self, request: Request):
        path, method = request.path, request.method.upper()
        if path == "/live":
            operations = {
                "GET": self.read_live,
                "PUT": self.put_live,
                "OPTIONS": self.live_options,
            }
            if method not in operations:
                raise ApiError("method_not_allowed")
            return f"{method} /live", operations[method], None
        if path == "/submission/keys":
            expected, route, operation, argument = (
                "POST",
                "POST /submission/keys",
                self.register_key,
                None,
            )
        elif path == "/submission/bundles":
            expected, route, operation, argument = (
                "POST",
                "POST /submission/bundles",
                self.submit_bundle,
                None,
            )
        elif path.startswith("/submission/bundles/") and path.count("/") == 3:
            expected, route = "GET", "GET /submission/bundles/{sha256}"
            operation, argument = self.read_bundle, path.rsplit("/", 1)[1]
        else:
            raise ApiError("not_found")
        if method != expected:
            raise ApiError("method_not_allowed")
        return route, operation, argument

    def _authenticate(self, request: Request) -> str:
        token = bearer_token(request.headers.get("authorization"))
        if token is None:
            raise ApiError("unauthenticated")
        try:
            account = self.resolver.resolve(token)
        except AuthUnavailable:
            raise ApiError("auth_unavailable") from None
        if account is None:
            raise ApiError("unauthenticated")
        return account

    # -- /live ------------------------------------------------------------------

    def live_options(self, _account, _request, _argument) -> Response:
        return Response(200, {})

    def read_live(self, _account, _request, _argument) -> Response:
        raw = self.storage.blobs.get_live()
        if raw is None:
            raise ApiError("live_unavailable")
        document = strict_loads(raw)
        live.validate_document(document)
        return Response(200, document)

    def put_live(self, account: str, request: Request, _argument) -> Response:
        try:
            envelope = strict_loads(request.body)
            if not isinstance(envelope, dict) or set(envelope) != {
                "document",
                "key_id",
                "signature",
            }:
                raise ValueError("envelope")
            live.validate_document(envelope["document"])
            if not _short_text(envelope["key_id"]):
                raise ValueError("key id")
        except (StrictJSONError, ValueError):
            raise ApiError("malformed_live") from None
        key = self.storage.keys.get(envelope["key_id"])
        if key is None:
            raise ApiError("unknown_key")
        if not _same_account(key.github_account, account):
            raise ApiError("key_not_owned")
        if (
            key.github_account.casefold() not in self.maintainers
            or key.algorithm != ALGORITHM
        ):
            raise ApiError("maintainer_required")
        public_key = _b64_exact(key.public_key, 32)
        signature = _b64_exact(envelope["signature"], 64)
        document_bytes = canonical_bytes(envelope["document"])
        if (
            public_key is None
            or signature is None
            or not ed25519.verify(public_key, document_bytes, signature)
        ):
            raise ApiError("invalid_live_signature")
        self.storage.blobs.put_live(document_bytes)
        return Response(200, envelope["document"])

    # -- POST /submission/keys ---------------------------------------------------

    def register_key(self, account: str, request: Request, _argument: None) -> Response:
        try:
            data = strict_loads(request.body)
        except StrictJSONError:
            raise ApiError("invalid_json") from None
        if not isinstance(data, dict):
            raise ApiError("malformed_key")
        if data.get("algorithm") != ALGORITHM:
            raise ApiError("unsupported_algorithm")
        if set(data) != {"algorithm", "public_key"}:
            raise ApiError("malformed_key")
        raw = _b64_exact(data["public_key"], 32)
        if raw is None or not ed25519.is_valid_public_key(raw):
            raise ApiError("malformed_key")
        record = KeyRecord(
            key_id="ed25519-" + hashlib.sha256(raw).hexdigest()[:32],
            github_account=account,
            algorithm=ALGORITHM,
            public_key=data["public_key"],
            created_at=self.now(),
        )
        existing = self.storage.keys.put_if_absent(record)
        if existing is not None:
            if _same_account(existing.github_account, account):
                raise ApiError(
                    "key_already_registered",
                    key_id=existing.key_id,
                    github_account=existing.github_account,
                    algorithm=existing.algorithm,
                )
            raise ApiError("key_already_registered")
        return Response(
            201,
            {
                "key_id": record.key_id,
                "github_account": account,
                "algorithm": ALGORITHM,
            },
        )

    # -- POST /submission/bundles ------------------------------------------------

    def submit_bundle(
        self, account: str, request: Request, _argument: None
    ) -> Response:
        try:
            parts = multipart.parse(
                request.body,
                request.headers.get("content-type", ""),
                max_parts=self.limits.max_files + 1,
            )
        except multipart.TooManyParts:
            raise ApiError("payload_too_large") from None
        except multipart.MultipartError:
            raise ApiError("malformed_request") from None
        bundles = [part for part in parts if part.name == "bundle"]
        if len(bundles) != 1:
            raise ApiError("malformed_request")
        uploaded: dict[str, bytes] = {}
        for part in parts:
            if part.name == "bundle":
                continue
            if part.name != "answers":
                raise ApiError("extra_files")
            if not part.filename:
                raise ApiError("malformed_request")
            if part.filename in uploaded:
                raise ApiError("duplicate_answer_file")
            uploaded[part.filename] = part.data

        envelope, key_id, runs, signature_text = _parse_envelope(bundles[0].data)
        _check_paths(runs)
        listed = {run.answer_file for run in runs}
        if listed - set(uploaded):
            raise ApiError("missing_files")
        if set(uploaded) - listed:
            raise ApiError("extra_files")

        key = self.storage.keys.get(key_id)
        if key is None:
            raise ApiError("unknown_key")
        if not _same_account(key.github_account, account):
            raise ApiError("key_not_owned")
        public_key = _b64_exact(key.public_key, 32)
        signature = _b64_exact(signature_text, 64)
        message = canonical_bytes(envelope["manifest"])
        if (
            public_key is None
            or signature is None
            or not ed25519.verify(public_key, message, signature)
        ):
            raise ApiError("invalid_signature")
        for run in runs:
            if hashlib.sha256(uploaded[run.answer_file]).hexdigest() != run.sha256:
                raise ApiError("answer_digest_mismatch")
        _check_pins(runs)

        canonical_envelope = canonical_bytes(envelope)
        digest = hashlib.sha256(canonical_envelope).hexdigest()
        existing = self.storage.bundles.get(digest)
        if existing is None:
            # Objects first: an index record never names missing objects.
            self.storage.blobs.put_bundle(digest, canonical_envelope, uploaded)
            existing = self.storage.bundles.put_if_absent(
                BundleRecord(
                    sha256=digest,
                    key_id=key_id,
                    github_account=account,
                    status="pending",
                    created_at=self.now(),
                )
            )
        if existing is not None:
            raise ApiError(
                "duplicate_bundle", sha256=existing.sha256, status=existing.status
            )
        return Response(202, {"sha256": digest, "key_id": key_id, "status": "pending"})

    # -- GET /submission/bundles/{sha256} ------------------------------------------

    def read_bundle(self, account: str, _request: Request, digest: str) -> Response:
        if not HEX64.fullmatch(digest):
            raise ApiError("malformed_digest")
        record = self.storage.bundles.get(digest)
        if record is None:
            raise ApiError("unknown_bundle")
        if (
            not _same_account(record.github_account, account)
            and account.casefold() not in self.maintainers
        ):
            raise ApiError("bundle_not_owned")
        body: dict[str, Any] = {
            "sha256": record.sha256,
            "key_id": record.key_id,
            "status": record.status,
            "verdict_agreement": (
                record.verdict_agreement if record.status == "complete" else None
            ),
            "evidence": list(record.evidence),
        }
        if record.status == "rejected":
            body["error"] = record.error or "rejected"
        return Response(200, body)
