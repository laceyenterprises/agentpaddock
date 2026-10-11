from quiet import in_quiet_hours


def should_page(alert, policy, now):
    """Critical alerts always page; anything else waits out the quiet hours."""
    if alert["severity"] == "critical":
        return True
    return not in_quiet_hours(now.time(), policy["quiet_start"], policy["quiet_end"])
