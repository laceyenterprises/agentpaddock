import policy
from freeze import in_freeze


def may_deploy(team, now, hotfix=False):
    """Whether `team` may ship a release at the datetime `now`.

    Hotfixes always ship. Anything else waits out the team's freeze window
    from policy.FREEZES.
    """
    if hotfix:
        return True
    start, end = policy.FREEZES[team]
    return not in_freeze(now.time(), start, end)
