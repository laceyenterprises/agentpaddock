import policy
from freeze import in_freeze


def may_deploy(team, now, hotfix=False):
    """Evaluate release policy using an already-converted local datetime."""
    if hotfix:
        return True
    start, end, days = policy.FREEZES[team]
    return not in_freeze(now, start, end, days)
