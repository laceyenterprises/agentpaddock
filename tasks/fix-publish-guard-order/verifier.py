import difflib
import gym_verifier as gv

PROTECTED = {
    "support.py": "class Store:\n"
    "    def __init__(self, enabled):\n"
    "        self.enabled = enabled\n"
    "        self.calls = []\n"
    "\n"
    "    def is_enabled(self, account):\n"
    "        return self.enabled\n"
    "\n"
    "    def send(self, account, payload):\n"
    "        self.calls.append((account, payload))\n"
    '        return "receipt"\n',
    "test_visible.py": "import unittest\n"
    "import target\n"
    "from support import Store\n"
    "\n"
    "\n"
    "class Visible(unittest.TestCase):\n"
    "    def test_smoke(self):\n"
    "        store = Store(True)\n"
    '        self.assertEqual(target.publish(store, "a", "payload"), '
    '"receipt")\n'
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    unittest.main()\n",
    "finding.md": "# Disabled account is published before authorization\n"
    "\n"
    "- **File:** target.py\n"
    "- **Lines:** 1-5\n"
    "- **Problem:** publish sends before checking is_enabled; a disabled "
    "account still causes a send.\n"
    "- **Why it matters:** Unauthorized outbound side effects cannot be "
    "undone.\n",
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
    "def publish(store, account, payload):\n"
    "    receipt = store.send(account, payload)\n"
    "    if not store.is_enabled(account):\n"
    "        raise PermissionError(account)\n"
    "    return receipt\n"
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
