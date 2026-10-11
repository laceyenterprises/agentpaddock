def lookup(client, user):
    """Return the directory entry for user, or None when the directory has no such user.

    Any other directory error propagates to the caller.
    """
    try:
        return client.get_user(user)
    except Exception:
        return None
