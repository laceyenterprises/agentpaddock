import json


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
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    for key, value in data.items():
        if type(value) is not type(DEFAULTS[key]):
            raise ConfigError(f"{key} must be {type(DEFAULTS[key]).__name__}")
    return data
