from gateway import GatewayTimeout


def charge(gateway, order_id, amount_cents, purpose, payment_id):
    for attempt in range(3):
        try:
            return gateway.create_charge(
                order_id, amount_cents, description=purpose, idempotency_key=None
            )
        except GatewayTimeout:
            if attempt == 2:
                raise
