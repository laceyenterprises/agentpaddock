from subscribers import unique


def send_list(subscribers):
    """The addresses one newsletter send goes to.

    Each address goes out exactly as the subscriber typed it, spaces and
    capitals included: the mail provider keys its suppression list on that raw
    spelling, so a rewritten address slips past an unsubscribe. The subscriber
    dicts are shared with the CRM sync, which runs after the send and must see
    them unchanged.
    """
    return [subscriber["email"] for subscriber in unique(subscribers) if "email" in subscriber]
