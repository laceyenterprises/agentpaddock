# Handle transient upstream failures

TemporaryUnavailable means this cursor has not been read and the caller should retry it. A genuinely empty page is a successful page and advances the cursor. store.append persists rows; store.cursor is persisted immediately. The outer scheduler retries only when TemporaryUnavailable escapes drain. Keep successful and empty responses compatible.
