import json

VERSION = 2


def load(root):
    files = {}
    for path in sorted((root / "files").glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if payload.get("version") == VERSION:
            files[payload["path_key"]] = payload["checkpoint"]
    try:
        identities = json.loads((root / "identities.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        identities = {}
    return files, identities
