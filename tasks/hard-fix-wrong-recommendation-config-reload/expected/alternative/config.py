import json
from pathlib import Path


class ConfigError(Exception):
    """The overrides file exists but cannot be used."""


DEFAULTS = {"merge_enabled": True, "max_workers": 4, "region": "us-east"}


def load_overrides(path):
    """Return the operator overrides in the JSON file at path.

    A missing file means no overrides and returns {}. A file that exists must
    hold a JSON object whose keys are names from DEFAULTS and whose values have
    exactly the type of the default; anything else raises ConfigError.
    """
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    problems = []
    if isinstance(data, dict):
        problems = [
            key
            for key, value in data.items()
            if key not in DEFAULTS or type(value) is not type(DEFAULTS[key])
        ]
    if not isinstance(data, dict) or problems:
        raise ConfigError(f"unusable overrides: {problems or type(data).__name__}")
    return dict(data)
