import backend
import plugins
import settings


def reconcile(client, accounts):
    """Deactivate local accounts whose user no longer exists in the directory.

    Returns {"deactivated": [...], "kept": [...], "skipped": [...]}, each in
    input order. An account whose presence check cannot be answered
    (backend.Unavailable) is skipped, never deactivated, and the run carries on
    with the next account.
    """
    check = plugins.resolve(settings.PRESENCE_CHECK)
    report = {"deactivated": [], "kept": [], "skipped": []}
    for start in range(0, len(accounts), settings.BATCH_SIZE):
        for account in accounts[start : start + settings.BATCH_SIZE]:
            try:
                bucket = "kept" if check(client, account) is not None else "deactivated"
            except backend.Unavailable:
                bucket = "skipped"
            report[bucket].append(account)
    return report
