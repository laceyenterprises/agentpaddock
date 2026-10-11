def canonical_email(email):
    return email.strip().lower()


def dedupe(records):
    """Return one record per email address, keeping the first one seen.

    Two addresses are the same when they match ignoring case and surrounding
    whitespace: "Ann@Example.com " and "ann@example.com" are one address.
    Kept records are returned unchanged and in input order. Records without
    an "email" key are always kept.
    """
    by_key = {}
    kept = []
    for record in records:
        if "email" not in record:
            kept.append(record)
        elif by_key.setdefault(canonical_email(record["email"]), record) is record:
            kept.append(record)
    return kept
