import config


def reload(state, path):
    """Apply the overrides file to the running daemon; called on SIGHUP.

    If the file cannot be used (config.ConfigError), reload keeps the last good
    configuration, records the error in state["degraded"] and keeps running.
    Operators clear it by fixing the file and sending SIGHUP again. Overrides
    are how operators turn merging off during an incident, so a bad edit must
    never switch merge_enabled back to its default.
    """
    try:
        overrides = config.load_overrides(path)
    except config.ConfigError as exc:
        state["degraded"] = str(exc)
        return state
    state["config"] = {**config.DEFAULTS, **overrides}
    state["degraded"] = None
    return state
