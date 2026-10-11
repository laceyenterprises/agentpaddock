class Store:
    def __init__(self, enabled):
        self.enabled = enabled
        self.calls = []

    def is_enabled(self, account):
        return self.enabled

    def send(self, account, payload):
        self.calls.append((account, payload))
        return "receipt"
