import difflib
import gym_verifier as gv

PROTECTED = {
    "support.py": "class FakeFS:\n"
    "    def __init__(self, replace_at=None):\n"
    '        self.owner = "orphan"\n'
    "        self.replace_at = replace_at\n"
    "        self.reads = 0\n"
    "        self.removed = []\n"
    "\n"
    "    def stat(self, path):\n"
    '        observed = {"identity": self.owner, "mtime": 0 if self.owner '
    '== "orphan" else 29}\n'
    "        self.reads += 1\n"
    "        if self.reads == self.replace_at:\n"
    '            self.owner = "live"\n'
    "        return observed\n"
    "\n"
    "    def unlink(self, path):\n"
    "        self.removed.append(self.owner)\n"
    "        self.owner = None\n"
    "\n"
    "    def remove_if_same(self, path, observed):\n"
    '        if self.owner != observed["identity"]:\n'
    "            return False\n"
    "        self.unlink(path)\n"
    "        return True\n",
    "test_visible.py": "import unittest\n"
    "import target\n"
    "from support import FakeFS\n"
    "\n"
    "\n"
    "class Visible(unittest.TestCase):\n"
    "    def test_smoke(self):\n"
    "        fs = FakeFS()\n"
    '        self.assertFalse(target.recover(fs, "lock", 10))\n'
    '        self.assertEqual(fs.owner, "orphan")\n'
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    unittest.main()\n",
    "finding.md": "- **Two waiters can both break the same stale lock**\n"
    "  - **File:** `file-lock.mjs`\n"
    "  - **Lines:** 24-29\n"
    "  - **Problem:** Recovery reads the lock's age with `statSync` and "
    "deletes the path later with `rmSync`. Every waiter polling a crashed "
    "holder's lock sees the same old `mtimeMs` and decides the lock is "
    "stale. If waiter A deletes it and takes a new lock with "
    '`openSync(lockPath, "wx")` before waiter B reaches its `rmSync`, B '
    "deletes A's live lock and then takes one of its own.\n"
    "  - **Why it matters:** Mutual exclusion fails exactly during crash "
    "recovery. A and B both run the critical section at once, which brings "
    "back the lost-update problem the lock exists to prevent.\n"
    "  - **Recommended fix:** Make breaking a stale lock an atomic "
    "compare-and-remove that deletes the file only while it is still the "
    "one judged stale. Prefer a kernel advisory lock released when its "
    "holder exits, using a permanent inode that is never unlinked. "
    "Otherwise use an actual atomic identity-checked removal primitive; a "
    "second stat or rename alone cannot establish that the path still names "
    "the observed stale lock.\n"
    "\n"
    "\n"
    "Fixture mapping: editable `target.py`. Lines above refer to the review "
    "fixture, not to this file.\n"
    "Python port with injected filesystem operations. remove_if_same is an "
    "atomic identity-checked removal contract; a second stat is "
    "deliberately insufficient. Tests schedule replacement at each "
    "observation boundary without timing or production filesystem probes.\n",
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
    "def recover(fs, path, now, stale_after=15):\n"
    '    """fs exposes stat, unlink and atomic remove_if_same(path, observed)."""\n'
    "    observed = fs.stat(path)\n"
    '    if now - observed["mtime"] > stale_after:\n'
    "        fs.unlink(path)\n"
    "        return True\n"
    "    return False\n"
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
gv.check("small_diff", changed is not None and lines <= 16, required=False)
gv.passed()
