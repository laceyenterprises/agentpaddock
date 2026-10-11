from backend import Unavailable
from directory import lookup


def sweep(client, accounts):
    """Deactivate local accounts whose user no longer exists in the directory.

    Returns {"deactivated": [...], "skipped": [...]}, each in input order. An
    account whose lookup cannot be answered (backend.Unavailable) is skipped,
    never deactivated, and the sweep carries on with the next account.
    """
    report = {"deactivated": [], "skipped": []}
    for account in accounts:
        try:
            entry = lookup(client, account)
        except Unavailable:
            report["skipped"].append(account)
            continue
        if entry is None:
            report["deactivated"].append(account)
    return report
