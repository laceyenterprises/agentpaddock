class NotFound(Exception):
    """The directory has no such user."""


class Unavailable(Exception):
    """The directory could not answer: a timeout, an outage or throttling."""


class DirectoryClient:
    """Client for the user directory. Tests pass a fake with the same interface."""

    def get_user(self, user):
        """Return the user's directory entry.

        Raises NotFound when the directory has no such user and Unavailable when
        it cannot answer.
        """
        raise NotImplementedError("network client")
