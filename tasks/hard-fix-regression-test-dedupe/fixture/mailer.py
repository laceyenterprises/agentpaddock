from contacts import dedupe


def recipients(records):
    """Addresses for one campaign send, as the contact records spell them."""
    return [record["email"] for record in dedupe(records) if "email" in record]
