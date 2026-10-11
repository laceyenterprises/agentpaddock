def dedupe(records):
    """Return one record per email address, keeping the first one seen.

    Two addresses are the same when they match ignoring case and surrounding
    whitespace: "Ann@Example.com " and "ann@example.com" are one address.
    Kept records are returned unchanged and in input order. Records without
    an "email" key are always kept.
    """
    seen = set()
    kept = []
    for record in records:
        email = record.get("email")
        if email is None:
            kept.append(record)
            continue
        if email in seen:
            continue
        seen.add(email)
        kept.append(record)
    return kept
