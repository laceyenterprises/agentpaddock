def provision(local, remote, branch, trunk):
    """Resolve the startpoint, then update the local ref as worktree -B does."""
    if branch in local:
        tip = local[branch]
    elif branch in remote:
        tip = remote[branch]
    else:
        tip = trunk
    local[branch] = tip
    return tip
