class GatewayTimeout(Exception):
    """The gateway did not answer in time.

    The charge may or may not have been applied: a timeout says nothing about
    whether the request reached the payment ledger before the connection dropped.
    """


class Gateway:
    """Client for the payment gateway. Tests pass a fake with the same interface."""

    def create_charge(self, order_id, amount_cents, idempotency_key=None):
        """Create a charge and return its charge id.

        Without an idempotency_key, every call that reaches the gateway creates a
        new charge. Calls that share an idempotency_key create at most one
        charge: a repeat returns the first call's charge id instead of charging
        again. Raises GatewayTimeout when the gateway does not answer in time.
        """
        raise NotImplementedError("network client")
