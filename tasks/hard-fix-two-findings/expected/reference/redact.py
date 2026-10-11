import re

SECRET = re.compile(r"\b(?:ghp|sk)-[A-Za-z0-9]{8,}")


def redact(text):
    """Replace every token that looks like a credential with [REDACTED]."""
    return SECRET.sub("[REDACTED]", text)
