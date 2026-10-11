import difflib
import gym_verifier as gv

PROTECTED = {
    "support.py": "class PageStore:\n"
    "    def __init__(self):\n"
    "        self.cursor = 7\n"
    "        self.pages = []\n"
    "\n"
    "    def append(self, page):\n"
    "        self.pages.append(page)\n"
    "\n"
    "\n"
    "class Client:\n"
    "    def __init__(self, page=None, fail=False):\n"
    '        self.page = ["row"] if page is None else page\n'
    "        self.fail = fail\n"
    "        self.cursors = []\n"
    "\n"
    "    def fetch(self, cursor):\n"
    "        self.cursors.append(cursor)\n"
    "        if self.fail:\n"
    "            import target\n"
    "\n"
    "            raise target.TemporaryUnavailable()\n"
    "        return self.page\n",
    "test_visible.py": "import unittest\n"
    "import target\n"
    "from support import Client, PageStore\n"
    "\n"
    "\n"
    "class Visible(unittest.TestCase):\n"
    "    def test_smoke(self):\n"
    "        store = PageStore()\n"
    '        self.assertEqual(target.drain(Client(["row"]), store), '
    '["row"])\n'
    "        self.assertEqual(store.cursor, 8)\n"
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    unittest.main()\n",
    "finding.md": "# Temporary fetch failure is mistaken for an empty page\n"
    "\n"
    "- **File:** target.py\n"
    "- **Lines:** 1-17\n"
    "- **Problem:** read_page converts TemporaryUnavailable to [], so drain "
    "appends and advances the cursor instead of preserving a retryable "
    "failure.\n"
    "- **Why it matters:** A retry skips data from the failed page.\n",
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
    "class TemporaryUnavailable(Exception):\n"
    "    pass\n"
    "\n"
    "\n"
    "def read_page(client, cursor):\n"
    "    try:\n"
    "        return client.fetch(cursor)\n"
    "    except TemporaryUnavailable:\n"
    "        return []\n"
    "\n"
    "\n"
    "def drain(client, store):\n"
    "    cursor = store.cursor\n"
    "    page = read_page(client, cursor)\n"
    "    store.append(page)\n"
    "    store.cursor = cursor + 1\n"
    "    return page\n"
)
STAGED = ["target.py", "support.py", "test_visible.py", "finding.md", "staged-manifest.json"]

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
