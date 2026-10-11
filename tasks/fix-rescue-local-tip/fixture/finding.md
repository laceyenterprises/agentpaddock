- **Rescue provisioning discards unpushed local commits**
  - **File:** `provision.sh`
  - **Lines:** 57-61
  - **Problem:** The branch override now asks origin first. When `$BRANCH` exists remotely it picks `origin/$BRANCH` as the startpoint, and `attach_existing_branch` then runs `git worktree add -B "$branch" ... "origin/$BRANCH"`. The `-B` flag moves the existing local branch to that startpoint even when the local branch is ahead.
  - **Why it matters:** Commits that a person or an earlier run made on the local branch but never pushed stop being reachable from any branch as soon as a rescue is provisioned. Before this change the local branch was preferred whenever it existed, so those commits survived.
  - **Recommended fix:** Test the local `refs/heads/$BRANCH` first and use `"$BRANCH"` as the startpoint when it exists; fall back to `origin/$BRANCH` only when there is no local branch.


Fixture mapping: editable `target.py`. Lines above refer to the review fixture, not to this file.
Python port of local/remote ref resolution and worktree -B mutation. Commit ancestry is represented by opaque tip strings; no Git subprocess is needed.
