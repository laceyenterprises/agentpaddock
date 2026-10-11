from rules import normalize


def dedupe(names):
    """Keep the first occurrence of each name under rules.normalize."""
    seen = set()
    kept = []
    for name in names:
        if name not in seen:
            seen.add(normalize(name))
            kept.append(name)
    return kept
