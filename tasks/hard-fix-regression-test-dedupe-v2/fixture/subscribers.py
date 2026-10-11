def unique(subscribers):
    """Return one subscriber per email address, keeping the first one seen.

    Two addresses are the same when they match ignoring case and surrounding
    whitespace: "Ann@Example.com " and "ann@example.com" are one address.
    Subscribers without an "email" key are always kept. Kept subscribers are
    the caller's own dicts, in input order.
    """
    seen = set()
    kept = []
    for subscriber in subscribers:
        email = subscriber.get("email")
        if email is None:
            kept.append(subscriber)
            continue
        if email in seen:
            continue
        seen.add(email)
        kept.append(subscriber)
    return kept
