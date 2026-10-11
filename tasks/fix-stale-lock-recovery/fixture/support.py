class FakeFS:
    def __init__(self, replace_at=None):
        self.owner = "orphan"
        self.replace_at = replace_at
        self.reads = 0
        self.removed = []

    def stat(self, path):
        observed = {"identity": self.owner, "mtime": 0 if self.owner == "orphan" else 29}
        self.reads += 1
        if self.reads == self.replace_at:
            self.owner = "live"
        return observed

    def unlink(self, path):
        self.removed.append(self.owner)
        self.owner = None

    def remove_if_same(self, path, observed):
        if self.owner != observed["identity"]:
            return False
        self.unlink(path)
        return True
