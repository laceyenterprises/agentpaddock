class NotFound(Exception):
    """A directory lookup found no entry.

    authoritative=False means this was only a cache miss. Before concluding
    absence, callers must query get_user(user, refresh=True). Only a NotFound
    from that authoritative query proves absence. A refresh may instead return
    an entry or raise Unavailable; it must not be converted to an absent user.
    """

    def __init__(self, user, authoritative=True):
        super().__init__(user)
        self.authoritative = authoritative


class Unavailable(Exception):
    """The directory could not answer: a timeout, an outage or throttling."""


class DirectoryClient:
    """Client for the user directory. Tests pass a fake with the same interface."""

    def get_user(self, user, refresh=False):
        """Return the user's directory entry.

        Raises NotFound for an authoritative absence or a cache miss (see above),
        and Unavailable when
        it cannot answer.
        """
        raise NotImplementedError("network client")
