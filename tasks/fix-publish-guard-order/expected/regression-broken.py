def publish(store, account, payload):
    if not store.is_enabled(account):
        raise PermissionError(account)
    return store.send(account, payload) if payload else None
