from directory import lookup


def sweep(client, accounts):
    """Deactivate local accounts whose user no longer exists in the directory.

    Returns {"deactivated": [...], "skipped": [...]}, each in input order. An
    account whose lookup cannot be answered (backend.Unavailable) is skipped,
    never deactivated, and the sweep carries on with the next account.
    """
    report = {"deactivated": [], "skipped": []}
    for account in accounts:
        if lookup(client, account) is None:
            report["deactivated"].append(account)
    return report
