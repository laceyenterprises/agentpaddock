from rules import normalize


def dedupe(names):
    """Keep the first occurrence of each name under rules.normalize."""
    seen = set()
    kept = []
    for name in names:
        key = normalize(name)
        if key not in seen:
            seen.add(key)
            kept.append(name)
    return kept
