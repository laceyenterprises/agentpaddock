from subscribers import unique


def send_list(subscribers):
    """Return (list_id, email) deliveries in input order.

    Lists have independent subscriptions: a match requires the same list_id
    AND the same normalized address. Missing list_id means the empty string.
    List ids are opaque strings, including punctuation and whitespace; emails
    are strings or None. No plus-tag or dot rewriting is supported. Each
    address goes out exactly as typed. Rows are shared with the CRM: retain
    the original dict objects and leave all rows untouched, even dropped ones.
    Input may be a one-shot iterable.
    """
    return [
        (row.get("list_id", ""), row["email"])
        for row in unique(subscribers)
        if row.get("email") is not None
    ]
