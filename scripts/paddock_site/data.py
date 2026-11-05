"""The site's data layer (PDW-03): every number the pages show, as JSON files.

Each file is computed from the committed ``results/`` tree and the local
``tasks/`` only through ``scripts/leaderboard.py``: its validation, the
counted-run rule (:func:`leaderboard.included`), first results, Wilson ranges,
board order, places and ties, prices, difficulty bands and checkpoint
disclosure. Nothing here re-derives one of those; a missing number becomes a
small public helper in ``leaderboard.py`` with its own test.

The trust filter (CONTRIBUTING.md "Trust levels"): standings, race and entry
files count only documents at ``reproduced`` or ``paddock-run``. The
paddock's own bench is ``paddock-run`` unless ``results/meta/trust.json`` says
otherwise; everything else defaults to ``self-reported``
(:func:`leaderboard.document_trust`). ``community.json`` lists every document
and every level with its badge. With no trusted document, every file says
``"state": "awaiting-trust-sign-off"`` and lists nothing: the pages never fall
back to untrusted data.

Races are the local tasks at the version their ``task.toml`` names. The
calendar is the races with at least :data:`leaderboard.DIFFICULTY_MIN_RUNS`
counted runs: the runs it takes to give a race a difficulty band. Standings
place the entries with a first result on every calendar race, so every placed
entry is measured on the same races; the others are listed as ``partial``.
Every entry with a counted result on a calendar race gets an entry file.

Checkpoints come only from record ``checks`` and the race's track card, shown
only as the card discloses (``label``: named with its clear rate; ``count``:
adds to "N more checks"; ``hidden``: omitted). A docs fact check is never
named. A race whose counted runs carry no ``checks`` has null stages, funnel
and retirements.

``scripts/build_site.py`` writes the files under ``site/data/``; nothing under
``site/`` is committed. Every file carries ``schema``, ``generatedAt`` (the
date of the newest results document, so the same ``results/`` builds the same
bytes) and ``basis`` (what was counted).

    data/standings.json         paddock-site-standings/v1
    data/races.json             paddock-site-races/v1
    data/races/<race-id>.json   paddock-site-race/v1
    data/entries/<entry-id>.json  paddock-site-entry/v1
    data/drivers.json           paddock-site-drivers/v1
    data/community.json         paddock-site-community/v1
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import leaderboard as lb

LIVE = "live"
AWAITING = "awaiting-trust-sign-off"
#: The challenge the engine-map ladders compare on.
LADDER_CHALLENGE = "hard-review"

SCHEMAS = {
    "standings": "paddock-site-standings/v1",
    "races": "paddock-site-races/v1",
    "race": "paddock-site-race/v1",
    "entry": "paddock-site-entry/v1",
    "drivers": "paddock-site-drivers/v1",
    "community": "paddock-site-community/v1",
}


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Race:
    """One local task at its current version, and its public track card."""

    id: str
    suite: str
    version: int
    title: str | None
    name: str
    #: The card's checkpoints in lap order, or ``()`` without a card.
    checkpoints: tuple[Mapping[str, Any], ...]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.suite, self.id, self.version)


def race_name(task_id: str, suite: str) -> str:
    """A readable name from a task id when the task has no track card."""
    words = task_id.removeprefix(f"{suite}-").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else task_id


def load_races(tasks_dir: Path) -> dict[tuple[str, str, int], Race]:
    """Local races by ``(suite, task, version)``; only public files are read."""
    races = {}
    for path in sorted(tasks_dir.glob("*/task.toml")):
        task = lb.read_toml(path)
        task_id, suite, version = (
            task.get("id"),
            task.get("suite"),
            task.get("task_version"),
        )
        if not (isinstance(task_id, str) and isinstance(suite, str)):
            continue
        if not isinstance(version, int) or isinstance(version, bool):
            continue
        card_path = path.parent / "track.toml"
        card = lb.read_toml(card_path) if card_path.is_file() else {}
        name = card.get("name")
        title = task.get("title")
        race = Race(
            id=task_id,
            suite=suite,
            version=version,
            title=title if isinstance(title, str) else None,
            name=name if isinstance(name, str) else race_name(task_id, suite),
            checkpoints=tuple(card.get("checkpoints") or ()),
        )
        races[race.key] = race
    return races


def document_id(document: Mapping[str, Any]) -> str:
    """``<date>-<id>``: a results document whatever its storage form."""
    return f"{document['date']}-{document['id']}"


def race_key(record: Mapping[str, Any]) -> tuple[str, str, int]:
    return (record["suite"], record["task"], record["task_version"])


def identity(record: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """``(car, config hash, driver, engine map)``: the identity triple plus the
    harness name, ordered so it sorts like :func:`leaderboard.row_key`."""
    return (
        record["harness"]["name"],
        record["harness"]["config_hash"],
        record["model"]["provider_id"],
        record["model"]["reasoning"] or "default",
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def entry_id(key: tuple[str, str, str, str]) -> str:
    """A stable slug of the identity: car, driver, engine map, config prefix."""
    car, config_hash, driver, engine_map = key
    short = config_hash.removeprefix("sha256:")[:8]
    return _slug(f"{car}-{driver}-{engine_map}-{short}")


def entry_ids(keys: Iterable[tuple[str, str, str, str]]) -> dict[tuple, str]:
    """Slugs for ``keys``; two that collide both gain a digest of the identity."""
    keys = sorted(set(keys))
    slugs = {key: entry_id(key) for key in keys}
    counts = defaultdict(int)
    for slug in slugs.values():
        counts[slug] += 1
    for key, slug in slugs.items():
        if counts[slug] > 1:
            digest = hashlib.sha256("\0".join(key).encode()).hexdigest()[:8]
            slugs[key] = f"{slug}-{digest}"
    return slugs


def _who(key: tuple[str, str, str, str]) -> dict[str, str]:
    car, config_hash, driver, engine_map = key
    return {"driver": driver, "car": car, "map": engine_map, "configHash": config_hash}


def _median(values: Iterable[Any]) -> float | None:
    measured = [value for value in values if value is not None]
    return statistics.median(measured) if measured else None


def _usd(record: Mapping[str, Any]) -> float | None:
    cost = record["cost"]
    return cost["usd"] if cost["source"] in lb.PRICED_SOURCES else None


def _lowest_trust(levels: Iterable[str]) -> str:
    return min(levels, key=lb.TRUST_LEVELS.index)


def _map_order(engine_map: str) -> tuple[int, str]:
    known = engine_map in lb.ENGINE_MAPS
    return (
        lb.ENGINE_MAPS.index(engine_map) if known else len(lb.ENGINE_MAPS),
        engine_map,
    )


# ---------------------------------------------------------------- the model


@dataclass
class SiteData:
    """Everything the files are cut from, computed once per build."""

    results: lb.Results
    races: dict[tuple[str, str, int], Race]
    trust: dict[str, tuple[str, str]]
    trusted: dict[str, Mapping[str, Any]]
    #: Every counted trusted run on a local race, by race.
    runs: dict[tuple[str, str, int], list[Mapping[str, Any]]]
    #: The first counted trusted result per identity on a local race, by race.
    firsts: dict[tuple[str, str, int], list[Mapping[str, Any]]]
    calendar: list[Race]
    record_trust: dict[int, str]
    #: Entry ids for every identity in every document, trusted or not, so an
    #: entry keeps one id on every page.
    ids: dict[tuple, str]

    @property
    def state(self) -> str:
        return LIVE if self.trusted else AWAITING

    @property
    def generated_at(self) -> str | None:
        return max(
            (doc["date"] for doc in self.results.documents.values()), default=None
        )


def model(results: lb.Results, tasks_dir: Path) -> SiteData:
    races = load_races(tasks_dir)
    trust = {
        name: lb.document_trust(name, document, results.trust)
        for name, document in results.documents.items()
    }
    trusted = {
        name: document
        for name, document in results.documents.items()
        if trust[name][0] in lb.TRUSTED_LEVELS
    }
    record_trust = {
        id(record): trust[name][0]
        for name, document in trusted.items()
        for record in document["records"]
    }
    runs: dict[tuple, list] = defaultdict(list)
    for name in sorted(trusted):
        for record in trusted[name]["records"]:
            if lb.included(record) and race_key(record) in races:
                runs[race_key(record)].append(record)
    firsts: dict[tuple, list] = defaultdict(list)
    for record in lb.first_results(trusted):
        if race_key(record) in races:
            firsts[race_key(record)].append(record)
    calendar = [
        races[key]
        for key in sorted(runs, key=lambda key: key[1])
        if len(runs[key]) >= lb.DIFFICULTY_MIN_RUNS
    ]
    ids = entry_ids(identity(record) for record in results.records)
    return SiteData(
        results,
        races,
        trust,
        trusted,
        dict(runs),
        dict(firsts),
        calendar,
        record_trust,
        ids,
    )


def _basis(data: SiteData, counts: str) -> dict[str, Any]:
    return {
        "counts": counts,
        "trust": list(lb.TRUSTED_LEVELS),
        "documents": sorted(document_id(data.trusted[name]) for name in data.trusted),
        "races": "local tasks at the version their task.toml names",
        "calendar": (
            f"races with at least {lb.DIFFICULTY_MIN_RUNS} counted runs"
            if data.trusted
            else "none until a results document is trusted"
        ),
        "calendarRaces": len(data.calendar),
    }


def _head(data: SiteData, kind: str, counts: str) -> dict[str, Any]:
    return {
        "schema": SCHEMAS[kind],
        "generatedAt": data.generated_at,
        "state": data.state,
        "basis": _basis(data, counts),
    }


# ---------------------------------------------------------------- standings


def _entry_rows(data: SiteData) -> tuple[list[dict], list[dict], dict[tuple, str]]:
    """Placed and partial standings rows, and every identity's entry id."""
    calendar = {race.key for race in data.calendar}
    by_identity: dict[tuple, list] = defaultdict(list)
    for key, records in data.firsts.items():
        for record in records:
            by_identity[identity(record)].append(record)
    ids = data.ids
    rows = []
    for key, records in by_identity.items():
        season = [record for record in records if race_key(record) in calendar]
        if not season:
            continue
        summary = lb.summarize(season)
        challenges: dict[str, list] = defaultdict(list)
        for record in season:
            challenges[record["suite"]].append(record)
        rows.append(
            {
                "key": key,
                **summary,
                "records": records,
                "full": {race_key(record) for record in season} == calendar,
                "trust": _lowest_trust(data.record_trust[id(r)] for r in season),
                "challenges": {
                    suite: lb.summarize(members)
                    for suite, members in sorted(challenges.items())
                },
            }
        )
    rows.sort(key=lb.standing_key)
    placed = [row for row in rows if row["full"]]
    for row, (place, tie) in zip(placed, lb.places(placed)):
        row["place"], row["tie"] = place, tie
    partial = [row for row in rows if not row["full"]]
    for row in partial:
        row["place"], row["tie"] = None, False
    return placed, partial, ids


def _standing(row: Mapping[str, Any], ids: Mapping[tuple, str]) -> dict[str, Any]:
    cost = row["cost_per_pass"]
    return {
        "id": ids[row["key"]],
        **_who(row["key"]),
        "k": row["passed"],
        "n": row["n"],
        "rate": row["rate"],
        "lo": row["ci"][0],
        "hi": row["ci"][1],
        "place": row["place"],
        "tie": row["tie"],
        "costPerPass": cost["usd"],
        "costSource": cost["source"],
        "tokensPerPass": row["tokens_per_pass"],
        "trust": row["trust"],
        "challenges": {
            suite: {"k": summary["passed"], "n": summary["n"]}
            for suite, summary in row["challenges"].items()
        },
    }


def standings(data: SiteData, rows=None) -> dict[str, Any]:
    placed, partial, ids = rows or _entry_rows(data)
    return {
        **_head(
            data,
            "standings",
            "each entry's first counted result on each calendar race",
        ),
        "entries": [_standing(row, ids) for row in placed],
        "partial": [_standing(row, ids) for row in partial],
    }


# ---------------------------------------------------------------- races


def _stages(race: Race, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not any(run.get("checks") for run in runs):
        return None
    summary = lb.checkpoint_summary(race.checkpoints, runs)
    return {
        "checkpoints": [
            {
                "label": checkpoint["label"],
                "recorded": checkpoint["recorded"],
                "clearRate": checkpoint["clear_rate"],
            }
            for checkpoint in summary["checkpoints"]
        ],
        "moreChecks": summary["more_checks"],
    }


def _race_summary(data: SiteData, race: Race) -> dict[str, Any]:
    runs = data.runs.get(race.key, [])
    summary = lb.summarize(runs)
    priced = [_usd(run) for run in runs]
    return {
        "id": race.id,
        "name": race.name,
        "title": race.title,
        "challenge": race.suite,
        "version": race.version,
        "calendar": race in data.calendar,
        "n": summary["n"],
        "k": summary["passed"],
        "rate": summary["rate"],
        "lo": summary["ci"][0],
        "hi": summary["ci"][1],
        "entries": len({identity(run) for run in runs}),
        "medianLapSeconds": _median(run["wall_seconds"] for run in runs),
        "medianCostUsd": _median(priced),
        "pricedRuns": sum(usd is not None for usd in priced),
        "difficulty": lb.difficulty(summary["passed"], summary["n"]),
        "stages": _stages(race, runs),
    }


def _raced(data: SiteData) -> list[Race]:
    return [data.races[key] for key in sorted(data.runs, key=lambda key: key[1])]


def races(data: SiteData) -> dict[str, Any]:
    return {
        **_head(data, "races", "every counted run on each race"),
        "races": [_race_summary(data, race) for race in _raced(data)],
    }


def _stations(race: Race) -> list[tuple[int, str]]:
    return [
        (index, checkpoint["label"])
        for index, checkpoint in enumerate(race.checkpoints)
        if lb.disclosure(checkpoint) == "label"
    ]


def _funnel(race: Race, runs: Sequence[Mapping[str, Any]]) -> list[int]:
    """Runs still racing at the start, after each disclosed checkpoint, and at
    the finish. A run retires at its first failed required checkpoint in card
    order; one that retires at an undisclosed checkpoint leaves at the next
    disclosed one. The finish counts runs that passed without retiring."""
    out = [len(runs)]
    retired = [lb.retirement(run, race.checkpoints) for run in runs]
    for index, _ in _stations(race):
        out.append(sum(at is None or at > index for at in retired))
    out.append(
        sum(
            at is None and run["verdict"] == lb.PASSED for at, run in zip(retired, runs)
        )
    )
    return out


def _retirements(race: Race, firsts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stations = _stations(race)
    station_of = {index: number for number, (index, _) in enumerate(stations, 1)}
    counts = {number: 0 for number in station_of.values()}
    undisclosed = unclassified = unrecorded = 0
    for run in firsts:
        if run["verdict"] == lb.PASSED and lb.retirement(run, race.checkpoints) is None:
            continue
        if not run.get("checks"):
            unrecorded += 1
            continue
        at = lb.retirement(run, race.checkpoints)
        if at is None:
            unclassified += 1
        elif at in station_of:
            counts[station_of[at]] += 1
        else:
            undisclosed += 1
    return {
        "checkpoints": [
            {"checkpoint": number, "label": label, "runs": counts[number]}
            for number, (_, label) in enumerate(stations, 1)
        ],
        "undisclosed": undisclosed,
        "unclassified": unclassified,
        "unrecorded": unrecorded,
    }


def race(data: SiteData, race: Race, ids: Mapping[tuple, str]) -> dict[str, Any]:
    runs = data.runs.get(race.key, [])
    firsts = sorted(data.firsts.get(race.key, []), key=identity)
    checked = [run for run in runs if run.get("checks")]
    funnel = None
    if checked:
        by_car: dict[str, list] = defaultdict(list)
        by_driver: dict[str, list] = defaultdict(list)
        for run in checked:
            by_car[run["harness"]["name"]].append(run)
            by_driver[run["model"]["provider_id"]].append(run)
        funnel = {
            "runs": len(checked),
            "all": _funnel(race, checked),
            "byCar": {car: _funnel(race, rows) for car, rows in sorted(by_car.items())},
            "byDriver": {
                driver: _funnel(race, rows)
                for driver, rows in sorted(by_driver.items())
            },
        }
    finishers = [
        run
        for run in firsts
        if run["verdict"] == lb.PASSED and lb.retirement(run, race.checkpoints) is None
    ]
    finishers.sort(
        key=lambda run: (
            run["wall_seconds"] is None,
            run["wall_seconds"] or 0,
            ids[identity(run)],
        )
    )
    summary = _race_summary(data, race)
    return {
        **_head(
            data,
            "race",
            "funnel: every counted run recording checks; "
            "classification: each entry's first counted result",
        ),
        **{
            key: summary[key] for key in ("id", "name", "title", "challenge", "version")
        },
        **{key: summary[key] for key in ("calendar", "n", "k", "rate", "lo", "hi")},
        "difficulty": summary["difficulty"],
        "checkpoints": [label for _, label in _stations(race)] if checked else None,
        "moreChecks": summary["stages"]["moreChecks"] if summary["stages"] else None,
        "funnel": funnel,
        "classification": {
            "starters": len(firsts),
            "finishers": [
                {
                    "entry": ids[identity(run)],
                    **_who(identity(run)),
                    "lapSeconds": run["wall_seconds"],
                    "costUsd": _usd(run),
                }
                for run in finishers
            ],
            "retired": len(firsts) - len(finishers),
            "retirements": _retirements(race, firsts)
            if any(run.get("checks") for run in runs)
            else None,
        },
    }


# ---------------------------------------------------------------- entries


def entry(
    data: SiteData, row: Mapping[str, Any], ids: Mapping[tuple, str]
) -> dict[str, Any]:
    key = row["key"]
    calendar = {race.key for race in data.calendar}
    mine = sorted(row["records"], key=lambda record: (record["suite"], record["task"]))
    season = [record for record in mine if race_key(record) in calendar]
    standing = _standing(row, ids)
    return {
        **_head(data, "entry", "the entry's first counted result on each race it ran"),
        "id": standing["id"],
        "identity": _who(key),
        "trust": standing["trust"],
        "totals": {"k": standing["k"], "n": standing["n"], "rate": standing["rate"]},
        "range": {"lo": standing["lo"], "hi": standing["hi"]},
        "place": standing["place"],
        "tie": standing["tie"],
        "fullCalendar": bool(row["full"]),
        "fuel": {
            "costPerPass": standing["costPerPass"],
            "costSource": standing["costSource"],
            "tokensPerPass": standing["tokensPerPass"],
        },
        "medianLapSeconds": _median(record["wall_seconds"] for record in season),
        "challenges": standing["challenges"],
        "races": [
            {
                "race": record["task"],
                "name": data.races[race_key(record)].name,
                "challenge": record["suite"],
                "version": record["task_version"],
                "calendar": race_key(record) in calendar,
                "passed": record["verdict"] == lb.PASSED,
                "lapSeconds": record["wall_seconds"],
                "costUsd": _usd(record),
            }
            for record in mine
        ],
    }


# ---------------------------------------------------------------- drivers


def drivers(data: SiteData, rows=None) -> dict[str, Any]:
    placed, _, ids = rows or _entry_rows(data)
    grouped: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for race_ in data.calendar:
        if race_.suite != LADDER_CHALLENGE:
            continue
        for run in data.runs[race_.key]:
            car, _, driver, engine_map = identity(run)
            grouped[(driver, car)][engine_map].append(run)
    ladders = []
    for (driver, car), levels in sorted(grouped.items()):
        if len(levels) < 2:
            continue
        rungs = []
        for engine_map in sorted(levels, key=_map_order):
            summary = lb.summarize(levels[engine_map])
            rungs.append(
                {
                    "map": engine_map,
                    "k": summary["passed"],
                    "n": summary["n"],
                    "rate": summary["rate"],
                    "lo": summary["ci"][0],
                    "hi": summary["ci"][1],
                    "costPerRun": lb.cost_per_run(levels[engine_map])["usd"],
                }
            )
        ladders.append({"driver": driver, "car": car, "levels": rungs})
    by_driver: dict[str, dict[str, dict[str, Mapping]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in placed:  # already in standing order: the first per car is its best
        car, _, driver, engine_map = row["key"]
        by_driver[driver][engine_map].setdefault(car, row)
    sets = []
    for driver, maps in sorted(by_driver.items()):
        engine_map = min(
            maps,
            key=lambda m: (
                -len(maps[m]),
                -sum(row["passed"] for row in maps[m].values()),
                _map_order(m),
            ),
        )
        if len(maps[engine_map]) < 2:
            continue
        sets.append(
            {
                "driver": driver,
                "map": engine_map,
                "cars": [
                    {
                        key: value
                        for key, value in _standing(row, ids).items()
                        if key
                        in (
                            "id",
                            "car",
                            "configHash",
                            "k",
                            "n",
                            "rate",
                            "lo",
                            "hi",
                            "place",
                            "costPerPass",
                        )
                    }
                    for row in maps[engine_map].values()
                ],
            }
        )
    return {
        **_head(
            data,
            "drivers",
            f"ladders: every counted {LADDER_CHALLENGE} run on the calendar; "
            "cars: placed standings entries",
        ),
        "ladderChallenge": LADDER_CHALLENGE,
        "ladders": ladders,
        "sameDriverDifferentCar": sets,
    }


# ---------------------------------------------------------------- community


def community(data: SiteData) -> dict[str, Any]:
    """Every document and entry at every trust level, each with its badge."""
    documents = []
    for name in sorted(data.results.documents):
        document = data.results.documents[name]
        level, origin = data.trust[name]
        documents.append(
            {
                "document": document_id(document),
                "date": document["date"],
                "kind": document["source"]["kind"],
                "fork": "fork" in document["source"],
                "trust": level,
                "trustOrigin": origin,
                "counted": level in lb.TRUSTED_LEVELS,
                "records": len(document["records"]),
                "countedRuns": sum(lb.included(r) for r in document["records"]),
            }
        )
    entries = []
    by_level = {}
    for level in lb.TRUST_LEVELS:
        docs = {
            name: doc
            for name, doc in data.results.documents.items()
            if data.trust[name][0] == level
        }
        grouped: dict[tuple, list] = defaultdict(list)
        for record in lb.first_results(docs):
            grouped[identity(record)].append(record)
        by_level[level] = grouped
    ids = data.ids
    for level in reversed(lb.TRUST_LEVELS):
        rows = [
            {"key": key, **lb.summarize(records)}
            for key, records in by_level[level].items()
        ]
        rows.sort(key=lb.standing_key)
        for row in rows:
            entries.append(
                {
                    "id": ids[row["key"]],
                    **_who(row["key"]),
                    "trust": level,
                    "k": row["passed"],
                    "n": row["n"],
                    "rate": row["rate"],
                    "lo": row["ci"][0],
                    "hi": row["ci"][1],
                    "costPerPass": row["cost_per_pass"]["usd"],
                }
            )
    return {
        "schema": SCHEMAS["community"],
        "generatedAt": data.generated_at,
        "state": LIVE if data.results.documents else AWAITING,
        "basis": {
            "counts": "each entry's first counted result per race, "
            "within the documents at one trust level",
            "trust": list(lb.TRUST_LEVELS),
            "documents": sorted(
                document_id(document) for document in data.results.documents.values()
            ),
        },
        "levels": list(lb.TRUST_LEVELS),
        "documents": documents,
        "entries": entries,
    }


# ---------------------------------------------------------------- files


def render(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def build(results: lb.Results, tasks_dir: Path) -> dict[str, str]:
    """``{path under the site root: JSON text}`` for every data file."""
    data = model(results, tasks_dir)
    rows = _entry_rows(data)
    placed, partial, ids = rows
    files = {
        "data/standings.json": standings(data, rows),
        "data/races.json": races(data),
        "data/drivers.json": drivers(data, rows),
        "data/community.json": community(data),
    }
    for race_ in _raced(data):
        files[f"data/races/{race_.id}.json"] = race(data, race_, ids)
    for row in placed + partial:
        files[f"data/entries/{ids[row['key']]}.json"] = entry(data, row, ids)
    return {name: render(value) for name, value in sorted(files.items())}
