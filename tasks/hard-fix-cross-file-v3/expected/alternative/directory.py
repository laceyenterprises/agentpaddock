from backend import NotFound


def lookup(client, user):
    """Return the directory entry for user, or None when the directory has no such user.

    backend.py defines when a missing response proves absence.
    Any other directory error propagates to the caller.
    """

    def query(refresh):
        try:
            return (
                client.get_user(user, refresh=True)
                if refresh
                else client.get_user(user)
            )
        except NotFound as missing:
            if refresh or missing.authoritative:
                return None
            return query(True)

    return query(False)
