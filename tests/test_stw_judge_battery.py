"""The STW-05 steward-judge battery parses and keeps its declared shape.

The battery is loaded by the Agent OS judge-eval runner, not by the gym
matrix; this checks only the file itself.
"""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path

BATTERY = Path(__file__).resolve().parents[1] / "batteries" / "stw-judge-v1.toml"
KEYS = {
    "id",
    "kind",
    "corpus_root",
    "corpora",
    "lanes",
    "repetitions",
    "seed",
    "judge",
    "budget",
}


@lru_cache(maxsize=1)
def _battery():
    return tomllib.loads(BATTERY.read_text(encoding="utf-8"))


def test_battery_parses_with_exact_keys():
    battery = _battery()
    assert set(battery) == KEYS
    assert battery["id"] == BATTERY.stem
    assert battery["kind"] == "steward-judge-eval"
    assert sorted(battery["corpora"]) == ["benign", "escapes", "injection"]
    assert battery["lanes"] == ["l1", "judge"]
    assert battery["repetitions"] >= 1
    assert type(battery["seed"]) is int


def test_corpus_root_is_relative():
    root = Path(_battery()["corpus_root"])
    assert not root.is_absolute() and ".." not in root.parts


def test_judge_and_budget_sections():
    battery = _battery()
    assert set(battery["judge"]) == {"models", "timeout_seconds"}
    models = battery["judge"]["models"]
    assert models and len(set(models)) == len(models)
    assert battery["judge"]["timeout_seconds"] > 0
    assert set(battery["budget"]) == {"max_in_flight", "max_total_tokens", "window"}
    assert type(battery["budget"]["max_in_flight"]) is int
    assert battery["budget"]["max_in_flight"] > 0
    assert re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]-(?:[01][0-9]|2[0-3]):[0-5][0-9]",
        battery["budget"]["window"],
    )
    # A real run needs an operator-set budget; the checked-in value is zero.
    assert battery["budget"]["max_total_tokens"] == 0
