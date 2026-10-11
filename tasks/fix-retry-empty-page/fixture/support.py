class PageStore:
    def __init__(self):
        self.cursor = 7
        self.pages = []

    def append(self, page):
        self.pages.append(page)


class Client:
    def __init__(self, page=None, fail=False):
        self.page = ["row"] if page is None else page
        self.fail = fail
        self.cursors = []

    def fetch(self, cursor):
        self.cursors.append(cursor)
        if self.fail:
            import target

            raise target.TemporaryUnavailable()
        return self.page
