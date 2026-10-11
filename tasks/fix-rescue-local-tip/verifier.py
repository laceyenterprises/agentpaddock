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
    '        self.assertEqual(target.provision({}, {"b": "remote"}, '
    '"b", "main"), "remote")\n'
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    unittest.main()\n",
    "finding.md": "- **Rescue provisioning discards unpushed local commits**\n"
    "  - **File:** `provision.sh`\n"
    "  - **Lines:** 57-61\n"
    "  - **Problem:** The branch override now asks origin first. When "
    "`$BRANCH` exists remotely it picks `origin/$BRANCH` as the startpoint, "
    'and `attach_existing_branch` then runs `git worktree add -B "$branch" '
    '... "origin/$BRANCH"`. The `-B` flag moves the existing local branch '
    "to that startpoint even when the local branch is ahead.\n"
    "  - **Why it matters:** Commits that a person or an earlier run made "
    "on the local branch but never pushed stop being reachable from any "
    "branch as soon as a rescue is provisioned. Before this change the "
    "local branch was preferred whenever it existed, so those commits "
    "survived.\n"
    "  - **Recommended fix:** Test the local `refs/heads/$BRANCH` first and "
    'use `"$BRANCH"` as the startpoint when it exists; fall back to '
    "`origin/$BRANCH` only when there is no local branch.\n"
    "\n"
    "\n"
    "Fixture mapping: editable `target.py`. Lines above refer to the review "
    "fixture, not to this file.\n"
    "Python port of local/remote ref resolution and worktree -B mutation. "
    "Commit ancestry is represented by opaque tip strings; no Git "
    "subprocess is needed.\n",
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
    "def provision(local, remote, branch, trunk):\n"
    '    """Resolve the startpoint, then update the local ref as worktree -B does."""\n'
    "    if branch in remote:\n"
    "        tip = remote[branch]\n"
    "    elif branch in local:\n"
    "        tip = local[branch]\n"
    "    else:\n"
    "        tip = trunk\n"
    "    local[branch] = tip\n"
    "    return tip\n"
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
