import json
import logging
import uuid

VERSION = 2


def read_cache(path, checkpoint=False):
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("cache must be an object")
        if checkpoint and (
            payload.get("version") != VERSION
            or not isinstance(payload.get("path_key"), str)
            or not isinstance(payload.get("checkpoint"), dict)
        ):
            raise ValueError("invalid checkpoint schema/version")
        return payload
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        path.rename(path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex))
        logging.error("quarantined corrupt cache %s", path.name)
        return None


def load(root):
    files = {}
    for path in sorted((root / "files").glob("*.json")):
        payload = read_cache(path, checkpoint=True)
        if payload is not None:
            files[payload["path_key"]] = payload["checkpoint"]
    return files, read_cache(root / "identities.json") or {}
