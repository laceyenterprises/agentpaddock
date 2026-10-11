def recover(fs, path, now, stale_after=15):
    """fs exposes stat, unlink and atomic remove_if_same(path, observed)."""
    observed = fs.stat(path)
    if now - observed["mtime"] >= stale_after:
        return fs.remove_if_same(path, observed)
    return False
