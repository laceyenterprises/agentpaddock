"""Dedicated endpoint HTTP boundary. Credentials are resolved in memory only."""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class EndpointError(RuntimeError):
    pass


def secret(secrets, ref):
    """Resolve ``ref`` through the host's secrets provider (:mod:`gym.host_services`)."""
    from gym.host_services import SecretUnavailable

    try:
        return secrets.resolve(ref, purpose="gym dedicated endpoint lifecycle")
    except SecretUnavailable as exc:
        raise EndpointError(str(exc)) from None


class API:
    def __init__(self, secrets, settings):
        self.secrets = secrets
        self.settings = settings
        self.tokens = {}

    def request(self, service, method, path, body=None, timeout=30):
        base = (
            "https://api.fireworks.ai" if service == "fireworks" else self.settings["litellm_url"]
        )
        parsed = urlparse(base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise EndpointError("API URL must not contain credentials or query parameters")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise EndpointError("remote API requires HTTPS")
        if service not in self.tokens:
            self.tokens[service] = secret(self.secrets, self.settings[f"{service}_ref"])
        req = Request(
            base.rstrip("/") + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + self.tokens[service],
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code == 404 and (method in {"GET", "DELETE"} or path == "/model/delete"):
                return {"state": "DELETED"}
            # Provider response bodies can echo credentials. Never retain them.
            raise EndpointError(f"{service} {method} failed: HTTP {exc.code}") from None
        except (URLError, TimeoutError, ValueError):
            raise EndpointError(f"{service} {method} failed") from None

    @property
    def parent(self):
        account = self.settings["account"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", account):
            raise EndpointError("invalid Fireworks account")
        return f"/v1/accounts/{account}"

    def shapes(self, model):
        result = self.request(
            "fireworks",
            "POST",
            self.parent + "/deploymentShapeVersions:match",
            {
                "createDeploymentRequest": {
                    "parent": self.parent.removeprefix("/v1/"),
                    "deployment": {"baseModel": model},
                }
            },
        )
        if result.get("nextPageToken"):
            raise EndpointError(
                "shape match pagination unsupported; refusing incomplete price comparison"
            )
        return result.get("deploymentShapeVersions", [])

    def create(self, lease, model, shape):
        return self.request(
            "fireworks",
            "POST",
            self.parent + "/deployments?" + urlencode({"deploymentId": lease["id"]}),
            {
                "baseModel": model,
                "deploymentShape": shape,
                "minReplicaCount": 0,
                "maxReplicaCount": 1,
                "maxWithRevocableReplicaCount": 0,
                "autoscalingPolicy": {"scaleToZeroWindow": "1200s"},
                "expireTime": lease["provider_expire_time"],
                "annotations": {"agent-os-gym": "true"},
            },
        )

    def get(self, name):
        return self.request("fireworks", "GET", "/v1/" + name)

    def delete(self, name):
        return self.request("fireworks", "DELETE", "/v1/" + name)

    def deployments(self):
        rows, token = [], ""
        while True:
            result = self.request(
                "fireworks",
                "GET",
                self.parent + "/deployments?" + urlencode({"pageSize": 200, "pageToken": token}),
            )
            rows.extend(result.get("deployments", []))
            next_token = result.get("nextPageToken", "")
            if not next_token:
                return rows
            if next_token == token:
                raise EndpointError("deployment pagination stalled")
            token = next_token

    def warm(self, lease):
        return self.request(
            "fireworks",
            "POST",
            "/inference/v1/chat/completions",
            {
                "model": lease["inference_model"],
                "messages": [{"role": "user", "content": "Reply OK"}],
                "max_tokens": 4,
            },
            timeout=lease["route_timeout_seconds"],
        )

    def route_up(self, lease):
        return self.request(
            "litellm",
            "POST",
            "/model/new",
            {
                "model_name": lease["route"],
                "model_info": {"id": lease["route_id"]},
                "litellm_params": {
                    "model": "fireworks_ai/" + lease["inference_model"],
                    "timeout": lease["route_timeout_seconds"],
                    "stream_timeout": lease["route_timeout_seconds"],
                    "num_retries": 0,
                },
            },
        )

    def route_down(self, lease):
        return self.request("litellm", "POST", "/model/delete", {"id": lease["route_id"]})
