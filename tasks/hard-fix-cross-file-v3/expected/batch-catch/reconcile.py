import plugins
import settings
from backend import Unavailable


def reconcile(client, accounts):
    check = plugins.resolve(settings.PRESENCE_CHECK)
    report = {"deactivated": [], "kept": [], "skipped": []}
    for start in range(0, len(accounts), settings.BATCH_SIZE):
        batch = accounts[start : start + settings.BATCH_SIZE]
        try:
            for account in batch:
                entry = check(client, account)
                report["deactivated" if entry is None else "kept"].append(account)
        except Unavailable:
            report["skipped"].append(account)
    return report
