class TemporaryUnavailable(Exception):
    pass


def read_page(client, cursor):
    try:
        return client.fetch(cursor)
    except TemporaryUnavailable:
        return []


def drain(client, store):
    cursor = store.cursor
    page = read_page(client, cursor)
    store.append(page)
    store.cursor = cursor + 1
    return page
