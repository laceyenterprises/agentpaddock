class TemporaryUnavailable(Exception):
    pass


def read_page(client, cursor):
    try:
        return client.fetch(cursor)
    except TemporaryUnavailable:
        raise


def drain(client, store):
    cursor = store.cursor
    page = read_page(client, cursor)
    store.append(page)
    store.cursor = cursor + 1 if page else cursor
    return page
