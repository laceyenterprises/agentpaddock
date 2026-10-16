"""Independent source inventory and exact state/edge adapter for owned objects."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sources():
    return {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "lib/python/gym").glob("*.py")}


def constants(text):
    values = {}
    for n in ast.parse(text).body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            target = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(target, ast.Name) and isinstance(n.value, ast.Constant):
                values[target.id] = n.value.value
    return values


def audit(src):
    outcome = constants(src["outcomes.py"])
    taint = constants(src["taint.py"])
    tree = ast.parse(src["outcomes.py"])
    collection = next(
        n.value for n in tree.body if isinstance(n, ast.AnnAssign) and n.target.id == "OUTCOMES"
    )
    values = {
        outcome[n.id] for n in ast.walk(collection) if isinstance(n, ast.Name) and n.id in outcome
    }
    gate = {"XFAIL_SHADOW", "SKIPPED_NO_TAINT_GATE"}
    values |= {taint[k] for k in gate}
    result = {
        "paddock.grade": [(v, "[*]", v) for v in sorted(values)]
        + [("end." + v, v, "[*]") for v in sorted(values)]
    }
    result["paddock.grade"] += [
        ("level." + v, v, outcome["AGENT_ERROR"])
        for v in sorted(values - {taint[k] for k in gate} - {outcome["AGENT_ERROR"]})
    ]
    tree = ast.parse(src["endpoints.py"])
    written = {
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and any(
            isinstance(t, ast.Subscript) and ast.unparse(t) == "lease['state']" for t in n.targets
        )
    }
    initial = {
        v.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Dict)
        for k, v in zip(n.keys, n.values)
        if isinstance(k, ast.Constant) and k.value == "state" and isinstance(v, ast.Constant)
    }
    assert initial == {"creating"} and written == {
        "warming",
        "ready",
        "closing",
        "closed",
    }
    result["paddock.endpoint"] = [
        ("create", "[*]", next(iter(initial))),
        ("warm", "creating", "warming"),
        ("register", "warming", "ready"),
    ]
    result["paddock.endpoint"] += [
        ("down." + s, s, "closing") for s in sorted((initial | written) - {"closed"})
    ]
    result["paddock.endpoint"] += [
        ("delete", "closing", "closed"),
        ("replay", "closed", "closed"),
        ("end", "closed", "[*]"),
    ]
    tree = ast.parse(src["standalone.py"])
    status = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "finish"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        ):
            status.add(n.args[0].value)
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
            and any(isinstance(t, ast.Name) and t.id == "status" for t in n.targets)
        ):
            status.add(n.value.value)
    assert "subprocess.Popen" in src["standalone.py"]
    result["paddock.process"] = [("spawn", "[*]", "running")]
    result["paddock.process"] += [
        (s, "running", s) for s in sorted(status - {"input_fixtures_missing"})
    ]
    result["paddock.process"] += [
        ("staging", "[*]", "input_fixtures_missing"),
        ("halted", "[*]", "canceled"),
    ]
    result["paddock.process"] += [("end." + s, s, "[*]") for s in sorted(status)]
    return result


def inventory(src=None):
    facts = {}
    for name, text in (src or sources()).items():
        selected = set()
        for n in ast.walk(ast.parse(text)):
            if isinstance(n, ast.keyword) and n.arg in {
                "state",
                "status",
                "terminal_status",
                "outcome",
            }:
                selected.add(ast.unparse(n))
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(
                    any(w in ast.unparse(t).lower() for w in ("state", "status", "outcome"))
                    for t in targets
                ):
                    selected.add(ast.unparse(n))
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value in {
                        "state",
                        "status",
                        "outcome",
                        "terminalStatus",
                    }:
                        selected.add(str(k.value) + ": " + ast.unparse(v))
            if isinstance(n, ast.Compare) and any(
                w in ast.unparse(n.left).lower() for w in ("state", "status", "outcome")
            ):
                selected.add(ast.unparse(n))
        if selected:
            facts[name] = sorted(selected)
    return facts


def definition():
    return json.loads((ROOT / "state-machines.json").read_text(encoding="utf-8"))


def verify(doc, src=None):
    src = src or sources()
    expected = audit(src)
    assert doc["sourceInventory"] == inventory(src)
    assert {o["id"] for o in doc["objects"]} == set(expected)
    for obj in doc["objects"]:
        edges = expected[obj["id"]]
        assert {
            (e["id"].removeprefix(obj["id"] + "."), e["from"], e["to"]) for e in obj["transitions"]
        } == set(edges)
        assert {s["name"] for s in obj["states"]} == {x for _, a, b in edges for x in (a, b)} - {
            "[*]"
        }
        for state in obj["states"]:
            if state["terminal"]:
                assert all(
                    e["to"] in {state["name"], "[*]"}
                    for e in obj["transitions"]
                    if e["from"] == state["name"]
                ), "terminal state cannot transition to another state"
            if not state["terminal"]:
                assert state["exitOwner"] and state["overdueRecipient"]
                assert 0 < state["maxDurationSeconds"] < float("inf")
                assert state["enforcement"] in {"present", "missing"}
                if state["enforcement"] == "missing":
                    assert state["deadEndEvidence"]
