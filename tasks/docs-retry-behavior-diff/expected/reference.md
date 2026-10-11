## Delivery
Delivery makes at most 3 attempts and retries only TimeoutError.
Other exceptions propagate. Exhaustion returns 4; success returns 0.
