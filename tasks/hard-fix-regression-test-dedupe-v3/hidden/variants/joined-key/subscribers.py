def unique(subscribers):
    """Select subscribers for the newsletter caller, keeping the first match seen.

    Two addresses are the same when they match ignoring case and surrounding
    whitespace: "Ann@Example.com " and "ann@example.com" are one address.
    newsletter.py defines the scope of a match. Missing or None emails are
    always kept; an empty string is an address like any other. Kept subscribers are
    the caller's own dicts, in input order.
    """
    seen = set()
    kept = []
    for subscriber in subscribers:
        email = subscriber.get("email")
        if email is None:
            kept.append(subscriber)
            continue
        key = subscriber.get("list_id", "") + "|" + email.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(subscriber)
    return kept
