import difflib
import gym_verifier as gv

PROTECTED = {
    "support.py": "",
    "test_visible.py": "import unittest\n"
    "import target\n"
    "\n"
    "\n"
    "class Visible(unittest.TestCase):\n"
    "    def test_smoke(self):\n"
    "        self.assertEqual(\n"
    '            target.configure({"tags": [], "limit": 3}, "a"), '
    '{"tags": ["a"], "limit": 3}\n'
    "        )\n"
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    unittest.main()\n",
    "finding.md": "# Shallow copy leaks tags between requests\n"
    "\n"
    "- **File:** target.py\n"
    "- **Lines:** 1-4\n"
    "- **Problem:** dict.copy shares the nested tags list; appending a "
    "request tag mutates the template and earlier results.\n"
    "- **Why it matters:** Later requests inherit unrelated tags and change "
    "previous responses.\n",
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
    "def configure(template, label):\n"
    "    selected = template.copy()\n"
    '    selected["tags"].append(label)\n'
    "    return selected\n"
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
