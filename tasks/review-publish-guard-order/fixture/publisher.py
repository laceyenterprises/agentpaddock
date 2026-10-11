def publish(store, account, payload):
    receipt = store.send(account, payload)
    if not store.is_enabled(account):
        raise PermissionError(account)
    return receipt
