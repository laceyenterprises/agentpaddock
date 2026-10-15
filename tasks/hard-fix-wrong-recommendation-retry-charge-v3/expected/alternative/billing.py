from gateway import GatewayTimeout


def charge(gateway, order_id, amount_cents, purpose, payment_id):
    key = f"{len(order_id)}:{order_id}{len(payment_id)}:{payment_id}"
    for remaining in (2, 1, 0):
        try:
            return gateway.create_charge(
                order_id, amount_cents, description=purpose, idempotency_key=key
            )
        except GatewayTimeout:
            if not remaining:
                raise
