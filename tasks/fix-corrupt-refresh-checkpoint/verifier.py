import difflib
import gym_verifier as gv

PROTECTED = {
    "support.py": "",
    "test_visible.py": "import unittest\n"
    "import tempfile\n"
    "from pathlib import Path\n"
    "import target\n"
    "\n"
    "\n"
    "class Visible(unittest.TestCase):\n"
    "    def test_smoke(self):\n"
    "        with tempfile.TemporaryDirectory() as directory:\n"
    "            self.assertEqual(target.load(Path(directory)), ({}, "
    "{}))\n"
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    unittest.main()\n",
    "finding.md": "- **A damaged checkpoint halts every later refresh**\n"
    "  - **File:** `progress_journal.py`\n"
    "  - **Lines:** 11-18 (the `ProgressJournal` constructor)\n"
    "  - **Problem:** The constructor decodes every checkpoint under "
    "`progress/files/` and the identity index with `json.loads`, and by "
    "design lets a decode or shape error escape. The refresh command builds "
    "the journal first and without a handler. One truncated or hand-edited "
    "file therefore makes construction raise on every pass, and nothing in "
    "the refresh path moves the bad file aside.\n"
    "  - **Why it matters:** A cache of progress becomes a hard "
    "precondition for the whole refresh, and the only way out is for an "
    "operator to find and delete the bad file by hand. The comment's "
    "reasoning does not hold: a source with no checkpoint is already reset "
    "and re-read from offset zero, so treating a damaged checkpoint as "
    "missing is as safe as first-time ingestion.\n"
    "  - **Recommended fix:** Per file, handle read and decode failures "
    "(`OSError`, `json.JSONDecodeError`, `UnicodeDecodeError`) and shape or "
    "version mismatches. Rename the bad file aside with a "
    "`.corrupt-<timestamp>` suffix, log the event at ERROR, and treat that "
    "source as having no checkpoint. Rebuild or empty a damaged identity "
    "index. Add a test that corrupts one checkpoint and the identity index, "
    "then checks that the pass completes and both files are quarantined.\n"
    "\n"
    "\n"
    "Fixture mapping: editable `target.py`. Lines above refer to the review "
    "fixture, not to this file.\n"
    "Port of the checkpoint constructor into load(root). The fixture keeps "
    "loading, per-file isolation, quarantine and reset semantics; database "
    "ingestion and alert transport are omitted. Logging stands in for the "
    "service alert boundary.\n"
    "\n"
    "Fixture contract: only decode, shape and version errors are corruption "
    "and get quarantined.\n"
    "A read that fails with any other `OSError` (for example permission "
    "denied or EIO) must propagate unchanged, with no rename and no "
    "quarantine file.\n"
    "`FileNotFoundError` means no checkpoint. This contract takes "
    "precedence over the review card’s Recommended fix for this fixture.\n",
    "staged-manifest.json": "{\n"
    '  "editable": [\n'
    '    "target.py"\n'
    "  ],\n"
    '  "files": [\n'
    '    "target.py",\n'
    '    "support.py",\n'
    '    "test_visible.py",\n'
    '    "finding.md"\n'
    "  ]\n"
    "}\n",
}
ORIGINAL = (
    "import json\n"
    "\n"
    "VERSION = 2\n"
    "\n"
    "\n"
    "def load(root):\n"
    "    files = {}\n"
    '    for path in sorted((root / "files").glob("*.json")):\n'
    "        payload = json.loads(path.read_text())\n"
    '        if payload.get("version") == VERSION:\n'
    '            files[payload["path_key"]] = payload["checkpoint"]\n'
    "    try:\n"
    '        identities = json.loads((root / "identities.json").read_text())\n'
    "    except FileNotFoundError:\n"
    "        identities = {}\n"
    "    return files, identities\n"
)
STAGED = [
    "target.py",
    "support.py",
    "test_visible.py",
    "finding.md",
    "staged-manifest.json",
]

# Check every staged input through capture; the immutable manifest is included.
allowed = set(gv.workspace_files()) == set(STAGED) and all(
    gv.workspace_text(name) == body for name, body in PROTECTED.items()
)
gv.check("edited_only_allowed_files", allowed, required=True)
outcome = gv.hidden_tests() or {}
complete = outcome.get("status") == "passed" and outcome.get("passed") == 3
gv.check("hidden_tests_pass", complete, required=True)
# Hidden completion covers both defect and regression methods once.
changed = gv.workspace_text("target.py")
diff = list(difflib.ndiff(ORIGINAL.splitlines(), (changed or "").splitlines()))
lines = sum(line.startswith(("+ ", "- ")) for line in diff)
gv.check("small_diff", changed is not None and lines <= 60, required=False)
gv.passed()
