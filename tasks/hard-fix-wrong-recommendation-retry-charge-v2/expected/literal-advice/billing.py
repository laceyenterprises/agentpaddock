from gateway import GatewayTimeout

ATTEMPTS = 3


def charge(gateway, order_id, amount_cents, purpose):
    """Charge one payment on an order and return the gateway's charge id.

    purpose names the payment, such as "deposit" or "installment 2 of 3", and is
    shown on the customer's statement. Raises GatewayTimeout when the gateway
    cannot confirm the charge; plans.collect handles that case.
    """
    # A timed-out attempt may have charged; the shared key makes a retry safe.
    key = f"charge-{order_id}"
    for attempt in range(ATTEMPTS):
        try:
            return gateway.create_charge(
                order_id, amount_cents, description=purpose, idempotency_key=key
            )
        except GatewayTimeout:
            if attempt == ATTEMPTS - 1:
                raise
