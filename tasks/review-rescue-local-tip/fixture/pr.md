# Keep the original workspace during a branch rescue

A --branch override for a rescue must reuse the existing branch without deleting the workspace that already holds it. Add a lock-protected helper that can release a finished holder, or provision a detached rescue while an active holder keeps the branch. Both the local branch and origin may exist, and local commits may not be pushed yet.

Trimmed from the provisioning shell: the new helper is shown whole and the branch override body is wrapped in provision_branch_override for readability. Environment variables and the small helpers it calls are supplied by provisioning. The lock is exclusive. Fetch refreshes origin/<branch> but does not update refs/heads/<branch>. A path with no holder is supported. Only the helper and the override changes are in scope; review line numbers refer to provision.sh.
