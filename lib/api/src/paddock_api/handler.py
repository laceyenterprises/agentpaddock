"""AWS Lambda entry point for an API Gateway HTTP API (payload format 2.0).

Configuration comes from the environment the template sets:

``KEYS_TABLE``, ``BUNDLES_TABLE``, ``BUNDLE_BUCKET``
    storage names.
``MAINTAINER_ACCOUNTS``
    comma-separated GitHub logins that may read every bundle; a deploy
    parameter, never committed.
``MAX_BODY_BYTES``, ``MAX_FILES``
    upload limits.
``EXPECTED_REGION``
    the stack's ``Region`` parameter; the function refuses to serve from any
    other region.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from typing import Any

from .app import ApiError, Limits, Request, Response, SubmissionApi
from .auth import GitHubUserResolver
from .storage import aws_storage

logging.getLogger("paddock_api").setLevel(logging.INFO)

_API: SubmissionApi | None = None

_HEADERS = {
    "content-type": "application/json",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}


def maintainers_from(value: str) -> frozenset[str]:
    return frozenset(login.strip() for login in value.split(",") if login.strip())


def build_api(environ: dict[str, str] | None = None) -> SubmissionApi:
    env = os.environ if environ is None else environ
    expected = env.get("EXPECTED_REGION", "")
    if expected and env.get("AWS_REGION") != expected:
        raise RuntimeError("deployed outside the configured region")
    import boto3  # provided by the Lambda runtime; see lib/api/requirements.txt

    region = env.get("AWS_REGION")
    storage = aws_storage(
        boto3.client("dynamodb", region_name=region),
        boto3.client("s3", region_name=region),
        keys_table=env["KEYS_TABLE"],
        bundles_table=env["BUNDLES_TABLE"],
        bucket=env["BUNDLE_BUCKET"],
    )
    return SubmissionApi(
        storage=storage,
        resolver=GitHubUserResolver(),
        maintainers=maintainers_from(env.get("MAINTAINER_ACCOUNTS", "")),
        limits=Limits(
            max_body_bytes=int(env.get("MAX_BODY_BYTES", Limits.max_body_bytes)),
            max_files=int(env.get("MAX_FILES", Limits.max_files)),
        ),
    )


def request_from_event(event: dict[str, Any], request_id: str = "") -> Request:
    """Translate an HTTP API v2 event; raises ``ApiError`` on an undecodable body."""
    http = (event.get("requestContext") or {}).get("http") or {}
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError("malformed_request") from None
    else:
        raw = body.encode("utf-8")
    return Request(
        method=str(http.get("method", "")),
        path=str(event.get("rawPath") or http.get("path") or ""),
        headers=headers,
        body=raw,
        request_id=request_id,
    )


def to_event_response(response: Response) -> dict[str, Any]:
    return {
        "statusCode": response.status,
        "headers": dict(_HEADERS),
        "body": json.dumps(response.body, sort_keys=True, ensure_ascii=False),
        "isBase64Encoded": False,
    }


def handle_event(
    api: SubmissionApi, event: dict[str, Any], request_id: str = ""
) -> dict[str, Any]:
    try:
        request = request_from_event(event, request_id)
    except ApiError as exc:
        return to_event_response(exc.response())
    return to_event_response(api.handle(request))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _API
    if _API is None:
        _API = build_api()
    return handle_event(_API, event, getattr(context, "aws_request_id", ""))
