import json

VERSION = 2


def load(root):
    files = {}
    for path in sorted((root / "files").glob("*.json")):
        payload = json.loads(path.read_text())
        if payload.get("version") == VERSION:
            files[payload["path_key"]] = payload["checkpoint"]
    try:
        identities = json.loads((root / "identities.json").read_text())
    except FileNotFoundError:
        identities = {}
    return files, identities
