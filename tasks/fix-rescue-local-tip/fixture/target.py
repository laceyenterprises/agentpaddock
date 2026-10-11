def provision(local, remote, branch, trunk):
    """Resolve the startpoint, then update the local ref as worktree -B does."""
    if branch in remote:
        tip = remote[branch]
    elif branch in local:
        tip = local[branch]
    else:
        tip = trunk
    local[branch] = tip
    return tip
