def address_key(email):
    return email.strip().lower()


def unique(subscribers):
    buckets = {}
    result = []
    for row in subscribers:
        email = row.get("email")
        if email is None:
            result.append(row)
        else:
            addresses = buckets.setdefault(row.get("list_id", ""), set())
            canonical = address_key(email)
            if canonical not in addresses:
                result.append(row)
                addresses.add(canonical)
    return result
