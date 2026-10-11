attach_existing_branch() {
  local repo_path="$1" repo_name="$2" branch="$3" workspace="$4" startpoint="$5"
  local lock_path holder owner_id holder_root dirty head remote_head rc=0
  local -a args=(worktree add --quiet --detach "$workspace" "$startpoint")
  lock_path="$(worktree_lock_path "$repo_name")"
  acquire_lock_dir "$lock_path" 900 || return $?
  holder="$(worktree_holding_branch "$repo_path" "$branch")"
  if [[ -n "$holder" && "$holder" != "$workspace" ]]; then
    owner_id="$(holder_owner_id "$holder")" || owner_id=""
    holder_root="$(holder_state_root "$holder")" || holder_root=""
    head="$(git -C "$holder" rev-parse --verify HEAD 2>/dev/null)" || head=""
    remote_head="$(git -C "$repo_path" rev-parse --verify "origin/$branch" 2>/dev/null)" || remote_head=""
    # Reclaim only a holder whose run is recorded as finished, and confirm that
    # no process still uses it: a finished run can leave a process behind.
    if [[ -n "$owner_id" && -n "$holder_root" && -n "$head" && -n "$remote_head" ]] \
      && holder_run_is_finished "$holder_root" "$holder_root/runs/$owner_id" \
      && holder_has_no_live_process "$holder" "$repo_name" "$branch" \
      && git -C "$repo_path" merge-base --is-ancestor "$head" "$remote_head"; then
      if dirty="$(git -C "$holder" status --porcelain --untracked-files=all)"; then
        if [[ -z "$dirty" ]] || PYTHONPATH="$(helper_pythonpath)${PYTHONPATH:+:$PYTHONPATH}" \
          "$PYTHON_BIN" - "$holder_root" "$owner_id" "$holder" <<'PYEOF'
from pathlib import Path
import sys
from workspace_archive import archive_dirty
archive_dirty(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
PYEOF
        then
          # Moving HEAD without dereferencing keeps uncommitted files and the index.
          git -C "$holder" update-ref --no-deref HEAD "$head" || true
        fi
      fi
    fi
  fi
  holder="$(worktree_holding_branch "$repo_path" "$branch")"
  if [[ -z "$holder" ]]; then
    args=(worktree add --quiet -B "$branch" "$workspace" "$startpoint")
  fi
  # Set the rollback flag first: even a failed add can leave admin state.
  WORKTREE_CREATED=1
  if git -C "$repo_path" "${args[@]}"; then
    rc=0
  else
    rc=$?
  fi
  if [[ "$rc" == 0 ]]; then
    git -C "$repo_path" config extensions.worktreeConfig true || rc=$?
    if [[ "$rc" == 0 ]]; then
      git -C "$workspace" config --worktree rescue.branch "$branch" || rc=$?
      git -C "$workspace" config --worktree remote.origin.push "HEAD:refs/heads/$branch" || rc=$?
    fi
  fi
  release_lock_dir "$lock_path" || return $?
  return "$rc"
}

provision_branch_override() {
    if git -C "$REPO_PATH" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      fetch_origin_branch "$REPO_PATH" "$BRANCH" "branch override"
      EXISTING_BRANCH_STARTPOINT="origin/$BRANCH"
    elif git -C "$REPO_PATH" show-ref --verify --quiet "refs/heads/$BRANCH"; then
      EXISTING_BRANCH_STARTPOINT="$BRANCH"
    else
      die "branch '$BRANCH' is neither local nor on origin; not creating a new branch"
    fi
    trap rollback_provision EXIT
    attach_existing_branch "$REPO_PATH" "$REPO" "$BRANCH" "$WORKSPACE_PATH" "$EXISTING_BRANCH_STARTPOINT"
    record_stage worktree-added
    prepare_clean_base \
      "$REPO_PATH" "$REPO" "$WORKSPACE_PATH" "$TRUNK" "$TASK_KIND" "$BRANCH" \
      "clean-base"
}
