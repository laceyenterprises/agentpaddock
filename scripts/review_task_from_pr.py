#!/usr/bin/env python3
"""Scaffold unverified review/fix tasks from a merged GitHub PR's review history.

Usage: review_task_from_pr.py --repo OWNER/NAME --pr 123 --out-dir /tmp/draft-tasks
Only gh APIs are read. Nothing here verifies a defect or associates a particular
commit with its remediation: the later-commit aggregate is a draft candidate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import shutil
import time
import sys
from pathlib import Path

# Load the single stdlib helper without changing the process import path.
_helper_path = Path(__file__).resolve().parents[1] / "lib/python/gym/review_checks.py"
_helper_spec = importlib.util.spec_from_file_location("gym_review_checks", _helper_path)
_helper = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helper)
result_schema_snippet = _helper.result_schema_snippet

# Delays before the second and third `gh api` attempts. Fixed, so the script
# loads no Agent OS package and can ship in paddock (PDK-02).
_RETRY_DELAYS_SECONDS = (0.25, 0.5)

_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
MARKER = "GYM-GROUND-TRUTH-NEVER-STAGE"


def gh_json(endpoint, *, paginate=False):
    command = ["gh", "api", endpoint]
    if paginate:
        command += ["--paginate", "--slurp"]
    for attempt in range(3):
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            transient = isinstance(exc, subprocess.TimeoutExpired) or re.search(
                r"timeout|connection reset|temporary failure|unexpected eof|http (?:500|502|503|504)|secondary rate limit",
                str(exc.stderr or ""),
                re.I,
            )
            if not transient or attempt == 2:
                raise
            time.sleep(_RETRY_DELAYS_SECONDS[attempt])
    data = json.loads(result.stdout)
    return [item for page in data for item in page] if paginate else data


def parse_findings(body):
    """Extract legacy and bold finding cards; retain raw lines for author review.

    Unstructured/unlocated findings are preserved with missing fields rather
    than assigning invented line windows. Wrapped problem text is preserved.
    """
    match = re.search(r"^##\s+Blocking\s+(?:issues|findings)\s*:?\s*$", body, re.M | re.I)
    if not match:
        return []
    section = re.split(r"^##\s+", body[match.end() :], maxsplit=1, flags=re.M)[0]
    if re.match(r"\s*(?:-\s*)?None\b", section, re.I):
        return []
    cards, current, active = [], {}, None

    def flush():
        nonlocal current, active
        if current:
            cards.append(current)
        current, active = {}, None

    for line in section.splitlines():
        title = re.match(r"^\s*###\s+(.+)|^\s*-\s+Title\s*:\s*(.+)", line, re.I)
        label = re.match(
            r"^\s*(?:-\s*)?(?:\*\*([^*]+?)(?::\*\*|\*\*:)|(File|Lines|Problem):)\s*(.*)",
            line,
            re.I,
        )
        bold_title = re.match(r"^\s*-\s+\*\*(.+?)\*\*\s*$", line)
        if title or (bold_title and not label):
            flush()
            current["title"] = (
                next(x for x in title.groups() if x) if title else bold_title[1]
            ).strip()
        elif label:
            key = (label[1] or label[2]).strip().lower()
            active = None
            if key not in ("file", "lines", "problem", "category"):
                continue
            if key == "category":
                key = "expected_category"
            if key == "file" and "file" in current:
                flush()
            active = key
            current[key] = label[3].strip().strip("`")
        elif line.strip() and active == "problem":
            current[active] += "\n" + line.strip()
    flush()
    return cards or [{"problem": section.strip()}]


def fetch(pr, repo):
    if type(pr) is not int or pr < 1:
        raise ValueError("PR number must be positive")
    if not _REPOSITORY.fullmatch(repo or ""):
        raise ValueError("repository must be OWNER/NAME")
    root = f"repos/{repo}"
    info = gh_json(f"{root}/pulls/{pr}")
    if not info.get("merged_at"):
        raise ValueError("source PR must be merged")
    reviews = gh_json(f"{root}/pulls/{pr}/reviews", paginate=True)
    commits = gh_json(f"{root}/pulls/{pr}/commits", paginate=True)
    reviewer_logins = {
        name
        for model in ("claude", "codex", "gemini")
        for name in (f"lacey-{model}-reviewer", f"{model}-reviewer-lacey")
    }
    candidates = [
        r
        for r in reviews
        if (r.get("user", {}).get("login", "").lower().removesuffix("[bot]") in reviewer_logins)
        and parse_findings(r.get("body") or "")
    ]
    if not candidates:
        raise ValueError("no review with Blocking issues or Blocking findings")
    # A source review is an author-selected draft, never treated as authoritative.
    review = min(candidates, key=lambda r: (r.get("submitted_at") or "", r["id"]))
    shas = [c["sha"] for c in commits]
    head = review.get("commit_id")
    if head not in shas:
        raise ValueError("reviewed head absent from PR commits (possibly rewritten history)")
    later = shas[shas.index(head) + 1 :]
    if not later:
        raise ValueError("no later commits after the reviewed head")
    # Compare against the reviewed commit's base, not today's moving base ref.
    first = gh_json(f"{root}/commits/{shas[0]}")
    base = first["parents"][0]["sha"]
    warnings = []
    if any(len(c.get("parents", [])) > 1 for c in [first, *commits]):
        warnings.append(
            "PR contains merge commits; draft diffs may include unrelated base-branch changes."
        )
    if len(commits) >= 250:
        warnings.append(
            "PR commit list reaches GitHub's 250-commit cap; history may be incomplete."
        )
    reviewed = gh_json(f"{root}/compare/{base}...{head}")
    remediation = gh_json(f"{root}/compare/{head}...{later[-1]}")
    for name, comparison in (("Reviewed", reviewed), ("Remediation", remediation)):
        if comparison.get("status") not in ("ahead", "identical"):
            raise ValueError("comparison history is not linear/ancestral")
        if len(comparison.get("files", [])) >= 300:
            warnings.append(
                f"{name} comparison reaches GitHub's 300-file cap; draft diff may be incomplete."
            )
    return dict(
        repo=repo,
        pr=pr,
        info=info,
        review=review,
        later_commits=later,
        base_sha=base,
        reviewed=reviewed,
        remediation=remediation,
        warnings=warnings,
    )


def _diff(comparison):
    # GitHub can omit binary/large patches. Explicit placeholders prevent a
    # missing patch from masquerading as an empty complete fixture.
    return (
        "\n".join(
            f"diff --git a/{f.get('previous_filename', f['filename'])} b/{f['filename']}\n"
            f"--- a/{f.get('previous_filename', f['filename'])}\n+++ b/{f['filename']}\n"
            + f.get("patch", "TODO: patch omitted by GitHub; retrieve and trim manually")
            for f in comparison.get("files", [])
        )
        + "\n"
    )


def scaffold(source, out_dir):
    """Create both skeletons; never overwrite existing work or emit verified truth."""
    directories = [out_dir / f"{suite}-pr-{source['pr']}" for suite in ("review", "fix")]
    for directory in directories:
        if directory.exists():
            raise FileExistsError(f"task directory exists: {directory}")
    provenance = dict(
        source_pr=f"https://github.com/{source['repo']}/pull/{source['pr']}",
        review_id=source["review"]["id"],
        reviewed_head=source["review"]["commit_id"],
        base_sha=source["base_sha"],
        later_commits=source["later_commits"],
        warnings=source["warnings"],
    )
    findings = [
        dict(
            status="unverified",
            **{key: {"status": "unverified", "value": value} for key, value in finding.items()},
        )
        for finding in parse_findings(source["review"]["body"])
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for suite, directory in zip(("review", "fix"), directories):
            directory.mkdir()
            created.append(directory)
            (directory / "expected").mkdir()
            (directory / "stages").mkdir()
            (directory / "fixture").mkdir()
            (directory / "fixture" / "README.md").write_text(
                "".join(f"WARNING: {warning}\n" for warning in source["warnings"])
                + "TODO: trim change.diff; add self-contained post-change Python files and pr.md.\n"
                "For fix: add pre-fix files, finding.md, visible tests and hidden regression tests.\n"
            )
            (directory / "fixture" / "change.diff").write_text(_diff(source["reviewed"]))
            if suite == "fix":
                # The fix audit reads the trusted staged manifest; the author
                # fills editable/files once the pre-fix fixture exists.
                (directory / "fixture" / "staged-manifest.json").write_text(
                    json.dumps({"editable": [], "files": []}, indent=2) + "\n"
                )
            (directory / "expected" / "reference-fix.diff").write_text(
                "# UNVERIFIED aggregate later-commit diff; author must identify the actual fix.\n"
                + _diff(source["remediation"])
            )
            (directory / "expected" / "ground_truth.json").write_text(
                json.dumps(
                    dict(
                        status="unverified",
                        findings=findings,
                        provenance=provenance,
                        expected_category={"status": "unverified", "value": None},
                        note="TODO: verify every finding and port line windows to the trimmed fixture",
                        __ground_truth_marker__=MARKER,
                    ),
                    indent=2,
                )
                + "\n"
            )
            (directory / "expected" / "source-review.md").write_text(source["review"]["body"])
            (directory / "prompt.md").write_text(
                "TODO: author the self-contained " + suite + " prompt.\n"
            )
            stage = directory / "stages" / "stage-input.sh"
            stage.write_text(
                '#!/bin/sh\n# TODO: stage only trimmed fixture files, never expected/.\necho "SCAFFOLD: fixture unverified" >&2\nexit 1\n'
            )
            stage.chmod(0o755)
            (directory / "verifier.py").write_text(
                "import gym_verifier as gv\nimport gym_review_checks as review_checks\n"
                'gv.fail("SCAFFOLD: ground truth and fixture unverified")\n'
            )
            schema = (
                result_schema_snippet()
                if suite == "review"
                else '[result_schema]\nsummary = { type = "string", required = true }\n'
            )
            (directory / "task.toml").write_text(
                "# UNVERIFIED scaffold; source provenance in expected/ground_truth.json\n"
                f'id = "{directory.name}"\ntitle = "Draft {suite} from PR {source["pr"]}"\n'
                f'suite = "{suite}"\npersona = "codex"\n[dispatch]\nprompt = "prompt.md"\n'
                '[[stages]]\nrun = "stages/stage-input.sh"\n'
                + schema
                + '[verifier]\nentrypoint = "verifier.py"\n'
            )
    except BaseException:
        for directory in reversed(created):
            shutil.rmtree(directory)
        raise
    return directories


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="source repository, OWNER/NAME")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument(
        "--out-dir", type=Path, default=Path(__file__).resolve().parents[1] / "draft-tasks"
    )
    args = parser.parse_args(argv)
    try:
        paths = scaffold(fetch(args.pr, args.repo), args.out_dir)
    except (
        ValueError,
        OSError,
        KeyError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("Unverified skeletons: " + ", ".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
