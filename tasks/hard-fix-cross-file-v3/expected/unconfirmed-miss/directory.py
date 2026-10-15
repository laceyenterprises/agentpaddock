from backend import NotFound


def lookup(client, user):
    """Return the directory entry for user, or None when the directory has no such user.

    backend.py defines when a missing response proves absence.
    Any other directory error propagates to the caller.
    """
    try:
        return client.get_user(user)
    except NotFound:
        return None
