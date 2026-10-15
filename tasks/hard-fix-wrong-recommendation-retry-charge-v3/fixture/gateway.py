class GatewayTimeout(Exception):
    """The gateway did not answer in time.

    The charge may or may not have been applied: a timeout says nothing about
    whether the request reached the payment ledger before the connection dropped.
    """


class Gateway:
    """Client for the payment gateway. Tests pass a fake with the same interface."""

    def create_charge(
        self, order_id, amount_cents, description="", idempotency_key=None
    ):
        """Create a charge and return its charge id.

        Without an idempotency_key, every call that reaches the gateway creates a
        new charge. The gateway remembers each idempotency_key for 30 days: a
        later call with a remembered key creates no charge and returns the first
        call's charge id, whatever its order, amount or description. Raises
        GatewayTimeout when the gateway does not answer in time.
        """
        raise NotImplementedError("network client")
