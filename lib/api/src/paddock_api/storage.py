"""Storage behind an interface: signing keys, the bundle index, bundle objects.

Three small protocols keep the request logic independent of AWS:

- :class:`KeyStore` holds registered public keys (DynamoDB in production);
- :class:`BundleIndex` holds one status record per bundle (DynamoDB);
- :class:`BundleBlobs` holds the envelope and answer bytes (S3).

Both ``put_if_absent`` methods are conditional writes: when the item already
exists they return the stored record and write nothing, which is how the API
answers ``409`` without a race. Bundle objects are written before the index
record, so an index record never names objects that are missing. The
``Memory*`` classes are the in-memory fakes the tests use.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

STATUSES = frozenset({"pending", "re-grading", "complete", "rejected"})


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    github_account: str
    algorithm: str
    public_key: str  # standard padded Base64 of the 32 raw bytes
    created_at: str


@dataclass(frozen=True)
class BundleRecord:
    sha256: str
    key_id: str
    github_account: str
    status: str
    created_at: str
    verdict_agreement: bool | None = None
    evidence: tuple[str, ...] = ()
    error: str | None = None


class KeyStore(Protocol):
    def get(self, key_id: str) -> KeyRecord | None: ...

    def put_if_absent(self, record: KeyRecord) -> KeyRecord | None:
        """Store ``record``; on an existing ``key_id`` return that record instead."""


class BundleIndex(Protocol):
    def get(self, sha256: str) -> BundleRecord | None: ...

    def put_if_absent(self, record: BundleRecord) -> BundleRecord | None:
        """Store ``record``; on an existing ``sha256`` return that record instead."""


class BundleBlobs(Protocol):
    def put_bundle(
        self, sha256: str, envelope: bytes, answers: dict[str, bytes]
    ) -> None:
        """Write the canonical envelope and every answer file under the digest."""


@dataclass(frozen=True)
class Storage:
    keys: KeyStore
    bundles: BundleIndex
    blobs: BundleBlobs


def envelope_key(sha256: str) -> str:
    """Object key of the canonical envelope: ``bundles/<sha256>/bundle.json``."""
    return f"bundles/{sha256}/bundle.json"


def object_keys(sha256: str, answer_paths: list[str]) -> dict[str, str]:
    """Answer paths to object keys: ``bundles/<sha256>/answers/<path>``.

    Only answer paths appear here. The envelope key comes from
    ``envelope_key``, so an answer file named ``bundle.json`` cannot take the
    envelope's slot.
    """
    prefix = f"bundles/{sha256}/answers/"
    return {path: prefix + path for path in answer_paths}


# -- In-memory fakes ---------------------------------------------------------


@dataclass
class MemoryKeyStore:
    items: dict[str, KeyRecord] = field(default_factory=dict)

    def get(self, key_id: str) -> KeyRecord | None:
        return self.items.get(key_id)

    def put_if_absent(self, record: KeyRecord) -> KeyRecord | None:
        existing = self.items.get(record.key_id)
        if existing is not None:
            return existing
        self.items[record.key_id] = record
        return None


@dataclass
class MemoryBundleIndex:
    items: dict[str, BundleRecord] = field(default_factory=dict)

    def get(self, sha256: str) -> BundleRecord | None:
        return self.items.get(sha256)

    def put_if_absent(self, record: BundleRecord) -> BundleRecord | None:
        existing = self.items.get(record.sha256)
        if existing is not None:
            return existing
        self.items[record.sha256] = record
        return None


@dataclass
class MemoryBundleBlobs:
    objects: dict[str, bytes] = field(default_factory=dict)

    def put_bundle(
        self, sha256: str, envelope: bytes, answers: dict[str, bytes]
    ) -> None:
        names = object_keys(sha256, list(answers))
        self.objects[envelope_key(sha256)] = envelope
        for path, data in answers.items():
            self.objects[names[path]] = data


def memory_storage() -> Storage:
    return Storage(MemoryKeyStore(), MemoryBundleIndex(), MemoryBundleBlobs())


# -- AWS implementations -----------------------------------------------------


def _conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _s(item: dict[str, Any], name: str) -> str:
    return item[name]["S"]


class DynamoKeyStore:
    """Keys table: partition key ``key_id`` (string)."""

    def __init__(self, client: Any, table: str) -> None:
        self._client = client
        self._table = table

    def get(self, key_id: str) -> KeyRecord | None:
        item = self._client.get_item(
            TableName=self._table, Key={"key_id": {"S": key_id}}, ConsistentRead=True
        ).get("Item")
        if not item:
            return None
        return KeyRecord(
            key_id=_s(item, "key_id"),
            github_account=_s(item, "github_account"),
            algorithm=_s(item, "algorithm"),
            public_key=_s(item, "public_key"),
            created_at=_s(item, "created_at"),
        )

    def put_if_absent(self, record: KeyRecord) -> KeyRecord | None:
        try:
            self._client.put_item(
                TableName=self._table,
                Item={
                    "key_id": {"S": record.key_id},
                    "github_account": {"S": record.github_account},
                    "algorithm": {"S": record.algorithm},
                    "public_key": {"S": record.public_key},
                    "created_at": {"S": record.created_at},
                },
                ConditionExpression="attribute_not_exists(key_id)",
            )
        except Exception as exc:
            if not _conditional_failure(exc):
                raise
            return self.get(record.key_id)
        return None


class DynamoBundleIndex:
    """Bundle index table: partition key ``sha256`` (string)."""

    def __init__(self, client: Any, table: str) -> None:
        self._client = client
        self._table = table

    def get(self, sha256: str) -> BundleRecord | None:
        item = self._client.get_item(
            TableName=self._table, Key={"sha256": {"S": sha256}}, ConsistentRead=True
        ).get("Item")
        if not item:
            return None
        agreement = item.get("verdict_agreement", {"NULL": True})
        status = _s(item, "status")
        if status not in STATUSES:
            raise ValueError("bundle index holds an unknown status")
        return BundleRecord(
            sha256=_s(item, "sha256"),
            key_id=_s(item, "key_id"),
            github_account=_s(item, "github_account"),
            status=status,
            created_at=_s(item, "created_at"),
            verdict_agreement=agreement["BOOL"] if "BOOL" in agreement else None,
            evidence=tuple(
                entry["S"] for entry in item.get("evidence", {"L": []})["L"]
            ),
            error=item.get("error", {}).get("S"),
        )

    def put_if_absent(self, record: BundleRecord) -> BundleRecord | None:
        item: dict[str, Any] = {
            "sha256": {"S": record.sha256},
            "key_id": {"S": record.key_id},
            "github_account": {"S": record.github_account},
            "status": {"S": record.status},
            "created_at": {"S": record.created_at},
            "verdict_agreement": (
                {"NULL": True}
                if record.verdict_agreement is None
                else {"BOOL": record.verdict_agreement}
            ),
            "evidence": {"L": [{"S": entry} for entry in record.evidence]},
        }
        if record.error is not None:
            item["error"] = {"S": record.error}
        try:
            self._client.put_item(
                TableName=self._table,
                Item=item,
                ConditionExpression="attribute_not_exists(sha256)",
            )
        except Exception as exc:
            if not _conditional_failure(exc):
                raise
            return self.get(record.sha256)
        return None


class S3BundleBlobs:
    """Bundle bucket; S3 checks each object against its SHA-256 on write."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        checksum = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ChecksumSHA256=checksum,
        )

    def put_bundle(
        self, sha256: str, envelope: bytes, answers: dict[str, bytes]
    ) -> None:
        names = object_keys(sha256, list(answers))
        for path, data in answers.items():
            self._put(names[path], data, "application/octet-stream")
        self._put(envelope_key(sha256), envelope, "application/json")


def aws_storage(
    dynamodb: Any, s3: Any, keys_table: str, bundles_table: str, bucket: str
) -> Storage:
    return Storage(
        DynamoKeyStore(dynamodb, keys_table),
        DynamoBundleIndex(dynamodb, bundles_table),
        S3BundleBlobs(s3, bucket),
    )
